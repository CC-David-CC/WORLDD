#include "identity/issuer_client.hpp"
#include "identity/bootstrap.hpp"
#include "identity/tls_support.hpp"
#include "core/protocol.hpp"
#include <google/protobuf/text_format.h>
#include <openssl/err.h>
#include <openssl/x509v3.h>
#include <algorithm>
#include <array>
#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <limits>
#include <poll.h>
#include <set>
#include <sstream>
#include <sys/socket.h>

namespace spatial::identity {
namespace {
using tls_detail::require;
using Clock = std::chrono::steady_clock;
bool dns_name(const std::string& name) {
    if (name.empty() || name.size() > 253) return false;
    std::size_t start = 0;
    while (start < name.size()) {
        const auto end = name.find('.', start);
        const auto label = name.substr(start, end == std::string::npos ? name.size()-start : end-start);
        if (label.empty() || label.size() > 63 || label.front() == '-' || label.back() == '-') return false;
        for (const unsigned char c : label)
            if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-')) return false;
        if (end == std::string::npos) return true;
        start = end+1;
    }
    return false;
}
bool outcome_matches(const v1::IssuerRequest& request, const v1::IssuerReply& reply) {
    return (request.has_hello() && reply.has_hello()) || (request.has_info() && reply.has_info()) ||
        ((request.has_challenge() || request.has_enrollment_begin() || request.has_authentication_begin()) && reply.has_challenge()) ||
        (request.has_execute() && (reply.has_policy() || reply.has_enrollment() || reply.has_identity_status() || reply.has_profile())) ||
        (request.has_profile_read() && reply.has_profile()) ||
        (request.has_enrollment_complete() && reply.has_enrollment()) ||
        (request.has_authentication_complete() && reply.has_authentication()) ||
        (request.has_credential_check() && reply.has_identity_status()) ||
        (request.has_revocations() && reply.has_revocations()) ||
        (request.has_actor_status() && reply.has_actor_status());
}
void check_actor_status(const v1::ActorStatusRead& request, const v1::ActorStatusBatch& batch,
                        const std::string& issuer_id) {
    require(batch.issuer_id() == issuer_id && batch.security_revision() <= INT64_MAX &&
            request.actor_ids_size() > 0 && request.actor_ids_size() <= 128 &&
            batch.actors_size() == request.actor_ids_size(), "Invalid actor status batch context");
    std::set<std::string> distinct;
    for (int index = 0; index < batch.actors_size(); ++index) {
        const auto& actor = batch.actors(index);
        require(actor.actor_id().size() == 32 && actor.actor_id() == request.actor_ids(index) &&
                distinct.insert(actor.actor_id()).second && (!actor.revoked() || actor.known()) &&
                actor.active() == (actor.known() && !actor.revoked()), "Invalid actor status subject or flags");
    }
}
void check_revocations(const v1::RevocationRead& request, const v1::RevocationBatch& batch,
                       const std::string& issuer_id) {
    const auto from = batch.from_revision(), through = batch.through_revision(), head = batch.head_revision();
    require(batch.issuer_id() == issuer_id && head <= INT64_MAX && from <= through && through <= head &&
            batch.events_size() <= 128, "Invalid revocation batch identity or range");
    if (!request.has_after_revision()) {
        require(batch.initial() && from == head && through == head && batch.events_size() == 0,
                "Invalid revocation baseline"); return;
    }
    require(!batch.initial() && from == request.after_revision() &&
            through-from == static_cast<std::uint64_t>(batch.events_size()) &&
            (batch.events_size() == 128 || through == head), "Incomplete revocation batch");
    auto revision = from;
    for (const auto& event : batch.events()) {
        require(event.revision() == ++revision && event.target().id().size() == 32 &&
                (event.target().kind() == v1::ACTOR_TARGET || event.target().kind() == v1::CREDENTIAL_TARGET),
                "Invalid revocation event");
    }
}
void check_hello(const wire::HelloReply& hello) {
    require(hello.protocol() == "spatial-identity-protobuf/1" && hello.implementation() == "production" &&
            hello.build_id().starts_with("sha256:") && hello.build_id().size() == 71 &&
            std::all_of(hello.build_id().begin()+7, hello.build_id().end(), [](char c) {
                return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
            }), "Unexpected issuer implementation");
    std::set<std::string> capabilities;
    for (const auto& capability : hello.capabilities())
        require(!capability.empty() && capability.size() <= 128 && capability.find('\0') == std::string::npos &&
                capabilities.insert(capability).second, "Invalid issuer capabilities");
    for (const auto* required : {"identity.issuer-control/v1", "identity.enrollment/v1", "identity.revocation/v1", "identity.authentication/v1"})
        require(capabilities.contains(required), "Issuer capability is missing");
}
}

struct IssuerClient::Impl {
    v1::IssuerTlsClientConfig config;
    tls_detail::Context context{nullptr, SSL_CTX_free};
    tls_detail::Fd socket;
    std::unique_ptr<SSL, decltype(&SSL_free)> tls{nullptr, SSL_free};
    std::function<void()> tick;
    std::chrono::milliseconds timeout{5000};
    Clock::time_point deadline;
    std::uint64_t sequence = 0;
    bool broken = false, busy = false;

    explicit Impl(const std::filesystem::path& path, std::function<void()> callback) : tick(std::move(callback)) {
        struct sigaction action{};
        require(sigaction(SIGPIPE, nullptr, &action) == 0 && action.sa_handler == SIG_IGN,
                "Issuer client process must ignore SIGPIPE");
        require(google::protobuf::TextFormat::ParseFromString(tls_detail::load_config(path), &config), "Invalid issuer client configuration");
        validate_message(config);
        require(config.protocol_version() == 1 && config.port() > 0 && config.port() <= 65535 &&
                dns_name(config.server_name()) && config.issuer_id().size() == 32 && config.issuer_public_key().size() == 32,
                "Invalid issuer client configuration fields");
        if (config.has_io_timeout_ms()) timeout = std::chrono::milliseconds(config.io_timeout_ms());
        require(timeout.count() >= 50 && timeout.count() <= 60000, "Issuer client timeout is out of range");
        in_addr name_address{};
        require(inet_pton(AF_INET, config.server_name().c_str(), &name_address) != 1, "Issuer server name must be a DNS name");
        sockaddr_in ipv4{}; sockaddr_in6 ipv6{};
        const sockaddr* address = nullptr; socklen_t length; int family;
        require(config.address().find('\0') == std::string::npos, "Invalid issuer numeric address");
        if (inet_pton(AF_INET, config.address().c_str(), &ipv4.sin_addr) == 1) {
            family = ipv4.sin_family = AF_INET; ipv4.sin_port = htons(config.port()); length = sizeof(ipv4);
            address = reinterpret_cast<const sockaddr*>(&ipv4);
        } else if (inet_pton(AF_INET6, config.address().c_str(), &ipv6.sin6_addr) == 1) {
            family = ipv6.sin6_family = AF_INET6; ipv6.sin6_port = htons(config.port()); length = sizeof(ipv6);
            address = reinterpret_cast<const sockaddr*>(&ipv6);
        } else throw std::runtime_error("Issuer address must be numeric IPv4 or IPv6");
        context = tls_detail::make_context(false, config.certificate_path(), config.private_key_path(), config.server_ca_path());
        socket.reset(::socket(family, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, IPPROTO_TCP));
        require(socket.value >= 0, "Cannot create issuer client socket");
        tls.reset(SSL_new(context.get()));
        require(tls && SSL_set_fd(tls.get(), socket.value) == 1, "Cannot create issuer TLS connection");
        SSL_set_hostflags(tls.get(), X509_CHECK_FLAG_NO_PARTIAL_WILDCARDS | X509_CHECK_FLAG_NEVER_CHECK_SUBJECT);
        require(SSL_set1_host(tls.get(), config.server_name().c_str()) == 1 &&
                SSL_set_tlsext_host_name(tls.get(), config.server_name().c_str()) == 1, "Cannot set issuer TLS server name");
        deadline = Clock::now()+timeout;
        const auto connected = connect(socket.value, address, length);
        if (connected < 0) {
            require(errno == EINPROGRESS, "Cannot connect to issuer");
            wait(POLLOUT);
            int error = 0; socklen_t size = sizeof(error);
            require(getsockopt(socket.value, SOL_SOCKET, SO_ERROR, &error, &size) == 0 && error == 0, "Issuer connection failed");
        }
        for (;;) {
            check(); ERR_clear_error(); const int result = SSL_connect(tls.get());
            if (result == 1) break;
            retry(SSL_get_error(tls.get(), result));
        }
        require(SSL_get_verify_result(tls.get()) == X509_V_OK, "Issuer certificate verification failed");
        v1::IssuerRequest hello; hello.set_protocol_version(1); hello.mutable_hello();
        const auto greeting = exchange(hello); require(greeting.has_hello(), "Issuer hello failed");
        v1::IssuerRequest info; info.set_protocol_version(1); info.mutable_info();
        require(exchange(info).has_info(), "Issuer identity check failed");
    }
    void poison() noexcept { broken = true; tls.reset(); socket.reset(); }
    void check() {
        if (tick) tick();
        require(Clock::now() < deadline, "Issuer client deadline exceeded");
    }
    void wait(short events) {
        for (;;) {
            check(); pollfd fd{socket.value, events, 0}; const auto result = poll(&fd, 1, 10);
            if (result < 0 && errno == EINTR) continue;
            require(result >= 0 && !(fd.revents & POLLNVAL), "Issuer client poll failed");
            if (fd.revents & (events | POLLERR | POLLHUP)) return;
        }
    }
    void retry(int error) {
        if (error == SSL_ERROR_WANT_READ) wait(POLLIN);
        else if (error == SSL_ERROR_WANT_WRITE) wait(POLLOUT);
        else throw std::runtime_error("Issuer TLS connection failed");
    }
    void transfer(char* data, std::size_t size, bool writing) {
        std::size_t offset = 0;
        while (offset < size) {
            check(); std::size_t count = 0; ERR_clear_error();
            const int result = writing ? SSL_write_ex(tls.get(), data+offset, size-offset, &count) :
                                         SSL_read_ex(tls.get(), data+offset, size-offset, &count);
            if (result != 1) retry(SSL_get_error(tls.get(), result));
            else { require(count > 0, "Issuer TLS made no progress"); offset += count; }
        }
    }
    v1::IssuerReply exchange(v1::IssuerRequest request) {
        require(sequence < std::numeric_limits<std::uint64_t>::max(), "Issuer request sequence exhausted");
        request.set_request_id(++sequence);
        std::ostringstream output; write_frame(output, request); auto bytes = output.str();
        transfer(bytes.data(), bytes.size(), true);
        std::array<unsigned char, 4> header{}; transfer(reinterpret_cast<char*>(header.data()), header.size(), false);
        const std::uint32_t length = (std::uint32_t(header[0]) << 24) | (std::uint32_t(header[1]) << 16) |
                                    (std::uint32_t(header[2]) << 8) | header[3];
        require(length <= max_frame_bytes, "Issuer reply exceeds size limit");
        bytes.resize(length); transfer(bytes.data(), bytes.size(), false);
        v1::IssuerReply reply;
        require(reply.ParseFromString(bytes) && reply.protocol_version() == 1 && reply.request_id() == sequence,
                "Invalid issuer reply identity");
        try { validate_message(reply); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid issuer reply fields"); }
        if (reply.has_error()) require(reply.error().code() != 0, "Issuer error lacks a valid code");
        else {
            require(outcome_matches(request, reply), "Issuer reply has the wrong outcome");
            if (reply.has_hello()) check_hello(reply.hello());
            if (reply.has_info()) require(reply.info().issuer_id() == config.issuer_id() &&
                    reply.info().public_key() == config.issuer_public_key() && reply.info().policy_revision() > 0,
                    "Issuer identity does not match configured pins");
            if (reply.has_revocations()) check_revocations(request.revocations(), reply.revocations(), config.issuer_id());
            if (reply.has_actor_status()) check_actor_status(request.actor_status(), reply.actor_status(), config.issuer_id());
            if (reply.has_profile()) {
                const auto& state = reply.profile();
                require(state.has_profile() && state.issuer_id() == config.issuer_id() &&
                        state.revision() > 0 && state.revision() <= INT64_MAX, "Invalid bootstrap profile response context");
                // Invalid peer data must poison the channel, not appear as a caller error.
                try { validate_bootstrap_profile(state.profile()); }
                catch (const std::invalid_argument&) { throw std::runtime_error("Invalid bootstrap profile response"); }
                if (request.has_profile_read())
                    require(state.profile().id() == request.profile_read().id() &&
                            state.revision() == request.profile_read().revision(), "Substituted bootstrap profile revision");
                if (state.profile().id() == "empty-v1")
                    require(state.revision() == 1 && state.profile().grants_size() == 0, "Invalid immutable empty profile");
            }
        }
        return reply;
    }
    v1::IssuerReply call(v1::IssuerRequest request) {
        require(!broken, "Issuer client is disabled after failure");
        if (busy) throw std::logic_error("Issuer client does not permit reentrant calls");
        validate_message(request);
        if (request.has_profile_read()) validate_bootstrap_read(request.profile_read(), true);
        const auto caller_id = request.request_id();
        require(sequence < std::numeric_limits<std::uint64_t>::max(), "Issuer request sequence exhausted");
        request.set_request_id(sequence+1);
        if (request.protocol_version() != 1 || request.ByteSizeLong() > max_frame_bytes)
            throw std::invalid_argument("Invalid issuer request version or size");
        busy = true; deadline = Clock::now()+timeout;
        try {
            auto reply = exchange(std::move(request)); reply.set_request_id(caller_id); busy = false; return reply;
        } catch (...) { busy = false; poison(); throw; }
    }
};
IssuerClient::IssuerClient(const std::filesystem::path& config, std::function<void()> tick)
    : impl_(std::make_unique<Impl>(config, std::move(tick))) {}
IssuerClient::~IssuerClient() = default;
v1::IssuerReply IssuerClient::call(v1::IssuerRequest request) { return impl_->call(std::move(request)); }
void IssuerClient::set_tick(std::function<void()> tick) { impl_->tick = std::move(tick); }
bool IssuerClient::failed() const noexcept { return impl_->broken; }
std::chrono::milliseconds IssuerClient::timeout() const noexcept { return impl_->timeout; }
}
