#pragma once

#include <array>
#include <cerrno>
#include <fcntl.h>
#include <string>
#include <string_view>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdexcept>

namespace spatial::workspace::detail {
inline constexpr std::string_view broker_instance_id_name = ".broker-instance-id";
inline constexpr std::string_view broker_instance_lock_name = ".broker-instance.lock";

inline bool is_broker_state_metadata_name(std::string_view name) noexcept {
    return name == broker_instance_id_name || name == broker_instance_lock_name;
}

inline void validate_broker_state_metadata(int root_fd, uid_t broker_uid) {
    auto fail = [](const char* message) { throw std::invalid_argument(message); };
    auto open_checked = [&](std::string_view name) {
        const std::string owned_name(name);
        const int fd = openat(root_fd, owned_name.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
        if (fd < 0) fail("Fresh storage volume has invalid broker state metadata");
        struct stat opened{}, named{};
        if (fstat(fd, &opened) != 0 || fstatat(root_fd, owned_name.c_str(), &named, AT_SYMLINK_NOFOLLOW) != 0 ||
            !S_ISREG(opened.st_mode) || opened.st_uid != broker_uid || (opened.st_mode & 07777) != 0600 ||
            opened.st_nlink != 1 || opened.st_dev != named.st_dev || opened.st_ino != named.st_ino) {
            close(fd);
            fail("Fresh storage volume has unsafe broker state metadata");
        }
        return fd;
    };

    const int identity_fd = open_checked(broker_instance_id_name);
    struct stat identity_stat{};
    if (fstat(identity_fd, &identity_stat) != 0 || identity_stat.st_size != 32) {
        close(identity_fd);
        fail("Fresh storage volume has malformed broker identity metadata");
    }
    std::array<char, 32> identity{};
    ssize_t count;
    do { count = pread(identity_fd, identity.data(), identity.size(), 0); } while (count < 0 && errno == EINTR);
    close(identity_fd);
    if (count != static_cast<ssize_t>(identity.size())) fail("Fresh storage volume has malformed broker identity metadata");
    for (unsigned char value : identity)
        if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'f')))
            fail("Fresh storage volume has malformed broker identity metadata");

    const int lock_fd = open_checked(broker_instance_lock_name);
    struct stat lock_stat{};
    if (fstat(lock_fd, &lock_stat) != 0 || lock_stat.st_size != 0) {
        close(lock_fd);
        fail("Fresh storage volume has malformed broker lock metadata");
    }
    const int lock_result = flock(lock_fd, LOCK_SH | LOCK_NB);
    const int lock_error = lock_result == 0 ? 0 : errno;
    if (lock_result == 0) {
        flock(lock_fd, LOCK_UN);
        close(lock_fd);
        fail("Fresh storage volume broker identity is not exclusively owned");
    }
    close(lock_fd);
    if (lock_error != EWOULDBLOCK && lock_error != EAGAIN)
        fail("Fresh storage volume broker identity lock cannot be verified");
}
} // namespace spatial::workspace::detail
