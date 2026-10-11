#include "workspace/storage_devices.hpp"
#include "identity/tls_support.hpp"
#include <charconv>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <linux/btrfs.h>
#include <linux/magic.h>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/sysmacros.h>
#include <unistd.h>

namespace spatial {
namespace {
using Budget = supervisor::v1::ProcessScopeBudget;
using Device = std::pair<unsigned, unsigned>;
unsigned device_major(dev_t value) { return major(value); }
unsigned device_minor(dev_t value) { return minor(value); }
#undef major
#undef minor
void require(bool value, const char* message) { if (!value) throw std::invalid_argument(message); }
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
std::string bounded_read(int descriptor) {
    std::string result; char bytes[4096];
    for (;;) {
        const auto count = read(descriptor, bytes, sizeof(bytes));
        if (count < 0 && errno == EINTR) continue;
        check(count >= 0 && result.size()+static_cast<std::size_t>(count) <= 65536,
              "Cannot read bounded storage I/O state");
        if (!count) return result;
        result.append(bytes, static_cast<std::size_t>(count));
    }
}
template<class T> T number(const std::string& value) {
    T result{};
    const auto parsed = std::from_chars(value.data(), value.data()+value.size(), result);
    require(!value.empty() && parsed.ec == std::errc{} && parsed.ptr == value.data()+value.size(),
            "Invalid kernel storage I/O limit");
    return result;
}
Budget kernel_io(const std::string& contents) {
    Budget budget; std::set<Device> devices;
    std::istringstream input(contents); std::string line;
    while (std::getline(input, line)) {
        if (line.empty()) continue;
        std::istringstream fields(line); std::string identity, field;
        require(static_cast<bool>(fields >> identity), "Missing kernel storage device");
        const auto colon = identity.find(':');
        require(colon != std::string::npos, "Invalid kernel storage device");
        Device device{number<unsigned>(identity.substr(0, colon)), number<unsigned>(identity.substr(colon+1))};
        require(devices.insert(device).second, "Duplicate kernel storage device");
        std::map<std::string, std::uint64_t> limits;
        while (fields >> field) {
            const auto equal = field.find('=');
            require(equal != std::string::npos, "Invalid kernel storage I/O field");
            const auto key = field.substr(0, equal), value = field.substr(equal+1);
            require(key == "rbps" || key == "wbps" || key == "riops" || key == "wiops",
                    "Unknown kernel storage I/O field");
            const auto limit = value == "max" ? 0 : number<std::uint64_t>(value);
            require(limits.emplace(key, limit).second && (value == "max" || limit > 0),
                    "Duplicate or zero kernel storage I/O limit");
        }
        // An unlimited or missing direction cannot establish bounded device coverage.
        if (limits.contains("rbps") && limits.contains("wbps") && limits.at("rbps") && limits.at("wbps")) {
            auto* io = budget.add_io(); io->set_major(device.first); io->set_minor(device.second);
            io->set_read_bytes_per_second(limits.at("rbps")); io->set_write_bytes_per_second(limits.at("wbps"));
        }
    }
    return budget;
}
}
void validate_storage_device_handles(const Budget& budget, std::span<const int> directories) {
    std::set<Device> covered, observed;
    for (const auto& io : budget.io()) covered.emplace(io.major(), io.minor());
    std::function<void(const std::filesystem::path&, unsigned)> filesystem_device;
    std::function<void(int, unsigned)> descriptor_device;
    const auto block_device = [&](dev_t number, unsigned depth) {
        const auto link = std::filesystem::path("/sys/dev/block") /
            (std::to_string(device_major(number)) + ":" + std::to_string(device_minor(number)));
        auto device = std::filesystem::canonical(link);
        if (std::filesystem::exists(device / "partition")) device = device.parent_path();
        std::ifstream stream(device / "dev"); unsigned major_value = 0, minor_value = 0; char colon = 0;
        require(static_cast<bool>(stream >> major_value >> colon >> minor_value) && colon == ':' &&
                covered.contains({major_value, minor_value}), "Workspace storage device has no I/O budget");
        if (!observed.emplace(major_value, minor_value).second) return;
        // A loop volume submits work to its own queue and writes a real backing file.
        // Both queues need explicit budgets. Never infer a device from Btrfs st_dev.
        if (std::filesystem::exists(device / "loop/backing_file")) {
            std::ifstream backing(device / "loop/backing_file"); std::string path; std::getline(backing, path);
            require(backing.good() || backing.eof(), "Cannot read loop backing storage identity");
            require(std::filesystem::path(path).is_absolute(), "Loop backing storage identity is unavailable");
            filesystem_device(path, depth+1);
        }
        if (std::filesystem::exists(device / "slaves")) {
            for (const auto& child : std::filesystem::directory_iterator(device / "slaves")) {
                std::ifstream identity(child.path() / "dev"); unsigned child_major = 0, child_minor = 0; char separator = 0;
                require(static_cast<bool>(identity >> child_major >> separator >> child_minor) && separator == ':' &&
                        covered.contains({child_major, child_minor}), "Stacked storage device has no I/O budget");
            }
        }
    };
    filesystem_device = [&](const std::filesystem::path& path, unsigned depth) {
        identity::tls_detail::Fd file(open(path.c_str(), O_PATH | O_NOFOLLOW | O_CLOEXEC));
        check(file.value >= 0, "Cannot pin workspace storage device");
        descriptor_device(file.value, depth);
    };
    descriptor_device = [&](int descriptor, unsigned depth) {
        require(depth < 8, "Storage device nesting exceeds supported bounds");
        struct stat info{}; struct statfs filesystem{};
        check(descriptor >= 0 && fstat(descriptor, &info) == 0 && fstatfs(descriptor, &filesystem) == 0,
              "Cannot identify workspace storage device");
        if (filesystem.f_type == BTRFS_SUPER_MAGIC) {
            // Reopen the held object, rather than resolving its caller-owned path again.
            const auto held = "/proc/self/fd/" + std::to_string(descriptor);
            identity::tls_detail::Fd query(open(held.c_str(), O_RDONLY | O_CLOEXEC));
            struct stat pinned{};
            check(query.value >= 0 && fstat(query.value, &pinned) == 0 && pinned.st_dev == info.st_dev && pinned.st_ino == info.st_ino,
                  "Cannot pin quota filesystem query authority");
            btrfs_ioctl_fs_info_args volume{};
            check(ioctl(query.value, BTRFS_IOC_FS_INFO, &volume) >= 0, "Cannot inspect quota volume devices");
            require(volume.num_devices == 1, "M03 quota storage requires one device");
            btrfs_ioctl_dev_info_args selected{}; selected.devid = volume.max_id;
            check(ioctl(query.value, BTRFS_IOC_DEV_INFO, &selected) >= 0, "Cannot inspect quota volume block device");
            const auto length = strnlen(reinterpret_cast<const char*>(selected.path), sizeof(selected.path));
            require(length > 0 && length < sizeof(selected.path), "Invalid quota volume block device path");
            const std::filesystem::path device(std::string(reinterpret_cast<const char*>(selected.path), length));
            struct stat block{};
            check(device.is_absolute() && stat(device.c_str(), &block) == 0 && S_ISBLK(block.st_mode),
                  "Quota volume block device is unavailable");
            block_device(block.st_rdev, depth);
        } else block_device(info.st_dev, depth);
    };
    for (const auto descriptor : directories) {
        struct stat info{};
        check(descriptor >= 0 && fstat(descriptor, &info) == 0 && S_ISDIR(info.st_mode),
              "Workspace storage boundary requires a directory");
        descriptor_device(descriptor, 0);
    }
}
void validate_current_storage_io(int directory) {
    identity::tls_detail::Fd membership(open("/proc/self/cgroup", O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
    check(membership.value >= 0, "Cannot inspect storage service cgroup");
    const auto text = bounded_read(membership.value);
    require(text.starts_with("0::/") && text.ends_with('\n') && text.find('\n') == text.size()-1,
            "Storage service requires one unified cgroup membership");
    const std::filesystem::path relative(text.substr(4, text.size()-5));
    require(!relative.empty() && !relative.is_absolute(), "Invalid storage service cgroup path");
    for (const auto& part : relative) require(part != "." && part != "..", "Invalid storage cgroup component");
    const auto path = std::filesystem::path("/sys/fs/cgroup") / relative;
    require(std::filesystem::canonical(path) == path, "Storage service cgroup path is not canonical");
    identity::tls_detail::Fd group(open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    struct statfs filesystem{};
    check(group.value >= 0 && fstatfs(group.value, &filesystem) == 0 && filesystem.f_type == CGROUP2_SUPER_MAGIC,
          "Storage service requires the kernel resource hierarchy");
    identity::tls_detail::Fd limits(openat(group.value, "io.max", O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
    check(limits.value >= 0, "Storage service requires enforced I/O limits");
    const auto budget = kernel_io(bounded_read(limits.value));
    validate_storage_device_handles(budget, std::span<const int>(&directory, 1));
}
}
