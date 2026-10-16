#include "world/identity_sessions.hpp"
#include "world/geometry_view_validation.hpp"
#include "world/diagnostic_timing.hpp"
#include "world/authority.hpp"
#include "world/actor_tls.hpp"
#include "world/queued_copy_status.hpp"
#include "platform/broker.hpp"
#include "identity/issuer_client.hpp"
#include "identity/participant_runtime.hpp"
#include "identity/keys.hpp"
#include "identity/bootstrap.hpp"
#include "identity/tls_support.hpp"
#include "world_session.pb.h"
#include <google/protobuf/text_format.h>
#include <openssl/rand.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <iostream>
#include <cstring>
#include <unistd.h>
#include <source_location>
#include <sstream>
#include <string_view>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>

namespace spatial {
namespace identity {
void participant_diagnostic(std::string_view, std::string_view, std::string_view,
                            std::uint64_t, std::uint64_t, std::uint64_t, bool);
}
namespace {
namespace pb = identity::v1;
using Clock = std::chrono::steady_clock;
struct CancellationFailure : std::runtime_error { using std::runtime_error::runtime_error; };
struct SupersededAuthorityRead : WorldError {
    SupersededAuthorityRead() : WorldError(wire::UNAVAILABLE, "Authority read was superseded by a newer security revision") {}
};
void require(bool condition, const char* message) { if (!condition) throw std::invalid_argument(message); }
std::string random_binding() {
    std::string bytes(32, '\0');
    if (RAND_bytes(reinterpret_cast<unsigned char*>(bytes.data()), bytes.size()) != 1)
        throw std::runtime_error("Cannot obtain world authentication entropy");
    return bytes;
}
std::string hex(const std::string& bytes) {
    constexpr char digits[] = "0123456789abcdef"; std::string result;
    for (const unsigned char byte : bytes) { result += digits[byte >> 4]; result += digits[byte & 15]; }
    return result;
}
struct Pending {
    pb::WorldAuthenticationBegin begin;
    pb::ActorCredential claims;
    std::string nonce, binding;
    Clock::time_point expires;
};
std::uint64_t next_channel_diagnostic_id = 0;
struct Channel {
    identity::tls_detail::Fd fd;
    std::unique_ptr<ActorTlsPeer> tls;
    std::uint64_t diagnostic_id = 0;
    std::string input, output, token;
    std::string view_id;
    std::uint64_t view_sequence = 0;
    std::size_t sent = 0;
    bool needs_observation = false;
    bool needs_geometry_observation = false;
    std::uint64_t cancellation_generation = 0;
    std::uint64_t active_request_id = 0, output_request_id = 0;
    bool in_dispatch = false, output_copy_status_request = false;
    bool output_revocation_denial = false;
    bool output_transport_write_attempted = false;
    Clock::time_point input_started = Clock::now(), output_started = Clock::now();
    std::optional<Pending> pending, prepared;
    std::optional<pb::ActorCredential> completing;
    std::optional<pb::WorldSessionReply> departure;
    pb::SignedCredential credential;
    pb::ActorCredential claims;
    explicit Channel(int descriptor) : fd(descriptor), diagnostic_id(++next_channel_diagnostic_id) {}
    void close_transport() { tls.reset(); fd.reset(); }
    int transport_read() {
        const bool empty = input.empty();
        const auto result = tls->read(input);
        if (empty && !input.empty()) input_started = Clock::now();
        return result;
    }
};
unsigned lifecycle_diagnostic_count = 0, reply_queued_diagnostic_count = 0;
unsigned close_diagnostic_count = 0;
void lifecycle_diagnostic(const Channel& channel, const char* reason) {
    if (lifecycle_diagnostic_count >= 256) return;
    const bool reply_queued = std::strcmp(reason, "reply_queued") == 0;
    if (reply_queued && reply_queued_diagnostic_count >= 128) return;
    if (!reply_queued && close_diagnostic_count >= 128) return;
    ++lifecycle_diagnostic_count;
    if (reply_queued) ++reply_queued_diagnostic_count;
    else ++close_diagnostic_count;
    const auto monotonic_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
        Clock::now().time_since_epoch()).count();
    std::cerr << "worldd: actor_lifecycle pid=" << getpid()
              << " monotonic_ns=" << monotonic_ns << " channel_id=" << channel.diagnostic_id
              << " reason=" << reason
              << " sent=" << channel.sent << " output_size=" << channel.output.size()
              << " cancellation_generation=" << channel.cancellation_generation
              << " active_request_id=" << channel.active_request_id
              << " output_request_id=" << channel.output_request_id
              << " copy_status_request=" << channel.output_copy_status_request
              << " revocation_denial=" << channel.output_revocation_denial
              << " transport_write_attempted=" << channel.output_transport_write_attempted
              << " authenticated=" << !channel.token.empty()
              << " in_dispatch=" << channel.in_dispatch
              << " tls=" << static_cast<bool>(channel.tls)
              << " tls_ready=" << (channel.tls && channel.tls->ready()) << '\n';
}
}

void prepare_actor_channel(int fd) {
    require(fd > STDERR_FILENO, "Actor channel must be an inherited descriptor above stderr");
    sockaddr_un local{}, peer{}; socklen_t local_size = sizeof(local), peer_size = sizeof(peer);
    int type = 0; socklen_t type_size = sizeof(type);
    ucred credentials{}; socklen_t credential_size = sizeof(credentials);
    require(getsockname(fd, reinterpret_cast<sockaddr*>(&local), &local_size) == 0 && local.sun_family == AF_UNIX &&
            local_size == sizeof(sa_family_t) && getpeername(fd, reinterpret_cast<sockaddr*>(&peer), &peer_size) == 0 &&
            peer.sun_family == AF_UNIX && peer_size == sizeof(sa_family_t) &&
            getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &type_size) == 0 && type == SOCK_STREAM &&
            getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credentials, &credential_size) == 0 &&
            credential_size == sizeof(credentials) && credentials.uid == geteuid(),
            "Actor channel must be a private unnamed local stream from the current supervisor user");
    require(fcntl(fd, F_SETFD, FD_CLOEXEC) == 0, "Cannot protect inherited actor channel");
    identity::tls_detail::nonblocking(fd);
}

struct IdentitySessions::Impl {
    WorldStore& world;
    Actors& actors;
    BrokerClient* broker;
    pb::WorldIdentityConfig config;
    std::unique_ptr<identity::IssuerClient> client;
    std::unique_ptr<identity::ParticipantRuntime> participant;
    std::optional<std::uint64_t> consumed_revision;
    std::uint64_t generation = 0, acknowledged_revision = 0, command_sequence = 0;
    pb::ParticipantCommand::OperationCase pending_operation = pb::ParticipantCommand::OPERATION_NOT_SET;
    bool closing = false, released = false;
    pb::IssuerInfo issuer;
    std::unique_ptr<WorldAuthority> authority;
    std::uint64_t observed_revision = 0;
    std::unique_ptr<ActorTlsListener> listener;
    std::vector<std::unique_ptr<Channel>> channels;
    std::set<std::string> admitted;
    std::map<std::string, std::set<std::string>> grants;
    std::string audience;
    Clock::time_point last_exchange = Clock::now();
    world_diagnostic::LastSuccessfulExchange last_successful_exchange;
    bool ready = false, broken = false, ticking = false;
    bool issuer_exchange_in_progress = false;
    bool diagnostic_service_failure_logged = false;
    std::optional<std::pair<std::uint64_t, bool>> diagnostic_cancellation_state;

    Impl(const std::filesystem::path& path, const std::vector<int>& descriptors, WorldStore& store, Actors& engine, BrokerClient* execution, int listener_fd, const std::filesystem::path& tls_config)
        : world(store), actors(engine), broker(execution) {
        require((!descriptors.empty() || listener_fd >= 0) && descriptors.size() <= 32, "World requires actor transport");
        if (listener_fd >= 0) {
            listener = std::make_unique<ActorTlsListener>(listener_fd, tls_config);
            require(descriptors.size() <= listener->limit(), "Local channels exceed the total actor connection limit");
        }
        require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(path), &config),
                "Invalid world identity configuration");
        validate_message(config);
        require(config.protocol_version() == 1 && (config.admission() == pb::REGISTERED_ACTORS ||
                config.admission() == pb::EXPLICIT_ACTORS), "World admission policy is required");
        require(config.authority_directory().empty() || (config.actor_grants().empty() && config.admitted_actor_ids().empty()),
                "Persistent authority cannot combine with static actor profiles or admission lists");
        for (const auto& id : config.admitted_actor_ids())
            require(id.size() == 32 && admitted.insert(id).second, "Invalid or duplicate admitted actor");
        const std::set<std::string> supported{"navigate", "interact", "observe", "world.write", "git.write", "process.launch"};
        for (const auto& item : config.actor_grants()) {
            require(item.actor_id().size() == 32 && !grants.contains(item.actor_id()), "Invalid or duplicate actor grant profile");
            auto& selected = grants[item.actor_id()];
            for (const auto& grant : item.grants())
                require(supported.contains(grant) && selected.insert(grant).second, "Invalid or duplicate actor grant");
        }
        for (const auto fd : descriptors) { prepare_actor_channel(fd); channels.push_back(std::make_unique<Channel>(fd)); }
        audience = "spatial.world-session/v1:"+world.info().world_id();
        client = std::make_unique<identity::IssuerClient>(config.issuer_client_config());
        pb::IssuerRequest request; request.set_protocol_version(1); request.mutable_info();
        const auto info = client->call(request);
        require(info.has_info(), "World issuer identity is unavailable"); issuer = info.info();
        if (!config.authority_directory().empty())
            authority = std::make_unique<WorldAuthority>(config.authority_directory(), world.info().world_id(), issuer.issuer_id());
        require(std::filesystem::path(config.participant_runtime_config()).is_absolute(), "World authority participant configuration is required");
        spatial::authority::v1::Domain domain; domain.set_kind(spatial::authority::v1::WORLD); domain.set_id(world.info().world_id());
        participant = std::make_unique<identity::ParticipantRuntime>(config.participant_runtime_config(),
            std::vector<spatial::authority::v1::Domain>{domain});
        const auto deadline = Clock::now()+std::max(client->timeout()*2, std::chrono::milliseconds(2000));
        while (!ready && !broken && Clock::now() < deadline) {
            tick(); actors.flush_input(); settle(); std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        require(ready && !broken, "World authority participation is unavailable");
    }
    void disconnect(Channel& channel, const char* reason = "unspecified") {
        lifecycle_diagnostic(channel, reason);
        ++channel.cancellation_generation;
        const auto token = std::move(channel.token); channel.token.clear();
        channel.credential.Clear(); channel.claims.Clear(); channel.pending.reset(); channel.prepared.reset(); channel.completing.reset();
        channel.needs_observation = false; channel.needs_geometry_observation = false; channel.view_id.clear();
        // Swap queued bytes out before cancellation callbacks can re-enter.
        // The pump sees empty output until Actors::close returns; deferred cancellation settles later.
        std::string pending_output;
        pending_output.swap(channel.output);
        const bool had_output = !pending_output.empty();
        const bool queued_copy_status = channel.output_copy_status_request;
        const bool queued_denial = channel.output_revocation_denial;
        const bool write_attempted = channel.output_transport_write_attempted;
        const auto queued_sent = channel.sent;
        const auto queued_request_id = channel.output_request_id;
        if (had_output) channel.sent = 0;

        if (!token.empty()) {
            try { actors.close(token); }
            catch (...) {
                lifecycle_diagnostic(channel, "cancellation_failure");
                channel.output_request_id = 0;
                channel.output_copy_status_request = false;
                channel.output_revocation_denial = false;
                channel.output_transport_write_attempted = false;
                channel.close_transport();
                throw CancellationFailure("Cannot confirm native actor cancellation");
            }
        }

        if (channel.fd.value < 0 || channel.departure || !channel.output.empty()) {
            // A nested cancellation may have closed the transport or queued newer output.
            // Never restore this stale frame over that state.
            return;
        }

        if (had_output) {
            const bool authority_invalidation =
                std::strcmp(reason, "credential_revocation") == 0 ||
                std::strcmp(reason, "authority_denied") == 0;
            try {
                const auto disposition = authority_invalidation
                    ? world_detail::replace_queued_copy_status_frame(pending_output,
                        queued_copy_status, queued_request_id, queued_sent,
                        write_attempted, queued_denial, channel.output_started)
                    : world_detail::QueuedReplyRevocationAction::CloseTransport;
                if (disposition == world_detail::QueuedReplyRevocationAction::ReplaceWithDenial) {
                    channel.output = std::move(pending_output);
                    channel.output_request_id = queued_request_id;
                    channel.output_copy_status_request = false;
                    channel.output_revocation_denial = true;
                    channel.output_transport_write_attempted = false;
                    channel.sent = 0;
                    lifecycle_diagnostic(channel, "disconnect_replace_copy_status");
                } else if (disposition == world_detail::QueuedReplyRevocationAction::PreserveDenial) {
                    channel.output = std::move(pending_output);
                    channel.output_request_id = queued_request_id;
                    channel.output_copy_status_request = false;
                    channel.output_revocation_denial = true;
                    channel.output_transport_write_attempted = write_attempted;
                    channel.sent = queued_sent;
                    lifecycle_diagnostic(channel, "disconnect_preserve_copy_status_denial");
                } else {
                    lifecycle_diagnostic(channel, "disconnect_drop_output");
                    channel.output_request_id = 0;
                    channel.output_copy_status_request = false;
                    channel.output_revocation_denial = false;
                    channel.output_transport_write_attempted = false;
                    channel.close_transport();
                }
            } catch (...) {
                // Optional denial construction must never undo cancellation or leak old bytes.
                channel.output.clear();
                lifecycle_diagnostic(channel, "disconnect_copy_status_replacement_failed");
                channel.output_request_id = 0;
                channel.output_copy_status_request = false;
                channel.output_revocation_denial = false;
                channel.output_transport_write_attempted = false;
                channel.close_transport();
            }
        }
    }
    void fail(const std::source_location location = std::source_location::current()) {
        if (!broken) std::cerr << "worldd: identity service disabled at identity_sessions.cpp:" << location.line() << '\n';
        broken = true;
        for (auto& channel : channels) disconnect(*channel, "identity_failure");
    }
    void poll_authority() {
        if (ticking || broken || !participant) return;
        ticking = true;
        struct Reset { bool& value; ~Reset() { value = false; } } reset{ticking};
        // Detect controller loss during component waits without dispatching new actions.
        for (auto& channel : channels) {
            if (channel->fd.value < 0) continue;
            pollfd fd{channel->fd.value, POLLIN, 0};
            const int result = poll(&fd, 1, 0);
            if (result < 0 && errno == EINTR) continue;
            bool lost = result < 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL));
            if (!lost && !channel->tls && (fd.revents & POLLIN)) {
                char byte;
                const auto count = recv(channel->fd.value, &byte, 1, MSG_PEEK | MSG_DONTWAIT);
                lost = !count || (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR);
            }
            if (!lost && channel->tls) {
                lost = channel->tls->expired(!channel->token.empty());
                if (lost) lifecycle_diagnostic(*channel, "tls_expired_poll");
                if (!lost && channel->tls->ready() && channel->output.empty()) {
                    lost = channel->transport_read() < 0;
                    if (lost) lifecycle_diagnostic(*channel, "tls_read_lost_poll");
                }
            }
            if (lost) {
                disconnect(*channel, "poll_transport_lost"); channel->close_transport(); channel->input.clear(); channel->output.clear();
            }
        }
        if (participant->failed()) {
            if (!diagnostic_service_failure_logged) {
                identity::participant_diagnostic("world", "participant_failed", participant->service_incarnation(),
                    observed_revision, consumed_revision.value_or(0), acknowledged_revision, true);
                diagnostic_service_failure_logged = true;
            }
            fail(); return;
        }
        while (auto update = participant->poll()) {
            if (update->has_authenticated()) {
                if (update->authenticated().issuer_id() != issuer.issuer_id() ||
                        update->authenticated().service_incarnation() != participant->service_incarnation()) { fail(); return; }
                continue;
            }
            if (!update->has_revocations()) {
                if (pending_operation == pb::ParticipantCommand::OPERATION_NOT_SET || update->request_id() != command_sequence) { fail(); return; }
                const auto expected = pending_operation; pending_operation = pb::ParticipantCommand::OPERATION_NOT_SET;
                if (update->has_error()) {
                    if (expected == pb::ParticipantCommand::kActivate && update->error().code() == wire::REVISION_CONFLICT) continue;
                    fail(); return;
                }
                if (expected == pb::ParticipantCommand::kActivate && update->has_activated()) {
                    if (update->activated().service_incarnation() != participant->service_incarnation()) { fail(); return; }
                    generation = update->activated().generation(); acknowledged_revision = update->activated().applied_revision(); ready = true;
                } else if (expected == pb::ParticipantCommand::kApplied && update->has_applied()) {
                    acknowledged_revision = update->applied().applied_revision();
                    identity::participant_diagnostic("world", "ack_result_consumed", participant->service_incarnation(),
                        acknowledged_revision, consumed_revision.value_or(0), acknowledged_revision, false);
                } else if (expected == pb::ParticipantCommand::kRelease && update->has_released()) released = true;
                else { fail(); return; }
                continue;
            }
            const auto& batch = update->revocations();
            if (batch.issuer_id() != issuer.issuer_id()) { fail(); return; }
            observed_revision = std::max(observed_revision, batch.head_revision());
            consumed_revision = batch.through_revision();
            if (batch.initial()) continue;
            if (batch.events_size()) {
                identity::participant_diagnostic("world", "revocation_received", participant->service_incarnation(),
                    batch.events(batch.events_size() - 1).revision(), batch.through_revision(),
                    acknowledged_revision, false);
            }
            for (const auto& event : batch.events()) {
                if (authority && event.target().kind() == pb::ACTOR_TARGET) authority->revoked(event.target().id());
                const auto affected = [&](const pb::ActorCredential& claims) {
                    return event.target().id() == (event.target().kind() == pb::ACTOR_TARGET ? claims.actor_id() : claims.credential_id());
                };
                for (auto& channel : channels) {
                    if ((!channel->token.empty() && affected(channel->claims)) ||
                            (channel->pending && affected(channel->pending->claims)) ||
                            (channel->prepared && affected(channel->prepared->claims)) ||
                            (channel->completing && affected(*channel->completing))) disconnect(*channel, "credential_revocation");
                }
            }
            sync_authority();
        }
    }
    void tick() {
        if (ticking) return;
        poll_authority();
        // Keep the action issuer connection alive during component waits too.
        // The polling guard has ended, so this call's progress callbacks still
        // detect controller loss and consume revocations while INFO is pending.
        if (!broken && participant && !issuer_exchange_in_progress &&
                Clock::now()-last_exchange >= std::chrono::seconds(1)) {
            pb::IssuerRequest request; request.mutable_info();
            try { exchange(std::move(request)); }
            catch (const WorldError&) { fail(); }
        }
    }
    void settle() {
        if(actors.cancellation_settled())for(auto& owned:channels){
            auto& channel=*owned;
            if(!channel.departure)continue;
            auto reply=std::move(*channel.departure);channel.departure.reset();
            try {
                const auto saved=actors.checkpoint_closed(reply.departed().actor_id());
                reply.mutable_departed()->set_world_revision(saved.world_revision());
            }catch(const std::exception&){
                reply.clear_outcome();reply.mutable_error()->set_code(wire::STORAGE_ERROR);
                reply.mutable_error()->set_message("Actor departed but context save was not confirmed");
            }
            if(channel.fd.value<0)continue;
            std::ostringstream output;write_frame(output,reply);channel.output=output.str();channel.sent=0;
            channel.output_copy_status_request = false;
            channel.output_revocation_denial = false;
            channel.output_transport_write_attempted = false;
            channel.output_started=Clock::now();
        }
        // Never called by an issuer, renderer, or broker wait callback.
        const bool cancellation_settled = actors.cancellation_settled();
        const auto target_revision = consumed_revision.value_or(0);
        const auto cancellation_state = std::make_pair(target_revision, cancellation_settled);
        if (!diagnostic_cancellation_state || *diagnostic_cancellation_state != cancellation_state) {
            const std::string diagnostic_incarnation = participant ? participant->service_incarnation() : std::string{};
            identity::participant_diagnostic("world",
                cancellation_settled ? "cancellation_settled" : "cancellation_pending",
                diagnostic_incarnation, target_revision, target_revision, acknowledged_revision, false);
            diagnostic_cancellation_state = cancellation_state;
        }
        if (broken || released || !consumed_revision || pending_operation != pb::ParticipantCommand::OPERATION_NOT_SET ||
                !cancellation_settled) return;
        pb::ParticipantCommand command;
        if (!ready) {
            if (*consumed_revision < observed_revision) return;
            command.mutable_activate()->set_synchronized_revision(*consumed_revision);
        } else if (acknowledged_revision < *consumed_revision) {
            auto* applied = command.mutable_applied(); applied->set_generation(generation);
            applied->set_from_revision(acknowledged_revision); applied->set_through_revision(acknowledged_revision+1);
        } else if (closing && *consumed_revision >= observed_revision) {
            command.mutable_release()->set_generation(generation);
            command.mutable_release()->set_applied_revision(acknowledged_revision);
        } else return;
        require(command_sequence < UINT64_MAX, "World participant sequence exhausted");
        command.set_request_id(++command_sequence); pending_operation = command.operation_case();
        const bool acknowledgement = command.has_applied();
        const auto target = acknowledgement ? command.applied().through_revision() : *consumed_revision;
        participant->submit(std::move(command));
        if (acknowledgement) identity::participant_diagnostic("world", "ack_queued",
            participant->service_incarnation(), target, *consumed_revision, acknowledged_revision, false);
    }
    void shutdown() {
        closing = true;
        for (auto& channel : channels) { disconnect(*channel, "world_shutdown"); channel->close_transport(); }
        const auto deadline = Clock::now()+std::max(client->timeout()*2, std::chrono::milliseconds(2000));
        while (!released && !broken && Clock::now() < deadline) {
            tick(); actors.flush_input(); settle(); std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!released) throw CancellationFailure("World participant release remains unresolved");
    }
    pb::IssuerReply exchange(pb::IssuerRequest request) {
        if (broken) throw WorldError(wire::UNAVAILABLE, "World identity service is disabled");
        if (issuer_exchange_in_progress) throw std::logic_error("Recursive world issuer exchange is not allowed");
        issuer_exchange_in_progress = true;
        struct ClearExchange { bool& active; ~ClearExchange() { active = false; } } clear{issuer_exchange_in_progress};
        request.set_protocol_version(1);
        const auto operation = request.has_info() ? "info" :
            request.has_credential_check() ? "credential_check" :
            request.has_actor_status() ? "actor_status" : "other";
        const auto since_last_success = last_successful_exchange.elapsed_ms(Clock::now());
        pb::IssuerReply reply;
        try { reply = client->call(std::move(request)); }
        catch (const CancellationFailure&) {
            std::cerr << world_diagnostic::issuer_failure_line(operation, since_last_success)
                << " " << client->failure_diagnostic() << '\n';
            throw;
        }
        catch (const std::exception&) {
            std::cerr << world_diagnostic::issuer_failure_line(operation, since_last_success)
                << " " << client->failure_diagnostic() << '\n';
            fail(); throw WorldError(wire::UNAVAILABLE, "World issuer connection failed");
        }
        if (broken) throw WorldError(wire::UNAVAILABLE, "World identity service is disabled");
        last_exchange = Clock::now();
        last_successful_exchange.record_success(last_exchange);
        if (reply.has_error()) {
            const auto code = reply.error().code();
            if (code == wire::STORAGE_ERROR || code == wire::INTERNAL_ERROR || code == wire::UNAVAILABLE) fail();
            throw WorldError(code, "Issuer rejected world authentication operation");
        }
        return reply;
    }
    void identity_matches(const pb::IdentityStatus& status, const pb::ActorCredential& claims, std::uint64_t request_revision) {
        if (status.issuer_id() != issuer.issuer_id() || status.actor_id() != claims.actor_id() ||
                status.credential_id() != claims.credential_id() || status.kind() != claims.kind() || status.security_revision() > INT64_MAX ||
                status.security_revision() < request_revision) {
            fail(); throw WorldError(wire::UNAVAILABLE, "Issuer returned inconsistent identity context");
        }
        if (status.security_revision() < observed_revision) throw SupersededAuthorityRead();
        observed_revision = std::max(observed_revision, status.security_revision());
    }
    pb::IdentityStatus current(pb::SignedCredential credential, pb::ActorCredential claims) {
        pb::IssuerRequest request; *request.mutable_credential_check() = credential;
        const auto request_revision = observed_revision;
        const auto status = exchange(std::move(request)).identity_status(); identity_matches(status, claims, request_revision);
        if (!status.active() || status.actor_revoked() || status.credential_revoked())
            throw WorldError(wire::UNAUTHORIZED, "Actor credential is revoked");
        return status;
    }
    pb::ActorCredential credential_claims(const pb::SignedCredential& credential) {
        pb::VerifyRequest verification; verification.set_expected_public_key(issuer.public_key());
        *verification.mutable_content() = credential.content(); *verification.mutable_proof() = credential.proof();
        pb::ActorCredential claims;
        require(credential.content().purpose() == pb::ISSUER_CREDENTIAL && identity::verify(verification) &&
                claims.ParseFromString(credential.content().payload()), "Invalid world credential signature");
        validate_message(claims);
        require(claims.protocol_version() == 1 && claims.issuer_id() == issuer.issuer_id() &&
                claims.actor_id().size() == 32 && claims.credential_id().size() == 32 && claims.public_key().size() == 32 &&
                (claims.kind() == pb::HUMAN || claims.kind() == pb::AGENT), "Invalid world credential identity");
        return claims;
    }
    authority::v1::ActorRef principal(const pb::ActorCredential& claims) const {
        authority::v1::ActorRef result; result.set_issuer_id(claims.issuer_id()); result.set_actor_id(claims.actor_id()); return result;
    }
    void sync_authority() {
        if (!authority) return;
        for (auto& channel : channels) {
            if (channel->token.empty()) continue;
            const auto actor = principal(channel->claims);
            if ((config.admission() == pb::EXPLICIT_ACTORS && !authority->allowed(actor, "enter")) ||
                    ((channel->needs_observation || channel->needs_geometry_observation) &&
                     !authority->allowed(actor, "observe"))) disconnect(*channel, "authority_denied");
        }
        actors.refresh_authority(); actors.flush_input();
    }
    void prepare(const authority::v1::ActorRef& actor, const authority::v1::ActorRef* extra = nullptr) {
        if (!authority) return;
        auto subjects = authority->subjects(actor);
        if (extra && extra->issuer_id().size() == 32 && extra->actor_id().size() == 32) subjects.push_back(*extra);
        prepare_subjects(actor, subjects);
    }
    void prepare_subjects(const authority::v1::ActorRef& actor,
                          const std::vector<authority::v1::ActorRef>& subjects) {
        if (!authority) return;
        std::set<std::string> ids;
        for (const auto& subject : subjects)
            if (subject.issuer_id() == issuer.issuer_id()) ids.insert(subject.actor_id());
        std::vector<pb::ActorState> states;
        std::optional<std::uint64_t> revision;
        // Each request is bounded by the checked client's I/O deadline.
        // All batches must describe one revision before any new permission is used.
        auto next = ids.begin();
        while (next != ids.end()) {
            pb::IssuerRequest request; auto* query = request.mutable_actor_status();
            while (next != ids.end() && query->actor_ids_size() < 128) query->add_actor_ids(*next++);
            const auto request_revision = observed_revision;
            const auto batch = exchange(std::move(request)).actor_status();
            if (batch.security_revision() < request_revision) {
                fail(); throw WorldError(wire::UNAVAILABLE, "Issuer returned an actor snapshot older than the requested context");
            }
            if ((revision && *revision != batch.security_revision()) || batch.security_revision() < observed_revision)
                throw SupersededAuthorityRead();
            revision = batch.security_revision();
            states.insert(states.end(), batch.actors().begin(), batch.actors().end());
        }
        if (revision) observed_revision = std::max(observed_revision, *revision);
        authority->update(states); sync_authority();
        const auto current_actor = std::find_if(states.begin(), states.end(), [&](const auto& value) { return value.actor_id() == actor.actor_id(); });
        if (current_actor == states.end() || !current_actor->active()) throw WorldError(wire::UNAUTHORIZED, "Actor identity is inactive");
    }
    pb::BootstrapProfileState bootstrap_profile(const pb::ActorCredential& claims) {
        pb::BootstrapProfileRead query; query.set_id(claims.bootstrap_profile());
        query.set_revision(claims.bootstrap_profile_revision());
        identity::validate_bootstrap_read(query, true);
        pb::IssuerRequest request; *request.mutable_profile_read() = query;
        return exchange(std::move(request)).profile();
    }
    pb::BootstrapGrantTemplate bootstrap_template(const authority::v1::BootstrapSelector& selector) {
        require(selector.issuer_id().size() == 32, "Bootstrap issuer identity is required");
        if (selector.issuer_id() != issuer.issuer_id()) throw WorldError(wire::FORBIDDEN, "Bootstrap issuer is not configured");
        require(!selector.template_id().empty() && selector.template_id().size() <= 128 &&
            selector.template_id().find('\0') == std::string::npos, "Bootstrap template identity is invalid");
        pb::ActorCredential claims; claims.set_bootstrap_profile(selector.profile_id());
        claims.set_bootstrap_profile_revision(selector.profile_revision());
        const auto profile = bootstrap_profile(claims);
        for (const auto& item : profile.profile().grants()) if (item.id() == selector.template_id()) return item;
        throw WorldError(wire::NOT_FOUND, "Bootstrap template is unavailable");
    }
    void bootstrap_live(Channel& channel, bool completing) {
        tick();
        if (broken) throw WorldError(wire::UNAVAILABLE, "World identity service is disabled");
        if (channel.fd.value < 0 || (completing ? !channel.completing : channel.token.empty()))
            throw WorldError(wire::UNAUTHORIZED, "Bootstrap actor channel was revoked or disconnected");
    }
    void bootstrap_ready(Channel& channel, const pb::SignedCredential& credential,
                         const pb::ActorCredential& claims, const std::vector<authority::v1::ActorRef>& subjects,
                         bool completing) {
        const auto status = current(credential, claims);
        prepare_subjects(principal(claims), subjects);
        bootstrap_live(channel, completing);
        if (observed_revision != status.security_revision())
            throw WorldError(wire::UNAVAILABLE, "Issuer security state changed during bootstrap preparation");
    }
    authority::v1::BootstrapReport apply_world_bootstrap(Channel& channel, pb::SignedCredential credential,
                                                       pb::ActorCredential claims, bool completing) {
        authority::v1::BootstrapReport report;
        report.set_issuer_id(claims.issuer_id()); report.set_profile_id(claims.bootstrap_profile());
        report.set_profile_revision(claims.bootstrap_profile_revision());
        report.mutable_domain()->set_kind(authority::v1::WORLD); report.mutable_domain()->set_id(world.info().world_id());
        // The issuer's built-in revision is immutable and contains no templates.
        if (claims.bootstrap_profile() == "empty-v1") {
            require(claims.bootstrap_profile_revision() == 1, "Invalid immutable empty profile revision");
            bootstrap_live(channel, completing); return report;
        }
        if (!authority) {
            report.mutable_error()->set_code(wire::UNAVAILABLE);
            report.mutable_error()->set_message("Persistent world authority is not configured");
            bootstrap_live(channel, completing); return report;
        }
        pb::BootstrapProfileState profile;
        try { profile = bootstrap_profile(claims); }
        catch (const WorldError& error) {
            if (error.code != wire::NOT_FOUND) throw;
            report.mutable_error()->set_code(error.code); report.mutable_error()->set_message(error.what());
            bootstrap_live(channel, completing); return report;
        }
        const auto actor = principal(claims);
        bootstrap_ready(channel, credential, claims, authority->subjects(actor), completing);
        for (const auto& item : profile.profile().grants()) {
            if (item.terms().scope().domain().kind() != authority::v1::WORLD ||
                item.terms().scope().domain().id() != world.info().world_id()) continue;
            auto* result = report.add_results(); result->set_template_id(item.id());
            authority::v1::BootstrapSelector selector; selector.set_issuer_id(claims.issuer_id());
            selector.set_profile_id(claims.bootstrap_profile()); selector.set_profile_revision(claims.bootstrap_profile_revision());
            selector.set_template_id(item.id());
            try {
                const auto binding = authority->bootstrap_binding(selector);
                bootstrap_ready(channel, credential, claims, authority->bootstrap_subjects({binding.id()}, actor), completing);
                *result->mutable_grant() = authority->materialize_bootstrap(binding.id(), actor, selector, item.terms());
            } catch (const WorldError& error) {
                // Authentication or issuer failure aborts this request. Earlier receipts remain durable.
                if (error.code == wire::UNAUTHORIZED || error.code == wire::UNAVAILABLE) throw;
                result->mutable_error()->set_code(error.code); result->mutable_error()->set_message(error.what());
            }
        }
        sync_authority(); bootstrap_live(channel, completing); return report;
    }
    pb::WorldSessionOpened activate(Channel& channel,const Pending& pending,const pb::IdentityStatus& status) {
        const auto actor=principal(pending.claims);
        wire::ActorOpenRequest open;open.set_id("identity:"+hex(issuer.issuer_id())+":"+hex(status.actor_id()));
        if(pending.begin.has_room())open.set_room(pending.begin.room());
        if(pending.begin.has_pose())*open.mutable_pose()=pending.begin.pose();
        const auto allowed=grants.find(status.actor_id());
        if(allowed!=grants.end())for(const auto& grant:allowed->second)open.add_grants(grant);
        Actors::Authority check;
        if(authority)check=[this,actor](const auto& right,const auto& scope){return authority->allowed(actor,right,scope);};
        const auto opened=actors.open(open,true,std::move(check),actor,!channel.tls);
        channel.token=opened.token();channel.credential=pending.begin.credential();channel.claims=pending.claims;
        channel.view_id=random_binding(); channel.view_sequence=0;
        pb::WorldSessionOpened result;result.set_actor_id(opened.id());result.set_world_id(world.info().world_id());
        *result.mutable_identity()=status;
        result.set_geometry_view_id(channel.view_id);
        result.set_authority_service_incarnation(participant->service_incarnation());
        result.set_authority_generation(generation);
        return result;
    }
    pb::WorldSessionReply dispatch(Channel& channel, const std::string& bytes,
            bool& self_revocation_receipt, bool& copy_status_request) {
        copy_status_request = false;
        pb::WorldSessionReply reply; reply.set_protocol_version(1);
        try {
            pb::WorldSessionRequest request;
            require(request.ParseFromString(bytes), "Malformed world session protobuf"); reply.set_request_id(request.request_id());
            channel.active_request_id = request.request_id(); channel.in_dispatch = true;
            struct DispatchTraceReset {
                Channel& channel;
                bool& copy_status_request;
                const pb::WorldSessionRequest& request;
                bool authenticated;
                bool valid_protocol_request = false;
                ~DispatchTraceReset() {
                    copy_status_request = valid_protocol_request &&
                        world_detail::authenticated_workspace_copy_status_request(request, authenticated);
                    channel.in_dispatch = false;
                    channel.active_request_id = 0;
                }
            } trace_reset{channel, copy_status_request, request, !channel.token.empty()};
            validate_message(request);
            if (request.protocol_version() != 1) throw WorldError(wire::UNSUPPORTED_VERSION, "Unsupported world session protocol");
            trace_reset.valid_protocol_request = true;
            if (request.has_close()) { disconnect(channel, "client_close"); reply.mutable_closed()->set_closed(true); return reply; }
            if (broken || !ready || closing) throw WorldError(wire::UNAVAILABLE, "World identity service is disabled");
            if(request.has_depart()) {
                require(request.depart().has_seq() && request.depart().transition_id().size()==32,"Invalid departure sequence or transition identity");
                if(channel.token.empty())throw WorldError(wire::UNAUTHORIZED,"Channel has no authenticated actor");
                current(channel.credential,channel.claims);
                if(channel.token.empty())throw WorldError(wire::UNAUTHORIZED,"World session was revoked");
                actors.consume_sequence(channel.token,request.depart().seq());
                const auto id=actors.depart(channel.token);
                channel.token.clear();disconnect(channel, "actor_departure");
                auto* departed=reply.mutable_departed();departed->set_transition_id(request.depart().transition_id());
                departed->set_world_id(world.info().world_id());departed->set_actor_id(id);
                channel.departure=reply;return reply;
            }
            if(request.has_activate()) {
                require(request.activate().transition_id().size()==32,"Invalid activation transition identity");
                if(!channel.prepared || channel.prepared->expires<=Clock::now() ||
                    channel.prepared->begin.transition_id()!=request.activate().transition_id())
                    throw WorldError(wire::UNAUTHORIZED,"No matching prepared destination");
                auto prepared=std::move(*channel.prepared);channel.prepared.reset();channel.completing=prepared.claims;
                struct Clear {Channel& channel;~Clear(){channel.completing.reset();}} clear{channel};
                const auto status=current(prepared.begin.credential(),prepared.claims);
                const auto actor=principal(prepared.claims);prepare(actor);
                if(!channel.completing)throw WorldError(wire::UNAUTHORIZED,"Prepared destination was revoked");
                if(config.admission()==pb::EXPLICIT_ACTORS &&
                    !(authority?authority->allowed(actor,"enter"):admitted.contains(status.actor_id())))
                    throw WorldError(wire::FORBIDDEN,"Actor is no longer admitted to this world");
                *reply.mutable_opened()=activate(channel,prepared,status);return reply;
            }
            if (request.has_begin()) {
                require(request.begin().transition_id().empty() || request.begin().transition_id().size()==32,"Invalid preparation transition identity");
                if(channel.prepared && channel.prepared->expires>Clock::now())
                    throw WorldError(wire::IN_USE,"Channel already has a prepared destination");
                channel.prepared.reset();
                if (!channel.token.empty()) throw WorldError(wire::IN_USE, "Channel already has an actor");
                if (channel.pending && Clock::now() < channel.pending->expires)
                    throw WorldError(wire::IN_USE, "Channel already has an authentication challenge");
                channel.pending.reset();
                Pending pending; pending.begin = request.begin(); pending.claims = credential_claims(pending.begin.credential());
                current(pending.begin.credential(), pending.claims);
                pending.binding = random_binding();
                pb::IssuerRequest check; auto* begin = check.mutable_authentication_begin();
                *begin->mutable_credential() = pending.begin.credential(); begin->set_audience(audience); begin->set_channel_binding(pending.binding);
                const auto expected = *begin;
                const auto challenge = exchange(std::move(check)).challenge();
                pb::AuthenticationChallenge content;
                if (challenge.nonce().size() != 32 || challenge.signing_request().purpose() != pb::ACTOR_AUTHENTICATION ||
                        !content.ParseFromString(challenge.signing_request().payload())) {
                    fail(); throw WorldError(wire::UNAVAILABLE, "Invalid issuer authentication challenge");
                }
                try { validate_message(content); }
                catch (const std::invalid_argument&) { fail(); throw WorldError(wire::UNAVAILABLE, "Invalid issuer challenge fields"); }
                if (content.protocol_version() != 1 || content.issuer_id() != issuer.issuer_id() || content.incarnation().size() != 32 ||
                        content.nonce() != challenge.nonce() || content.request().SerializeAsString() != expected.SerializeAsString() ||
                        content.lifetime_ms() < 50 || content.lifetime_ms() > 60000) {
                    fail(); throw WorldError(wire::UNAVAILABLE, "Issuer challenge changed authentication context");
                }
                pending.nonce = challenge.nonce(); pending.expires = Clock::now()+std::chrono::milliseconds(content.lifetime_ms());
                channel.pending = std::move(pending); *reply.mutable_challenge() = challenge;
            } else if (request.has_geometry_view()) {
                if (channel.token.empty() || channel.view_id.size() != 32)
                    throw WorldError(wire::UNAUTHORIZED, "Channel has no active actor session");
                const auto status = current(channel.credential, channel.claims);
                const auto actor = principal(channel.claims);
                if ((config.admission() == pb::EXPLICIT_ACTORS &&
                     !(authority ? authority->allowed(actor, "enter") : admitted.contains(status.actor_id()))) ||
                    (authority ? !authority->allowed(actor, "observe") : !grants[status.actor_id()].contains("observe")))
                    throw WorldError(wire::FORBIDDEN, "Actor lacks active world observation authority");
                if (channel.view_sequence == UINT64_MAX) throw WorldError(wire::STALE_SEQUENCE, "Geometry view sequence exhausted");
                const auto geometry_started = Clock::now();
                std::uint64_t construction_ms = 0;
                try {
                    *reply.mutable_geometry_view() = actors.geometry_view(channel.token, world.info().world_id(),
                        "identity:"+hex(issuer.issuer_id())+":"+hex(status.actor_id()), channel.view_id,
                        generation, ++channel.view_sequence);
                    construction_ms = world_diagnostic::bounded_ms(Clock::now() - geometry_started);
                } catch (...) {
                    construction_ms = world_diagnostic::bounded_ms(Clock::now() - geometry_started);
                    std::cerr << world_diagnostic::geometry_failure_line("construction", construction_ms, std::nullopt) << '\n';
                    throw;
                }
                reply.mutable_geometry_view()->set_authority_service_incarnation(participant->service_incarnation());
                const auto validation_started = Clock::now();
                try { validate_geometry_view(reply.geometry_view()); }
                catch (...) {
                    const auto validation_ms = world_diagnostic::bounded_ms(Clock::now() - validation_started);
                    std::cerr << world_diagnostic::geometry_failure_line("validation", construction_ms, validation_ms) << '\n';
                    throw;
                }
                channel.needs_geometry_observation = true;
            } else if (request.has_complete()) {
                auto pending = std::move(channel.pending); channel.pending.reset();
                if (!pending || pending->expires <= Clock::now() || pending->nonce != request.complete().nonce())
                    throw WorldError(wire::UNAUTHORIZED, "No matching world authentication challenge");
                // Keep the in-flight credential visible to revocation while issuer I/O yields.
                channel.completing = pending->claims;
                struct ClearCompletion {
                    Channel& channel;
                    ~ClearCompletion() { channel.completing.reset(); }
                } completion{channel};
                pb::IssuerRequest complete; *complete.mutable_authentication_complete() = request.complete();
                const auto request_revision = observed_revision;
                const auto result = exchange(std::move(complete)).authentication();
                identity_matches(result.identity(), pending->claims, request_revision);
                if (result.audience() != audience || result.channel_binding() != pending->binding ||
                        !result.identity().active() || result.identity().actor_revoked() || result.identity().credential_revoked()) {
                    fail(); throw WorldError(wire::UNAVAILABLE, "Issuer authentication context mismatch");
                }
                const auto status = current(pending->begin.credential(), pending->claims);
                const auto actor = principal(pending->claims);
                *reply.mutable_bootstrap_attempt() = apply_world_bootstrap(channel, pending->begin.credential(), pending->claims, true);
                prepare(actor);
                if (!channel.completing) throw WorldError(wire::UNAUTHORIZED, "World authentication was revoked");
                if (config.admission() == pb::EXPLICIT_ACTORS &&
                        !(authority ? authority->allowed(actor, "enter") : admitted.contains(status.actor_id())))
                    throw WorldError(wire::FORBIDDEN, "Actor is not admitted to this world");
                if(!pending->begin.transition_id().empty()) {
                    pending->expires=Clock::now()+std::chrono::seconds(60);
                    auto* value=reply.mutable_prepared();value->set_transition_id(pending->begin.transition_id());
                    value->mutable_actor()->set_actor_id("identity:"+hex(issuer.issuer_id())+":"+hex(status.actor_id()));
                    value->mutable_actor()->set_world_id(world.info().world_id());*value->mutable_actor()->mutable_identity()=status;
                    channel.prepared=std::move(*pending);
                }else *reply.mutable_opened()=activate(channel,*pending,status);
            } else if (request.has_inspect() || request.has_action() || request.has_grants() || request.has_delegate() || request.has_revoke() ||
                       request.has_bootstrap_bind() || request.has_bootstrap_revoke() || request.has_bootstrap_apply()) {
                if (channel.token.empty()) throw WorldError(wire::UNAUTHORIZED, "Channel has no authenticated actor");
                const auto actor = principal(channel.claims);
                try {
                    current(channel.credential, channel.claims);
                } catch (const SupersededAuthorityRead&) { throw;
                } catch (const WorldError&) { disconnect(channel, "issuer_or_authority_denied"); throw; }
                if (channel.token.empty()) throw WorldError(wire::UNAUTHORIZED, "World session was revoked");
                if (request.has_bootstrap_bind()) {
                    require(request.bootstrap_bind().has_seq(), "Actor sequence is required");
                    actors.consume_sequence(channel.token, request.bootstrap_bind().seq());
                } else if (request.has_bootstrap_revoke()) {
                    require(request.bootstrap_revoke().has_seq(), "Actor sequence is required");
                    actors.consume_sequence(channel.token, request.bootstrap_revoke().seq());
                } else if (request.has_bootstrap_apply()) {
                    require(request.bootstrap_apply().has_seq(), "Actor sequence is required");
                    actors.consume_sequence(channel.token, request.bootstrap_apply().seq());
                } else if (request.has_delegate()) {
                    require(request.delegate().has_seq(), "Actor sequence is required");
                    actors.consume_sequence(channel.token, request.delegate().seq());
                } else if (request.has_revoke()) {
                    require(request.revoke().has_seq(), "Actor sequence is required");
                    actors.consume_sequence(channel.token, request.revoke().seq());
                }
                try {
                    prepare(actor, request.has_delegate() && !request.delegate().has_workspace() ? &request.delegate().terms().subject() : nullptr);
                } catch (const SupersededAuthorityRead&) { throw;
                } catch (const WorldError&) { disconnect(channel, "issuer_or_authority_denied"); throw; }
                if (channel.token.empty()) throw WorldError(wire::UNAUTHORIZED, "World session was revoked");
                if (request.has_bootstrap_bind() || request.has_bootstrap_revoke() || request.has_bootstrap_apply()) {
                    const bool workspace = request.has_bootstrap_bind() ? request.bootstrap_bind().has_workspace() :
                        request.has_bootstrap_revoke() ? request.bootstrap_revoke().has_workspace() : request.bootstrap_apply().has_workspace();
                    if (workspace) {
                        if (!broker) throw WorldError(wire::UNAVAILABLE, "No workspace authority is connected");
                        const auto selected = request.has_bootstrap_bind() ? request.bootstrap_bind().workspace() :
                            request.has_bootstrap_revoke() ? request.bootstrap_revoke().workspace() : request.bootstrap_apply().workspace();
                        require(!selected.empty() && selected.size() <= 256 && selected.find('\0') == std::string::npos,
                            "Explicit workspace identity is required");
                        if (request.has_bootstrap_bind()) {
                            const auto& source = request.bootstrap_bind();
                            require(source.has_expected_revision(), "Authority revision is required");
                            broker_wire::BootstrapBindRequest operation; operation.set_workspace(selected);
                            *operation.mutable_principal() = actor; *operation.mutable_credential() = channel.credential;
                            *operation.mutable_selector() = source.selector(); operation.set_parent_id(source.parent_id());
                            operation.set_expected_revision(source.expected_revision());
                            *reply.mutable_binding() = broker->bind_bootstrap(operation);
                        } else if (request.has_bootstrap_revoke()) {
                            const auto& source = request.bootstrap_revoke();
                            require(source.has_expected_revision(), "Authority revision is required");
                            broker_wire::BootstrapRevokeRequest operation; operation.set_workspace(selected);
                            *operation.mutable_principal() = actor; *operation.mutable_credential() = channel.credential;
                            operation.set_id(source.id()); operation.set_expected_revision(source.expected_revision());
                            *reply.mutable_binding() = broker->revoke_bootstrap(operation);
                        } else {
                            broker_wire::BootstrapApplyRequest operation; operation.set_workspace(selected);
                            *operation.mutable_principal() = actor; *operation.mutable_credential() = channel.credential;
                            *reply.mutable_bootstrap() = broker->apply_bootstrap(operation);
                        }
                        bootstrap_live(channel, false); return reply;
                    }
                    if (!authority) throw WorldError(wire::UNIMPLEMENTED, "Persistent world authority is not configured");
                    // Copies remain valid if a participant callback clears the channel during issuer I/O.
                    const auto credential = channel.credential; const auto claims = channel.claims;
                    if (request.has_bootstrap_bind()) {
                        const auto& operation = request.bootstrap_bind();
                        require(operation.has_expected_revision(), "Authority revision is required");
                        const auto item = bootstrap_template(operation.selector());
                        bootstrap_ready(channel, credential, claims, authority->subjects(actor), false);
                        *reply.mutable_binding() = authority->bind_bootstrap(actor, operation.selector(), operation.parent_id(),
                            item.terms(), operation.expected_revision());
                    } else if (request.has_bootstrap_revoke()) {
                        const auto& operation = request.bootstrap_revoke();
                        require(operation.has_expected_revision(), "Authority revision is required");
                        bootstrap_ready(channel, credential, claims, authority->subjects(actor), false);
                        *reply.mutable_binding() = authority->revoke_bootstrap(actor, operation.id(), operation.expected_revision());
                    } else *reply.mutable_bootstrap() = apply_world_bootstrap(channel, credential, claims, false);
                    sync_authority();
                } else if (request.has_inspect()) *reply.mutable_state() = actors.inspect(channel.token);
                else if (request.has_grants() || request.has_delegate() || request.has_revoke()) {
                    const bool workspace = request.has_grants() ? request.grants().has_workspace() :
                        request.has_delegate() ? request.delegate().has_workspace() : request.revoke().has_workspace();
                    if (workspace) {
                        if (!broker) throw WorldError(wire::UNAVAILABLE, "No workspace authority is connected");
                        const auto selected = request.has_grants() ? request.grants().workspace() :
                            request.has_delegate() ? request.delegate().workspace() : request.revoke().workspace();
                        require(!selected.empty() && selected.size() <= 256 && selected.find('\0') == std::string::npos,
                                "Explicit workspace identity is required");
                        if (request.has_grants()) {
                            broker_wire::GrantListRequest operation; operation.set_workspace(selected); *operation.mutable_principal() = actor;
                            operation.set_after_id(request.grants().after_id()); operation.set_limit(request.grants().limit());
                            *reply.mutable_grants() = broker->grants(operation);
                        } else if (request.has_delegate()) {
                            require(request.delegate().has_expected_revision(), "Authority revision is required");
                            broker_wire::GrantDelegateRequest operation; operation.set_workspace(selected); *operation.mutable_principal() = actor;
                            operation.set_parent_id(request.delegate().parent_id()); *operation.mutable_terms() = request.delegate().terms();
                            operation.set_expected_revision(request.delegate().expected_revision()); *reply.mutable_grant() = broker->delegate(operation);
                        } else {
                            require(request.revoke().has_expected_revision(), "Authority revision is required");
                            broker_wire::GrantRevokeRequest operation; operation.set_workspace(selected); *operation.mutable_principal() = actor;
                            operation.set_id(request.revoke().id()); operation.set_expected_revision(request.revoke().expected_revision());
                            *reply.mutable_grant() = broker->revoke(operation);
                        }
                        return reply;
                    }
                    if (!authority) throw WorldError(wire::UNIMPLEMENTED, "Persistent world authority is not configured");
                    if (request.has_grants()) {
                        *reply.mutable_grants() = authority->list(actor, request.grants().after_id(), request.grants().limit());
                    } else if (request.has_delegate()) {
                        const auto& operation = request.delegate();
                        require(operation.has_seq() && operation.has_expected_revision(), "Sequence and authority revision are required");
                        *reply.mutable_grant() = authority->delegate(actor, operation.parent_id(), operation.terms(), operation.expected_revision());
                        sync_authority();
                    } else {
                        const auto& operation = request.revoke();
                        require(operation.has_seq() && operation.has_expected_revision(), "Sequence and authority revision are required");
                        *reply.mutable_grant() = authority->revoke(actor, operation.id(), operation.expected_revision());
                        // This local commit can remove the caller's own admission or observation.
                        // Its receipt confirms that completed mutation; it grants no continuing access.
                        self_revocation_receipt = (config.admission() == pb::EXPLICIT_ACTORS && !authority->allowed(actor, "enter")) ||
                            ((channel.needs_observation || channel.needs_geometry_observation) && !authority->allowed(actor, "observe"));
                        sync_authority();
                    }
                } else {
                    auto action = request.action(); require(action.token().empty(), "Actor channel cannot supply a session token");
                    action.set_token(channel.token); *reply.mutable_action() = actors.act(action);
                    channel.needs_observation = action.has_observe() || action.has_aim();
                }
            } else throw WorldError(wire::UNKNOWN_METHOD, "No recognized world session operation");
        } catch (const CancellationFailure&) { throw;
        } catch (const WorldError& error) {
            reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
        } catch (const std::invalid_argument& error) {
            reply.mutable_error()->set_code(wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what());
        } catch (const std::exception&) {
            reply.mutable_error()->set_code(wire::INTERNAL_ERROR); reply.mutable_error()->set_message("World session operation failed");
        }
        return reply;
    }
    void pump() {
        // Preserve maintenance cadence even when pump is used without serve's tick.
        tick();
        for (auto& owned : channels) {
            auto& channel = *owned; if (channel.fd.value < 0) continue;
            auto drop = [&](const char* reason) {
                disconnect(channel, reason); channel.close_transport(); channel.input.clear(); channel.output.clear();
            };
            const auto now = Clock::now();
            const auto timeout = channel.tls ? channel.tls->timeout() : std::chrono::milliseconds(10000);
            if ((!channel.input.empty() && now-channel.input_started > timeout) ||
                    (!channel.output.empty() && now-channel.output_started > timeout)) { drop("io_timeout"); continue; }
            if (channel.tls) {
                if (channel.tls->expired(!channel.token.empty())) { drop("tls_expired_pump"); continue; }
                const auto handshake = channel.tls->handshake();
                if (handshake < 0) { drop("tls_handshake_failed"); continue; }
                if (!handshake) continue;
                if (channel.tls->read_needs_write()) {
                    if (channel.transport_read() < 0) { drop("tls_read_failed"); continue; }
                    if (channel.tls->read_needs_write()) continue;
                }
            }
            pollfd fd{channel.fd.value, static_cast<short>(channel.output.empty() ? POLLIN : POLLOUT), 0};
            const int count = poll(&fd, 1, 0);
            if (count < 0 && errno == EINTR) continue;
            if (count < 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL))) { drop("poll_error_or_hangup"); continue; }
            if(channel.departure)continue;
            if (!channel.output.empty() && (channel.tls || (fd.revents & POLLOUT))) {
                if (authority && (channel.needs_observation || channel.needs_geometry_observation) &&
                        !authority->allowed(principal(channel.claims), "observe")) { drop("observation_authority_revoked"); continue; }
                if (channel.needs_observation) {
                    try { if (!actors.application_observation_valid(channel.token)) { drop("application_observation_invalid"); continue; } }
                    catch (const WorldError&) { drop("application_observation_error"); continue; }
                }
                if (channel.tls) {
                    channel.output_transport_write_attempted = true;
                    if (channel.tls->write(channel.output, channel.sent) < 0) { drop("tls_write_failed"); continue; }
                } else {
                    channel.output_transport_write_attempted = true;
                    const auto written = write(channel.fd.value, channel.output.data()+channel.sent, channel.output.size()-channel.sent);
                    if (written < 0 && errno != EAGAIN && errno != EINTR) { drop("socket_write_failed"); continue; }
                    if (written > 0) channel.sent += static_cast<std::size_t>(written);
                }
                if (channel.sent == channel.output.size()) {
                    channel.output.clear(); channel.output_request_id = 0;
                    channel.needs_observation = false; channel.needs_geometry_observation = false;
                    channel.output_copy_status_request = false;
                    channel.output_revocation_denial = false;
                    channel.output_transport_write_attempted = false;
                }
            }
            if (channel.tls && channel.output.empty()) {
                if (channel.transport_read() < 0) { drop("tls_read_failed"); continue; }
                if (channel.tls->read_needs_write()) continue;
            }
            if (!channel.tls && channel.output.empty() && (fd.revents & POLLIN)) {
                std::array<char, 65536> bytes{};
                const auto received = read(channel.fd.value, bytes.data(), std::min(bytes.size(), max_frame_bytes+4-channel.input.size()));
                if (!received) { drop("peer_eof"); continue; }
                if (received < 0 && errno != EAGAIN && errno != EINTR) { drop("socket_read_failed"); continue; }
                if (received > 0) {
                    if (channel.input.empty()) channel.input_started = now;
                    channel.input.append(bytes.data(), static_cast<std::size_t>(received));
                }
            }
            if (!channel.output.empty() || channel.input.size() < 4) continue;
            const auto* h = reinterpret_cast<const unsigned char*>(channel.input.data());
            const auto length = (std::uint32_t(h[0]) << 24) | (std::uint32_t(h[1]) << 16) | (std::uint32_t(h[2]) << 8) | h[3];
            if (length > max_frame_bytes) { drop("oversize_frame"); continue; }
            if (channel.input.size() < length+4) continue;
            const auto bytes = channel.input.substr(4, length); channel.input.erase(0, length+4); channel.input_started = now;
            const auto cancellation = channel.cancellation_generation;
            bool self_revocation_receipt = false;
            bool copy_status_request = false;
            pb::WorldSessionReply reply;
            try { reply = dispatch(channel, bytes, self_revocation_receipt, copy_status_request); }
            catch (const CancellationFailure&) {
                lifecycle_diagnostic(channel, "cancellation_failure_leaving_dispatch");
                throw;
            }
            if (channel.fd.value < 0 || channel.departure) continue;
            bool copy_status_denial = false;
            if (cancellation != channel.cancellation_generation && !reply.has_closed() &&
                    !(self_revocation_receipt && reply.has_grant())) {
                const auto id = reply.request_id();
                const auto code = reply.has_error() ? reply.error().code() : wire::UNAUTHORIZED;
                reply.Clear(); reply.set_protocol_version(1); reply.set_request_id(id);
                reply.mutable_error()->set_code(code); reply.mutable_error()->set_message("World session changed during operation");
                copy_status_denial = copy_status_request;
            }
            if (reply.ByteSizeLong() > max_frame_bytes) {
                const auto id = reply.request_id();
                reply.Clear(); reply.set_protocol_version(1); reply.set_request_id(id);
                reply.mutable_error()->set_code(wire::RESOURCE_EXHAUSTED);
                reply.mutable_error()->set_message("World session reply exceeds frame limit");
                channel.needs_geometry_observation = false;
            }
            std::ostringstream output; write_frame(output, reply); channel.output = output.str(); channel.sent = 0;
            channel.output_request_id = reply.request_id();
            channel.output_copy_status_request = copy_status_request && !copy_status_denial;
            channel.output_revocation_denial = copy_status_denial;
            channel.output_transport_write_attempted = false;
            lifecycle_diagnostic(channel, "reply_queued");
            channel.output_started = Clock::now();
        }
        std::erase_if(channels, [](const auto& channel) { return channel->fd.value < 0 && !channel->departure; });
        if (listener && !broken && ready && !closing) {
            const auto fd = listener->accept();
            if (fd >= 0) {
                auto channel = std::make_unique<Channel>(fd);
                if (channels.size() < listener->limit()) {
                    channel->tls = listener->peer(fd); channels.push_back(std::move(channel));
                }
            }
        }
    }
};
IdentitySessions::IdentitySessions(const std::filesystem::path& config, const std::vector<int>& channels, WorldStore& world, Actors& actors, BrokerClient* broker,
    int listener, const std::filesystem::path& tls_configuration)
    : impl_(std::make_unique<Impl>(config, channels, world, actors, broker, listener, tls_configuration)) {}
IdentitySessions::~IdentitySessions() {
    for (auto& channel : impl_->channels) { try { impl_->disconnect(*channel, "shutdown"); } catch (const std::exception&) {} }
}
void IdentitySessions::set_tick(std::function<void()> callback) {
    impl_->client->set_tick([this, callback = std::move(callback)] { impl_->tick(); callback(); });
}
void IdentitySessions::tick() { impl_->tick(); }
void IdentitySessions::pump() { impl_->pump(); }
void IdentitySessions::settle() { impl_->settle(); }
void IdentitySessions::shutdown() { impl_->shutdown(); }
}
