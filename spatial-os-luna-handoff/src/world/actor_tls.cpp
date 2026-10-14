#include "world/actor_tls.hpp"
#include "core/protocol.hpp"
#include "world_session.pb.h"
#include <google/protobuf/text_format.h>
#include <openssl/err.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <netinet/in.h>
#include <poll.h>
#include <optional>
#include <sys/socket.h>

namespace spatial {
namespace {
using Clock = std::chrono::steady_clock;
using Milliseconds = std::chrono::milliseconds;
using identity::tls_detail::require;
int available(int descriptor, short wanted, SSL* tls = nullptr) {
    pollfd fd{descriptor, wanted, 0};
    const auto result = poll(&fd, 1, 0);
    if (result < 0 && errno == EINTR) return 0;
    if (result < 0 || (fd.revents & (POLLHUP | POLLERR | POLLNVAL))) return -1;
    return (fd.revents & wanted) || (tls && wanted == POLLIN && SSL_pending(tls) > 0) ? 1 : 0;
}
}
void prepare_actor_listener(int fd) {
    require(fd > STDERR_FILENO, "Actor TLS listener must be above stderr");
    sockaddr_storage address{}; socklen_t size = sizeof(address);
    int accepting = 0, type = 0, protocol = 0; socklen_t option = sizeof(int);
    require(getsockname(fd, reinterpret_cast<sockaddr*>(&address), &size) == 0 &&
        (address.ss_family == AF_INET || address.ss_family == AF_INET6) &&
        getsockopt(fd, SOL_SOCKET, SO_ACCEPTCONN, &accepting, &option) == 0 && accepting &&
        getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &option) == 0 && type == SOCK_STREAM &&
        getsockopt(fd, SOL_SOCKET, SO_PROTOCOL, &protocol, &option) == 0 && protocol == IPPROTO_TCP,
        "Actor TLS listener must be a listening TCP socket");
    identity::tls_detail::nonblocking(fd);
}
ActorTlsListener::ActorTlsListener(int fd, const std::filesystem::path& path) : descriptor_(fd) {
    prepare_actor_listener(fd);
    identity::v1::WorldActorTlsConfig config;
    require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(path), &config),
        "Invalid actor TLS configuration");
    validate_message(config);
    require(config.protocol_version() == 1, "Unsupported actor TLS configuration version");
    if (config.has_io_timeout_ms()) timeout_ = Milliseconds(config.io_timeout_ms());
    if (config.has_idle_timeout_ms()) idle_ = Milliseconds(config.idle_timeout_ms());
    if (config.has_connection_limit()) limit_ = config.connection_limit();
    require(timeout_ >= Milliseconds(50) && timeout_ <= Milliseconds(60000) &&
        idle_ >= Milliseconds(50) && idle_ <= Milliseconds(300000) && limit_ >= 1 && limit_ <= 128,
        "Actor TLS limits are out of range");
    context_ = identity::tls_detail::make_context(true, config.certificate_path(), config.private_key_path(), config.client_ca_path());
}
int ActorTlsListener::accept() {
    const auto ready = available(descriptor_.value, POLLIN);
    require(ready >= 0, "Actor TLS listener failed");
    if (!ready) return -1;
    const auto fd = accept4(descriptor_.value, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
    if (fd < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR && errno != ECONNABORTED)
        throw std::runtime_error("Cannot accept actor TLS channel");
    return fd;
}
std::unique_ptr<ActorTlsPeer> ActorTlsListener::peer(int fd) {
    return std::make_unique<ActorTlsPeer>(fd, context_.get(), timeout_, idle_);
}
struct ActorTlsPeer::Impl {
    int descriptor;
    std::unique_ptr<SSL, decltype(&SSL_free)> tls{nullptr, SSL_free};
    Milliseconds timeout, idle;
    Clock::time_point started = Clock::now(), activity = started;
    std::optional<Clock::time_point> read_retry_started;
    bool established = false, writing = false;
    short wanted = POLLIN, read_wanted = POLLIN;
    std::array<char, 65536> bytes{};
    std::size_t read_size = 0;
    Impl(int fd, SSL_CTX* context, Milliseconds duration, Milliseconds idle_duration)
        : descriptor(fd), tls(SSL_new(context), SSL_free), timeout(duration), idle(idle_duration) {
        require(tls && SSL_set_fd(tls.get(), fd) == 1, "Cannot allocate actor TLS channel");
        SSL_set_accept_state(tls.get());
    }
    int retry(int result, short& next) {
        const auto error = SSL_get_error(tls.get(), result);
        if (error == SSL_ERROR_WANT_READ) { next = POLLIN; return 0; }
        if (error == SSL_ERROR_WANT_WRITE) { next = POLLOUT; return 0; }
        return -1;
    }
};
ActorTlsPeer::ActorTlsPeer(int fd, SSL_CTX* context, Milliseconds timeout, Milliseconds idle)
    : impl_(std::make_unique<Impl>(fd, context, timeout, idle)) {}
ActorTlsPeer::~ActorTlsPeer() = default;
bool ActorTlsPeer::ready() const { return impl_->established; }
bool ActorTlsPeer::read_needs_write() const { return impl_->read_wanted == POLLOUT; }
Milliseconds ActorTlsPeer::timeout() const { return impl_->timeout; }
bool ActorTlsPeer::expired(bool authenticated) const {
    return !impl_->established ? Clock::now()-impl_->started >= impl_->timeout :
        (impl_->read_retry_started && Clock::now()-*impl_->read_retry_started >= impl_->timeout) ||
        (!authenticated && Clock::now()-impl_->activity >= impl_->idle);
}
int ActorTlsPeer::handshake() {
    auto& p = *impl_;
    if (p.established) return 1;
    const auto ready = available(p.descriptor, p.wanted);
    if (ready <= 0) return ready;
    ERR_clear_error(); const auto result = SSL_accept(p.tls.get());
    if (result != 1) return p.retry(result, p.wanted);
    p.established = true; p.activity = Clock::now(); p.wanted = POLLIN; return 1;
}
int ActorTlsPeer::read(std::string& input) {
    auto& p = *impl_;
    if (!p.established || p.writing) return 0;
    const auto ready = available(p.descriptor, p.read_wanted, p.tls.get());
    if (ready <= 0) return ready;
    if (!p.read_retry_started) p.read_size = std::min(p.bytes.size(), max_frame_bytes+4-input.size());
    if (!p.read_size) return 0;
    std::size_t count = 0;
    ERR_clear_error(); const auto result = SSL_read_ex(p.tls.get(), p.bytes.data(), p.read_size, &count);
    if (result != 1) {
        const auto status = p.retry(result, p.read_wanted);
        if (!status && p.read_wanted == POLLOUT && !p.read_retry_started) p.read_retry_started = Clock::now();
        return status;
    }
    p.read_retry_started.reset(); p.read_wanted = POLLIN; p.activity = Clock::now(); input.append(p.bytes.data(), count); return 1;
}
int ActorTlsPeer::write(const std::string& output, std::size_t& sent) {
    auto& p = *impl_;
    if (p.read_wanted == POLLOUT) return 0;
    const auto ready = available(p.descriptor, p.writing ? p.wanted : POLLOUT, p.tls.get());
    if (ready <= 0) return ready;
    std::size_t count = 0;
    p.writing = true;
    ERR_clear_error(); const auto result = SSL_write_ex(p.tls.get(), output.data()+sent, output.size()-sent, &count);
    if (result != 1) return p.retry(result, p.wanted);
    p.writing = false; p.activity = Clock::now(); sent += count; return 1;
}
}
