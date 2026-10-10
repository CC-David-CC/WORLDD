#include "identity/tls_server.hpp"
#include "identity/tls_support.hpp"
#include "identity/protocol.hpp"
#include "core/protocol.hpp"
#include <google/protobuf/text_format.h>
#include <openssl/err.h>
#include <openssl/ssl.h>
#include <openssl/rand.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace spatial::identity {
namespace {
using Clock = std::chrono::steady_clock;
using Milliseconds = std::chrono::milliseconds;
using tls_detail::require;
using tls_detail::Fd;
using tls_detail::nonblocking;

struct Peer {
    enum class Phase { handshake, header, payload, waiting, reply };
    Fd socket;
    std::unique_ptr<SSL, decltype(&SSL_free)> tls;
    Phase phase = Phase::handshake;
    short wanted = POLLIN;
    Clock::time_point deadline;
    std::array<unsigned char, 4> header{};
    std::string payload, output;
    std::optional<v1::IssuerRequest> waiting;
    IssuerConnection connection;
    std::size_t offset = 0;
    Peer(int fd, SSL_CTX* ctx, Milliseconds timeout)
        : socket(fd), tls(SSL_new(ctx), SSL_free), deadline(Clock::now()+timeout) {
        require(tls && SSL_set_fd(tls.get(), fd) == 1, "Cannot allocate issuer TLS connection");
        SSL_set_accept_state(tls.get());
    }
    bool retry(int result) {
        // No OpenSSL call may intervene between I/O and SSL_get_error.
        const auto error = SSL_get_error(tls.get(), result);
        if (error == SSL_ERROR_WANT_READ) { wanted = POLLIN; return true; }
        if (error == SSL_ERROR_WANT_WRITE) { wanted = POLLOUT; return true; }
        // SSL_free then close; do not call SSL_shutdown after fatal TLS errors.
        return false;
    }
    void send(const v1::IssuerReply& reply, Milliseconds timeout) {
        std::ostringstream stream;
        write_frame(stream, reply);
        output = stream.str(); payload.clear(); offset = 0;
        phase = Phase::reply; wanted = POLLOUT; deadline = Clock::now()+timeout;
    }
    void respond(Issuer& issuer, Milliseconds timeout) {
        const auto reply = handle_issuer_frame(payload, issuer, &connection);
        v1::IssuerRequest request;
        if (reply.has_revocations() && request.ParseFromString(payload) && request.revocations().has_after_revision() &&
                request.revocations().wait_ms() && reply.revocations().events_size() == 0) {
            waiting = std::move(request); payload.clear(); offset = 0; phase = Phase::waiting; wanted = 0;
            deadline = Clock::now()+Milliseconds(waiting->revocations().wait_ms());
        } else send(reply, timeout);
    }
    bool step(Issuer& issuer, Milliseconds timeout, Milliseconds idle) {
        if (phase == Phase::waiting) {
            try {
                if (Clock::now() < deadline && issuer.security_revision() == waiting->revocations().after_revision()) return true;
            } catch (const std::exception&) {
                // The shared dispatcher turns storage failure into a typed response.
            }
            send(dispatch_issuer(*waiting, issuer, &connection), timeout); waiting.reset(); return true;
        }
        if (phase == Phase::handshake) {
            ERR_clear_error(); const int result = SSL_accept(tls.get());
            if (result != 1) return retry(result);
            connection.binding.resize(32);
            require(RAND_bytes(reinterpret_cast<unsigned char*>(connection.binding.data()), connection.binding.size()) == 1,
                    "Cannot allocate issuer TLS connection identity");
            phase = Phase::header; wanted = POLLIN; deadline = Clock::now()+idle; return true;
        }
        std::size_t count = 0;
        if (phase == Phase::reply) {
            ERR_clear_error(); const int result = SSL_write_ex(tls.get(), output.data()+offset, output.size()-offset, &count);
            if (result != 1) return retry(result);
            offset += count; wanted = POLLOUT;
            if (offset == output.size()) {
                output.clear(); offset = 0; phase = Phase::header; wanted = POLLIN; deadline = Clock::now()+idle;
            }
            return true;
        }
        void* destination = phase == Phase::header ? static_cast<void*>(header.data()+offset) : payload.data()+offset;
        const auto remaining = (phase == Phase::header ? header.size() : payload.size())-offset;
        ERR_clear_error(); const int result = SSL_read_ex(tls.get(), destination, remaining, &count);
        if (result != 1) return retry(result);
        if (phase == Phase::header && offset == 0) deadline = Clock::now()+timeout;
        offset += count; wanted = POLLIN;
        if (phase == Phase::header && offset == header.size()) {
            const std::uint32_t length = (std::uint32_t(header[0]) << 24) | (std::uint32_t(header[1]) << 16) |
                (std::uint32_t(header[2]) << 8) | std::uint32_t(header[3]);
            if (length > max_frame_bytes) return false;
            payload.resize(length); offset = 0; phase = Phase::payload;
            if (!length) respond(issuer, timeout);
        } else if (phase == Phase::payload && offset == payload.size()) respond(issuer, timeout);
        return true;
    }
};
}

struct IssuerTlsServer::Impl {
    Fd listener;
    std::unique_ptr<SSL_CTX, decltype(&SSL_CTX_free)> context{nullptr, SSL_CTX_free};
    Milliseconds timeout{5000}, idle{60000};
    std::size_t limit = 32;
    std::vector<std::unique_ptr<Peer>> peers;
    explicit Impl(int fd, const std::filesystem::path& path) : listener(fd) {
        require(fd > STDERR_FILENO, "TLS listener must be an inherited descriptor above stderr");
        sockaddr_storage address{}; socklen_t address_size = sizeof(address);
        int accepting = 0, type = 0, protocol = 0; socklen_t option_size = sizeof(accepting);
        require(getsockname(fd, reinterpret_cast<sockaddr*>(&address), &address_size) == 0 &&
                (address.ss_family == AF_INET || address.ss_family == AF_INET6) &&
                getsockopt(fd, SOL_SOCKET, SO_ACCEPTCONN, &accepting, &option_size) == 0 && accepting &&
                getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &option_size) == 0 && type == SOCK_STREAM &&
                getsockopt(fd, SOL_SOCKET, SO_PROTOCOL, &protocol, &option_size) == 0 && protocol == IPPROTO_TCP,
                "TLS listener must be a listening TCP socket");
        nonblocking(fd);
        v1::IssuerTlsServerConfig config;
        require(google::protobuf::TextFormat::ParseFromString(tls_detail::load_config(path), &config), "Invalid TLS configuration protobuf");
        validate_message(config);
        require(config.protocol_version() == 1, "Unsupported TLS configuration version");
        if (config.has_io_timeout_ms()) timeout = Milliseconds(config.io_timeout_ms());
        if (config.has_idle_timeout_ms()) idle = Milliseconds(config.idle_timeout_ms());
        if (config.has_connection_limit()) limit = config.connection_limit();
        require(timeout >= Milliseconds(50) && timeout <= Milliseconds(60000) &&
                idle >= Milliseconds(50) && idle <= Milliseconds(300000) && limit >= 1 && limit <= 128,
                "TLS limits are out of range");
        context = tls_detail::make_context(true, config.certificate_path(), config.private_key_path(), config.client_ca_path());
    }
    void serve(Issuer& issuer) {
        auto started = Clock::now();
        for (;;) {
            std::vector<pollfd> fds{{STDIN_FILENO, POLLIN, 0}, {listener.value, POLLIN, 0}};
            for (const auto& peer : peers) fds.push_back({peer->socket.value, peer->wanted, 0});
            const int result = poll(fds.data(), fds.size(), 10);
            const auto polled = Clock::now();
            if (result < 0 && errno == EINTR) continue;
            require(result >= 0, "Cannot poll issuer TLS listener");
            if (fds[0].revents & (POLLIN | POLLHUP | POLLERR | POLLNVAL)) {
                char byte = 0; const auto count = read(STDIN_FILENO, &byte, 1);
                if (!count) return;
                if (count < 0 && errno == EINTR) continue;
                throw std::runtime_error("TLS supervisor channel accepts EOF only");
            }
            require(!(fds[1].revents & (POLLERR | POLLHUP | POLLNVAL)), "Issuer TLS listener failed");
            // One bounded step per peer, then admit at most one new connection.
            for (std::size_t i = peers.size(); i > 0; --i) {
                auto& peer = *peers[i-1]; const auto events = fds[i+1].revents;
                const bool waiting = peer.phase == Peer::Phase::waiting;
                bool keep = (waiting || Clock::now() < peer.deadline) && !(events & POLLNVAL) &&
                    !(waiting && (events & (POLLHUP | POLLERR)));
                const bool reading = peer.phase == Peer::Phase::header || peer.phase == Peer::Phase::payload;
                if (keep && (waiting || events || (reading && peer.wanted == POLLIN && SSL_pending(peer.tls.get()) > 0)))
                    keep = peer.step(issuer, timeout, idle);
                if (!keep) peers.erase(peers.begin()+static_cast<std::ptrdiff_t>(i-1));
            }
            if (fds[1].revents & POLLIN) {
                const int fd = accept4(listener.value, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
                if (fd < 0) {
                    if (errno == EINTR || errno == EAGAIN || errno == ECONNABORTED) continue;
                    throw std::runtime_error("Cannot accept issuer TLS peer");
                }
                if (peers.size() >= limit) { close(fd); continue; }
                peers.push_back(std::make_unique<Peer>(fd, context.get(), timeout));
            }
            const auto finished = Clock::now();
            if (finished-started >= Milliseconds(100)) {
                // Fixed timing fields only. Do not log peer identities or request data.
                std::fprintf(stderr, "issuer TLS loop delayed monotonic_us=%lld poll_ms=%lld work_ms=%lld\n",
                    static_cast<long long>(std::chrono::duration_cast<std::chrono::microseconds>(finished.time_since_epoch()).count()),
                    static_cast<long long>(std::chrono::duration_cast<Milliseconds>(polled-started).count()),
                    static_cast<long long>(std::chrono::duration_cast<Milliseconds>(finished-polled).count()));
            }
            started = finished;
        }
    }
};
IssuerTlsServer::IssuerTlsServer(int fd, const std::filesystem::path& config) : impl_(std::make_unique<Impl>(fd, config)) {}
IssuerTlsServer::~IssuerTlsServer() = default;
void IssuerTlsServer::serve(Issuer& issuer) { impl_->serve(issuer); }
}
