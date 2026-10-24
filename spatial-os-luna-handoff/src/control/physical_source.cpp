#include "control/physical_source.hpp"
#include <charconv>
#include <fcntl.h>
#include <fstream>
#include <linux/input-event-codes.h>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>
#include <vector>

namespace spatial::control::physical_detail {
namespace {
struct Fd {
    int value;
    ~Fd() { if (value >= 0) ::close(value); }
};
std::optional<std::string> small_file(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input) return {};
    std::string data(4097, '\0');
    input.read(data.data(), static_cast<std::streamsize>(data.size()));
    const auto count = input.gcount();
    if (!input.eof() || count <= 0 || count > 4096) return {};
    data.resize(static_cast<std::size_t>(count));
    return data;
}
bool bit(const std::filesystem::path& path, unsigned index) {
    const auto data = small_file(path);
    if (!data) return false;
    std::istringstream input(*data);
    std::vector<unsigned long> words;
    std::string word;
    while (input >> word) {
        unsigned long value = 0;
        const auto result = std::from_chars(word.data(), word.data() + word.size(), value, 16);
        if (result.ec != std::errc{} || result.ptr != word.data() + word.size()) return false;
        words.push_back(value);
    }
    constexpr unsigned bits = sizeof(unsigned long) * 8;
    const auto offset = index / bits;
    return offset < words.size() && (words[words.size() - 1 - offset] & (1UL << (index % bits))) != 0;
}
}
std::optional<KernelIdentity> kernel_identity(const std::filesystem::path& configured, v1::DeviceKind kind) {
    if (!configured.is_absolute() || (kind != v1::KEYBOARD && kind != v1::MOUSE)) return {};
    std::error_code error;
    const auto node = std::filesystem::canonical(configured, error);
    if (error) return {};
    Fd fd{open(node.c_str(), O_PATH | O_NOFOLLOW | O_CLOEXEC)};
    struct stat pinned{};
    if (fd.value < 0 || fstat(fd.value, &pinned) != 0 || !S_ISCHR(pinned.st_mode)) return {};
    const auto metadata = std::filesystem::canonical(std::filesystem::path("/sys/dev/char") /
        (std::to_string(major(pinned.st_rdev)) + ":" + std::to_string(minor(pinned.st_rdev))), error);
    if (error || std::filesystem::canonical(metadata / "subsystem", error) != "/sys/class/input" || error) return {};
    const auto name = metadata.filename().string();
    if (name.size() <= 5 || name.compare(0, 5, "event") != 0 || name.find_first_not_of("0123456789", 5) != std::string::npos)
        return {};
    const auto number = small_file(metadata / "dev");
    if (!number || *number != std::to_string(major(pinned.st_rdev)) + ":" + std::to_string(minor(pinned.st_rdev)) + "\n")
        return {};
    const auto capabilities = metadata / "device/capabilities";
    if (!bit(capabilities / "ev", EV_KEY)) return {};
    if (kind == v1::KEYBOARD) {
        if (!bit(capabilities / "key", KEY_A) || !bit(capabilities / "key", KEY_Z) || !bit(capabilities / "key", KEY_ENTER)) return {};
    } else if (!bit(capabilities / "ev", EV_REL) || !bit(capabilities / "rel", REL_X) ||
               !bit(capabilities / "rel", REL_Y) || !bit(capabilities / "key", BTN_LEFT) || !bit(capabilities / "key", BTN_RIGHT))
        return {};
    struct stat current{};
    if (stat(node.c_str(), &current) != 0 || current.st_dev != pinned.st_dev || current.st_ino != pinned.st_ino ||
        current.st_rdev != pinned.st_rdev || std::filesystem::canonical(configured, error) != node || error)
        return {};
    return KernelIdentity{node, pinned.st_dev, pinned.st_ino, pinned.st_rdev};
}
}
