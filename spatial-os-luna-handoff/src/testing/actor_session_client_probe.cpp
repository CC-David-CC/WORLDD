#include "world/actor_session_client.hpp"
#include "identity/keys.hpp"
#include "core/protocol.hpp"
#include <array>
#include <cstdlib>
#include <exception>
#include <chrono>
#include <functional>
#include <poll.h>
#include <thread>
#include <vector>

#include <cerrno>
#include <fcntl.h>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <sys/socket.h>
#include <unistd.h>

namespace {
using namespace spatial;

void expect_closed_after_failed_key_load() {
    int sockets[2]{};
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) != 0) throw std::runtime_error("socketpair failed");
    const int transferred = sockets[0];
    bool construction_succeeded = false;
    try {
        ActorSessionClient client(transferred, {}, "/definitely/missing/actor-key.pem", "fixture-world");
        construction_succeeded = true;
    } catch (const std::exception&) {
    }
    if (construction_succeeded) throw std::runtime_error("missing key unexpectedly accepted");
    errno = 0;
    if (fcntl(transferred, F_GETFD) != -1 || errno != EBADF)
        throw std::runtime_error("descriptor leaked when key loading failed");
    close(sockets[1]);
}

void expect_invalid_descriptor_rejected() {
    auto key = identity::KeyPair::generate();
    const auto root = std::filesystem::temp_directory_path() /
        ("actor-session-client-fixture-" + std::to_string(getpid()));
    std::filesystem::create_directory(root);
    chmod(root.c_str(), 0700);
    const auto path = root / "actor.pem";
    key.save_new(path);
    bool rejected = false;
    try {
        ActorSessionClient client(-1, {}, path, "fixture-world");
    } catch (const std::exception&) {
        rejected = true;
    }
    std::filesystem::remove_all(root);
    if (!rejected) throw std::runtime_error("invalid descriptor unexpectedly accepted");
}

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

// Fixture I/O has its own deadline. A broken probe cannot hang a test-box job.
void transfer(int fd, char* bytes, std::size_t size, bool writing) {
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    std::size_t offset = 0;
    while (offset < size) {
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now()).count();
        check(remaining > 0, "fixture deadline expired");
        pollfd p{fd, static_cast<short>(writing ? POLLOUT : POLLIN), 0};
        const int ready = poll(&p, 1, static_cast<int>(remaining));
        if (ready < 0 && errno == EINTR) continue;
        check(ready > 0, "fixture poll failed");
        const auto n = writing ? send(fd, bytes + offset, size - offset, MSG_NOSIGNAL | MSG_DONTWAIT)
                               : recv(fd, bytes + offset, size - offset, MSG_DONTWAIT);
        if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        check(n > 0, "fixture stream lost");
        offset += static_cast<std::size_t>(n);
    }
}

std::string frame(const pb::WorldSessionReply& reply) {
    const auto body = reply.SerializeAsString();
    const auto size = static_cast<std::uint32_t>(body.size());
    std::string result;
    for (int shift : {24, 16, 8, 0}) result.push_back(static_cast<char>(size >> shift));
    return result + body;
}

void send_bytes(int fd, std::string bytes) { transfer(fd, bytes.data(), bytes.size(), true); }
void send_reply(int fd, const pb::WorldSessionReply& reply, bool fragmented = false) {
    auto bytes = frame(reply);
    if (!fragmented) { send_bytes(fd, bytes); return; }
    // Split both the header and body across writes with distinct arrival times.
    send_bytes(fd, bytes.substr(0, 1));
    std::this_thread::sleep_for(2ms);
    send_bytes(fd, bytes.substr(1, 4));
    std::this_thread::sleep_for(2ms);
    send_bytes(fd, bytes.substr(5));
}

pb::WorldSessionRequest receive(int fd, pb::WorldSessionRequest::OperationCase operation,
                                std::uint64_t id) {
    std::array<unsigned char, 4> header{};
    transfer(fd, reinterpret_cast<char*>(header.data()), header.size(), false);
    const std::uint32_t size = (std::uint32_t(header[0]) << 24) |
        (std::uint32_t(header[1]) << 16) | (std::uint32_t(header[2]) << 8) | header[3];
    check(size > 0 && size <= max_frame_bytes, "bad fixture request size");
    std::string bytes(size, '\0');
    transfer(fd, bytes.data(), bytes.size(), false);
    pb::WorldSessionRequest request;
    check(request.ParseFromString(bytes), "bad request protobuf");
    validate_message(request);
    check(request.protocol_version() == 1 && request.request_id() == id &&
        request.operation_case() == operation, "unexpected request envelope or operation");
    return request;
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

void handshake(int fd, const Fixture& f, bool prepared = false, bool fragmented = false,
               const std::function<void(pb::WorldSessionReply&)>& mutate = {}) {
    const auto begin = receive(fd, pb::WorldSessionRequest::kBegin, 1);
    check(begin.begin().credential().SerializeAsString() == f.credential.SerializeAsString(),
          "begin credential mismatch");
    check(begin.begin().transition_id() == (prepared ? f.transition : ""), "begin transition mismatch");
    auto challenge = challenge_for(f);
    auto response = reply_for(1);
    *response.mutable_challenge() = challenge;
    send_reply(fd, response, fragmented);
    const auto complete = receive(fd, pb::WorldSessionRequest::kComplete, 2);
    check(complete.complete().nonce() == challenge.nonce(), "complete nonce mismatch");
    pb::VerifyRequest verification;
    *verification.mutable_content() = challenge.signing_request();
    *verification.mutable_proof() = complete.complete().proof();
    verification.set_expected_public_key(f.actor.public_key());
    check(identity::verify(verification), "client authentication signature invalid");
    response = reply_for(2);
    if (prepared) {
        response.mutable_prepared()->set_transition_id(f.transition);
        opened(f, response.mutable_prepared()->mutable_actor());
    } else opened(f, response.mutable_opened());
    if (mutate) mutate(response);
    send_reply(fd, response, fragmented);
}

void no_request(int fd) {
    char byte;
    errno = 0;
    check(recv(fd, &byte, 1, MSG_PEEK | MSG_DONTWAIT) == -1 &&
          (errno == EAGAIN || errno == EWOULDBLOCK), "invalid operation sent bytes or closed channel");
}

void peer_closed(int fd) {
    pollfd p{fd, POLLIN, 0};
    check(poll(&p, 1, 1000) > 0, "failed client did not close");
    char byte;
    check(recv(fd, &byte, 1, MSG_DONTWAIT) == 0, "failed client replayed or left channel open");
}

void inactive(ActorSessionClient& client, int peer, const Fixture& f) {
    rejects([&] { client.inspect(); });
    rejects([&] { client.action({}); });
    rejects([&] { client.depart(1, f.transition); });
    no_request(peer);
    check(!client.failed(), "local lifecycle guard poisoned client");
}

void failed_no_replay(ActorSessionClient& client, int peer, const Fixture& f) {
    check(client.failed(), "invalid peer response did not fail client");
    rejects([&] { client.authenticate(); });
    rejects([&] { client.activate(f.transition); });
    rejects([&] { client.depart(1, f.transition); });
    rejects([&] { client.inspect(); });
    rejects([&] { client.action({}); });
    peer_closed(peer);
}

// Each case has a new production client and a private authenticated endpoint.
void session(Fixture& f, const std::function<void(ActorSessionClient&, int)>& run,
             const std::function<void(int)>& peer, std::chrono::milliseconds timeout = 500ms,
             bool close_after_reply = false) {
    int sockets[2];
    check(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0, "socketpair failed");
    int capacity = 4096;
    check(setsockopt(sockets[0], SOL_SOCKET, SO_SNDBUF, &capacity, sizeof(capacity)) == 0,
          "cannot bound fixture send buffer");
    ActorSessionClient client(sockets[0], f.credential, f.key_path, f.world, timeout);
    std::exception_ptr peer_error;
    std::thread worker([&] {
        try { peer(sockets[1]); } catch (...) { peer_error = std::current_exception(); }
        // Keep the endpoint for close/no-request assertions in the caller.
        if (peer_error) shutdown(sockets[1], SHUT_RDWR);
        if (close_after_reply) close(sockets[1]);
    });
    std::exception_ptr client_error;
    try { run(client, sockets[1]); } catch (...) {
        client_error = std::current_exception();
        shutdown(sockets[1], SHUT_RDWR);
    }
    worker.join();
    if (!close_after_reply) close(sockets[1]);
    if (client_error) std::rethrow_exception(client_error);
    if (peer_error) std::rethrow_exception(peer_error);
}

void success(Fixture& f) {
    session(f, [&](auto& client, int peer) {
        inactive(client, peer, f);
        rejects([&] { client.activate(f.transition); });
        rejects([&] { client.authenticate({}, "short"); });
        no_request(peer);
        check(client.authenticate().has_opened(), "authentication failed");
        rejects([&] { client.authenticate(); });
        rejects([&] { client.activate(f.transition); });
        rejects([&] { client.depart(1, "short"); });
        no_request(peer);
        check(client.inspect().state().id() == f.actor_id, "inspection failed");
        check(client.action({}).action().accepted(), "action failed");
        check(client.depart(7, f.transition).has_departed(), "departure failed");
        inactive(client, peer, f);
        rejects([&] { client.authenticate(); });
        rejects([&] { client.activate(f.transition); });
        no_request(peer);
    }, [&](int fd) {
        handshake(fd, f, false, true);
        receive(fd, pb::WorldSessionRequest::kInspect, 3);
        auto reply = reply_for(3); reply.mutable_state()->set_id(f.actor_id); send_reply(fd, reply);
        receive(fd, pb::WorldSessionRequest::kAction, 4);
        reply = reply_for(4); reply.mutable_action()->set_accepted(true); send_reply(fd, reply);
        const auto request = receive(fd, pb::WorldSessionRequest::kDepart, 5);
        check(request.depart().seq() == 7 && request.depart().transition_id() == f.transition,
              "departure request mismatch");
        reply = reply_for(5);
        auto* receipt = reply.mutable_departed();
        receipt->set_actor_id(f.actor_id); receipt->set_world_id(f.world);
        receipt->set_transition_id(f.transition); receipt->set_world_revision(9); send_reply(fd, reply);
    });
}

void prepared_success(Fixture& f) {
    session(f, [&](auto& client, int peer) {
        check(client.authenticate({}, f.transition).has_prepared(), "preparation failed");
        inactive(client, peer, f);
        rejects([&] { client.authenticate(); });
        rejects([&] { client.activate(std::string(32, 'x')); });
        rejects([&] { client.activate("short"); });
        no_request(peer);
        check(client.activate(f.transition).has_opened(), "activation failed");
        rejects([&] { client.activate(f.transition); }); no_request(peer);
        check(client.inspect().has_state(), "activated session inactive");
    }, [&](int fd) {
        handshake(fd, f, true);
        const auto request = receive(fd, pb::WorldSessionRequest::kActivate, 3);
        check(request.activate().transition_id() == f.transition, "activation token mismatch");
        auto reply = reply_for(3); opened(f, reply.mutable_opened()); send_reply(fd, reply);
        receive(fd, pb::WorldSessionRequest::kInspect, 4);
        reply = reply_for(4); reply.mutable_state()->set_id(f.actor_id); send_reply(fd, reply);
    });
}

void bad_challenge(Fixture& f, const std::string& fault) {
    session(f, [&](auto& client, int peer) {
        rejects([&] { client.authenticate(); }); failed_no_replay(client, peer, f);
    }, [&](int fd) {
        receive(fd, pb::WorldSessionRequest::kBegin, 1);
        auto reply = reply_for(1);
        auto challenge = challenge_for(f);
        pb::AuthenticationChallenge context;
        check(context.ParseFromString(challenge.signing_request().payload()), "fixture context parse");
        if (fault == "purpose") challenge.mutable_signing_request()->set_purpose(pb::ISSUER_ADMINISTRATION);
        if (fault == "issuer") context.set_issuer_id(std::string(32, 'x'));
        if (fault == "credential") context.mutable_request()->mutable_credential()->mutable_proof()->set_signature(std::string(64, 'x'));
        if (fault == "audience") context.mutable_request()->set_audience("wrong-world");
        if (fault == "nonce") context.set_nonce(std::string(32, 'x'));
        if (fault == "nonce-length") challenge.set_nonce("short");
        if (fault == "binding") context.mutable_request()->set_channel_binding("short");
        if (fault == "version") context.set_protocol_version(2);
        if (fault == "lifetime") context.set_lifetime_ms(0);
        if (fault == "incarnation") context.set_incarnation("short");
        challenge.mutable_signing_request()->set_payload(context.SerializeAsString());
        if (fault == "payload") challenge.mutable_signing_request()->set_payload(std::string(1, '\xff'));
        *reply.mutable_challenge() = challenge;
        send_reply(fd, reply);
    });
}

void bad_envelope(Fixture& f, const std::string& fault) {
    session(f, [&](auto& client, int peer) {
        rejects([&] { client.authenticate(); }); failed_no_replay(client, peer, f);
    }, [&](int fd) {
        receive(fd, pb::WorldSessionRequest::kBegin, 1);
        auto reply = reply_for(1); *reply.mutable_challenge() = challenge_for(f);
        if (fault == "version") reply.set_protocol_version(2);
        if (fault == "request-id") reply.set_request_id(99);
        if (fault == "outcome") reply.mutable_opened();
        if (fault == "error") reply.mutable_error()->set_message("fixture denial");
        if (fault == "missing-outcome") reply.clear_outcome();
        if (fault == "unknown-field") reply.GetReflection()->MutableUnknownFields(&reply)->AddVarint(999, 1);
        send_reply(fd, reply);
    });
}

void bad_receipt(Fixture& f, const std::string& stage, const std::string& fault) {
    const bool prepared = stage == "prepared" || stage == "activate";
    auto mutate = [&](pb::WorldSessionReply& reply) {
        if (stage == "depart") {
            auto* receipt = reply.mutable_departed();
            if (fault == "actor") receipt->set_actor_id("wrong-actor");
            if (fault == "world") receipt->set_world_id("wrong-world");
            if (fault == "transition") receipt->set_transition_id(std::string(32, 'x'));
        } else {
            auto* receipt = reply.has_prepared() ? reply.mutable_prepared()->mutable_actor() : reply.mutable_opened();
            if (fault == "actor") receipt->set_actor_id("wrong-actor");
            if (fault == "world") receipt->set_world_id("wrong-world");
            if (fault == "transition") reply.mutable_prepared()->set_transition_id(std::string(32, 'x'));
        }
    };
    session(f, [&](auto& client, int peer) {
        if (stage == "opened" || stage == "prepared") {
            rejects([&] { client.authenticate({}, prepared ? f.transition : ""); });
        } else {
            client.authenticate({}, prepared ? f.transition : "");
            if (stage == "activate") rejects([&] { client.activate(f.transition); });
            else rejects([&] { client.depart(1, f.transition); });
        }
        failed_no_replay(client, peer, f);
    }, [&](int fd) {
        handshake(fd, f, prepared, false,
            stage == "opened" || stage == "prepared" ? mutate : std::function<void(pb::WorldSessionReply&)>{});
        if (stage == "opened" || stage == "prepared") return;
        receive(fd, stage == "activate" ? pb::WorldSessionRequest::kActivate : pb::WorldSessionRequest::kDepart, 3);
        auto reply = reply_for(3);
        if (stage == "activate") opened(f, reply.mutable_opened());
        else {
            auto* receipt = reply.mutable_departed();
            receipt->set_actor_id(f.actor_id); receipt->set_world_id(f.world); receipt->set_transition_id(f.transition);
        }
        mutate(reply); send_reply(fd, reply);
    });
}

void bad_frame(Fixture& f, const std::string& fault) {
    session(f, [&](auto& client, int peer) {
        const auto start = std::chrono::steady_clock::now();
        rejects([&] { client.authenticate(); });
        const auto elapsed = std::chrono::steady_clock::now() - start;
        check(elapsed < 1500ms, "transport failure exceeded bound");
        if (fault == "timeout" || fault == "partial-timeout")
            check(elapsed >= 70ms, "timeout failed before its deadline");
        failed_no_replay(client, peer, f);
    }, [&](int fd) {
        receive(fd, pb::WorldSessionRequest::kBegin, 1);
        auto reply = reply_for(1); *reply.mutable_challenge() = challenge_for(f);
        auto bytes = frame(reply);
        if (fault == "header") { send_bytes(fd, bytes.substr(0, 2)); shutdown(fd, SHUT_WR); }
        if (fault == "body") { send_bytes(fd, bytes.substr(0, bytes.size() - 1)); shutdown(fd, SHUT_WR); }
        if (fault == "zero") send_bytes(fd, std::string(4, '\0'));
        if (fault == "oversize") send_bytes(fd, std::string("\x00\x10\x00\x01", 4));
        if (fault == "protobuf") send_bytes(fd, std::string("\x00\x00\x00\x01\xff", 5));
        if (fault == "timeout") { /* No response. Leave peer open. */ }
        if (fault == "partial-timeout") send_bytes(fd, bytes.substr(0, 5));
    }, 100ms);
}

void uncertain_loss(Fixture& f, const std::string& stage) {
    session(f, [&](auto& client, int peer) {
        client.authenticate({}, stage == "activate" ? f.transition : "");
        if (stage == "activate") rejects([&] { client.activate(f.transition); });
        if (stage == "depart") rejects([&] { client.depart(1, f.transition); });
        if (stage == "action") rejects([&] { client.action({}); });
        failed_no_replay(client, peer, f);
    }, [&](int fd) {
        handshake(fd, f, stage == "activate");
        const auto operation = stage == "activate" ? pb::WorldSessionRequest::kActivate :
            stage == "depart" ? pb::WorldSessionRequest::kDepart : pb::WorldSessionRequest::kAction;
        receive(fd, operation, 3); // Effect may have happened. Lose its reply only.
        shutdown(fd, SHUT_WR);
    });
}

void backpressure(Fixture& f) {
    session(f, [&](auto& client, int peer) {
        client.authenticate();
        core::v1::ActorActionRequest action;
        action.mutable_key()->set_keysym(std::string(900000, 'x'));
        action.mutable_key()->set_pressed(true);
        action.set_seq(1);
        const auto start = std::chrono::steady_clock::now();
        rejects([&] { client.action(action); });
        const auto elapsed = std::chrono::steady_clock::now() - start;
        check(elapsed >= 70ms && elapsed < 1500ms, "backpressure deadline exceeded or failed early");
        check(client.failed(), "backpressure did not poison client");
        rejects([&] { client.action(action); });
        // Drain the one partial request. EOF must follow, with no repeated frame.
        std::string received;
        std::array<char, 8192> bytes{};
        for (;;) {
            const auto n = recv(peer, bytes.data(), bytes.size(), MSG_DONTWAIT);
            if (n == 0) break;
            check(n > 0, "backpressure drain did not reach EOF");
            received.append(bytes.data(), static_cast<std::size_t>(n));
        }
        check(received.size() > 4 && received.size() < 900000, "write did not encounter partial-frame backpressure");
        pb::WorldSessionRequest expected;
        expected.set_protocol_version(1); expected.set_request_id(3);
        *expected.mutable_action() = action;
        const auto body = expected.SerializeAsString();
        std::string bytes_expected;
        for (int shift : {24, 16, 8, 0})
            bytes_expected.push_back(static_cast<char>(static_cast<std::uint32_t>(body.size()) >> shift));
        bytes_expected += body;
        check(bytes_expected.compare(0, received.size(), received) == 0,
              "partial write contained a replay or changed request");
        peer_closed(peer);
    }, [&](int fd) { handshake(fd, f); /* Stop reading after authentication. */ }, 100ms);
}

void buffered_final_reply(Fixture& f) {
    session(f, [&](auto& client, int) {
        client.authenticate();
        check(client.inspect().has_state(), "buffered final response rejected");
        check(!client.failed(), "complete final response poisoned client");
    }, [&](int fd) {
        handshake(fd, f);
        receive(fd, pb::WorldSessionRequest::kInspect, 3);
        auto reply = reply_for(3); reply.mutable_state()->set_id(f.actor_id);
        send_reply(fd, reply);
        // session closes the peer completely, so POLLIN may accompany POLLHUP.
    }, 500ms, true);
}

}

int main() {
    try {
        expect_closed_after_failed_key_load();
        expect_invalid_descriptor_rejected();
        Fixture fixture;
        std::size_t count = 2;
        auto run = [&](const std::string& name, const std::function<void()>& test) {
            try { test(); } catch (const std::exception& error) {
                throw std::runtime_error(name + ": " + error.what());
            }
            ++count;
            std::cout << "PASS " << name << '\n';
        };
        run("authentication-fragments-lifecycle-departure", [&] { success(fixture); });
        run("prepared-inactivity-activation", [&] { prepared_success(fixture); });
        for (const std::string fault : {"purpose", "issuer", "credential", "audience", "nonce",
                "nonce-length", "binding", "version", "lifetime", "incarnation", "payload"})
            run("challenge-" + fault, [&] { bad_challenge(fixture, fault); });
        for (const std::string fault : {"version", "request-id", "outcome", "error", "missing-outcome", "unknown-field"})
            run("envelope-" + fault, [&] { bad_envelope(fixture, fault); });
        for (const std::string stage : {"opened", "prepared", "activate", "depart"}) {
            for (const std::string fault : {"actor", "world"})
                run(stage + "-" + fault, [&] { bad_receipt(fixture, stage, fault); });
            if (stage == "prepared" || stage == "depart")
                run(stage + "-transition", [&] { bad_receipt(fixture, stage, "transition"); });
        }
        for (const std::string fault : {"header", "body", "zero", "oversize", "protobuf", "timeout", "partial-timeout"})
            run("frame-" + fault, [&] { bad_frame(fixture, fault); });
        for (const std::string stage : {"activate", "depart", "action"})
            run("uncertain-loss-no-replay-" + stage, [&] { uncertain_loss(fixture, stage); });
        run("write-backpressure", [&] { backpressure(fixture); });
        run("buffered-final-reply", [&] { buffered_final_reply(fixture); });
        std::cout << count << " actor-session-client probe cases passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
