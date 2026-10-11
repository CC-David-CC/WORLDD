#include "platform/compositor_config.hpp"
#include "core/protocol.hpp"
#include <cerrno>
#include <climits>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace spatial {
namespace {
void require(bool okay, const char* message) {
    if (!okay) throw std::invalid_argument(std::string("Native compositor configuration: ") + message);
}
void erase(std::string& bytes) {
    if (!bytes.empty()) explicit_bzero(bytes.data(), bytes.size());
    bytes.clear();
}
}
CompositorConfig::CompositorConfig(int descriptor) {
    require(descriptor > STDERR_FILENO, "expected private descriptor");
    descriptor_ = descriptor;
    std::string bytes;
    try {
        require(prctl(PR_SET_DUMPABLE, 0) == 0, "cannot disable process inspection");
        constexpr int seals = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
        const int actual = fcntl(descriptor_, F_GET_SEALS);
        struct stat info{};
        require(actual >= 0 && (actual & seals) == seals && fstat(descriptor_, &info) == 0 &&
                S_ISREG(info.st_mode) && info.st_size > 0 && info.st_size <= 1048576,
                "expected sealed bounded protobuf");
        const int flags = fcntl(descriptor_, F_GETFD);
        require(flags >= 0 && fcntl(descriptor_, F_SETFD, flags | FD_CLOEXEC) == 0,
                "cannot restrict descriptor inheritance");
        bytes.resize(static_cast<std::size_t>(info.st_size));
        std::size_t used = 0;
        while (used < bytes.size()) {
            const auto count = pread(descriptor_, bytes.data() + used, bytes.size() - used, used);
            if (count < 0 && errno == EINTR) continue;
            require(count > 0, "cannot read sealed protobuf");
            used += static_cast<std::size_t>(count);
        }
        require(value_.ParseFromString(bytes), "malformed protobuf");
        try { validate_message(value_); }
        catch (const std::exception&) { require(false, "unsupported protobuf fields"); }
        require(value_.protocol_version() == 1 && value_.has_display_number() && value_.display_number() <= 65535 &&
                value_.compositor_cookie().size() == 32 && value_.server_id().size() == 32 &&
                value_.has_server_pid() && value_.server_pid() > 0 && value_.server_pid() <= INT_MAX &&
                value_.has_server_uid(), "invalid protocol, credential, or server pins");
        erase(bytes);
    } catch (...) {
        erase(bytes);
        consume();
        throw;
    }
}
CompositorConfig::~CompositorConfig() { consume(); }
std::string CompositorConfig::display() const { return ":" + std::to_string(value_.display_number()); }
void CompositorConfig::consume() {
    if (descriptor_ >= 0) close(descriptor_);
    descriptor_ = -1;
    erase(*value_.mutable_compositor_cookie());
}
}
