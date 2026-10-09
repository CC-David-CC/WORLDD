#include "world/identity_sessions.hpp"
#include "world/authority.hpp"
#include "platform/broker.hpp"
#include "identity/issuer_client.hpp"
#include "identity/issuer_watch.hpp"
#include "identity/keys.hpp"
#include "identity/tls_support.hpp"
#include "world_session.pb.h"
#include <google/protobuf/text_format.h>
#include <openssl/rand.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sstream>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>

namespace spatial {
namespace {
namespace pb = identity::v1;
using Clock = std::chrono::steady_clock;
struct CancellationFailure : std::runtime_error { using std::runtime_error::runtime_error; };
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
struct Channel {
    identity::tls_detail::Fd fd;
    std::string input, output, token;
    std::size_t sent = 0;
    bool needs_observation = false;
    Clock::time_point input_started = Clock::now(), output_started = Clock::now();
    std::optional<Pending> pending;
    std::optional<pb::ActorCredential> completing;
    pb::SignedCredential credential;
    pb::ActorCredential claims;
    explicit Channel(int descriptor) : fd(descriptor) {}
};
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
    std::unique_ptr<identity::IssuerWatcher> watcher;
    pb::IssuerInfo issuer;
    std::unique_ptr<WorldAuthority> authority;
    std::uint64_t observed_revision = 0;
    std::vector<std::unique_ptr<Channel>> channels;
    std::set<std::string> admitted;
    std::map<std::string, std::set<std::string>> grants;
    std::string audience;
    Clock::time_point last_exchange = Clock::now();
    bool ready = false, broken = false, ticking = false;

    Impl(const std::filesystem::path& path, const std::vector<int>& descriptors, WorldStore& store, Actors& engine, BrokerClient* execution)
        : world(store), actors(engine), broker(execution) {
        require(!descriptors.empty() && descriptors.size() <= 32, "World requires between one and 32 actor channels");
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
        watcher = std::make_unique<identity::IssuerWatcher>(config.issuer_client_config());
        const auto deadline = Clock::now()+client->timeout()+std::chrono::milliseconds(100);
        while (!ready && !broken && Clock::now() < deadline) { tick(); std::this_thread::sleep_for(std::chrono::milliseconds(1)); }
        require(ready && !broken, "World revocation baseline is unavailable");
    }
    void disconnect(Channel& channel) {
        const auto token = std::move(channel.token); channel.token.clear();
        channel.credential.Clear(); channel.claims.Clear(); channel.pending.reset(); channel.completing.reset(); channel.needs_observation = false;
        if (!channel.output.empty()) { channel.output.clear(); channel.fd.reset(); }
        if (!token.empty()) {
            try { actors.close(token); }
            catch (const std::exception&) { throw CancellationFailure("Cannot confirm native actor cancellation"); }
        }
    }
    void fail() {
        broken = true;
        for (auto& channel : channels) disconnect(*channel);
    }
    void tick() {
        if (ticking || broken || !watcher) return;
        ticking = true;
        struct Reset { bool& value; ~Reset() { value = false; } } reset{ticking};
        // Detect controller loss during component waits without dispatching new actions.
        for (auto& channel : channels) {
            if (channel->fd.value < 0) continue;
            pollfd fd{channel->fd.value, POLLIN, 0};
            const int result = poll(&fd, 1, 0);
            if (result < 0 && errno == EINTR) continue;
            bool lost = result < 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL));
            if (!lost && (fd.revents & POLLIN)) {
                char byte;
                const auto count = recv(channel->fd.value, &byte, 1, MSG_PEEK | MSG_DONTWAIT);
                lost = !count || (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR);
            }
            if (lost) {
                disconnect(*channel); channel->fd.reset(); channel->input.clear(); channel->output.clear();
            }
        }
        if (watcher->failed()) { fail(); return; }
        while (auto update = watcher->poll()) {
            if (update->has_error() || update->batch().issuer_id() != issuer.issuer_id()) { fail(); return; }
            observed_revision = std::max(observed_revision, update->batch().head_revision());
            if (update->batch().initial()) { ready = true; continue; }
            for (const auto& event : update->batch().events()) {
                if (authority && event.target().kind() == pb::ACTOR_TARGET) authority->revoked(event.target().id());
                const auto affected = [&](const pb::ActorCredential& claims) {
                    return event.target().id() == (event.target().kind() == pb::ACTOR_TARGET ? claims.actor_id() : claims.credential_id());
                };
                for (auto& channel : channels) {
                    if ((!channel->token.empty() && affected(channel->claims)) ||
                            (channel->pending && affected(channel->pending->claims)) ||
                            (channel->completing && affected(*channel->completing))) disconnect(*channel);
                }
            }
            sync_authority();
        }
    }
    pb::IssuerReply exchange(pb::IssuerRequest request) {
        if (broken) throw WorldError(wire::UNAVAILABLE, "World identity service is disabled");
        request.set_protocol_version(1);
        pb::IssuerReply reply;
        try { reply = client->call(std::move(request)); }
        catch (const CancellationFailure&) { throw; }
        catch (const std::exception&) { fail(); throw WorldError(wire::UNAVAILABLE, "World issuer connection failed"); }
        if (broken) throw WorldError(wire::UNAVAILABLE, "World identity service is disabled");
        last_exchange = Clock::now();
        if (reply.has_error()) {
            const auto code = reply.error().code();
            if (code == wire::STORAGE_ERROR || code == wire::INTERNAL_ERROR || code == wire::UNAVAILABLE) fail();
            throw WorldError(code, "Issuer rejected world authentication operation");
        }
        return reply;
    }
    void identity_matches(const pb::IdentityStatus& status, const pb::ActorCredential& claims) {
        if (status.issuer_id() != issuer.issuer_id() || status.actor_id() != claims.actor_id() ||
                status.credential_id() != claims.credential_id() || status.kind() != claims.kind() || status.security_revision() > INT64_MAX ||
                status.security_revision() < observed_revision) {
            fail(); throw WorldError(wire::UNAVAILABLE, "Issuer returned inconsistent identity context");
        }
        observed_revision = std::max(observed_revision, status.security_revision());
    }
    pb::IdentityStatus current(pb::SignedCredential credential, pb::ActorCredential claims) {
        pb::IssuerRequest request; *request.mutable_credential_check() = credential;
        const auto status = exchange(std::move(request)).identity_status(); identity_matches(status, claims);
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
                    (channel->needs_observation && !authority->allowed(actor, "observe"))) disconnect(*channel);
        }
        actors.refresh_authority(); actors.flush_input();
    }
    void prepare(const authority::v1::ActorRef& actor, const authority::v1::ActorRef* extra = nullptr) {
        if (!authority) return;
        auto subjects = authority->subjects(actor);
        if (extra && extra->issuer_id().size() == 32 && extra->actor_id().size() == 32) subjects.push_back(*extra);
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
            const auto batch = exchange(std::move(request)).actor_status();
            if ((revision && *revision != batch.security_revision()) || batch.security_revision() < observed_revision) {
                fail(); throw WorldError(wire::UNAVAILABLE, "Actor status changed during authority preparation");
            }
            revision = batch.security_revision();
            states.insert(states.end(), batch.actors().begin(), batch.actors().end());
        }
        if (revision) observed_revision = std::max(observed_revision, *revision);
        authority->update(states); sync_authority();
        const auto current_actor = std::find_if(states.begin(), states.end(), [&](const auto& value) { return value.actor_id() == actor.actor_id(); });
        if (current_actor == states.end() || !current_actor->active()) throw WorldError(wire::UNAUTHORIZED, "Actor identity is inactive");
    }
    pb::WorldSessionReply dispatch(Channel& channel, const std::string& bytes) {
        pb::WorldSessionReply reply; reply.set_protocol_version(1);
        try {
            pb::WorldSessionRequest request;
            require(request.ParseFromString(bytes), "Malformed world session protobuf"); reply.set_request_id(request.request_id());
            validate_message(request);
            if (request.protocol_version() != 1) throw WorldError(wire::UNSUPPORTED_VERSION, "Unsupported world session protocol");
            if (request.has_close()) { disconnect(channel); reply.mutable_closed()->set_closed(true); return reply; }
            if (broken) throw WorldError(wire::UNAVAILABLE, "World identity service is disabled");
            if (request.has_begin()) {
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
                const auto result = exchange(std::move(complete)).authentication();
                identity_matches(result.identity(), pending->claims);
                if (result.audience() != audience || result.channel_binding() != pending->binding ||
                        !result.identity().active() || result.identity().actor_revoked() || result.identity().credential_revoked()) {
                    fail(); throw WorldError(wire::UNAVAILABLE, "Issuer authentication context mismatch");
                }
                const auto status = current(pending->begin.credential(), pending->claims);
                const auto actor = principal(pending->claims);
                prepare(actor);
                if (!channel.completing) throw WorldError(wire::UNAUTHORIZED, "World authentication was revoked");
                if (config.admission() == pb::EXPLICIT_ACTORS &&
                        !(authority ? authority->allowed(actor, "enter") : admitted.contains(status.actor_id())))
                    throw WorldError(wire::FORBIDDEN, "Actor is not admitted to this world");
                wire::ActorOpenRequest open; open.set_id("identity:"+hex(issuer.issuer_id())+":"+hex(status.actor_id()));
                if (pending->begin.has_room()) open.set_room(pending->begin.room());
                if (pending->begin.has_pose()) *open.mutable_pose() = pending->begin.pose();
                const auto allowed = grants.find(status.actor_id());
                if (allowed != grants.end()) for (const auto& grant : allowed->second) open.add_grants(grant);
                Actors::Authority check;
                if (authority) check = [this, actor](const auto& right, const auto& scope) { return authority->allowed(actor, right, scope); };
                const auto opened = actors.open(open, true, std::move(check), actor);
                channel.token = opened.token(); channel.credential = pending->begin.credential(); channel.claims = pending->claims;
                auto* value = reply.mutable_opened(); value->set_actor_id(opened.id()); value->set_world_id(world.info().world_id());
                *value->mutable_identity() = status;
            } else if (request.has_inspect() || request.has_action() || request.has_grants() || request.has_delegate() || request.has_revoke()) {
                if (channel.token.empty()) throw WorldError(wire::UNAUTHORIZED, "Channel has no authenticated actor");
                const auto actor = principal(channel.claims);
                try {
                    current(channel.credential, channel.claims);
                } catch (const WorldError&) { disconnect(channel); throw; }
                if (channel.token.empty()) throw WorldError(wire::UNAUTHORIZED, "World session was revoked");
                if (request.has_delegate()) {
                    require(request.delegate().has_seq(), "Actor sequence is required");
                    actors.consume_sequence(channel.token, request.delegate().seq());
                } else if (request.has_revoke()) {
                    require(request.revoke().has_seq(), "Actor sequence is required");
                    actors.consume_sequence(channel.token, request.revoke().seq());
                }
                try {
                    prepare(actor, request.has_delegate() && !request.delegate().has_workspace() ? &request.delegate().terms().subject() : nullptr);
                } catch (const WorldError&) { disconnect(channel); throw; }
                if (channel.token.empty()) throw WorldError(wire::UNAUTHORIZED, "World session was revoked");
                if (request.has_inspect()) *reply.mutable_state() = actors.inspect(channel.token);
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
        // Keep the checked action connection alive during an idle human session.
        // The issuer's service idle timeout must exceed this one-second interval.
        if (!broken && Clock::now()-last_exchange >= std::chrono::seconds(1)) {
            pb::IssuerRequest request; request.mutable_info();
            try { exchange(std::move(request)); }
            catch (const WorldError&) { fail(); }
        }
        for (auto& owned : channels) {
            auto& channel = *owned; if (channel.fd.value < 0) continue;
            auto drop = [&] { disconnect(channel); channel.fd.reset(); channel.input.clear(); channel.output.clear(); };
            const auto now = Clock::now();
            if ((!channel.input.empty() && now-channel.input_started > std::chrono::seconds(10)) ||
                    (!channel.output.empty() && now-channel.output_started > std::chrono::seconds(10))) { drop(); continue; }
            pollfd fd{channel.fd.value, static_cast<short>(channel.output.empty() ? POLLIN : POLLOUT), 0};
            const int count = poll(&fd, 1, 0);
            if (count < 0 && errno == EINTR) continue;
            if (count < 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL))) { drop(); continue; }
            if (!channel.output.empty() && (fd.revents & POLLOUT)) {
                if (authority && channel.needs_observation && !authority->allowed(principal(channel.claims), "observe")) { drop(); continue; }
                if (channel.needs_observation) {
                    try { if (!actors.application_observation_valid(channel.token)) { drop(); continue; } }
                    catch (const WorldError&) { drop(); continue; }
                }
                const auto written = write(channel.fd.value, channel.output.data()+channel.sent, channel.output.size()-channel.sent);
                if (written < 0 && errno != EAGAIN && errno != EINTR) { drop(); continue; }
                if (written > 0) { channel.sent += static_cast<std::size_t>(written); if (channel.sent == channel.output.size()) { channel.output.clear(); channel.needs_observation = false; } }
            }
            if (channel.output.empty() && (fd.revents & POLLIN)) {
                std::array<char, 65536> bytes{};
                const auto received = read(channel.fd.value, bytes.data(), std::min(bytes.size(), max_frame_bytes+4-channel.input.size()));
                if (!received || (received < 0 && errno != EAGAIN && errno != EINTR)) { drop(); continue; }
                if (received > 0) {
                    if (channel.input.empty()) channel.input_started = now;
                    channel.input.append(bytes.data(), static_cast<std::size_t>(received));
                }
            }
            if (!channel.output.empty() || channel.input.size() < 4) continue;
            const auto* h = reinterpret_cast<const unsigned char*>(channel.input.data());
            const auto length = (std::uint32_t(h[0]) << 24) | (std::uint32_t(h[1]) << 16) | (std::uint32_t(h[2]) << 8) | h[3];
            if (length > max_frame_bytes) { drop(); continue; }
            if (channel.input.size() < length+4) continue;
            const auto bytes = channel.input.substr(4, length); channel.input.erase(0, length+4); channel.input_started = now;
            const auto reply = dispatch(channel, bytes);
            if (channel.fd.value < 0) continue;
            std::ostringstream output; write_frame(output, reply); channel.output = output.str(); channel.sent = 0;
            channel.output_started = Clock::now();
        }
    }
};
IdentitySessions::IdentitySessions(const std::filesystem::path& config, const std::vector<int>& channels, WorldStore& world, Actors& actors, BrokerClient* broker)
    : impl_(std::make_unique<Impl>(config, channels, world, actors, broker)) {}
IdentitySessions::~IdentitySessions() {
    for (auto& channel : impl_->channels) { try { impl_->disconnect(*channel); } catch (const std::exception&) {} }
}
void IdentitySessions::set_tick(std::function<void()> callback) {
    impl_->client->set_tick([this, callback = std::move(callback)] { impl_->tick(); callback(); });
}
void IdentitySessions::tick() { impl_->tick(); }
void IdentitySessions::pump() { impl_->pump(); }
}
