#include "identity/issuer_client.hpp"
#include "identity/bootstrap.hpp"
#include "identity/participants.hpp"
#include "identity/keys.hpp"
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
#include <map>
#include <optional>
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
        ((request.has_challenge() || request.has_enrollment_begin() || request.has_authentication_begin() ||
            request.has_authority_authentication_begin() || request.has_authority_launch_begin() || request.has_authority_fence_begin() ||
            request.has_authority_retire_begin()) && reply.has_challenge()) ||
        (request.has_execute() && (reply.has_policy() || reply.has_enrollment() || reply.has_identity_status() || reply.has_profile() ||
            reply.has_participant() || reply.has_registry() || reply.has_authority_barrier() || reply.has_supervision())) ||
        (request.has_authority_launch_complete() && reply.has_authority_launch_authorization()) ||
        (request.has_authority_fence_complete() && reply.has_authority_fence()) ||
        (request.has_authority_retire_complete() && reply.has_authority_retirement()) ||
        (request.has_profile_read() && reply.has_profile()) ||
        (request.has_enrollment_complete() && reply.has_enrollment()) ||
        (request.has_authentication_complete() && reply.has_authentication()) ||
        (request.has_authority_authentication_complete() && reply.has_authority_authentication()) ||
        ((request.has_authority_activate() || request.has_authority_applied() || request.has_authority_release() ||
            request.has_authority_state()) && reply.has_authority_activation()) ||
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
    struct ServiceChallenge {
        v1::SignRequest signing_request;
        v1::AuthorityAuthenticationChallenge context;
        Clock::time_point expires;
    };
    std::map<std::string, ServiceChallenge> service_challenges;
    std::string service_binding, issuer_incarnation, launch_binding;
    bool service_authenticated = false;
    std::optional<v1::AuthorityAuthenticationResult> service_identity;
    std::optional<v1::AuthorityActivationState> own_activation, latest_activation;
    std::uint64_t service_head = 0;
    struct BarrierChallenge {
        v1::SignRequest signing_request;
        v1::IssuerChallenge context;
        Clock::time_point expires;
    };
    std::map<std::string, BarrierChallenge> barrier_challenges;
    std::map<std::string, BarrierChallenge> supervision_challenges;

    struct FenceChallenge {
        v1::SignRequest signing_request;
        v1::AuthorityFenceChallenge context;
        Clock::time_point expires;
    };
    std::map<std::string, FenceChallenge> fence_challenges;
    void remember_fence_challenge(const v1::AuthorityFenceBegin& request, const v1::ChallengeReply& reply, Clock::time_point sent) {
        require(reply.nonce().size() == 32 && reply.has_signing_request() &&
                reply.signing_request().purpose() == v1::AUTHORITY_FENCING && reply.signing_request().has_payload(),
                "Invalid fence challenge envelope");
        FenceChallenge pending; pending.signing_request = reply.signing_request(); auto& value = pending.context;
        require(value.ParseFromString(pending.signing_request.payload()), "Invalid fence challenge payload");
        try { validate_message(value); validate_fence_begin(request); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid fence challenge fields"); }
        require(value.protocol_version() == 1 && value.issuer_id() == config.issuer_id() &&
                value.issuer_incarnation().size() == 32 && value.nonce() == reply.nonce() &&
                value.connection_binding().size() == 32 && value.has_request() &&
                value.request().SerializeAsString() == request.SerializeAsString() && value.supervisor_public_key().size() == 32 &&
                value.supervisor_public_key() != config.issuer_public_key() && value.lifetime_ms() >= 50 && value.lifetime_ms() <= 60000 &&
                value.audience() == "identityd/authority-fence/v1", "Invalid fence challenge context");
        require((issuer_incarnation.empty() || issuer_incarnation == value.issuer_incarnation()) &&
                (launch_binding.empty() || launch_binding == value.connection_binding()) &&
                (service_binding.empty() || service_binding == value.connection_binding()), "Fence challenge changed connection context");
        require(!fence_challenges.contains(reply.nonce()), "Duplicate fence challenge nonce");
        std::erase_if(fence_challenges, [](const auto& item) { return item.second.expires <= Clock::now(); });
        require(fence_challenges.size() < 256, "Too many fence challenges");
        pending.expires = sent + std::chrono::milliseconds(value.lifetime_ms());
        require(Clock::now() < pending.expires, "Fence challenge expired in transit");
        issuer_incarnation = value.issuer_incarnation(); launch_binding = value.connection_binding();
        fence_challenges.emplace(reply.nonce(), std::move(pending));
    }
    void check_fence_result(const v1::AuthorityFenceComplete& request, const v1::AuthorityFenceState& result,
                            const std::optional<FenceChallenge>& pending) {
        require(pending && Clock::now() < pending->expires, "Unrequested or expired fence success");
        const auto& expected = pending->context;
        const auto& target = expected.request();
        try { validate_participant_record(result.registration(), config.issuer_id()); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid fence registration"); }
        require(result.issuer_id() == config.issuer_id() && result.registration().participant().id() == target.participant_id() &&
                result.registration().registered_revision() == target.registered_revision() && result.generation() == target.generation() &&
                result.service_incarnation() == target.service_incarnation() && result.authorization_id() == target.authorization_id() &&
                result.launch_id() == target.launch_id() && result.supervision_revision() == target.supervision_revision() &&
                result.fence_id().size() == 32 && result.security_revision() <= INT64_MAX &&
                result.supervisor_public_key() == expected.supervisor_public_key() &&
                result.supervisor_public_key() != result.registration().participant().public_key(), "Invalid fence result context");
        try {
            v1::VerifyRequest verification; *verification.mutable_content() = pending->signing_request;
            *verification.mutable_proof() = request.proof(); verification.set_expected_public_key(expected.supervisor_public_key());
            require(verify(verification), "Fence accepted an invalid supervisor proof");
        } catch (const std::invalid_argument&) { throw std::runtime_error("Invalid fence proof"); }
    }
    struct RetirementChallenge {
        v1::SignRequest signing_request;
        v1::AuthorityRetireChallenge context;
        Clock::time_point expires;
    };
    std::map<std::string, RetirementChallenge> retirement_challenges;
    void remember_retirement_challenge(const v1::AuthorityRetireBegin& request, const v1::ChallengeReply& reply, Clock::time_point sent) {
        require(reply.nonce().size() == 32 && reply.has_signing_request() &&
            reply.signing_request().purpose() == v1::AUTHORITY_RETIREMENT && reply.signing_request().has_payload(),
            "Invalid retirement challenge envelope");
        RetirementChallenge pending; pending.signing_request = reply.signing_request(); auto& value = pending.context;
        require(value.ParseFromString(pending.signing_request.payload()), "Invalid retirement challenge payload");
        try { validate_message(value); validate_retire_begin(request); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid retirement challenge fields"); }
        require(value.protocol_version() == 1 && value.issuer_id() == config.issuer_id() && value.issuer_incarnation().size() == 32 &&
            value.nonce() == reply.nonce() && value.connection_binding().size() == 32 && value.has_request() &&
            value.request().SerializeAsString() == request.SerializeAsString() && value.supervisor_public_key().size() == 32 &&
            value.supervisor_public_key() != config.issuer_public_key() && value.lifetime_ms() >= 50 && value.lifetime_ms() <= 60000 &&
            value.audience() == "identityd/authority-retirement/v1", "Invalid retirement challenge context");
        require((issuer_incarnation.empty() || issuer_incarnation == value.issuer_incarnation()) &&
            (launch_binding.empty() || launch_binding == value.connection_binding()) &&
            (service_binding.empty() || service_binding == value.connection_binding()), "Retirement challenge changed connection context");
        require(!retirement_challenges.contains(reply.nonce()), "Duplicate retirement challenge nonce");
        std::erase_if(retirement_challenges, [](const auto& item) { return item.second.expires <= Clock::now(); });
        require(retirement_challenges.size() < 256, "Too many retirement challenges");
        pending.expires = sent + std::chrono::milliseconds(value.lifetime_ms());
        require(Clock::now() < pending.expires, "Retirement challenge expired in transit");
        issuer_incarnation = value.issuer_incarnation(); launch_binding = value.connection_binding();
        retirement_challenges.emplace(reply.nonce(), std::move(pending));
    }
    void check_retirement_result(const v1::AuthorityRetireComplete& request, const v1::AuthorityRetirementState& result,
        const std::optional<RetirementChallenge>& pending) {
        require(pending && Clock::now() < pending->expires, "Unrequested or expired retirement success");
        const auto& expected = pending->context;
        try { validate_retirement_state(result, config.issuer_id()); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid retirement result fields"); }
        require(result.request().SerializeAsString() == expected.request().SerializeAsString() &&
            result.supervisor_public_key() == expected.supervisor_public_key(), "Invalid retirement result context");
        try {
            v1::VerifyRequest verification; *verification.mutable_content() = pending->signing_request;
            *verification.mutable_proof() = request.proof(); verification.set_expected_public_key(expected.supervisor_public_key());
            require(verify(verification), "Retirement accepted an invalid supervisor proof");
        } catch (const std::invalid_argument&) { throw std::runtime_error("Invalid retirement proof"); }
    }
    struct LaunchChallenge {
        v1::SignRequest signing_request;
        v1::AuthorityLaunchChallenge context;
        Clock::time_point expires;
    };
    std::map<std::string, LaunchChallenge> launch_challenges;
    void remember_launch_challenge(const v1::AuthorityLaunchBegin& request, const v1::ChallengeReply& reply, Clock::time_point sent) {
        require(reply.nonce().size() == 32 && reply.has_signing_request() &&
                reply.signing_request().purpose() == v1::AUTHORITY_LAUNCH_AUTHORIZATION && reply.signing_request().has_payload(),
                "Invalid launch challenge envelope");
        LaunchChallenge pending; pending.signing_request = reply.signing_request(); auto& value = pending.context;
        require(value.ParseFromString(pending.signing_request.payload()), "Invalid launch challenge payload");
        try { validate_message(value); validate_launch_begin(request, config.issuer_id()); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid launch challenge fields"); }
        require(value.protocol_version() == 1 && value.issuer_id() == config.issuer_id() &&
                value.issuer_incarnation() == request.service().issuer_incarnation() && value.issuer_incarnation().size() == 32 &&
                value.nonce() == reply.nonce() && value.connection_binding().size() == 32 && value.has_request() &&
                value.request().SerializeAsString() == request.SerializeAsString() && value.supervisor_public_key().size() == 32 &&
                value.supervisor_public_key() != request.service().registration().participant().public_key() &&
                value.supervisor_public_key() != config.issuer_public_key() && value.lifetime_ms() >= 50 && value.lifetime_ms() <= 60000 &&
                value.audience() == "identityd/authority-launch/v1", "Invalid launch challenge context");
        require((issuer_incarnation.empty() || issuer_incarnation == value.issuer_incarnation()) &&
                (launch_binding.empty() || launch_binding == value.connection_binding()) &&
                (service_binding.empty() || service_binding == value.connection_binding()), "Launch challenge changed connection context");
        require(!launch_challenges.contains(reply.nonce()), "Duplicate launch challenge nonce");
        std::erase_if(launch_challenges, [](const auto& item) { return item.second.expires <= Clock::now(); });
        require(launch_challenges.size() < 256, "Too many launch challenges");
        pending.expires = sent + std::chrono::milliseconds(value.lifetime_ms());
        require(Clock::now() < pending.expires, "Launch challenge expired in transit");
        issuer_incarnation = value.issuer_incarnation(); launch_binding = value.connection_binding();
        launch_challenges.emplace(reply.nonce(), std::move(pending));
    }
    void check_launch_result(const v1::AuthorityLaunchComplete& request, const v1::AuthorityLaunchAuthorization& result,
                             const std::optional<LaunchChallenge>& pending) {
        require(pending && Clock::now() < pending->expires, "Unrequested or expired launch success");
        const auto& expected = pending->context;
        require(result.has_request() && result.request().SerializeAsString() == expected.request().SerializeAsString() &&
                result.authorization_id().size() == 32 && result.supervisor_public_key() == expected.supervisor_public_key() &&
                result.generation() <= INT64_MAX && (result.generation() == 0 || (result.request().previous_generation() < INT64_MAX &&
                result.generation() == result.request().previous_generation()+1)), "Invalid launch authorization result");
        try {
            v1::VerifyRequest verification; *verification.mutable_content() = pending->signing_request;
            *verification.mutable_proof() = request.proof(); verification.set_expected_public_key(expected.supervisor_public_key());
            require(verify(verification), "Launch authorization accepted an invalid supervisor proof");
        } catch (const std::invalid_argument&) { throw std::runtime_error("Invalid launch authorization proof"); }
    }
    void remember_supervision_challenge(const v1::IssuerOperation& request, const v1::ChallengeReply& reply, Clock::time_point sent) {
        require(reply.nonce().size() == 32 && reply.has_signing_request() &&
                reply.signing_request().purpose() == v1::ISSUER_ADMINISTRATION && reply.signing_request().has_payload(),
                "Invalid supervisor challenge envelope");
        BarrierChallenge pending; pending.signing_request = reply.signing_request(); auto& value = pending.context;
        require(value.ParseFromString(pending.signing_request.payload()), "Invalid supervisor challenge payload");
        try { validate_message(value); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid supervisor challenge fields"); }
        require(value.protocol_version() == 1 && value.issuer_id() == config.issuer_id() && value.incarnation().size() == 32 &&
                value.nonce() == reply.nonce() && value.owner_public_key().size() == 32 && value.has_operation() &&
                value.operation().SerializeAsString() == request.SerializeAsString() && value.lifetime_ms() >= 50 &&
                value.lifetime_ms() <= 60000 && value.audience() == "identityd/admin/v1", "Invalid supervisor challenge context");
        if (request.has_supervisor_bind()) {
            const auto& bind = request.supervisor_bind();
            require(bind.participant_id().size() == 32 && bind.public_key().size() == 32 && bind.registered_revision() > 0 &&
                    bind.registered_revision() <= INT64_MAX && bind.has_expected_revision() && bind.expected_revision() <= INT64_MAX,
                    "Invalid supervisor binding request");
        } else require(request.has_supervisor_read() && request.supervisor_read().participant_id().size() == 32,
                       "Invalid supervisor read request");
        require(issuer_incarnation.empty() || issuer_incarnation == value.incarnation(), "Issuer incarnation changed on one connection");
        require(!supervision_challenges.contains(reply.nonce()), "Duplicate supervisor challenge nonce");
        std::erase_if(supervision_challenges, [](const auto& item) { return item.second.expires <= Clock::now(); });
        require(supervision_challenges.size() < 256, "Too many supervisor challenges");
        pending.expires = sent + std::chrono::milliseconds(value.lifetime_ms());
        require(Clock::now() < pending.expires, "Supervisor challenge expired in transit");
        issuer_incarnation = value.incarnation(); supervision_challenges.emplace(reply.nonce(), std::move(pending));
    }
    void check_supervision_result(const v1::IssuerExecute& request, const v1::AuthoritySupervisorBinding& result,
                                  const std::optional<BarrierChallenge>& pending) {
        require(pending && Clock::now() < pending->expires, "Unrequested or expired supervisor success");
        try {
            v1::VerifyRequest verification; *verification.mutable_content() = pending->signing_request;
            *verification.mutable_proof() = request.proof(); verification.set_expected_public_key(pending->context.owner_public_key());
            require(verify(verification), "Supervisor operation accepted an invalid owner proof");
            validate_participant_record(result.registration(), config.issuer_id());
        } catch (const std::invalid_argument&) { throw std::runtime_error("Invalid supervisor registration or proof"); }
        require(result.public_key().size() == 32 && result.public_key() != config.issuer_public_key() &&
                result.public_key() != pending->context.owner_public_key() &&
                result.public_key() != result.registration().participant().public_key() &&
                result.revision() > 0 && result.revision() <= INT64_MAX, "Invalid supervisor binding identity or revision");
        const auto& operation = pending->context.operation();
        if (operation.has_supervisor_bind()) {
            const auto& bind = operation.supervisor_bind();
            require(result.registration().participant().id() == bind.participant_id() &&
                    result.registration().registered_revision() == bind.registered_revision() && result.public_key() == bind.public_key() &&
                    bind.expected_revision() < INT64_MAX && result.revision() == bind.expected_revision()+1,
                    "Supervisor binding does not match owner request");
        } else require(result.registration().participant().id() == operation.supervisor_read().participant_id(),
                       "Supervisor read returned another participant");
    }

    void remember_barrier_challenge(const v1::IssuerOperation& request, const v1::ChallengeReply& reply, Clock::time_point sent) {
        require(reply.nonce().size() == 32 && reply.has_signing_request() &&
                reply.signing_request().purpose() == v1::ISSUER_ADMINISTRATION && reply.signing_request().has_payload(),
                "Invalid barrier challenge envelope");
        BarrierChallenge pending; pending.signing_request = reply.signing_request(); auto& value = pending.context;
        require(value.ParseFromString(pending.signing_request.payload()), "Invalid barrier challenge payload");
        try { validate_message(value); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid barrier challenge fields"); }
        const auto& read = request.barrier_read();
        require(value.protocol_version() == 1 && value.issuer_id() == config.issuer_id() && value.incarnation().size() == 32 &&
                value.nonce() == reply.nonce() && value.owner_public_key().size() == 32 &&
                value.has_operation() && value.operation().SerializeAsString() == request.SerializeAsString() &&
                value.lifetime_ms() >= 50 && value.lifetime_ms() <= 60000 && value.audience() == "identityd/admin/v1" &&
                read.revision() > 0 && read.revision() <= INT64_MAX && read.limit() <= 16 &&
                (read.after_participant_id().empty() || read.after_participant_id().size() == 32), "Invalid barrier challenge context");
        require(issuer_incarnation.empty() || issuer_incarnation == value.incarnation(), "Issuer incarnation changed on one connection");
        require(!barrier_challenges.contains(reply.nonce()), "Duplicate barrier challenge nonce");
        std::erase_if(barrier_challenges, [](const auto& item) { return item.second.expires <= Clock::now(); });
        require(barrier_challenges.size() < 256, "Too many barrier challenges");
        pending.expires = sent + std::chrono::milliseconds(value.lifetime_ms());
        require(Clock::now() < pending.expires, "Barrier challenge expired in transit");
        issuer_incarnation = value.incarnation(); barrier_challenges.emplace(reply.nonce(), std::move(pending));
    }
    void check_barrier_result(const v1::IssuerExecute& request, const v1::AuthorityBarrierStatus& result,
                              const std::optional<BarrierChallenge>& pending) {
        require(pending && Clock::now() < pending->expires, "Unrequested or expired barrier success");
        const auto& read = pending->context.operation().barrier_read();
        const auto limit = read.limit() ? read.limit() : 16;
        require(result.issuer_id() == config.issuer_id() && result.revision() == read.revision() &&
                result.has_target() && result.target().id().size() == 32 &&
                (result.target().kind() == v1::ACTOR_TARGET || result.target().kind() == v1::CREDENTIAL_TARGET) &&
                result.participant_count() <= INT64_MAX && result.pending_count() <= result.participant_count() &&
                result.complete() == (result.pending_count() == 0) && static_cast<unsigned>(result.obligations_size()) <= limit &&
                static_cast<std::uint64_t>(result.obligations_size()) <= result.participant_count(), "Invalid barrier status context or counts");
        std::string previous = read.after_participant_id(); std::set<std::string> keys;
        std::set<std::uint64_t> registrations; std::set<std::pair<int,std::string>> domains;
        std::uint64_t pending_count = 0;
        try {
            v1::VerifyRequest verification; *verification.mutable_content() = pending->signing_request;
            *verification.mutable_proof() = request.proof(); verification.set_expected_public_key(pending->context.owner_public_key());
            require(verify(verification), "Barrier query accepted an invalid owner proof");
            for (const auto& obligation : result.obligations()) {
                validate_participant_record(obligation.registration(), config.issuer_id());
                const auto& record = obligation.registration(); const auto& participant = record.participant();
                require((previous.empty() || previous < participant.id()) && keys.insert(participant.public_key()).second &&
                        registrations.insert(record.registered_revision()).second && obligation.generation() > 0 &&
                        obligation.generation() <= INT64_MAX && obligation.service_incarnation().size() == 32 &&
                        obligation.applied_revision() <= INT64_MAX &&
                        obligation.complete() == (obligation.applied_revision() >= result.revision() || obligation.has_retirement()),
                        "Invalid barrier obligation");
                if (obligation.has_retirement()) {
                    const auto& retired = obligation.retirement();
                    validate_retirement_state(retired, config.issuer_id());
                    require(retired.registration().SerializeAsString() == record.SerializeAsString() &&
                        retired.request().owner().generation() == obligation.generation() &&
                        retired.request().owner().service_incarnation() == obligation.service_incarnation() &&
                        retired.applied_revision() == obligation.applied_revision() && retired.security_revision() >= result.revision(),
                        "Retirement does not match barrier obligation");
                }
                for (const auto& domain : participant.domains())
                    require(domains.insert(participant_detail::domain_key(domain)).second, "Conflicting barrier domains");
                previous = participant.id(); if (!obligation.complete()) ++pending_count;
            }
        } catch (const std::invalid_argument&) { throw std::runtime_error("Invalid barrier response or proof"); }
        const auto complete_count = static_cast<std::uint64_t>(result.obligations_size())-pending_count;
        require(pending_count <= result.pending_count() && complete_count <= result.participant_count()-result.pending_count(),
                "Barrier page contradicts global counts");
        if (!result.next_after().empty())
            require(static_cast<unsigned>(result.obligations_size()) == limit && result.next_after() == previous &&
                    result.participant_count() > static_cast<std::uint64_t>(result.obligations_size()), "Invalid barrier continuation");
        else if (read.after_participant_id().empty())
            require(static_cast<std::uint64_t>(result.obligations_size()) == result.participant_count() && pending_count == result.pending_count(),
                    "Incomplete barrier result");
    }
    static bool same_generation(const v1::AuthorityActivationState& a, const v1::AuthorityActivationState& b) {
        return a.generation() == b.generation() && a.service_incarnation() == b.service_incarnation() &&
            a.issuer_incarnation() == b.issuer_incarnation() && a.connection_binding() == b.connection_binding() &&
            a.registration().SerializeAsString() == b.registration().SerializeAsString();
    }
    void check_activation_result(const v1::IssuerRequest& request, const v1::AuthorityActivationState& state) {
        require(service_identity.has_value(), "Service activation response without authenticated identity");
        const auto& identity = *service_identity;
        require(state.issuer_id() == config.issuer_id() && state.has_registration() &&
                state.registration().SerializeAsString() == identity.registration().SerializeAsString() &&
                state.generation() > 0 && state.generation() <= INT64_MAX && state.service_incarnation().size() == 32 &&
                state.issuer_incarnation().size() == 32 && state.connection_binding().size() == 32 &&
                state.applied_revision() <= state.head_revision() && state.head_revision() <= INT64_MAX &&
                state.head_revision() >= service_head, "Invalid activation state context");
        const bool ours = state.service_incarnation() == identity.service_incarnation() &&
            state.issuer_incarnation() == identity.issuer_incarnation() && state.connection_binding() == identity.connection_binding();
        if (!request.has_authority_state()) require(ours, "Mutation result belongs to another service connection");
        if (request.has_authority_activate()) {
            require(!own_activation && request.authority_activate().has_synchronized_revision() && state.active() &&
                    state.applied_revision() == request.authority_activate().synchronized_revision() &&
                    state.applied_revision() == state.head_revision() &&
                    (!latest_activation || state.generation() > latest_activation->generation()), "Invalid activation success");
        } else if (request.has_authority_applied()) {
            const auto& applied = request.authority_applied();
            require(own_activation && own_activation->active() && same_generation(*own_activation, state) && state.active() &&
                    applied.generation() == state.generation() && applied.has_from_revision() && applied.has_through_revision() &&
                    applied.from_revision() < INT64_MAX && applied.through_revision() == applied.from_revision()+1 &&
                    state.applied_revision() == applied.through_revision() &&
                    (own_activation->applied_revision() == applied.from_revision() || own_activation->applied_revision() == applied.through_revision()),
                    "Invalid service application success");
        } else if (request.has_authority_release()) {
            const auto& release = request.authority_release();
            require(own_activation && same_generation(*own_activation, state) && !state.active() &&
                    release.generation() == state.generation() && release.has_applied_revision() &&
                    release.applied_revision() == state.applied_revision() && own_activation->applied_revision() == state.applied_revision() &&
                    (!own_activation->active() || state.applied_revision() == state.head_revision()), "Invalid service release success");
        } else if (latest_activation) {
            require(state.generation() >= latest_activation->generation(), "Activation generation rolled back");
            if (state.generation() == latest_activation->generation())
                require(same_generation(*latest_activation,state) && state.applied_revision() >= latest_activation->applied_revision() &&
                        (latest_activation->active() || !state.active()), "Activation state rolled back or changed identity");
        }
        if (ours) {
            if (own_activation) require(same_generation(*own_activation,state) && state.applied_revision() >= own_activation->applied_revision() &&
                    (own_activation->active() || !state.active()), "Own activation changed identity or rolled back");
            own_activation = state;
        }
        if (!latest_activation || state.generation() >= latest_activation->generation()) latest_activation = state;
        service_head = state.head_revision();
    }

    void remember_service_challenge(const v1::AuthorityAuthenticationBegin& request,
                                    const v1::ChallengeReply& reply, Clock::time_point sent) {
        require(!service_authenticated && reply.nonce().size() == 32 && reply.has_signing_request() &&
                reply.signing_request().purpose() == v1::AUTHORITY_PARTICIPATION &&
                reply.signing_request().has_payload(), "Invalid service challenge envelope");
        ServiceChallenge pending;
        pending.signing_request = reply.signing_request();
        auto& value = pending.context;
        require(value.ParseFromString(reply.signing_request().payload()), "Invalid service challenge payload");
        try { validate_message(value); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid service challenge fields"); }
        require(value.protocol_version() == 1 && value.issuer_id() == config.issuer_id() &&
                value.issuer_incarnation().size() == 32 && value.nonce() == reply.nonce() &&
                value.has_request() && request.participant_id().size() == 32 &&
                request.registered_revision() > 0 && request.registered_revision() <= INT64_MAX &&
                request.service_incarnation().size() == 32 &&
                value.request().participant_id() == request.participant_id() &&
                value.request().registered_revision() == request.registered_revision() &&
                value.request().service_incarnation() == request.service_incarnation() &&
                value.connection_binding().size() == 32 && value.lifetime_ms() >= 50 && value.lifetime_ms() <= 60000 &&
                value.audience() == "identityd/authority-participation/v1", "Invalid service challenge context");
        require((launch_binding.empty() || launch_binding == value.connection_binding()) &&
                (service_binding.empty() || service_binding == value.connection_binding()) &&
                (issuer_incarnation.empty() || issuer_incarnation == value.issuer_incarnation()),
                "Service challenge changed connection context");
        require(!service_challenges.contains(reply.nonce()), "Duplicate service challenge nonce");
        std::erase_if(service_challenges, [](const auto& entry) { return entry.second.expires <= Clock::now(); });
        require(service_challenges.size() < 256, "Too many service challenges");
        // Count network time against the advertised lifetime, never extend it on receipt.
        pending.expires = sent + std::chrono::milliseconds(value.lifetime_ms());
        require(Clock::now() < pending.expires, "Service challenge expired in transit");
        service_binding = value.connection_binding(); issuer_incarnation = value.issuer_incarnation();
        service_challenges.emplace(reply.nonce(), std::move(pending));
    }
    void check_service_result(const v1::AuthorityAuthenticationComplete& request,
                              const v1::AuthorityAuthenticationResult& result,
                              const std::optional<ServiceChallenge>& pending) {
        require(!service_authenticated && pending.has_value() && Clock::now() < pending->expires,
                "Unrequested or expired service authentication success");
        const auto& expected = pending->context;
        require(result.has_registration() && result.issuer_id() == config.issuer_id() &&
                result.issuer_incarnation() == expected.issuer_incarnation() &&
                result.connection_binding() == expected.connection_binding() &&
                result.service_incarnation() == expected.request().service_incarnation() &&
                result.security_revision() <= INT64_MAX, "Invalid service authentication context");
        const auto& registration = result.registration();
        try {
            validate_participant_record(registration, config.issuer_id());
            require(registration.participant().id() == expected.request().participant_id() &&
                    registration.registered_revision() == expected.request().registered_revision(),
                    "Substituted service registration");
            v1::VerifyRequest verification;
            *verification.mutable_content() = pending->signing_request;
            *verification.mutable_proof() = request.proof();
            verification.set_expected_public_key(registration.participant().public_key());
            require(verify(verification), "Service authentication accepted an invalid proof");
        } catch (const std::invalid_argument&) { throw std::runtime_error("Invalid service authentication result"); }
        service_authenticated = true;
        service_identity = result; service_head = result.security_revision();
        service_challenges.clear();
    }

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
        std::optional<BarrierChallenge> barrier_pending;
        if (request.has_execute()) {
            auto node = barrier_challenges.extract(request.execute().nonce());
            if (!node.empty()) barrier_pending = std::move(node.mapped());
        }
        std::optional<BarrierChallenge> supervision_pending;
        if (request.has_execute()) {
            // Consume even when the issuer denies the operation.
            auto node = supervision_challenges.extract(request.execute().nonce());
            if (!node.empty()) supervision_pending = std::move(node.mapped());
        }
        std::optional<RetirementChallenge> retirement_pending;
        if (request.has_authority_retire_complete()) {
            auto node = retirement_challenges.extract(request.authority_retire_complete().nonce());
            if (!node.empty()) retirement_pending = std::move(node.mapped());
        }
        std::optional<FenceChallenge> fence_pending;
        if (request.has_authority_fence_complete()) {
            auto node = fence_challenges.extract(request.authority_fence_complete().nonce());
            if (!node.empty()) fence_pending = std::move(node.mapped());
        }
        std::optional<LaunchChallenge> launch_pending;
        if (request.has_authority_launch_complete()) {
            auto node = launch_challenges.extract(request.authority_launch_complete().nonce());
            if (!node.empty()) launch_pending = std::move(node.mapped());
        }
        std::optional<ServiceChallenge> service_pending;
        if (request.has_authority_authentication_complete()) {
            // Consume before I/O. A typed denial must not leave a reusable proof context.
            auto node = service_challenges.extract(request.authority_authentication_complete().nonce());
            if (!node.empty()) service_pending = std::move(node.mapped());
        }
        const auto sent = Clock::now();
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
            if (request.has_challenge() && request.challenge().has_barrier_read())
                remember_barrier_challenge(request.challenge(), reply.challenge(), sent);
            if (request.has_challenge() && (request.challenge().has_supervisor_bind() || request.challenge().has_supervisor_read()))
                remember_supervision_challenge(request.challenge(), reply.challenge(), sent);
            if (supervision_pending) require(reply.has_supervision(), "Supervisor operation returned another outcome");
            if (reply.has_supervision()) check_supervision_result(request.execute(), reply.supervision(), supervision_pending);
            if (barrier_pending) require(reply.has_authority_barrier(), "Barrier query returned another outcome");
            if (reply.has_authority_barrier()) check_barrier_result(request.execute(), reply.authority_barrier(), barrier_pending);
            if (request.has_authority_retire_begin()) remember_retirement_challenge(request.authority_retire_begin(), reply.challenge(), sent);
            if (request.has_authority_retire_complete()) check_retirement_result(request.authority_retire_complete(), reply.authority_retirement(), retirement_pending);
            if (request.has_authority_fence_begin()) remember_fence_challenge(request.authority_fence_begin(), reply.challenge(), sent);
            if (request.has_authority_fence_complete()) check_fence_result(request.authority_fence_complete(), reply.authority_fence(), fence_pending);
            if (request.has_authority_launch_begin()) remember_launch_challenge(request.authority_launch_begin(), reply.challenge(), sent);
            if (request.has_authority_launch_complete()) check_launch_result(request.authority_launch_complete(), reply.authority_launch_authorization(), launch_pending);
            if (reply.has_authority_activation()) check_activation_result(request, reply.authority_activation());
            if (request.has_authority_authentication_begin())
                remember_service_challenge(request.authority_authentication_begin(), reply.challenge(), sent);
            if (request.has_authority_authentication_complete())
                check_service_result(request.authority_authentication_complete(), reply.authority_authentication(), service_pending);
            if (reply.has_hello()) check_hello(reply.hello());
            if (reply.has_info()) require(reply.info().issuer_id() == config.issuer_id() &&
                    reply.info().public_key() == config.issuer_public_key() && reply.info().policy_revision() > 0,
                    "Issuer identity does not match configured pins");
            if (reply.has_revocations()) check_revocations(request.revocations(), reply.revocations(), config.issuer_id());
            if (reply.has_actor_status()) check_actor_status(request.actor_status(), reply.actor_status(), config.issuer_id());
            try {
                if (reply.has_participant()) validate_participant_record(reply.participant(), config.issuer_id());
                if (reply.has_registry()) validate_registry_page(reply.registry(), config.issuer_id());
            } catch (const std::invalid_argument&) { throw std::runtime_error("Invalid authority participant response"); }
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
