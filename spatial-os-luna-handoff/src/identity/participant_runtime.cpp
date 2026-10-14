#include "identity/participant_runtime.hpp"
#include "identity/participant_stream.hpp"
#include <poll.h>
#include <climits>
#include "identity/issuer_client.hpp"
#include "identity/keys.hpp"
#include "identity/participants.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <openssl/rand.h>
#include <algorithm>
#include <deque>
#include <iostream>
#include <mutex>
#include <set>
#include <string_view>
#include <syncstream>
#include <thread>

namespace spatial::identity {
namespace {
namespace core = spatial::core::v1;
using participant_detail::require;
using Domains = std::set<std::pair<int, std::string>>;
Domains domain_set(const v1::AuthorityParticipant& participant) {
    Domains result;
    for (const auto& domain : participant.domains()) result.insert(participant_detail::domain_key(domain));
    return result;
}
std::string diagnostic_cause(std::string_view text) {
    std::string result;
    for (const unsigned char byte : text.substr(0, 240))
        result.push_back(byte >= 32 && byte <= 126 ? static_cast<char>(byte) : '?');
    return result;
}
}
struct ParticipantRuntime::Impl {
    std::string service_incarnation;
    mutable std::mutex mutex;
    std::deque<v1::ParticipantCommand> commands;
    std::deque<v1::ParticipantUpdate> updates;
    std::set<std::uint64_t> pending, forwarded_requests;
    bool inherited_channel = false;
    std::optional<std::uint64_t> delivered_revision;
    bool broken = false, closed = false, releasing = false;
    // Destruction requests stop and joins before destroying shared state.
    std::jthread worker;

    void fail_locked(core::ErrorCode code, const std::string& message, std::uint64_t request = 0,
                     const char* stage = "consumer", std::string_view cause = {}) {
        if (broken) return;
        std::osyncstream(std::cerr) << "authority participant failed: code=" << static_cast<int>(code)
            << " request=" << request << " stage=" << stage << " cause="
            << diagnostic_cause(cause.empty() ? std::string_view(message) : cause) << '\n';
        broken = true; commands.clear(); updates.clear(); pending.clear(); forwarded_requests.clear();
        auto& update = updates.emplace_back(); update.set_protocol_version(1); update.set_request_id(request);
        update.mutable_error()->set_code(code); update.mutable_error()->set_message(message);
    }
    bool publish(v1::ParticipantUpdate update) {
        std::lock_guard lock(mutex);
        if (broken) return false;
        if (updates.size() >= 256) {
            fail_locked(core::RESOURCE_EXHAUSTED, "Authority participant consumer fell behind"); return false;
        }
        update.set_protocol_version(1); updates.push_back(std::move(update)); return true;
    }
    void check_stop(std::stop_token stop) {
        std::lock_guard lock(mutex);
        if (stop.stop_requested() || broken) throw std::runtime_error("Authority participant stopped");
    }
    bool reply_error(const v1::IssuerReply& reply, const char* stage, std::uint64_t request = 0) {
        if (!reply.has_error()) return false;
        std::lock_guard lock(mutex); fail_locked(reply.error().code(), reply.error().message(), request, stage); return true;
    }
    void run(std::stop_token stop, const v1::AuthorityParticipantRuntimeConfig& config, const Domains& expected) {
        std::uint64_t current_request = 0;
        const char* stage = "key_load";
        try {
            auto key = KeyPair::load(config.private_key_path());
            stage = "issuer_connect";
            IssuerClient client(config.issuer_client_config(), [this, stop] { check_stop(stop); });
            v1::IssuerRequest begin; begin.set_protocol_version(1);
            auto* auth = begin.mutable_authority_authentication_begin();
            auth->set_participant_id(config.participant_id()); auth->set_registered_revision(config.registered_revision());
            auth->set_service_incarnation(service_incarnation);
            stage = "authentication_begin";
            auto reply = client.call(std::move(begin)); if (reply_error(reply, stage)) return;
            v1::IssuerRequest complete; complete.set_protocol_version(1);
            auto* proof = complete.mutable_authority_authentication_complete();
            proof->set_nonce(reply.challenge().nonce()); *proof->mutable_proof() = key.sign(reply.challenge().signing_request());
            stage = "authentication_complete";
            reply = client.call(std::move(complete)); if (reply_error(reply, stage)) return;
            stage = "identity_check";
            const auto& identity = reply.authority_authentication();
            const auto& registration = identity.registration();
            require(registration.participant().id() == config.participant_id() &&
                    registration.registered_revision() == config.registered_revision() &&
                    registration.participant().public_key() == key.public_key() &&
                    domain_set(registration.participant()) == expected, "Registered authority domains or identity do not match runtime");
            v1::ParticipantUpdate update; *update.mutable_authenticated() = identity;
            if (!publish(std::move(update))) return;

            std::optional<std::uint64_t> cursor;
            std::optional<v1::AuthorityActivationState> active;
            const auto wait = static_cast<std::uint32_t>(std::min<std::int64_t>(100, client.timeout().count()/2));
            bool backlog = false;
            while (!stop.stop_requested()) {
                check_stop(stop);
                std::optional<v1::ParticipantCommand> command;
                std::optional<std::uint64_t> delivered;
                bool queued = false, forwarded = false;
                {
                    std::lock_guard lock(mutex); delivered = delivered_revision;
                    // Publish the initial feed before accepting mutations.
                    if (cursor && !commands.empty()) {
                        command = std::move(commands.front()); commands.pop_front();
                        forwarded = forwarded_requests.erase(command->request_id()) != 0;
                    }
                    queued = !commands.empty();
                }
                if (command) {
                    current_request = command->request_id();
                    stage = command->has_activate() ? "activate" : command->has_applied() ? "applied" : "release";
                    v1::IssuerRequest request; request.set_protocol_version(1);
                    if (command->has_activate()) {
                        require(!active && delivered && command->activate().has_synchronized_revision() &&
                                (forwarded ? command->activate().synchronized_revision() <= *delivered :
                                    command->activate().synchronized_revision() == *delivered),
                                "Activation requires the consumed synchronized revision");
                        *request.mutable_authority_activate() = command->activate();
                    } else if (command->has_applied()) {
                        const auto& applied = command->applied();
                        require(active && active->active() && delivered && applied.generation() == active->generation() &&
                                applied.has_from_revision() && applied.has_through_revision() &&
                                applied.from_revision() == active->applied_revision() && applied.from_revision() < INT64_MAX &&
                                applied.through_revision() == applied.from_revision()+1 && applied.through_revision() <= *delivered,
                                "Acknowledgement requires one contiguous consumed revision and the active generation");
                        *request.mutable_authority_applied() = applied;
                    } else {
                        const auto& release = command->release();
                        require(active && active->active() && release.generation() == active->generation() &&
                                release.has_applied_revision() && release.applied_revision() == active->applied_revision(),
                                "Release requires the active acknowledged generation");
                        *request.mutable_authority_release() = release;
                    }
                    reply = client.call(std::move(request));
                    update.Clear(); update.set_request_id(command->request_id());
                    if (reply.has_error() && command->has_activate() && reply.error().code() == core::REVISION_CONFLICT) {
                        // Fetch new events; the consumer must synchronize and explicitly request activation again.
                        *update.mutable_error() = reply.error();
                    } else {
                        if (reply_error(reply, stage, command->request_id())) return;
                        active = reply.authority_activation();
                        if (command->has_activate()) *update.mutable_activated() = *active;
                        else if (command->has_applied()) *update.mutable_applied() = *active;
                        else {
                            *update.mutable_released() = *active;
                            { std::lock_guard lock(mutex); closed = true; }
                            publish(std::move(update)); return;
                        }
                    }
                    if (!publish(std::move(update))) return;
                    current_request = 0;
                }
                v1::IssuerRequest request; request.set_protocol_version(1);
                auto* read = request.mutable_revocations();
                if (cursor) read->set_after_revision(*cursor);
                read->set_wait_ms(!cursor || backlog || queued ? 0 : wait);
                stage = "revocations";
                reply = client.call(std::move(request)); if (reply_error(reply, stage)) return;
                auto batch = std::move(*reply.mutable_revocations());
                const bool initial = !cursor; cursor = batch.through_revision(); backlog = *cursor < batch.head_revision();
                if (initial || batch.events_size()) {
                    update.Clear(); *update.mutable_revocations() = std::move(batch);
                    if (!publish(std::move(update))) return;
                }
            }
        } catch (const std::invalid_argument& error) {
            std::lock_guard lock(mutex);
            if (!stop.stop_requested()) fail_locked(core::INVALID_ARGUMENT, "Invalid authority participant context or command",
                current_request, stage, error.what());
        } catch (const std::exception& error) {
            std::lock_guard lock(mutex);
            if (!stop.stop_requested()) fail_locked(core::UNAVAILABLE, "Authority participant channel failed; reconciliation is required",
                current_request, stage, error.what());
        }
    }
    void run_private(std::stop_token stop, const v1::AuthorityParticipantRuntimeConfig& config,
                     const Domains& expected, ParticipantStream& stream) {
        using Clock = std::chrono::steady_clock;
        std::uint64_t current_request = 0, head = 0;
        std::optional<v1::AuthorityAuthenticationResult> identity;
        std::optional<v1::AuthorityActivationState> active;
        std::optional<std::uint64_t> cursor;
        std::optional<v1::ParticipantCommand> waiting;
        auto deadline = Clock::now()+std::chrono::seconds(5);
        auto startup = deadline;
        try {
            while (!stop.stop_requested()) {
                check_stop(stop);
                if (!cursor && Clock::now() >= startup) throw std::runtime_error("Private participant startup deadline exceeded");
                if (waiting && Clock::now() >= deadline) throw std::runtime_error("Private participant command deadline exceeded");
                if (cursor && !waiting) {
                    std::optional<std::uint64_t> delivered;
                    {
                        std::lock_guard lock(mutex); delivered = delivered_revision;
                        if (!commands.empty()) { waiting = std::move(commands.front()); commands.pop_front(); }
                    }
                    if (waiting) {
                        current_request = waiting->request_id();
                        if (waiting->has_activate()) {
                            require(!active && delivered && waiting->activate().has_synchronized_revision() &&
                                waiting->activate().synchronized_revision() == *delivered,
                                "Activation requires the consumed synchronized revision");
                        } else if (waiting->has_applied()) {
                            const auto& applied = waiting->applied();
                            require(active && active->active() && delivered && applied.generation() == active->generation() &&
                                applied.has_from_revision() && applied.has_through_revision() &&
                                applied.from_revision() == active->applied_revision() && applied.from_revision() < INT64_MAX &&
                                applied.through_revision() == applied.from_revision()+1 && applied.through_revision() <= *delivered,
                                "Acknowledgement requires one contiguous consumed revision and the active generation");
                        } else {
                            const auto& release = waiting->release();
                            require(active && active->active() && release.generation() == active->generation() &&
                                release.has_applied_revision() && release.applied_revision() == active->applied_revision(),
                                "Release requires the active acknowledged generation");
                        }
                        stream.send(waiting->SerializeAsString()); deadline = Clock::now()+std::chrono::seconds(5);
                    }
                }
                stream.flush();
                pollfd fd{stream.descriptor(), static_cast<short>(POLLIN | (stream.pending_output() ? POLLOUT : 0)), 0};
                const int count = ::poll(&fd,1,5);
                if (count < 0 && errno == EINTR) continue;
                require(count >= 0 && !(fd.revents & POLLNVAL), "Private participant poll failed");
                auto frame = stream.receive();
                if (!frame) continue;
                v1::ParticipantUpdate update;
                require(update.ParseFromString(*frame), "Malformed private participant update"); validate_message(update);
                require(update.protocol_version() == 1 && update.outcome_case() != v1::ParticipantUpdate::OUTCOME_NOT_SET,
                    "Invalid private participant update envelope");
                if (update.has_error()) {
                    require(core::ErrorCode_IsValid(update.error().code()) && update.error().code() != core::ERROR_CODE_UNSPECIFIED &&
                        ((waiting && update.request_id() == waiting->request_id()) || update.request_id() == 0),
                        "Uncorrelated private participant error");
                    if (waiting && waiting->has_activate() && update.request_id() == waiting->request_id() &&
                        update.error().code() == core::REVISION_CONFLICT) {
                        waiting.reset(); current_request = 0;
                        if (!publish(std::move(update))) return;
                        continue;
                    }
                    std::lock_guard lock(mutex);
                    fail_locked(update.error().code(), update.error().message(), update.request_id(), "private_channel"); return;
                }
                if (!identity) {
                    require(update.has_authenticated() && update.request_id() == 0, "Private participant requires authentication first");
                    const auto& value = update.authenticated();
                    validate_participant_record(value.registration(), config.supervisor_issuer_id());
                    require(value.issuer_id() == config.supervisor_issuer_id() && value.issuer_incarnation().size() == 32 &&
                        value.connection_binding().size() == 32 && value.service_incarnation() == config.service_incarnation() &&
                        value.security_revision() <= INT64_MAX && value.registration().participant().id() == config.participant_id() &&
                        value.registration().registered_revision() == config.registered_revision() &&
                        domain_set(value.registration().participant()) == expected, "Private participant authentication changed identity");
                    identity = value; head = value.security_revision();
                } else if (update.has_revocations()) {
                    const auto& batch = update.revocations();
                    const auto from = batch.from_revision(), through = batch.through_revision(), latest = batch.head_revision();
                    require(update.request_id() == 0 && batch.issuer_id() == config.supervisor_issuer_id() &&
                        from <= through && through <= latest && latest <= INT64_MAX && latest >= head && batch.events_size() <= 128,
                        "Invalid private revocation range or identity");
                    if (!cursor) require(batch.initial() && from == latest && through == latest && batch.events_size() == 0,
                        "Invalid private revocation baseline");
                    else {
                        require(!batch.initial() && from == *cursor && through-from == static_cast<std::uint64_t>(batch.events_size()) &&
                            (batch.events_size() == 128 || through == latest), "Incomplete private revocation batch");
                        auto revision = from;
                        for (const auto& event : batch.events()) require(event.revision() == ++revision && event.target().id().size() == 32 &&
                            (event.target().kind() == v1::ACTOR_TARGET || event.target().kind() == v1::CREDENTIAL_TARGET),
                            "Invalid private revocation event");
                    }
                    cursor = through; head = latest;
                } else {
                    require(cursor && waiting && update.request_id() == waiting->request_id(), "Unsolicited private participant result");
                    require((waiting->has_activate() && update.has_activated()) || (waiting->has_applied() && update.has_applied()) ||
                        (waiting->has_release() && update.has_released()), "Mismatched private participant operation");
                    const auto& state = update.has_activated() ? update.activated() : update.has_applied() ? update.applied() : update.released();
                    require(state.issuer_id() == identity->issuer_id() &&
                        state.registration().SerializeAsString() == identity->registration().SerializeAsString() &&
                        state.issuer_incarnation() == identity->issuer_incarnation() && state.connection_binding() == identity->connection_binding() &&
                        state.service_incarnation() == identity->service_incarnation() && state.generation() > 0 && state.generation() <= INT64_MAX &&
                        state.applied_revision() <= state.head_revision() && state.head_revision() <= INT64_MAX && state.head_revision() >= head,
                        "Private participant result changed identity or revision");
                    if (waiting->has_activate()) require(!active && state.active() &&
                        state.applied_revision() == waiting->activate().synchronized_revision() && state.applied_revision() == state.head_revision(),
                        "Invalid private activation success");
                    else {
                        require(active && state.generation() == active->generation() && state.applied_revision() >= active->applied_revision(),
                            "Private participant generation changed");
                        if (waiting->has_applied()) require(state.active() && state.applied_revision() == waiting->applied().through_revision(),
                            "Invalid private acknowledgement success");
                        else require(!state.active() && state.applied_revision() == active->applied_revision() &&
                            state.applied_revision() == state.head_revision(), "Invalid private release success");
                    }
                    active = state; head = state.head_revision(); waiting.reset(); current_request = 0;
                    if (update.has_released()) {
                        { std::lock_guard lock(mutex); closed = true; }
                        publish(std::move(update)); return;
                    }
                }
                if (!publish(std::move(update))) return;
            }
        } catch (const std::invalid_argument& error) {
            std::lock_guard lock(mutex);
            if (!stop.stop_requested()) fail_locked(core::INVALID_ARGUMENT, "Invalid private participant context or command",
                current_request, "private_channel", error.what());
        } catch (const std::exception& error) {
            std::lock_guard lock(mutex);
            if (!stop.stop_requested()) fail_locked(core::UNAVAILABLE, "Private participant channel failed; reconciliation is required",
                current_request, "private_channel", error.what());
        }
    }
    Impl(const std::filesystem::path& path, const std::vector<authority::v1::Domain>& domains) {
        v1::AuthorityParticipantRuntimeConfig config;
        require(google::protobuf::TextFormat::ParseFromString(tls_detail::load_config(path), &config),
                "Invalid authority participant runtime configuration");
        validate_message(config);
        require(config.protocol_version() == 1 && config.participant_id().size() == 32 &&
                config.registered_revision() > 0 && config.registered_revision() <= INT64_MAX &&
                (!config.has_service_incarnation() || config.service_incarnation().size() == 32),
                "Invalid authority participant configuration");
        if (config.has_supervisor_channel_fd()) require(config.supervisor_channel_fd() >= 3 &&
                config.supervisor_channel_fd() <= INT_MAX && config.supervisor_issuer_id().size() == 32 &&
                config.has_service_incarnation() && config.issuer_client_config().empty() && config.private_key_path().empty(),
                "Invalid inherited participant configuration");
        else require(config.supervisor_issuer_id().empty() && std::filesystem::path(config.issuer_client_config()).is_absolute() &&
                std::filesystem::path(config.private_key_path()).is_absolute(), "Invalid authority participant configuration");
        service_incarnation = config.service_incarnation();
        if (!config.has_service_incarnation()) {
            service_incarnation.resize(32);
            require(RAND_bytes(reinterpret_cast<unsigned char*>(service_incarnation.data()), 32) == 1,
                    "Service incarnation generation failed");
        }
        v1::AuthorityParticipant expected; expected.set_id(config.participant_id()); expected.set_public_key(std::string(32, '\0'));
        for (const auto& domain : domains) *expected.add_domains() = domain;
        validate_participant(expected);
        if (config.has_supervisor_channel_fd()) {
            inherited_channel = true;
            auto stream = std::make_unique<ParticipantStream>(static_cast<int>(config.supervisor_channel_fd()), getppid());
            worker = std::jthread([this, config, domains = domain_set(expected), stream = std::move(stream)](std::stop_token stop) {
                run_private(stop, config, domains, *stream);
            });
        } else worker = std::jthread([this, config, domains = domain_set(expected)](std::stop_token stop) { run(stop, config, domains); });
    }
};
ParticipantRuntime::ParticipantRuntime(const std::filesystem::path& path, const std::vector<authority::v1::Domain>& domains)
    : impl_(std::make_unique<Impl>(path, domains)) {}
ParticipantRuntime::~ParticipantRuntime() = default;
std::string ParticipantRuntime::service_incarnation() const { return impl_->service_incarnation; }
void ParticipantRuntime::submit(v1::ParticipantCommand command) { submit_impl(std::move(command), false); }
void ParticipantRuntime::submit_forwarded(v1::ParticipantCommand command) { submit_impl(std::move(command), true); }
void ParticipantRuntime::submit_impl(v1::ParticipantCommand command, bool forwarded) {
    require(!forwarded || !impl_->inherited_channel, "Only a direct issuer connection can forward child commands");
    validate_message(command);
    require(command.operation_case() != v1::ParticipantCommand::OPERATION_NOT_SET, "Participant command requires an operation");
    std::lock_guard lock(impl_->mutex);
    if (impl_->broken || impl_->closed || impl_->releasing) throw std::runtime_error("Authority participant channel is unavailable");
    require(!impl_->pending.contains(command.request_id()), "Participant command request ID is already pending");
    if (impl_->pending.size() >= 128) {
        impl_->fail_locked(core::RESOURCE_EXHAUSTED, "Authority participant command queue overflow"); return;
    }
    if (command.has_release()) {
        require(impl_->pending.empty(), "Consume all command results before requesting release");
        impl_->releasing = true;
    }
    impl_->pending.insert(command.request_id());
    if (forwarded) impl_->forwarded_requests.insert(command.request_id());
    impl_->commands.push_back(std::move(command));
}
std::optional<v1::ParticipantUpdate> ParticipantRuntime::poll() {
    std::lock_guard lock(impl_->mutex);
    if (impl_->updates.empty()) return {};
    auto update = std::move(impl_->updates.front()); impl_->updates.pop_front();
    if (update.has_revocations()) impl_->delivered_revision = update.revocations().through_revision();
    else if (!update.has_authenticated()) impl_->pending.erase(update.request_id());
    return update;
}
bool ParticipantRuntime::failed() const { std::lock_guard lock(impl_->mutex); return impl_->broken; }
}
