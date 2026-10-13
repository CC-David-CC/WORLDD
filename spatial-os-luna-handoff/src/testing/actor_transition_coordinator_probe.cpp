#include "world/actor_transition_coordinator.hpp"
#include "identity/keys.hpp"
#include "core/protocol.hpp"
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <functional>
#include <iostream>
#include <poll.h>
#include <stdexcept>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {
using namespace spatial;
namespace pb = identity::v1;
using namespace std::chrono_literals;
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
template<class F> void rejects(F f) {
    bool rejected = false;
    try { f(); } catch (const std::exception&) { rejected = true; }
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
    char identity_byte;

    explicit Fixture(char actor_byte = 'a') : identity_byte(actor_byte) {
        std::string pattern = (std::filesystem::temp_directory_path() /
            "actor-transition-run02-XXXXXX").string();
        check(mkdtemp(pattern.data()) != nullptr, "mkdtemp failed");
        root = pattern;
        key_path = root / "actor.pem";
        actor.save_new(key_path);
        pb::ActorCredential claims;
        claims.set_protocol_version(1);
        claims.set_issuer_id(std::string(32, 'i'));
        claims.set_actor_id(std::string(32, actor_byte));
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
        for (int i = 0; i < 32; ++i) {
            const char digits[] = "0123456789abcdef";
            actor_id += digits[static_cast<unsigned char>(actor_byte) >> 4];
            actor_id += digits[static_cast<unsigned char>(actor_byte) & 15];
        }
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

void opened(const Fixture& f, pb::WorldSessionOpened* result, bool active = true) {
    result->set_world_id(f.world);
    result->set_actor_id(f.actor_id);
    auto* status = result->mutable_identity();
    status->set_issuer_id(std::string(32, 'i'));
    status->set_actor_id(std::string(32, f.identity_byte));
    status->set_credential_id(std::string(32, 'c'));
    status->set_kind(pb::HUMAN);
    status->set_active(true);
    status->set_security_revision(1);
    if (active) {
        result->set_geometry_view_id(std::string(32, 'v'));
        result->set_authority_service_incarnation(std::string(32, 's'));
        result->set_authority_generation(7);
    }
}

void handshake(int fd, const Fixture& f, bool prepared = false, bool fragmented = false,
               const std::function<void(pb::WorldSessionReply&)>& mutate = {},
               const std::function<void(const pb::WorldAuthenticationBegin&)>& inspect_begin = {}) {
    const auto begin = receive(fd, pb::WorldSessionRequest::kBegin, 1);
    check(begin.begin().credential().SerializeAsString() == f.credential.SerializeAsString(),
          "begin credential mismatch");
    check(begin.begin().transition_id() == (prepared ? f.transition : ""), "begin transition mismatch");
    if (inspect_begin) inspect_begin(begin.begin());
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
        opened(f, response.mutable_prepared()->mutable_actor(), false);
    } else opened(f, response.mutable_opened());
    if (mutate) mutate(response);
    send_reply(fd, response, fragmented);
}


void closed(int fd) {
    pollfd p{fd, POLLIN, 0};
    check(poll(&p, 1, 1500) > 0, "abandoned preparation not closed");
    char byte;
    check(recv(fd, &byte, 1, MSG_DONTWAIT) == 0, "unexpected request or replay before EOF");
}

// A real private endpoint and independent wire peer. No coordinator/client mocks.
struct Endpoint {
    int peer = -1;
    std::unique_ptr<ActorSessionClient> client;
    std::thread worker;
    std::exception_ptr error;
    explicit Endpoint(Fixture& f) {
        int sockets[2];
        check(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0, "socketpair failed");
        peer = sockets[1];
        try { client = std::make_unique<ActorSessionClient>(sockets[0], f.credential, f.key_path, f.world, 150ms); }
        catch (...) { close(peer); throw; }
    }
    void start(std::function<void(int)> run) {
        worker = std::thread([this, run = std::move(run)] {
            try { run(peer); } catch (...) { error = std::current_exception(); shutdown(peer, SHUT_RDWR); }
        });
    }
    void finish() { worker.join(); if (error) std::rethrow_exception(error); }
    ~Endpoint() {
        client.reset();
        if (worker.joinable()) { shutdown(peer, SHUT_RDWR); worker.join(); }
        close(peer);
    }
};

void denial(int fd, std::uint64_t id, bool malformed = false,
            core::v1::ErrorCode code = core::v1::FORBIDDEN) {
    auto reply = reply_for(id);
    reply.mutable_error()->set_code(code);
    reply.mutable_error()->set_message("fixture policy rejection");
    if (malformed) reply.set_request_id(id + 1);
    send_reply(fd, reply);
}
void state(int fd, const Fixture& f, std::uint64_t id) {
    receive(fd, pb::WorldSessionRequest::kInspect, id);
    auto reply = reply_for(id); reply.mutable_state()->set_id(f.actor_id); send_reply(fd, reply);
}

void transition_case(const std::string& fault, bool room_requested = false, bool pose_requested = false) {
    Fixture source_fixture;
    source_fixture.world = "source-world";
    Fixture destination_fixture(fault == "configured-actor" ? 'x' : 'a');
    destination_fixture.world = "destination-world";
    const std::string requested_destination = destination_fixture.world;
    if (fault == "configured-world") destination_fixture.world = "false-destination";
    Endpoint source(source_fixture), destination(destination_fixture);
    std::atomic<bool> inspected{false}, prepared{false}, departure_receipt_sent{false};
    core::v1::Pose pose;
    pose.mutable_position()->set_x(4); pose.mutable_position()->set_y(2); pose.mutable_position()->set_z(-3);
    pose.mutable_orientation()->set_w(1);
    const bool early = fault == "prepare-denied" || fault == "prepare-lost" ||
        fault == "prepared-actor" || fault == "configured-actor" || fault == "source-world" ||
        fault == "prepared-world" || fault == "prepared-transition" || fault == "configured-world";
    const bool departure_bad = fault == "depart-denied" || fault == "depart-storage-error" || fault == "depart-internal-error" || fault == "depart-lost" ||
        fault == "depart-timeout" || fault == "depart-actor" || fault == "depart-world" ||
        fault == "depart-transition" || fault == "depart-malformed-error";
    const bool active = !early && !departure_bad && fault != "activate-denied" &&
        fault != "activate-lost" && fault != "activate-timeout" && fault != "activate-actor" &&
        fault != "activate-world" && fault != "activate-malformed-error" && fault != "activate-internal-error";
    source.start([&](int fd) {
        handshake(fd, source_fixture);
        if (fault == "source-world") { state(fd, source_fixture, 3); closed(fd); return; }
        receive(fd, pb::WorldSessionRequest::kInspect, 3);
        auto inspection = reply_for(3); inspection.mutable_state()->set_id(source_fixture.actor_id);
        inspected = true; send_reply(fd, inspection);
        if (early) { state(fd, source_fixture, 4); closed(fd); return; }
        const auto request = receive(fd, pb::WorldSessionRequest::kDepart, 4);
        check(prepared.load(), "source departed before destination preparation");
        check(request.depart().seq() == 17 && request.depart().transition_id() == source_fixture.transition,
              "departure context not forwarded");
        if (fault == "depart-denied" || fault == "depart-storage-error" || fault == "depart-internal-error") {
            denial(fd, 4, false, fault == "depart-storage-error" ? core::v1::STORAGE_ERROR :
                fault == "depart-internal-error" ? core::v1::INTERNAL_ERROR : core::v1::FORBIDDEN);
            closed(fd); return; // No departure success receipt. No destination activation.
        }
        if (fault == "depart-malformed-error") { denial(fd, 4, true); closed(fd); return; }
        if (fault == "depart-lost") { shutdown(fd, SHUT_WR); closed(fd); return; }
        if (fault == "depart-timeout") { closed(fd); return; }
        auto reply = reply_for(4);
        auto* receipt = reply.mutable_departed();
        receipt->set_actor_id(fault == "depart-actor" ? "wrong" : source_fixture.actor_id);
        receipt->set_world_id(fault == "depart-world" ? "wrong" : source_fixture.world);
        receipt->set_transition_id(fault == "depart-transition" ? std::string(32, 'x') : source_fixture.transition);
        receipt->set_world_revision(91);
        char unexpected;
        check(recv(destination.peer, &unexpected, 1, MSG_PEEK | MSG_DONTWAIT) == -1 &&
            (errno == EAGAIN || errno == EWOULDBLOCK), "activation sent before departure receipt");
        // Publish before writing. Activation still requires the client to read and validate this frame.
        departure_receipt_sent = true;
        send_reply(fd, reply);
        closed(fd);
    });
    destination.start([&](int fd) {
        if (fault == "configured-actor" || fault == "configured-world" || fault == "source-world") { closed(fd); return; }
        if (fault == "prepare-lost") {
            receive(fd, pb::WorldSessionRequest::kBegin, 1);
            check(inspected.load(), "destination preparation preceded inspection");
            shutdown(fd, SHUT_WR);
            closed(fd); return;
        }
        handshake(fd, destination_fixture, true, true, [&](auto& reply) {
            if (fault == "prepare-denied") {
                reply.mutable_error()->set_code(core::v1::FORBIDDEN);
                reply.mutable_error()->set_message("fixture admission rejection after authentication");
            }
            if (fault == "prepared-actor") reply.mutable_prepared()->mutable_actor()->set_actor_id("wrong");
            if (fault == "prepared-world") reply.mutable_prepared()->mutable_actor()->set_world_id("wrong");
            if (fault == "prepared-transition") reply.mutable_prepared()->set_transition_id(std::string(32, 'x'));
            char unexpected;
            check(recv(source.peer, &unexpected, 1, MSG_PEEK | MSG_DONTWAIT) == -1 &&
                (errno == EAGAIN || errno == EWOULDBLOCK), "departure sent before prepared reply");
            prepared = true;
        }, [&](const auto& begin) {
            check(inspected.load(), "destination preparation preceded inspection");
            check(begin.has_room() == room_requested, "destination room presence changed");
            if (room_requested) check(begin.room() == "explicit-room", "destination room not forwarded");
            check(begin.has_pose() == pose_requested, "destination pose presence changed");
            if (pose_requested) check(begin.pose().SerializeAsString() == pose.SerializeAsString(), "pose not forwarded");
        });
        if (early || departure_bad) { closed(fd); return; }
        auto request = receive(fd, pb::WorldSessionRequest::kActivate, 3);
        check(departure_receipt_sent.load(), "activation preceded source departure reply");
        check(request.activate().transition_id() == source_fixture.transition, "activation identity changed");
        if (fault == "activate-denied" || fault == "activate-malformed-error" || fault == "activate-internal-error") {
            denial(fd, 3, fault == "activate-malformed-error",
                fault == "activate-internal-error" ? core::v1::INTERNAL_ERROR : core::v1::FORBIDDEN);
            closed(fd); return;
        }
        if (fault == "activate-lost") { shutdown(fd, SHUT_WR); closed(fd); return; }
        if (fault == "activate-timeout") { closed(fd); return; }
        auto reply = reply_for(3); opened(destination_fixture, reply.mutable_opened());
        if (fault == "activate-actor") reply.mutable_opened()->set_actor_id("wrong");
        if (fault == "activate-world") reply.mutable_opened()->set_world_id("wrong");
        send_reply(fd, reply);
        if (active) state(fd, destination_fixture, 4);
        closed(fd);
    });
    source.client->authenticate();
    if (fault == "source-world") {
        bool factory_called = false;
        rejects([&] { ActorTransitionCoordinator::transition(*source.client, "false-source", destination_fixture.world,
            source_fixture.transition, 17, [&] { factory_called = true; return std::move(destination.client); }); });
        check(!factory_called && !source.client->failed(), "source mismatch caused effects");
        source.client->inspect(); destination.client.reset();
    } else {
        auto outcome = ActorTransitionCoordinator::transition(*source.client, source_fixture.world, requested_destination,
            source_fixture.transition, 17, [&] { return std::move(destination.client); },
            room_requested ? "explicit-room" : "", pose_requested ? &pose : nullptr);
        const auto& result = outcome.result;
        check(result.protocol_version() == 1 && result.actor_id() == source_fixture.actor_id &&
            result.source_world_id() == source_fixture.world && result.destination_world_id() == requested_destination &&
            result.transition_id() == source_fixture.transition && result.host_presentation_selection_pending(), "result context lost");
        check(result.source_inspection_confirmed(), "source inspection fact lost");
        check(result.destination_preparation_confirmed() == !early, "preparation fact incorrect");
        check(result.source_departure_confirmed() == (!early && !departure_bad), "departure fact incorrect");
        check(result.has_departure_world_revision() == result.source_departure_confirmed(), "revision fact invented or lost");
        if (result.has_departure_world_revision()) check(result.departure_world_revision() == 91, "departure revision changed");
        check(result.destination_activation_confirmed() == active && bool(outcome.destination) == active, "activation fact incorrect");
        auto phase = pb::DESTINATION_ACTIVE;
        bool uncertain = false;
        if (early) {
            uncertain = fault != "configured-actor" && fault != "configured-world";
            phase = fault == "prepare-denied" || !uncertain ? pb::PREPARATION_REJECTED : pb::PREPARATION_UNCERTAIN;
        } else if (departure_bad) {
            uncertain = true;
            phase = pb::DEPARTURE_UNCERTAIN;
        } else if (!active) {
            uncertain = true;
            phase = pb::ACTIVATION_UNCERTAIN;
        }
        if (!active) check(!result.detail().empty(), "failure omitted detail");
        check(result.phase() == phase && result.effect_uncertain() == uncertain, "rejection/uncertainty distinction lost");
        const bool matched_error = fault == "prepare-denied" || fault == "depart-denied" ||
            fault == "depart-storage-error" || fault == "depart-internal-error" ||
            fault == "activate-denied" || fault == "activate-internal-error";
        check(result.has_server_error() == matched_error, "matched error evidence lost or invented");
        if (matched_error) {
            const auto code = fault == "depart-storage-error" ? core::v1::STORAGE_ERROR :
                fault == "depart-internal-error" || fault == "activate-internal-error" ? core::v1::INTERNAL_ERROR : core::v1::FORBIDDEN;
            check(result.server_error().code() == code, "typed error code lost");
        }
        pb::ActorTransitionResult decoded;
        check(decoded.ParseFromString(result.SerializeAsString()) && decoded.SerializeAsString() == result.SerializeAsString(),
              "typed result did not round-trip");
        if (early) {
            check(!source.client->failed(), "untouched source became unusable");
            source.client->inspect();
        } else {
            check(source.client->failed() == departure_bad, "departure error retained usable source");
            rejects([&] { source.client->action({}); });
            rejects([&] { source.client->authenticate(); });
            rejects([&] { source.client->depart(17, source_fixture.transition); });
            rejects([&] { source.client->inspect(); });
        }
        if (active) {
            rejects([&] { outcome.destination->activate(source_fixture.transition); });
            check(outcome.destination->inspect().state().id() == source_fixture.actor_id, "destination unusable");
        }
        outcome.destination.reset();
    }
    source.client.reset();
    source.finish(); destination.finish();
}

void source_inspection_failure(const std::string& fault) {
    Fixture f; Endpoint source(f);
    source.start([&](int fd) {
        handshake(fd, f);
        receive(fd, pb::WorldSessionRequest::kInspect, 3);
        if (fault == "rejection") { denial(fd, 3); state(fd, f, 4); }
        if (fault == "lost") shutdown(fd, SHUT_WR);
        closed(fd);
    });
    source.client->authenticate();
    bool factory_called = false;
    auto outcome = ActorTransitionCoordinator::transition(*source.client, f.world, "destination", f.transition, 7,
        [&]() -> std::unique_ptr<ActorSessionClient> { factory_called = true; return {}; });
    const auto& r = outcome.result;
    check(!factory_called && !outcome.destination && !r.source_inspection_confirmed() &&
        !r.destination_preparation_confirmed() && !r.source_departure_confirmed() &&
        !r.destination_activation_confirmed() && !r.has_departure_world_revision(), "inspection failure invented facts or effects");
    check(r.phase() == (fault == "rejection" ? pb::SOURCE_INSPECTION_REJECTED : pb::SOURCE_INSPECTION_UNCERTAIN) &&
        r.effect_uncertain() == (fault != "rejection"), "inspection failure classification wrong");
    if (fault == "rejection") source.client->inspect();
    else { check(source.client->failed(), "uncertain inspection did not fail closed"); rejects([&] { source.client->inspect(); }); }
    source.client.reset(); source.finish();
}

// Direct client proof: only a checked error envelope becomes a typed rejection.
void client_error_case(const std::string& fault) {
    Fixture f; Endpoint endpoint(f);
    endpoint.start([&](int fd) {
        handshake(fd, f);
        receive(fd, pb::WorldSessionRequest::kDepart, 3);
        if (fault == "rejection") { denial(fd, 3); }
        else if (fault == "malformed-error") denial(fd, 3, true);
        else if (fault == "lost") shutdown(fd, SHUT_WR);
        closed(fd);
    });
    endpoint.client->authenticate();
    bool typed = false, threw = false;
    try { endpoint.client->depart(5, f.transition); }
    catch (const ActorSessionRejected& e) {
        threw = typed = true;
        check(e.error().code() == core::v1::FORBIDDEN && std::string(e.what()) == "fixture policy rejection",
              "typed rejection lost error details");
    } catch (const std::exception&) { threw = true; }
    check(threw && typed == (fault == "rejection"), "client misclassified response");
    check(endpoint.client->failed(), "mutating error did not fail closed");
    rejects([&] { endpoint.client->depart(5, f.transition); });
    rejects([&] { endpoint.client->activate(f.transition); });
    rejects([&] { endpoint.client->authenticate(); });
    rejects([&] { endpoint.client->action({}); });
    rejects([&] { endpoint.client->inspect(); });
    endpoint.client.reset(); endpoint.finish();
}
}
int main() {
    try {
        std::size_t count = 0;
        auto run = [&](const std::string& name, auto test) {
            try { test(); } catch (const std::exception& e) { throw std::runtime_error(name + ": " + e.what()); }
            std::cout << "PASS " << name << '\n'; ++count;
        };
        for (const std::string fault : {"success", "prepare-denied", "prepare-lost", "configured-actor", "configured-world", "prepared-actor",
                "prepared-world", "prepared-transition", "source-world", "depart-denied", "depart-storage-error", "depart-internal-error", "depart-lost", "depart-timeout",
                "depart-actor", "depart-world", "depart-transition", "depart-malformed-error", "activate-denied",
                "activate-internal-error", "activate-lost", "activate-timeout", "activate-actor", "activate-world", "activate-malformed-error"})
            run(fault, [&] { transition_case(fault); });
        run("forward-room", [&] { transition_case("success", true, false); });
        run("forward-pose", [&] { transition_case("success", false, true); });
        run("forward-room-pose", [&] { transition_case("success", true, true); });
        for (const std::string fault : {"rejection", "lost", "timeout", "malformed-error"})
            run("client-error-" + fault, [&] { client_error_case(fault); });
        for (const std::string fault : {"rejection", "lost", "timeout"})
            run("source-inspection-" + fault, [&] { source_inspection_failure(fault); });
        std::cout << count << " actor transition probe cases passed\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
