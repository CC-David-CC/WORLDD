#include "workspace/native_launch.hpp"
#include "x11/native_channel.hpp"
#include "supervisor/native_scope.hpp"
#include <iostream>
#include "core/protocol.hpp"
#include "world/store.hpp"
#include "broker.pb.h"
#include "security.pb.h"
#include <array>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <linux/memfd.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <unistd.h>

namespace spatial {
namespace {
namespace native_security = ::spatial::security::v1;
using Clock = std::chrono::steady_clock;
struct Fd {
    int value = -1;
    ~Fd() { reset(); }
    void reset() { if (value >= 0) close(value); value = -1; }
};
[[noreturn]] void unavailable() { throw WorldError(wire::UNAVAILABLE, "Native application authority channel is unavailable"); }
void require(bool ok, const char* message) { if (!ok) throw std::invalid_argument(message); }

std::string sealed_configuration(int fd) {
    struct stat info{};
    constexpr int seals = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
    require(fd >= 3 && fstat(fd, &info) == 0 && S_ISREG(info.st_mode) && info.st_size > 0 && info.st_size <= 4096,
            "Native launch configuration requires a bounded inherited file");
    const int actual = fcntl(fd, F_GET_SEALS);
    require(actual >= 0 && (actual & seals) == seals, "Native launch configuration must be fully sealed");
    std::string data(static_cast<std::size_t>(info.st_size), '\0');
    std::size_t used = 0;
    while (used < data.size()) {
        const auto count = pread(fd, data.data()+used, data.size()-used, static_cast<off_t>(used));
        if (count < 0 && errno == EINTR) continue;
        require(count > 0, "Incomplete native launch configuration"); used += static_cast<std::size_t>(count);
    }
    return data;
}
workspace::v1::NativeLaunchConfig configuration(int fd) {
    Fd input{fd}; // NativeLaunch consumes its inherited configuration; the reader only borrows it.
    const auto data = sealed_configuration(fd);
    workspace::v1::NativeLaunchConfig result;
    require(result.ParseFromString(data), "Malformed native launch configuration");
    validate_message(result);
    require(result.protocol_version() == 1 && result.has_display_number() && result.display_number() <= 65535 &&
            result.broker_cookie().size() == 32 && result.server_id().size() == 32 && result.has_server_pid() &&
            result.server_pid() > 0 && result.has_server_uid(), "Invalid native launch configuration");
    return result;
}
int authority_file(const std::string& number, const std::string& cookie) {
    std::string bytes(2, static_cast<char>(255)); // FamilyWild, native Xauthority format.
    for (const auto& value : {std::string(), number, std::string("MIT-MAGIC-COOKIE-1"), cookie}) {
        bytes += static_cast<char>(value.size() >> 8); bytes += static_cast<char>(value.size()); bytes += value;
    }
    Fd file{static_cast<int>(syscall(SYS_memfd_create, "spatial-application-authority", MFD_CLOEXEC | MFD_ALLOW_SEALING))};
    if (file.value < 0) unavailable();
    std::size_t used = 0;
    while (used < bytes.size()) {
        const auto count = write(file.value, bytes.data()+used, bytes.size()-used);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) unavailable();
        used += static_cast<std::size_t>(count);
    }
    if (lseek(file.value, 0, SEEK_SET) < 0 || fcntl(file.value, F_ADD_SEALS,
            F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) < 0) unavailable();
    const int result = file.value; file.value = -1; return result;
}
}

struct NativeLaunch::Impl : NativeChannel {};

NativeLaunch::NativeLaunch(int sealed_config_fd) : impl_(std::make_unique<Impl>()) {
    if (prctl(PR_SET_DUMPABLE, 0) != 0) unavailable();
    impl_->connect_server(configuration(sealed_config_fd), native_security::BROKER);
}
NativeLaunch::~NativeLaunch() = default;
const std::string& NativeLaunch::server_id() const { return impl_->server_id(); }

std::unique_ptr<NativeCredential> NativeLaunch::issue(const std::string& workspace, const std::string& application,
                                                     const std::function<void()>& checkpoint) {
    checkpoint();
    check();
    native_security::Request request; auto* issue = request.mutable_issue();
    issue->set_role(native_security::APPLICATION); issue->set_workspace(workspace); issue->set_application(application);
    // Finish the bounded native exchange before rechecking actor authority.
    // An actor revocation must not tear down other applications' shared issuer.
    const auto reply = impl_->call(request);
    if (reply.code() == native_security::RESOURCE_EXHAUSTED) throw WorldError(wire::RESOURCE_EXHAUSTED, "Native credential budget exhausted");
    std::unique_ptr<NativeCredential> result;
    try {
        const auto& credential = reply.credential();
        if (reply.code() != native_security::OK || !reply.has_credential() || credential.id().size() != 32 || credential.cookie().size() != 32 ||
                credential.role() != native_security::APPLICATION || credential.workspace() != workspace || credential.application() != application) unavailable();
        Fd file{authority_file(impl_->number(), credential.cookie())};
        result.reset(new NativeCredential(*this, credential.id(), file.value));
        file.value = -1;
    } catch (...) { impl_->reset(); throw; }
    checkpoint(); // The credential destructor revokes only this tentative grant on denial.
    return result;
}
void NativeLaunch::revoke(const std::string& id) noexcept {
    if (!impl_->connected()) return;
    try {
        native_security::Request request; request.mutable_revoke()->set_credential_id(id);
        const auto reply = impl_->call(request);
        if (reply.code() != native_security::OK || !reply.has_revoked() || !reply.revoked()) impl_->reset();
    } catch (...) { impl_->reset(); }
}
void NativeLaunch::check() { impl_->check(); }

NativeCredential::NativeCredential(NativeLaunch& owner, std::string id, int fd) : owner_(owner), id_(std::move(id)), authority_fd_(fd) {}
NativeCredential::~NativeCredential() { close(authority_fd_); owner_.revoke(id_); }
int NativeCredential::socket_fd() const { return owner_.impl_->socket_fd(); }
const std::string& NativeCredential::display_number() const { return owner_.impl_->number(); }

namespace supervisor {
struct NativeScope::Impl {
    std::unique_ptr<NativeLaunch::Impl> native = std::make_unique<NativeLaunch::Impl>();
    v1::NativeScopeReady ready;
    bool stopped = false;
};

NativeScope::NativeScope(int sealed_config_fd) : impl_(std::make_unique<Impl>()) {
    if (prctl(PR_SET_DUMPABLE, 0) != 0) unavailable();
    const auto bytes = sealed_configuration(sealed_config_fd);
    v1::NativeScopeConfig config;
    require(config.ParseFromString(bytes), "Malformed native scope configuration");
    validate_message(config);
    require(config.protocol_version() == 1 && config.has_display_number() && config.display_number() <= 65535 &&
            config.supervisor_cookie().size() == 32 && config.server_id().size() == 32 &&
            config.has_server_pid() && config.server_pid() > 0 && config.has_server_uid() &&
            config.launch_id().size() == 32 &&
            (config.role() == native_security::BROKER || config.role() == native_security::COMPOSITOR),
            "Invalid native scope configuration");
    workspace::v1::NativeLaunchConfig connection;
    connection.set_display_number(config.display_number()); connection.set_broker_cookie(config.supervisor_cookie());
    connection.set_server_id(config.server_id()); connection.set_server_pid(config.server_pid());
    connection.set_server_uid(config.server_uid());
    impl_->native->connect_server(connection, native_security::SUPERVISOR);
    native_security::Request issue; issue.mutable_issue()->set_role(config.role());
    const auto result = impl_->native->call(issue);
    const auto& grant = result.credential();
    if (result.code() != native_security::OK || !result.has_credential() || grant.id().size() != 32 ||
            grant.cookie().size() != 32 || grant.role() != config.role() ||
            !grant.workspace().empty() || !grant.application().empty()) unavailable();
    *impl_->ready.mutable_credential() = grant;
    auto* identity = impl_->ready.mutable_identity();
    identity->set_launch_id(config.launch_id()); identity->set_server_id(config.server_id());
    identity->set_credential_id(grant.id()); identity->set_role(grant.role());
}
v1::NativeScopeReady NativeScope::ready() const {
    require(!impl_->stopped, "Native authority scope has stopped");
    return impl_->ready;
}
v1::NativeScopeStopped NativeScope::stop() {
    if (!impl_->stopped) {
        native_security::Request request;
        request.mutable_revoke()->set_credential_id(impl_->ready.identity().credential_id());
        const auto reply = impl_->native->call(request);
        if (reply.code() != native_security::OK || !reply.has_revoked() || !reply.revoked()) {
            impl_->native->reset(); unavailable();
        }
        impl_->stopped = true;
        impl_->ready.clear_credential();
    }
    v1::NativeScopeStopped result; *result.mutable_identity() = impl_->ready.identity(); return result;
}
NativeScope::~NativeScope() {
    try { stop(); }
    catch (const std::exception& error) {
        std::cerr << "native authority cleanup unconfirmed: " << error.what() << '\n'; _exit(2);
    }
}
}
}
