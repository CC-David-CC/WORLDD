#include "world/actor_session_client.hpp"
#include "identity/keys.hpp"
#include "identity/tls_support.hpp"
#include "core/protocol.hpp"
#include "actor_session_client.pb.h"
#include <google/protobuf/text_format.h>
#include <openssl/pem.h>
#include <openssl/x509v3.h>
#include <openssl/err.h>
#include <arpa/inet.h>
#include <poll.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <unistd.h>
#include <csignal>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <thread>
#include <array>

namespace {
using namespace spatial;
namespace pb = identity::v1;
using namespace std::chrono_literals;

void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

template<class F> void rejects(F operation) {
    bool rejected = false;
    try { operation(); } catch (const std::exception&) { rejected = true; }
    check(rejected, "operation unexpectedly accepted");
}

struct Fixture {
    identity::KeyPair actor = identity::KeyPair::generate();
    identity::KeyPair issuer = identity::KeyPair::generate();
    std::filesystem::path root;
    std::filesystem::path key_path;
    pb::SignedCredential credential;
    std::string world = "fixture-world";
    std::string transition = std::string(32, 't');
    std::string actor_id;

    Fixture() {
        std::string pattern = (std::filesystem::temp_directory_path() /
            "actor-client-run04-XXXXXX").string();
        check(mkdtemp(pattern.data()) != nullptr, "mkdtemp failed");
        root = pattern;
        key_path = root / "actor.pem";
        actor.save_new(key_path);
        pb::ActorCredential claims;
        claims.set_protocol_version(1);
        claims.set_issuer_id(std::string(32, 'i'));
        claims.set_actor_id(std::string(32, 'a'));
        claims.set_credential_id(std::string(32, 'c'));
        claims.set_public_key(actor.public_key());
        claims.set_kind(pb::HUMAN);
        claims.set_enrollment_id(std::string(32, 'r'));
        claims.set_bootstrap_profile("fixture-profile");
        claims.set_bootstrap_profile_revision(1);
        claims.set_policy_revision(1);
        claims.set_audience("spatial/actor-credential/v1");
        credential.mutable_content()->set_purpose(pb::ISSUER_CREDENTIAL);
        credential.mutable_content()->set_payload(claims.SerializeAsString());
        *credential.mutable_proof() = issuer.sign(credential.content());
        pb::VerifyRequest verify;
        *verify.mutable_content() = credential.content();
        *verify.mutable_proof() = credential.proof();
        verify.set_expected_public_key(issuer.public_key());
        check(identity::verify(verify), "fixture credential signature invalid");
        actor_id = "identity:";
        for (int i = 0; i < 32; ++i) actor_id += "69";
        actor_id += ':';
        for (int i = 0; i < 32; ++i) actor_id += "61";
    }
    ~Fixture() { std::error_code error; std::filesystem::remove_all(root, error); }
};

std::string frame(const pb::WorldSessionReply& reply) {
    const auto body = reply.SerializeAsString();
    const auto size = static_cast<std::uint32_t>(body.size());
    std::string result;
    for (int shift : {24, 16, 8, 0}) result.push_back(static_cast<char>(size >> shift));
    return result + body;
}

pb::WorldSessionReply reply_for(std::uint64_t id) {
    pb::WorldSessionReply reply;
    reply.set_protocol_version(1);
    reply.set_request_id(id);
    return reply;
}

pb::ChallengeReply challenge_for(const Fixture& f) {
    pb::AuthenticationChallenge context;
    context.set_protocol_version(1);
    context.set_issuer_id(std::string(32, 'i'));
    context.set_incarnation(std::string(32, 'e'));
    context.set_nonce(std::string(32, 'n'));
    context.set_lifetime_ms(1000);
    *context.mutable_request()->mutable_credential() = f.credential;
    context.mutable_request()->set_audience("spatial.world-session/v1:" + f.world);
    // This opaque value comes from the trusted peer, not from a client-selected binding.
    context.mutable_request()->set_channel_binding(std::string(32, 'b'));
    pb::ChallengeReply challenge;
    challenge.set_nonce(context.nonce());
    challenge.mutable_signing_request()->set_purpose(pb::ACTOR_AUTHENTICATION);
    challenge.mutable_signing_request()->set_payload(context.SerializeAsString());
    return challenge;
}

void opened(const Fixture& f, pb::WorldSessionOpened* result) {
    result->set_world_id(f.world);
    result->set_actor_id(f.actor_id);
    auto* status = result->mutable_identity();
    status->set_issuer_id(std::string(32, 'i'));
    status->set_actor_id(std::string(32, 'a'));
    status->set_credential_id(std::string(32, 'c'));
    status->set_kind(pb::HUMAN);
    status->set_active(true);
    status->set_security_revision(1);
}

using Key = std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)>;
using Cert = std::unique_ptr<X509, decltype(&X509_free)>;
struct Certificate {
    Key key{EVP_PKEY_Q_keygen(nullptr, nullptr, "EC", "prime256v1"), EVP_PKEY_free};
    Cert cert{X509_new(), X509_free};
    Certificate(const char* name, Certificate* ca, long serial) {
        check(key && cert, "certificate allocation");
        check(X509_set_version(cert.get(), 2) == 1 &&
              ASN1_INTEGER_set(X509_get_serialNumber(cert.get()), serial) == 1 &&
              X509_gmtime_adj(X509_getm_notBefore(cert.get()), -60) &&
              X509_gmtime_adj(X509_getm_notAfter(cert.get()), 3600) &&
              X509_set_pubkey(cert.get(), key.get()) == 1, "certificate fields");
        auto* subject = X509_get_subject_name(cert.get());
        check(X509_NAME_add_entry_by_txt(subject, "CN", MBSTRING_ASC,
            reinterpret_cast<const unsigned char*>(name), -1, -1, 0) == 1, "certificate subject");
        check(X509_set_issuer_name(cert.get(), ca ? X509_get_subject_name(ca->cert.get()) : subject) == 1,
              "certificate issuer");
        X509V3_CTX ctx{};
        X509V3_set_ctx(&ctx, ca ? ca->cert.get() : cert.get(), cert.get(), nullptr, nullptr, 0);
        auto extension = [&](int nid, const char* value) {
            std::unique_ptr<X509_EXTENSION, decltype(&X509_EXTENSION_free)> ext(
                X509V3_EXT_conf_nid(nullptr, &ctx, nid, value), X509_EXTENSION_free);
            check(ext && X509_add_ext(cert.get(), ext.get(), -1) == 1, "certificate extension");
        };
        extension(NID_basic_constraints, ca ? "critical,CA:FALSE" : "critical,CA:TRUE");
        extension(NID_key_usage, ca ? "critical,digitalSignature" : "critical,keyCertSign,cRLSign");
        if (ca) {
            extension(NID_ext_key_usage, "serverAuth,clientAuth");
            extension(NID_subject_alt_name, "DNS:actor.fixture");
        }
        check(X509_sign(cert.get(), ca ? ca->key.get() : key.get(), EVP_sha256()) > 0, "certificate signing");
    }
    void save(const std::filesystem::path& root, const std::string& name) {
        auto write = [&](const std::filesystem::path& path, bool secret) {
            FILE* file = fopen(path.c_str(), "w");
            check(file != nullptr, "certificate file open");
            const int result = secret ? PEM_write_PrivateKey(file, key.get(), nullptr, nullptr, 0, nullptr, nullptr)
                                      : PEM_write_X509(file, cert.get());
            const int closed = fclose(file);
            check(result == 1 && closed == 0 && chmod(path.c_str(), 0600) == 0, "certificate file write");
        };
        write(root / (name + ".pem"), false);
        write(root / (name + ".key"), true);
    }
};

std::size_t descriptors() {
    std::size_t count = 0;
    for ([[maybe_unused]] const auto& entry : std::filesystem::directory_iterator("/proc/self/fd")) ++count;
    return count;
}

struct TlsFixture : Fixture {
    Certificate ca{"fixture-ca", nullptr, 1};
    Certificate other{"other-ca", nullptr, 2};
    Certificate server{"actor.fixture", &ca, 3};
    Certificate client{"client", &ca, 4};
    Certificate invalid{"invalid-client", &other, 5};
    pb::ActorSessionClientTlsConfig config;
    TlsFixture() {
        ca.save(root, "ca"); other.save(root, "other"); server.save(root, "server");
        client.save(root, "client"); invalid.save(root, "invalid");
        config.set_protocol_version(1); config.set_address("127.0.0.1");
        config.set_server_name("actor.fixture"); config.set_expected_world_id(world);
        config.set_certificate_path((root / "client.pem").string());
        config.set_private_key_path((root / "client.key").string());
        config.set_server_ca_path((root / "ca.pem").string());
        config.set_actor_signing_key_path(key_path.string());
        *config.mutable_actor_credential() = credential;
        config.set_io_timeout_ms(300);
    }
    std::filesystem::path save_config() {
        std::string text;
        check(google::protobuf::TextFormat::PrintToString(config, &text), "config serialization");
        const auto path = root / "config.pbtxt";
        std::ofstream out(path); out << text; out.close();
        check(out.good() && chmod(path.c_str(), 0600) == 0, "config write");
        return path;
    }
};

// A real TLS 1.3 peer. All server operations have a separate absolute deadline.
struct Peer {
    int fd;
    std::unique_ptr<SSL, decltype(&SSL_free)> ssl;
    std::chrono::steady_clock::time_point deadline = std::chrono::steady_clock::now() + 3s;
    Peer(int socket, SSL_CTX* context) : fd(socket), ssl(SSL_new(context), SSL_free) {
        check(ssl && SSL_set_fd(ssl.get(), fd) == 1, "peer SSL allocation");
    }
    ~Peer() { ssl.reset(); close(fd); }
    void retry(int error) {
        check(error == SSL_ERROR_WANT_READ || error == SSL_ERROR_WANT_WRITE, "peer TLS failure");
        const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now()).count();
        check(left > 0, "peer deadline");
        pollfd p{fd, static_cast<short>(error == SSL_ERROR_WANT_READ ? POLLIN : POLLOUT), 0};
        check(poll(&p, 1, static_cast<int>(left)) > 0, "peer poll");
    }
    void accept() {
        for (;;) {
            ERR_clear_error(); const int result = SSL_accept(ssl.get());
            if (result == 1) break;
            retry(SSL_get_error(ssl.get(), result));
        }
        check(SSL_version(ssl.get()) == TLS1_3_VERSION && SSL_get_verify_result(ssl.get()) == X509_V_OK,
              "peer mutual TLS verification");
        std::unique_ptr<X509, decltype(&X509_free)> cert(SSL_get1_peer_certificate(ssl.get()), X509_free);
        check(cert != nullptr, "peer missing client certificate");
    }
    void io(char* data, std::size_t size, bool write) {
        std::size_t offset = 0;
        while (offset < size) {
            check(std::chrono::steady_clock::now() < deadline, "peer transfer deadline");
            std::size_t n = 0; ERR_clear_error();
            const int result = write ? SSL_write_ex(ssl.get(), data + offset, size - offset, &n)
                                     : SSL_read_ex(ssl.get(), data + offset, size - offset, &n);
            if (result != 1) retry(SSL_get_error(ssl.get(), result));
            else { check(n > 0, "peer progress"); offset += n; }
        }
    }
    void send(std::string bytes) { io(bytes.data(), bytes.size(), true); }
    void reply(const pb::WorldSessionReply& message, bool fragmented) {
        const auto bytes = frame(message);
        if (!fragmented) { send(bytes); return; } // One SSL_write, one TLS record. Keep TCP open.
        send(bytes.substr(0, 1)); std::this_thread::sleep_for(10ms);
        send(bytes.substr(1, 4)); std::this_thread::sleep_for(10ms); send(bytes.substr(5));
    }
    pb::WorldSessionRequest receive(pb::WorldSessionRequest::OperationCase operation, std::uint64_t id) {
        std::array<unsigned char, 4> header{};
        io(reinterpret_cast<char*>(header.data()), 4, false);
        const auto size = (std::uint32_t(header[0]) << 24) | (std::uint32_t(header[1]) << 16) |
                         (std::uint32_t(header[2]) << 8) | header[3];
        check(size > 0 && size <= max_frame_bytes, "peer frame size");
        std::string bytes(size, '\0'); io(bytes.data(), bytes.size(), false);
        pb::WorldSessionRequest request;
        check(request.ParseFromString(bytes), "peer protobuf"); validate_message(request);
        check(request.protocol_version() == 1 && request.request_id() == id && request.operation_case() == operation,
              "peer unexpected request or replay");
        return request;
    }
    void closed_without_replay() {
        char byte; std::size_t n = 0;
        for (;;) {
            ERR_clear_error(); const int result = SSL_read_ex(ssl.get(), &byte, 1, &n);
            check(result != 1, "failed client replayed bytes");
            const int error = SSL_get_error(ssl.get(), result);
            if (error == SSL_ERROR_WANT_READ || error == SSL_ERROR_WANT_WRITE) { retry(error); continue; }
            check(error == SSL_ERROR_ZERO_RETURN || error == SSL_ERROR_SSL || error == SSL_ERROR_SYSCALL,
                  "peer close classification");
            break;
        }
    }
};

void failed(ActorSessionClient& client, const TlsFixture& f) {
    check(client.failed(), "failure did not poison TLS client");
    rejects([&] { client.authenticate(); }); rejects([&] { client.inspect(); });
    rejects([&] { client.action({}); }); rejects([&] { client.activate(f.transition); });
    rejects([&] { client.depart(1, f.transition); });
}

void protocol(Peer& peer, const TlsFixture& f, bool fragmented) {
    const auto begin = peer.receive(pb::WorldSessionRequest::kBegin, 1);
    check(begin.begin().credential().SerializeAsString() == f.credential.SerializeAsString(), "credential mismatch");
    auto challenge = challenge_for(f); auto response = reply_for(1);
    *response.mutable_challenge() = challenge; peer.reply(response, fragmented);
    const auto complete = peer.receive(pb::WorldSessionRequest::kComplete, 2);
    check(complete.complete().nonce() == challenge.nonce(), "nonce mismatch");
    pb::VerifyRequest verification;
    *verification.mutable_content() = challenge.signing_request();
    *verification.mutable_proof() = complete.complete().proof();
    verification.set_expected_public_key(f.actor.public_key());
    check(identity::verify(verification), "TLS client signature invalid");
    response = reply_for(2); opened(f, response.mutable_opened()); peer.reply(response, fragmented);
}

// Listener binds only loopback. No external service or production authentication bypass exists.
void session(TlsFixture& f, const std::string& mode) {
    const auto baseline = descriptors();
    auto context = identity::tls_detail::make_context(true, f.root / "server.pem", f.root / "server.key", f.root / "ca.pem");
    identity::tls_detail::Fd listener(socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0));
    check(listener.value >= 0, "listener socket");
    sockaddr_in address{}; address.sin_family = AF_INET; address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    check(bind(listener.value, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 &&
          listen(listener.value, 1) == 0, "loopback listener");
    socklen_t length = sizeof(address);
    check(getsockname(listener.value, reinterpret_cast<sockaddr*>(&address), &length) == 0, "listener address");
    f.config.set_port(ntohs(address.sin_port)); const auto path = f.save_config();
    std::exception_ptr server_error;
    bool verified = false;
    std::thread worker([&] {
        try {
            pollfd p{listener.value, POLLIN, 0}; check(poll(&p, 1, 1500) > 0, "accept deadline");
            Peer peer(accept4(listener.value, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK), context.get());
            check(peer.fd >= 0, "accept failed");
            if (mode == "handshake-timeout") { std::this_thread::sleep_for(500ms); return; }
            if (mode == "dns" || mode == "ca" || mode == "invalid-client") {
                rejects([&] { peer.accept(); }); return;
            }
            peer.accept(); verified = true;
            if (mode == "one-record" || mode == "fragments" || mode == "closed-write") {
                protocol(peer, f, mode == "fragments");
                if (mode == "closed-write") { shutdown(peer.fd, SHUT_RDWR); return; }
                peer.receive(pb::WorldSessionRequest::kInspect, 3);
                auto response = reply_for(3); response.mutable_state()->set_id(f.actor_id);
                peer.reply(response, mode == "fragments");
                // Hold the TCP stream open. Poll-before-SSL_read stalls on buffered payload.
                peer.closed_without_replay(); return;
            }
            peer.receive(pb::WorldSessionRequest::kBegin, 1);
            auto response = reply_for(1); *response.mutable_challenge() = challenge_for(f);
            auto bytes = frame(response);
            if (mode == "timeout") { peer.closed_without_replay(); return; }
            if (mode == "partial-timeout") {
                // Header and payload consume the same exchange budget.
                std::this_thread::sleep_for(180ms); peer.send(bytes.substr(0, 4));
                std::this_thread::sleep_for(180ms);
                // A reset deadline would still be waiting here. The absolute deadline closes first.
                peer.closed_without_replay(); return;
            }
            if (mode == "truncated-header" || mode == "truncated-body") {
                peer.send(bytes.substr(0, mode == "truncated-header" ? 2 : bytes.size() - 1));
                (void)SSL_shutdown(peer.ssl.get()); peer.closed_without_replay(); return;
            }
            if (mode == "malformed") peer.send(std::string("\x00\x00\x00\x01\xff", 5));
            else {
                if (mode == "rejected") { response.mutable_error()->set_code(core::v1::FORBIDDEN); response.mutable_error()->set_message("fixture rejection"); }
                if (mode == "request-id") response.set_request_id(99);
                if (mode == "version") response.set_protocol_version(2);
                if (mode == "outcome") response.mutable_opened();
                if (mode == "unknown") response.GetReflection()->MutableUnknownFields(&response)->AddVarint(999, 1);
                peer.reply(response, false);
            }
            peer.closed_without_replay();
        } catch (...) { server_error = std::current_exception(); }
    });
    std::exception_ptr client_error;
    const auto start = std::chrono::steady_clock::now();
    try {
        if (mode == "dns" || mode == "ca" || mode == "handshake-timeout") {
            rejects([&] { ActorSessionClient client(path); });
        } else if (mode == "invalid-client") {
            std::unique_ptr<ActorSessionClient> client;
            try { client = std::make_unique<ActorSessionClient>(path); }
            catch (const std::runtime_error&) { /* TLS may reject during construction. */ }
            if (client) { rejects([&] { client->authenticate(); }); failed(*client, f); }
        } else {
            ActorSessionClient client(path);
            if (mode == "one-record" || mode == "fragments" || mode == "closed-write") {
                check(client.authenticate().has_opened(), "TLS authentication");
                if (mode == "closed-write") {
                    std::this_thread::sleep_for(60ms);
                    rejects([&] { client.inspect(); }); failed(client, f);
                } else check(client.inspect().state().id() == f.actor_id && !client.failed(), "TLS inspect");
            } else {
                rejects([&] { client.authenticate(); }); failed(client, f);
            }
        }
        const auto elapsed = std::chrono::steady_clock::now() - start;
        check(elapsed < 1500ms, "client exceeded bounded time");
        if (mode == "timeout" || mode == "partial-timeout" || mode == "handshake-timeout")
            check(elapsed >= 250ms && elapsed < 440ms, "deadline restarted or failed early");
    } catch (...) { client_error = std::current_exception(); }
    worker.join(); listener.reset(); context.reset();
    check(descriptors() == baseline, "TLS descriptor leak");
    if (client_error) std::rethrow_exception(client_error);
    if (server_error) std::rethrow_exception(server_error);
    if (mode != "dns" && mode != "ca" && mode != "invalid-client" && mode != "handshake-timeout")
        check(verified, "production mutual TLS path was not reached");
}

void config_rejection(TlsFixture& f, const std::string& mode) {
    identity::tls_detail::Fd listener(socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0));
    check(listener.value >= 0, "config listener socket");
    sockaddr_in address{}; address.sin_family = AF_INET; address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    check(bind(listener.value, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 &&
          listen(listener.value, 1) == 0, "config loopback listener");
    socklen_t length = sizeof(address);
    check(getsockname(listener.value, reinterpret_cast<sockaddr*>(&address), &length) == 0, "config listener address");
    f.config.set_port(ntohs(address.sin_port));
    if (mode == "missing-client") f.config.clear_certificate_path();
    if (mode == "missing-key") f.config.clear_private_key_path();
    if (mode == "missing-ca") f.config.clear_server_ca_path();
    if (mode == "timeout-low") f.config.set_io_timeout_ms(49);
    if (mode == "timeout-high") f.config.set_io_timeout_ms(60001);
    if (mode == "config-version") f.config.set_protocol_version(2);
    if (mode == "config-port") f.config.set_port(65536);
    if (mode == "config-address") f.config.set_address("localhost");
    if (mode == "config-name") f.config.set_server_name(std::string("bad\0name", 8));
    if (mode == "config-world") f.config.clear_expected_world_id();
    if (mode == "config-unknown") f.config.GetReflection()->MutableUnknownFields(&f.config)->AddVarint(999, 1);
    if (mode == "actor-key") f.config.mutable_actor_credential()->mutable_content()->set_payload("bad");
    if (mode == "mismatched-key") f.config.set_private_key_path((f.root / "server.key").string());
    const auto path = f.save_config(); const auto baseline = descriptors();
    rejects([&] { ActorSessionClient client(path); });
    check(descriptors() == baseline, "constructor leaked descriptor");
    pollfd pending{listener.value, POLLIN, 0};
    check(poll(&pending, 1, 0) == 0, "invalid configuration attempted network I/O or fallback");
}
}
int main() {
    try {
        std::size_t count = 0;
        auto run = [&](const std::string& name, const std::function<void()>& test) {
            try { test(); } catch (const std::exception& error) { throw std::runtime_error(name + ": " + error.what()); }
            ++count; std::cout << "PASS " << name << '\n';
        };
        run("SIGPIPE-contract-no-global-side-effects", [&] {
            struct sigaction original{}, before{}, after{};
            check(sigaction(SIGPIPE, nullptr, &original) == 0, "get signal disposition");
            before.sa_handler = SIG_DFL; sigemptyset(&before.sa_mask);
            check(sigaction(SIGPIPE, &before, nullptr) == 0, "set fixture signal disposition");
            bool contract_rejected = false;
            try { ActorSessionClient client("/nonexistent/config"); }
            catch (const std::runtime_error& error) {
                contract_rejected = std::string(error.what()) == "Actor TLS client process must ignore SIGPIPE";
            }
            check(contract_rejected, "SIGPIPE contract was not checked before TLS I/O");
            check(sigaction(SIGPIPE, nullptr, &after) == 0 && after.sa_handler == SIG_DFL,
                  "library changed SIGPIPE disposition");
            check(sigaction(SIGPIPE, &original, nullptr) == 0, "restore signal disposition");
        });
        // Application-level contract, identical to IssuerClient callers. Library never sets this.
        struct sigaction ignored{}; ignored.sa_handler = SIG_IGN; sigemptyset(&ignored.sa_mask);
        check(sigaction(SIGPIPE, &ignored, nullptr) == 0, "fixture ignore SIGPIPE");
        for (const std::string mode : {"one-record", "fragments", "dns", "ca", "invalid-client", "handshake-timeout",
                "timeout", "partial-timeout", "truncated-header", "truncated-body", "malformed", "rejected",
                "request-id", "version", "outcome", "unknown", "closed-write"}) {
            run(mode, [&] {
                TlsFixture f;
                if (mode == "dns") f.config.set_server_name("wrong.fixture");
                if (mode == "ca") f.config.set_server_ca_path((f.root / "other.pem").string());
                if (mode == "invalid-client") {
                    f.config.set_certificate_path((f.root / "invalid.pem").string());
                    f.config.set_private_key_path((f.root / "invalid.key").string());
                }
                session(f, mode);
            });
        }
        for (const std::string mode : {"missing-client", "missing-key", "missing-ca", "timeout-low", "timeout-high",
                "config-version", "config-port", "config-address", "config-name", "config-world", "config-unknown",
                "actor-key", "mismatched-key"})
            run(mode, [&] { TlsFixture f; config_rejection(f, mode); });
        std::cout << count << " actor-session-client TLS cases passed\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
