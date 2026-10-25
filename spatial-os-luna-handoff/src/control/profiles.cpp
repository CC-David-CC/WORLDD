#include "control/profiles.hpp"
#include "core/protocol.hpp"
#include <X11/Xlib.h>
#include <array>
#include <cerrno>
#include <cmath>
#include <fcntl.h>
#include <set>
#include <stdexcept>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>

namespace spatial::control {
namespace {
void need(bool value, const char* message) { if (!value) throw std::invalid_argument(message); }
bool name(const std::string& value, std::size_t limit) {
    return !value.empty() && value.size() <= limit && value.find('\0') == std::string::npos &&
           value.find_first_of("\r\n\t") == std::string::npos;
}
struct Fd {
    int value;
    explicit Fd(int fd) : value(fd) { if (fd < 0) throw std::runtime_error("Cannot open control preferences"); }
    ~Fd() { ::close(value); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
};
void private_regular(int fd) {
    struct stat st{};
    need(fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == geteuid() &&
         (st.st_mode & 0777) == 0600 && st.st_nlink == 1 && st.st_size <= 65536,
         "Control preferences require a private bounded regular file");
}
}
const v1::ControlProfile& active_profile(const v1::ControlProfiles& profiles) {
    for (const auto& profile : profiles.profiles()) if (profile.id() == profiles.active_profile()) return profile;
    throw std::invalid_argument("Active control profile is missing");
}
void validate_profiles(const v1::ControlProfiles& profiles) {
    validate_message(profiles);
    need(profiles.protocol_version() == 1 && profiles.profiles_size() >= 1 && profiles.profiles_size() <= 32 &&
         profiles.ByteSizeLong() <= 65536, "Invalid control profile collection");
    std::set<std::string> ids;
    for (const auto& profile : profiles.profiles()) {
        need(name(profile.id(), 64) && name(profile.label(), 80) && ids.insert(profile.id()).second,
             "Invalid or duplicate control profile identity");
        need(profile.movement() == v1::FREE_FLIGHT || profile.movement() == v1::LEVEL_MOVEMENT,
             "Unknown control movement style");
        need(profile.interaction() == v1::CLICK_TO_CONTROL || profile.interaction() == v1::TOGGLE_CONTROL ||
             profile.interaction() == v1::HOLD_TO_NAVIGATE, "Unknown control interaction style");
        need(profile.key_delivery() == v1::KEY_TAP || profile.key_delivery() == v1::KEY_HOLD,
             "Unknown application key delivery style");
        need(std::isfinite(profile.mouse_radians_per_count()) && profile.mouse_radians_per_count() > 0 &&
             profile.mouse_radians_per_count() <= .1, "Invalid mouse sensitivity");
        need(profile.bindings_size() <= 64, "Too many control bindings");
        std::set<std::string> keys;
        std::set<int> actions;
        for (const auto& binding : profile.bindings()) {
            need(name(binding.keysym(), 64) && keys.insert(binding.keysym()).second &&
                 XStringToKeysym(binding.keysym().c_str()) != NoSymbol &&
                 binding.action() >= v1::FORWARD && binding.action() <= v1::CANCEL &&
                 actions.insert(binding.action()).second, "Invalid or conflicting control binding");
        }
        need(actions.contains(v1::SWITCH_MODE) && actions.contains(v1::OPEN_CONTROLS) && actions.contains(v1::CANCEL),
             "Control profiles require distinct mode, menu, and cancellation bindings");
        need(profile.spacemouse_axes_size() == (profile.spacemouse_calibrated() ? 6 : 0),
             "SpaceMouse calibration requires exactly six axes or an explicit uncalibrated profile");
        std::set<int> axes;
        for (const auto& axis : profile.spacemouse_axes()) {
            need(axis.raw_axis() >= -6 && axis.raw_axis() <= 6 && axis.raw_axis() != 0 &&
                 axes.insert(std::abs(axis.raw_axis())).second && std::isfinite(axis.center()) &&
                 std::abs(axis.center()) <= 1e9 && std::isfinite(axis.full_scale()) &&
                 axis.full_scale() > 0 && axis.full_scale() <= 1e9, "Invalid signed axis calibration");
        }
    }
    active_profile(profiles);
}
v1::ControlProfiles default_profiles() {
    v1::ControlProfiles result; result.set_protocol_version(1); result.set_active_profile("level-click");
    for (const auto movement : {v1::FREE_FLIGHT, v1::LEVEL_MOVEMENT}) {
        for (const auto interaction : {v1::CLICK_TO_CONTROL, v1::TOGGLE_CONTROL, v1::HOLD_TO_NAVIGATE}) {
            auto* p = result.add_profiles(); p->set_movement(movement); p->set_interaction(interaction);
            p->set_key_delivery(v1::KEY_TAP);
            const std::string movement_id = movement == v1::FREE_FLIGHT ? "flight" : "level";
            const std::string interaction_id = interaction == v1::CLICK_TO_CONTROL ? "click" :
                interaction == v1::TOGGLE_CONTROL ? "toggle" : "hold";
            p->set_id(movement_id + "-" + interaction_id);
            p->set_label((movement == v1::FREE_FLIGHT ? "Flight / " : "Level movement / ") + interaction_id);
            p->set_mouse_radians_per_count(.0025);
            const std::pair<const char*, v1::BindingAction> bindings[] = {
                {"w",v1::FORWARD},{"s",v1::BACKWARD},{"a",v1::LEFT},{"d",v1::RIGHT},
                {"space",v1::UP},{"Control_L",v1::DOWN},{"q",v1::ROLL_LEFT},{"e",v1::ROLL_RIGHT},
                {"F9",v1::SWITCH_MODE},{"F10",v1::OPEN_CONTROLS},{"F12",v1::CANCEL}};
            for (const auto& [key, action] : bindings) {
                auto* b = p->add_bindings(); b->set_keysym(key); b->set_action(action);
            }
        }
    }
    validate_profiles(result); return result;
}
v1::ControlProfiles load_profiles(const std::filesystem::path& path) {
    need(path.is_absolute(), "Control preference path must be absolute");
    Fd fd(open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC)); private_regular(fd.value);
    struct stat st{}; need(fstat(fd.value, &st) == 0 && st.st_size > 0, "Empty control preferences");
    std::string bytes(static_cast<std::size_t>(st.st_size), '\0');
    std::size_t offset = 0;
    while (offset < bytes.size()) {
        const auto count = read(fd.value, bytes.data()+offset, bytes.size()-offset);
        if (count < 0 && errno == EINTR) continue;
        need(count > 0, "Truncated control preferences"); offset += static_cast<std::size_t>(count);
    }
    char extra; need(read(fd.value, &extra, 1) == 0, "Control preferences changed during read");
    v1::ControlProfiles profiles;
    need(profiles.ParseFromString(bytes), "Malformed control preferences");
    validate_profiles(profiles); return profiles;
}
void save_profiles(const std::filesystem::path& path, const v1::ControlProfiles& profiles) {
    validate_profiles(profiles);
    need(path.is_absolute() && name(path.filename().string(), 128), "Invalid control preference path");
    const auto parent = path.parent_path();
    need(std::filesystem::canonical(parent) == parent, "Control preference directory must be canonical");
    Fd directory(open(parent.c_str(), O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
    struct stat st{}; need(fstat(directory.value, &st) == 0 && st.st_uid == geteuid() &&
        (st.st_mode & 0077) == 0, "Control preference directory must be private and owned");
    // Never replace a symlink, another user's file, or a multiply linked file.
    if (fstatat(directory.value, path.filename().c_str(), &st, AT_SYMLINK_NOFOLLOW) == 0) {
        need(S_ISREG(st.st_mode) && st.st_uid == geteuid() && (st.st_mode & 0777) == 0600 && st.st_nlink == 1,
             "Unsafe existing control preference destination");
    } else need(errno == ENOENT, "Cannot inspect control preference destination");
    std::array<unsigned char, 16> nonce{};
    need(getrandom(nonce.data(), nonce.size(), 0) == static_cast<ssize_t>(nonce.size()), "Cannot obtain preference nonce");
    constexpr char hex[] = "0123456789abcdef";
    std::string temporary = ".control-";
    for (const auto byte : nonce) { temporary += hex[byte >> 4]; temporary += hex[byte & 15]; }
    Fd fd(openat(directory.value, temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
    try {
        const auto bytes = profiles.SerializeAsString();
        std::size_t offset = 0;
        while (offset < bytes.size()) {
            const auto count = write(fd.value, bytes.data()+offset, bytes.size()-offset);
            if (count < 0 && errno == EINTR) continue;
            need(count > 0, "Cannot write control preferences"); offset += static_cast<std::size_t>(count);
        }
        need(fsync(fd.value) == 0, "Cannot sync control preferences");
        need(renameat(directory.value, temporary.c_str(), directory.value, path.filename().c_str()) == 0,
             "Cannot publish control preferences");
        need(fsync(directory.value) == 0, "Control preference publication durability is uncertain");
    } catch (...) { unlinkat(directory.value, temporary.c_str(), 0); throw; }
}
}
