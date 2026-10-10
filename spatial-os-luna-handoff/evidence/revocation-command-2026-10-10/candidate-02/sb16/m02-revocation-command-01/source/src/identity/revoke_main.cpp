#include "identity/issuer_client.hpp"
#include "identity/keys.hpp"
#include "core/protocol.hpp"
#include <algorithm>
#include <chrono>
#include <csignal>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <sys/stat.h>

namespace {
namespace pb = spatial::identity::v1;
namespace wire = spatial::wire;
using Clock = std::chrono::steady_clock;
struct RemoteError { wire::Error value; };
struct WaitExpired {};
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
void check_target(const pb::IdentityReference& target) {
    if (target.id().size() != 32 || (target.kind() != pb::ACTOR_TARGET && target.kind() != pb::CREDENTIAL_TARGET))
        throw std::invalid_argument("A 32-byte actor or credential target is required");
}
pb::RevocationCommand input() {
    std::string bytes;
    if (!spatial::read_frame(std::cin, bytes) || std::cin.peek() != std::char_traits<char>::eof())
        throw std::invalid_argument("Expected one complete protobuf frame and EOF");
    pb::RevocationCommand command;
    if (!command.ParseFromString(bytes)) throw std::invalid_argument("Malformed revocation command");
    spatial::validate_message(command);
    if (command.protocol_version() != 1 || (!command.has_revoke() && !command.has_resume()))
        throw std::invalid_argument("Expected revocation command version 1 and one operation");
    if (command.has_wait_ms() && (command.wait_ms() < 50 || command.wait_ms() > 60000))
        throw std::invalid_argument("Completion wait must be between 50 and 60000 milliseconds");
    if (command.has_revoke()) {
        check_target(command.revoke().target());
        if (!command.revoke().has_expected_security_revision() || command.revoke().expected_security_revision() >= INT64_MAX)
            throw std::invalid_argument("A valid expected security revision is required");
    } else {
        check_target(command.resume().target());
        if (!command.resume().revision() || command.resume().revision() > INT64_MAX)
            throw std::invalid_argument("Resume requires the original positive revision");
    }
    return command;
}
pb::IssuerReply call(spatial::identity::IssuerClient& client, pb::IssuerRequest request) {
    request.set_protocol_version(1);
    auto reply = client.call(std::move(request));
    if (reply.has_error()) throw RemoteError{reply.error()};
    return reply;
}
class Owner {
public:
    Owner(spatial::identity::IssuerClient& client, const spatial::identity::KeyPair& key)
        : client_(client), key_(key) {
        pb::IssuerRequest request; request.mutable_info();
        auto reply = call(client_, std::move(request));
        require(reply.has_info(), "Issuer identity response is missing");
        issuer_ = reply.info().issuer_id();
    }
    pb::IssuerRequest prepare(const pb::IssuerOperation& operation) {
        pb::IssuerRequest request; *request.mutable_challenge() = operation;
        const auto started = Clock::now();
        const auto reply = call(client_, std::move(request));
        require(reply.has_challenge(), "Administration challenge is missing");
        const auto& challenge = reply.challenge();
        pb::IssuerChallenge context;
        require(challenge.nonce().size() == 32 && challenge.has_signing_request() &&
            challenge.signing_request().purpose() == pb::ISSUER_ADMINISTRATION &&
            context.ParseFromString(challenge.signing_request().payload()), "Invalid administration challenge envelope");
        spatial::validate_message(context);
        require(context.protocol_version() == 1 && context.issuer_id() == issuer_ && context.incarnation().size() == 32 &&
            (incarnation_.empty() || context.incarnation() == incarnation_) && context.nonce() == challenge.nonce() &&
            context.owner_public_key() == key_.public_key() && context.operation().SerializeAsString() == operation.SerializeAsString() &&
            context.lifetime_ms() >= 50 && context.lifetime_ms() <= 60000 && context.audience() == "identityd/admin/v1" &&
            Clock::now() < started + std::chrono::milliseconds(context.lifetime_ms()), "Administration challenge context mismatch");
        incarnation_ = context.incarnation();
        pb::IssuerRequest execute;
        execute.mutable_execute()->set_nonce(challenge.nonce());
        *execute.mutable_execute()->mutable_proof() = key_.sign(challenge.signing_request());
        return execute;
    }
    pb::IssuerReply execute(const pb::IssuerOperation& operation) { return call(client_, prepare(operation)); }
    void check_identity(const pb::IdentityStatus& status, const pb::IdentityReference& target) const {
        const bool actor = target.kind() == pb::ACTOR_TARGET;
        require(status.issuer_id() == issuer_ && status.actor_id().size() == 32 &&
            (status.kind() == pb::HUMAN || status.kind() == pb::AGENT) && status.security_revision() <= INT64_MAX &&
            status.active() == (!status.actor_revoked() && !status.credential_revoked()) &&
            (actor ? status.actor_id() == target.id() && status.credential_id().empty() && !status.credential_revoked()
                   : status.credential_id() == target.id()), "Identity response context mismatch");
    }
private:
    spatial::identity::IssuerClient& client_;
    const spatial::identity::KeyPair& key_;
    std::string issuer_, incarnation_;
};
bool definitely_rejected(wire::ErrorCode code) {
    return code == wire::INVALID_ARGUMENT || code == wire::UNSUPPORTED_VERSION || code == wire::UNKNOWN_METHOD ||
        code == wire::UNAUTHORIZED || code == wire::FORBIDDEN || code == wire::NOT_FOUND || code == wire::REVISION_CONFLICT;
}
void run(const pb::RevocationCommand& command, const char* config, const char* key_path, pb::RevocationResult& result) {
    *result.mutable_target() = command.has_revoke() ? command.revoke().target() : command.resume().target();
    if (command.has_resume()) {
        result.set_revision(command.resume().revision()); result.set_state(pb::REVOCATION_COMMIT_UNCERTAIN);
    }
    auto key = spatial::identity::KeyPair::load(key_path);
    spatial::identity::IssuerClient client(config); Owner owner(client, key);
    if (command.has_revoke()) {
        pb::IssuerOperation operation; *operation.mutable_identity_read() = result.target();
        const auto before = owner.execute(operation);
        require(before.has_identity_status(), "Identity status response is missing");
        owner.check_identity(before.identity_status(), result.target());
        if (before.identity_status().security_revision() != command.revoke().expected_security_revision()) {
            wire::Error error; error.set_code(wire::REVISION_CONFLICT); error.set_message("Issuer security revision changed");
            throw RemoteError{error};
        }
        const bool revoked = result.target().kind() == pb::ACTOR_TARGET ? before.identity_status().actor_revoked()
                                                                      : before.identity_status().credential_revoked();
        if (revoked) throw std::invalid_argument("Target is already revoked; resume with its original revision");
        operation.Clear(); *operation.mutable_identity_revoke() = command.revoke();
        auto request = owner.prepare(operation);
        result.set_revision(command.revoke().expected_security_revision() + 1);
        result.set_state(pb::REVOCATION_COMMIT_UNCERTAIN);
        pb::IssuerReply committed;
        try { committed = call(client, std::move(request)); }
        catch (const RemoteError& error) {
            if (definitely_rejected(error.value.code())) {
                result.set_state(pb::REVOCATION_REJECTED); result.clear_revision();
            }
            throw;
        }
        require(committed.has_identity_status(), "Revocation commit response is missing");
        const auto& status = committed.identity_status(); owner.check_identity(status, result.target());
        require(status.security_revision() == result.revision() && !status.active() &&
            (result.target().kind() == pb::ACTOR_TARGET ? status.actor_revoked() : status.credential_revoked()),
            "Revocation commit response does not confirm the requested mutation");
        result.set_state(pb::REVOCATION_COMMITTED);
    }
    const auto deadline = Clock::now() + std::chrono::milliseconds(command.has_wait_ms() ? command.wait_ms() : 5000);
    client.set_tick([deadline] { if (Clock::now() >= deadline) throw WaitExpired{}; });
    for (;;) {
        pb::IssuerOperation operation; operation.mutable_barrier_read()->set_revision(result.revision());
        auto reply = owner.execute(operation);
        require(reply.has_authority_barrier() && reply.authority_barrier().target().SerializeAsString() == result.target().SerializeAsString(),
            "Cancellation barrier does not match the requested target");
        *result.mutable_barrier() = reply.authority_barrier(); result.set_state(pb::REVOCATION_COMMITTED);
        if (result.barrier().complete()) { result.set_state(pb::REVOCATION_COMPLETE); return; }
        std::this_thread::sleep_until(std::min(deadline, Clock::now() + std::chrono::milliseconds(50)));
    }
}
}

int main(int argc, char** argv) {
    umask(0077); signal(SIGPIPE, SIG_IGN);
    pb::RevocationResult result; result.set_protocol_version(1); result.set_state(pb::REVOCATION_NOT_SUBMITTED);
    try {
        if (argc != 5 || std::string_view(argv[1]) != "--config" || std::string_view(argv[3]) != "--key")
            throw std::invalid_argument("Usage: spatial-revoke --config ABSOLUTE_FILE --key ABSOLUTE_FILE");
        run(input(), argv[2], argv[4], result);
    } catch (const RemoteError& error) { *result.mutable_error() = error.value;
    } catch (const WaitExpired&) {
        result.mutable_error()->set_code(wire::TIMEOUT);
        result.mutable_error()->set_message("Cancellation completion is unresolved at the wait deadline");
    } catch (const std::invalid_argument& error) {
        result.mutable_error()->set_code(wire::INVALID_ARGUMENT); result.mutable_error()->set_message(error.what());
    } catch (const std::exception&) {
        result.mutable_error()->set_code(wire::UNAVAILABLE);
        result.mutable_error()->set_message("Revocation transport, key access, or response validation failed; no automatic replay");
    }
    if (result.state() == pb::REVOCATION_NOT_SUBMITTED && result.has_error()) result.set_state(pb::REVOCATION_REJECTED);
    try { spatial::write_frame(std::cout, result); }
    catch (const std::exception&) { std::cerr << "spatial-revoke: output transport failed\n"; return 2; }
    return result.state() == pb::REVOCATION_COMPLETE ? 0 : 1;
}
