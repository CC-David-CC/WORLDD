#include "identity/tls_server.hpp"
#include "identity/protocol.hpp"
#include "core/protocol.hpp"
#include <google/protobuf/text_format.h>
#include <openssl/err.h>
#include <openssl/ssl.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
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
void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}
struct Fd {
    int value;
    explicit Fd(int fd) : value(fd) {}
    ~Fd() { if (value >= 0) close(value); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
};
// Open relative to a checked private parent. OpenSSL reads this same descriptor.
int open_config_file(const std::filesystem::path& path, bool secret) {
    require(path.is_absolute() && !path.filename().empty() && path.string().find('\0') == std::string::npos,
            "TLS material requires an absolute file path");
    Fd parent(open(path.parent_path().c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    struct stat status{};
    require(parent.value >= 0 && fstat(parent.value, &status) == 0 && status.st_uid == geteuid() &&
            (status.st_mode & 0077) == 0, "TLS material requires an owned private parent directory");
    Fd file(openat(parent.value, path.filename().c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
    require(file.value >= 0 && fstat(file.value, &status) == 0 && S_ISREG(status.st_mode) &&
            status.st_uid == geteuid() && status.st_nlink == 1 && (status.st_mode & (secret ? 0077 : 0022)) == 0 &&
            status.st_size > 0 && status.st_size <= 65536, "Unsafe or missing TLS material");
    const int fd = file.value; file.value = -1; return fd;
}
std::string read_config(int fd) {
    std::string data;
    std::array<char, 4096> buffer{};
    for (;;) {
        const auto count = read(fd, buffer.data(), buffer.size());
        if (count < 0 && errno == EINTR) continue;
        require(count >= 0, "Cannot read TLS configuration");
        if (!count) return data;
        require(data.size()+static_cast<std::size_t>(count) <= 65536, "TLS configuration exceeds size limit");
        data.append(buffer.data(), count);
    }
}
std::string descriptor_path(int fd) { return "/proc/self/fd/"+std::to_string(fd); }
void nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL);
    require(flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 &&
            fcntl(fd, F_SETFD, FD_CLOEXEC) == 0, "Cannot configure issuer socket");
}
int no_password(char*, int, int, void*) { return 0; }

struct Peer {
    enum class Phase { handshake, header, payload, reply };
    Fd socket;
    std::unique_ptr<SSL, decltype(&SSL_free)> tls;
    Phase phase = Phase::handshake;
    short wanted = POLLIN;
    Clock::time_point deadline;
    std::array<unsigned char, 4> header{};
    std::string payload, output;
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
    void respond(Issuer& issuer, Milliseconds timeout) {
        std::ostringstream stream;
        write_frame(stream, handle_issuer_frame(payload, issuer));
        output = stream.str(); payload.clear(); offset = 0;
        phase = Phase::reply; wanted = POLLOUT; deadline = Clock::now()+timeout;
    }
    bool step(Issuer& issuer, Milliseconds timeout, Milliseconds idle) {
        if (phase == Phase::handshake) {
            ERR_clear_error(); const int result = SSL_accept(tls.get());
            if (result != 1) return retry(result);
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
        Fd config_file(open_config_file(path, false));
        v1::IssuerTlsServerConfig config;
        require(google::protobuf::TextFormat::ParseFromString(read_config(config_file.value), &config), "Invalid TLS configuration protobuf");
        validate_message(config);
        require(config.protocol_version() == 1, "Unsupported TLS configuration version");
        if (config.has_io_timeout_ms()) timeout = Milliseconds(config.io_timeout_ms());
        if (config.has_idle_timeout_ms()) idle = Milliseconds(config.idle_timeout_ms());
        if (config.has_connection_limit()) limit = config.connection_limit();
        require(timeout >= Milliseconds(50) && timeout <= Milliseconds(60000) &&
                idle >= Milliseconds(50) && idle <= Milliseconds(300000) && limit >= 1 && limit <= 128,
                "TLS limits are out of range");
        Fd certificate(open_config_file(config.certificate_path(), false));
        Fd key(open_config_file(config.private_key_path(), true));
        Fd trust(open_config_file(config.client_ca_path(), false));
        context.reset(SSL_CTX_new(TLS_server_method()));
        require(context && SSL_CTX_set_min_proto_version(context.get(), TLS1_3_VERSION) == 1 &&
                SSL_CTX_set_max_proto_version(context.get(), TLS1_3_VERSION) == 1, "Cannot require TLS 1.3");
        SSL_CTX_set_default_passwd_cb(context.get(), no_password);
        require(SSL_CTX_use_certificate_chain_file(context.get(), descriptor_path(certificate.value).c_str()) == 1 &&
                SSL_CTX_use_PrivateKey_file(context.get(), descriptor_path(key.value).c_str(), SSL_FILETYPE_PEM) == 1 &&
                SSL_CTX_check_private_key(context.get()) == 1, "Invalid TLS certificate or private key");
        require(SSL_CTX_load_verify_locations(context.get(), descriptor_path(trust.value).c_str(), nullptr) == 1,
                "Cannot load explicit service certificate trust");
        SSL_CTX_set_verify(context.get(), SSL_VERIFY_PEER | SSL_VERIFY_FAIL_IF_NO_PEER_CERT, nullptr);
        SSL_CTX_set_verify_depth(context.get(), 4);
        SSL_CTX_set_session_cache_mode(context.get(), SSL_SESS_CACHE_OFF);
        SSL_CTX_set_options(context.get(), SSL_OP_NO_TICKET | SSL_OP_NO_RENEGOTIATION);
        require(SSL_CTX_set_num_tickets(context.get(), 0) == 1 && SSL_CTX_set_max_early_data(context.get(), 0) == 1,
                "Cannot disable TLS tickets and early data");
    }
    void serve(Issuer& issuer) {
        for (;;) {
            std::vector<pollfd> fds{{STDIN_FILENO, POLLIN, 0}, {listener.value, POLLIN, 0}};
            for (const auto& peer : peers) fds.push_back({peer->socket.value, peer->wanted, 0});
            const int result = poll(fds.data(), fds.size(), 10);
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
                bool keep = Clock::now() < peer.deadline && !(events & POLLNVAL);
                const bool reading = peer.phase == Peer::Phase::header || peer.phase == Peer::Phase::payload;
                if (keep && (events || (reading && peer.wanted == POLLIN && SSL_pending(peer.tls.get()) > 0)))
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
        }
    }
};
IssuerTlsServer::IssuerTlsServer(int fd, const std::filesystem::path& config) : impl_(std::make_unique<Impl>(fd, config)) {}
IssuerTlsServer::~IssuerTlsServer() = default;
void IssuerTlsServer::serve(Issuer& issuer) { impl_->serve(issuer); }
}
