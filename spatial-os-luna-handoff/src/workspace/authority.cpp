#include "workspace/authority.hpp"
#include <iostream>
#include "workspace/store.hpp"
#include "authority/store.hpp"
#include "identity/issuer_client.hpp"
#include "identity/participant_runtime.hpp"
#include "identity/tls_support.hpp"
#include "identity/bootstrap.hpp"
#include "identity/keys.hpp"
#include "world/store.hpp"
#include <google/protobuf/text_format.h>
#include <algorithm>
#include <cerrno>
#include <array>
#include <map>
#include <optional>
#include <set>
#include <thread>
#include <tuple>
#include <sys/random.h>

namespace spatial {
namespace {
namespace a = authority::v1;
namespace id = identity::v1;
namespace pb = workspace::v1;
using Clock = std::chrono::steady_clock;
void require(bool value, const char* text) { if (!value) throw std::invalid_argument(text); }
void principal(const a::ActorRef& value) {
    validate_message(value);
    require(value.actor_id().size() == 32 && value.issuer_id().size() == 32, "Authenticated actor reference is required");
}
std::string actor_name(const a::ActorRef& value) {
    constexpr char digits[] = "0123456789abcdef";
    const auto hex = [&](const std::string& bytes) {
        std::string out;
        for (const unsigned char byte : bytes) { out += digits[byte >> 4]; out += digits[byte & 15]; }
        return out;
    };
    return "identity:"+hex(value.issuer_id())+":"+hex(value.actor_id());
}
a::Domain domain(const std::string& workspace) {
    a::Domain value; value.set_kind(a::WORKSPACE); value.set_id(workspace); return value;
}
}
struct WorkspaceAuthority::Impl {
    std::map<std::string, std::unique_ptr<authority::Store>> stores;
    std::unique_ptr<identity::IssuerClient> client;
    std::unique_ptr<identity::ParticipantRuntime> participant;
    std::string issuer, issuer_key;
    std::optional<std::string> bootstrap_credential;
    bool bootstrap_revoked = false;
    std::map<std::string, bool> active;
    std::set<std::string> revoked;
    std::map<std::tuple<std::string, std::string, int, std::vector<std::string>>, bool> decisions;
    std::uint64_t observed_revision = 0;
    bool observed_revision_initialized = false;
    std::string incarnation = std::string(16, '\0');
    std::uint64_t epoch = 1;
    std::optional<std::uint64_t> consumed_revision;
    std::uint64_t acknowledged_revision = 0, generation = 0, command_sequence = 0, cancellation_sequence = 0;
    id::ParticipantCommand::OperationCase pending_operation = id::ParticipantCommand::OPERATION_NOT_SET;
    bool ready = false, broken = false, changed = true, closing = false, released = false;
    bool polling = false, issuer_exchange_in_progress = false;
    Clock::time_point last_exchange = Clock::now();
    std::function<void()> observer = [] {};

    Impl(const std::filesystem::path& path, ExecutionStore& execution) {
        std::size_t random_used = 0;
        while (random_used < incarnation.size()) {
            const auto count = getrandom(incarnation.data() + random_used, incarnation.size() - random_used, 0);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) throw std::runtime_error("Unable to create authorization incarnation");
            random_used += static_cast<std::size_t>(count);
        }
        pb::WorkspaceAuthorityConfig config;
        require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(path), &config), "Invalid workspace authority configuration");
        validate_message(config);
        require(config.protocol_version() == 1 && config.bindings_size() > 0 && config.bindings_size() <= 64, "Configure one to 64 workspace authorities");
        require(!config.participant_runtime_config().empty(), "Workspace participant configuration is required");
        std::vector<a::Domain> domains;
        for (const auto& binding : config.bindings()) {
            domains.push_back(domain(binding.workspace()));
            require(!stores.contains(binding.workspace()), "Duplicate workspace authority binding");
            if (binding.workspace() == "workspace-registry") {
                try {
                    execution.workspace_get(binding.workspace());
                    throw std::invalid_argument("Reserved workspace registry cannot have execution state");
                } catch (const WorldError& error) {
                    if (error.code != wire::NOT_FOUND) throw;
                }
            } else execution.workspace_get(binding.workspace());
            stores.emplace(binding.workspace(), std::make_unique<authority::Store>(binding.authority_directory(), domain(binding.workspace()), false));
        }
        client = std::make_unique<identity::IssuerClient>(config.issuer_client_config());
        id::IssuerRequest request; request.set_protocol_version(1); request.mutable_info();
        const auto reply = client->call(request);
        require(reply.has_info(), "Workspace issuer is unavailable"); issuer = reply.info().issuer_id();
        issuer_key = reply.info().public_key();
        participant = std::make_unique<identity::ParticipantRuntime>(config.participant_runtime_config(), domains);
        client->set_tick([this] { tick(); });
        const auto deadline = Clock::now()+client->timeout()+std::chrono::milliseconds(100);
        // Construction has no owned jobs or control reservations to cancel.
        while (!ready && !broken && Clock::now() < deadline) {
            tick(); changed = false; settle(true); std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        require(ready && !broken, "Workspace participation is unavailable");
    }
    void invalidate() { decisions.clear(); changed = true; }
    void bump_epoch() {
        if (epoch == UINT64_MAX) { broken = true; active.clear(); invalidate(); throw WorldError(wire::RESOURCE_EXHAUSTED, "Authorization epoch exhausted"); }
        ++epoch; invalidate();
    }
    void fail() { if (!broken) { broken = true; if (epoch != UINT64_MAX) ++epoch; ++cancellation_sequence; active.clear(); invalidate(); } }
    void poll_authority() {
        if (polling) return;
        polling = true;
        struct ResetPoll { bool& active; ~ResetPoll() { active = false; } } reset{polling};
        if (broken || !participant) { observer(); return; }
        if (participant->failed()) { fail(); observer(); return; }
        while (auto update = participant->poll()) {
            if (update->has_authenticated()) {
                if (update->authenticated().issuer_id() != issuer) { fail(); observer(); return; }
                continue;
            }
            if (!update->has_revocations()) {
                if (pending_operation == id::ParticipantCommand::OPERATION_NOT_SET || update->request_id() != command_sequence) {
                    fail(); observer(); return;
                }
                const auto expected = pending_operation; pending_operation = id::ParticipantCommand::OPERATION_NOT_SET;
                if (update->has_error()) {
                    if (expected == id::ParticipantCommand::kActivate && update->error().code() == wire::REVISION_CONFLICT) continue;
                    fail(); observer(); return;
                }
                if (expected == id::ParticipantCommand::kActivate && update->has_activated()) {
                    generation = update->activated().generation(); acknowledged_revision = update->activated().applied_revision(); ready = true;
                } else if (expected == id::ParticipantCommand::kApplied && update->has_applied()) {
                    acknowledged_revision = update->applied().applied_revision();
                } else if (expected == id::ParticipantCommand::kRelease && update->has_released()) released = true;
                else { fail(); observer(); return; }
                continue;
            }
            const auto& batch = update->revocations();
            if (batch.issuer_id() != issuer) { fail(); observer(); return; }
            if (observed_revision_initialized && batch.head_revision() > observed_revision) bump_epoch();
            observed_revision = std::max(observed_revision, batch.head_revision());
            observed_revision_initialized = true;
            consumed_revision = batch.through_revision();
            for (const auto& event : batch.events()) {
                ++cancellation_sequence;
                if (bootstrap_credential && event.target().kind() == id::CREDENTIAL_TARGET &&
                        event.target().id() == *bootstrap_credential && !bootstrap_revoked) { bootstrap_revoked = true; bump_epoch(); }
                if (event.target().kind() == id::ACTOR_TARGET) {
                    const auto id = event.target().id();
                    const bool was_active = active.contains(id) && active.at(id);
                    const bool was_revoked = revoked.contains(id);
                    revoked.insert(id); active[id] = false;
                    if (was_active || !was_revoked) bump_epoch();
                }
            }
        }
        observer();
    }
    void keepalive() {
        if (!polling && !broken && participant && !issuer_exchange_in_progress &&
                Clock::now()-last_exchange >= std::chrono::seconds(1)) {
            id::IssuerRequest request; request.mutable_info();
            try { exchange(std::move(request)); }
            catch (const WorldError&) {
                std::cerr << "Workspace issuer keepalive failed; authority is unavailable\n";
                fail();
            }
        }
    }
    void tick() {
        if (polling) return;
        poll_authority();
        // The polling scope has ended. INFO progress must still consume revocations
        // and run cached control cancellation without recursively starting INFO.
        keepalive();
    }
    void settle(bool effects_settled) {
        // Called only after dispatch, process reconciliation, and control cancellation.
        // A wait callback can consume another revision after reconciliation. changed blocks that acknowledgement.
        if (!effects_settled || changed || broken || released || !consumed_revision ||
                pending_operation != id::ParticipantCommand::OPERATION_NOT_SET) return;
        id::ParticipantCommand command;
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
        require(command_sequence < UINT64_MAX, "Workspace participant sequence exhausted");
        command.set_request_id(++command_sequence); pending_operation = command.operation_case();
        participant->submit(std::move(command));
    }
    void shutdown(const std::function<void()>& reconcile) {
        closing = true;
        const auto deadline = Clock::now()+std::max(client->timeout()*2, std::chrono::milliseconds(2000));
        while (!released && !broken && Clock::now() < deadline) {
            tick(); reconcile(); settle(true); std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!released) throw std::runtime_error("Workspace participant release remains unresolved");
    }
    id::IssuerReply exchange(id::IssuerRequest request) {
        if (broken) throw WorldError(wire::UNAVAILABLE, "Workspace identity service is disabled");
        if (issuer_exchange_in_progress) throw std::logic_error("Recursive workspace issuer exchange is not allowed");
        issuer_exchange_in_progress = true;
        struct ClearExchange { bool& active; ~ClearExchange() { active = false; } } clear{issuer_exchange_in_progress};
        request.set_protocol_version(1); id::IssuerReply reply;
        try { reply = client->call(std::move(request)); }
        catch (const std::exception&) { fail(); throw WorldError(wire::UNAVAILABLE, "Workspace issuer connection failed"); }
        if (broken) throw WorldError(wire::UNAVAILABLE, "Workspace identity service is disabled");
        last_exchange = Clock::now();
        // Revocation during a delayed reply cancels this operation, not actor-owned execution.
        // Consume the checked reply first so the issuer transport remains synchronized.
        if (bootstrap_credential && bootstrap_revoked)
            throw WorldError(wire::UNAUTHORIZED, "Workspace bootstrap credential was revoked during issuer I/O");
        if (reply.has_error()) {
            const auto code = reply.error().code();
            if (code == wire::STORAGE_ERROR || code == wire::INTERNAL_ERROR || code == wire::UNAVAILABLE) fail();
            throw WorldError(code, "Issuer rejected workspace authority request");
        }
        return reply;
    }
    authority::Store& store(const std::string& workspace) {
        const auto found = stores.find(workspace);
        if (found == stores.end()) throw WorldError(wire::FORBIDDEN, "Persistent workspace authority is unavailable");
        return *found->second;
    }
    bool identity_active(const a::ActorRef& actor) const {
        const auto found = active.find(actor.actor_id());
        return ready && !broken && !closing && actor.issuer_id() == issuer && !revoked.contains(actor.actor_id()) && found != active.end() && found->second;
    }
    void prepare(const std::string& workspace, const a::ActorRef& actor, const a::ActorRef* extra = nullptr) {
        principal(actor);
        if (actor.issuer_id() != issuer) throw WorldError(wire::FORBIDDEN, "Workspace issuer does not match actor identity");
        auto subjects = store(workspace).subjects(actor);
        if (extra && extra->actor_id().size() == 32 && extra->issuer_id().size() == 32) subjects.push_back(*extra);
        prepare_subjects(actor, subjects);
    }
    void prepare_subjects(const a::ActorRef& actor, const std::vector<a::ActorRef>& subjects) {
        std::set<std::string> ids;
        for (const auto& subject : subjects) if (subject.issuer_id() == issuer) ids.insert(subject.actor_id());
        std::vector<id::ActorState> states; std::optional<std::uint64_t> revision;
        auto next = ids.begin();
        while (next != ids.end()) {
            id::IssuerRequest request; auto* query = request.mutable_actor_status();
            while (next != ids.end() && query->actor_ids_size() < 128) query->add_actor_ids(*next++);
            const auto batch = exchange(std::move(request)).actor_status();
            if ((revision && *revision != batch.security_revision()) || batch.security_revision() < observed_revision) {
                // A concurrent issuer update invalidates this snapshot, not unrelated execution.
                // Watcher events still invalidate the affected actors and dependent grants.
                throw WorldError(wire::UNAVAILABLE, "Actor status changed during workspace authority preparation");
            }
            revision = batch.security_revision(); states.insert(states.end(), batch.actors().begin(), batch.actors().end());
        }
        bool changed_state = revision && observed_revision_initialized && *revision > observed_revision;
        if (revision) {
            observed_revision = std::max(observed_revision, *revision);
            observed_revision_initialized = true;
        }
        for (const auto& state : states) {
            const bool was_active = active.contains(state.actor_id()) && active.at(state.actor_id());
            const bool was_revoked = revoked.contains(state.actor_id());
            if (state.revoked()) revoked.insert(state.actor_id());
            const bool now_active = state.active() && !revoked.contains(state.actor_id());
            active[state.actor_id()] = now_active;
            changed_state = changed_state || (was_active != now_active) || (was_revoked != revoked.contains(state.actor_id()));
        }
        if (changed_state) bump_epoch();
        invalidate();
        if (!identity_active(actor)) throw WorldError(wire::UNAUTHORIZED, "Workspace actor identity is inactive");
    }
    id::ActorCredential claims(const id::SignedCredential& credential, const a::ActorRef& actor) {
        principal(actor); validate_message(credential);
        id::VerifyRequest verification; verification.set_expected_public_key(issuer_key);
        *verification.mutable_content() = credential.content(); *verification.mutable_proof() = credential.proof();
        id::ActorCredential result;
        require(credential.content().purpose() == id::ISSUER_CREDENTIAL && identity::verify(verification) &&
            result.ParseFromString(credential.content().payload()), "Invalid workspace bootstrap credential signature");
        validate_message(result);
        require(result.protocol_version() == 1 && result.issuer_id() == issuer && result.issuer_id() == actor.issuer_id() &&
            result.actor_id() == actor.actor_id() && result.credential_id().size() == 32 && result.public_key().size() == 32 &&
            (result.kind() == id::HUMAN || result.kind() == id::AGENT), "Workspace bootstrap credential does not match principal");
        return result;
    }
    struct BootstrapGuard {
        Impl& owner;
        id::ActorCredential claims;
        BootstrapGuard(Impl& value, const id::SignedCredential& credential, const a::ActorRef& actor)
            : owner(value), claims(value.claims(credential, actor)) {
            require(!owner.bootstrap_credential, "Nested workspace bootstrap operation");
            owner.bootstrap_credential = claims.credential_id(); owner.bootstrap_revoked = false;
        }
        ~BootstrapGuard() { owner.bootstrap_credential.reset(); owner.bootstrap_revoked = false; }
    };
    void bootstrap_live(const a::ActorRef& actor) {
        tick();
        if (broken) throw WorldError(wire::UNAVAILABLE, "Workspace identity service is disabled");
        if (bootstrap_revoked || revoked.contains(actor.actor_id()))
            throw WorldError(wire::UNAUTHORIZED, "Workspace bootstrap identity was revoked");
    }
    id::IdentityStatus current(const id::SignedCredential& credential, const id::ActorCredential& claims) {
        id::IssuerRequest request; *request.mutable_credential_check() = credential;
        const auto result = exchange(std::move(request)).identity_status();
        if (result.issuer_id() != issuer || result.actor_id() != claims.actor_id() ||
            result.credential_id() != claims.credential_id() || result.kind() != claims.kind() ||
            result.security_revision() > INT64_MAX) {
            fail(); throw WorldError(wire::UNAVAILABLE, "Workspace credential status has inconsistent identity context");
        }
        if (result.security_revision() < observed_revision)
            throw WorldError(wire::UNAVAILABLE, "Issuer security state changed during workspace credential check");
        if (observed_revision_initialized && result.security_revision() > observed_revision) bump_epoch();
        observed_revision = result.security_revision();
        observed_revision_initialized = true;
        if (!result.active() || result.actor_revoked() || result.credential_revoked())
            throw WorldError(wire::UNAUTHORIZED, "Workspace bootstrap credential is revoked");
        return result;
    }
    void bootstrap_ready(const id::SignedCredential& credential, const id::ActorCredential& claims,
                         const a::ActorRef& actor, const std::vector<a::ActorRef>& subjects) {
        const auto status = current(credential, claims);
        prepare_subjects(actor, subjects); bootstrap_live(actor);
        if (observed_revision != status.security_revision())
            throw WorldError(wire::UNAVAILABLE, "Issuer security state changed during workspace bootstrap preparation");
    }
    id::BootstrapProfileState profile(const std::string& name, std::uint64_t revision) {
        id::BootstrapProfileRead query; query.set_id(name); query.set_revision(revision);
        identity::validate_bootstrap_read(query, true);
        id::IssuerRequest request; *request.mutable_profile_read() = query;
        return exchange(std::move(request)).profile();
    }
    id::BootstrapGrantTemplate bootstrap_template(const a::BootstrapSelector& selector) {
        require(selector.issuer_id().size() == 32 && !selector.template_id().empty() && selector.template_id().size() <= 128 &&
            selector.template_id().find('\0') == std::string::npos, "Bootstrap template identity is invalid");
        if (selector.issuer_id() != issuer) throw WorldError(wire::FORBIDDEN, "Workspace bootstrap issuer is not configured");
        const auto selected = profile(selector.profile_id(), selector.profile_revision());
        for (const auto& item : selected.profile().grants()) if (item.id() == selector.template_id()) return item;
        throw WorldError(wire::NOT_FOUND, "Workspace bootstrap template is unavailable");
    }
    bool allowed(const std::string& workspace, const a::ActorRef& actor, a::Right right,
                 const std::vector<std::string>& components = {}) {
        if (!identity_active(actor)) return false;
        const auto key = std::make_tuple(workspace, actor.SerializeAsString(), static_cast<int>(right), components);
        const auto cached = decisions.find(key); if (cached != decisions.end()) return cached->second;
        a::Scope scope; *scope.mutable_domain() = domain(workspace);
        for (const auto& component : components) scope.add_components(component);
        const bool allowed = store(workspace).check(actor, scope, right, [this](const auto& value) { return identity_active(value); }).allowed();
        if (decisions.size() >= 4096) decisions.clear();
        decisions.emplace(key, allowed);
        return allowed;
    }
};
WorkspaceAuthority::WorkspaceAuthority(const std::filesystem::path& path, ExecutionStore& execution)
    : impl_(std::make_unique<Impl>(path, execution)) {}
WorkspaceAuthority::~WorkspaceAuthority() = default;
bool WorkspaceAuthority::bound(const std::string& workspace) const { return impl_->stores.contains(workspace); }
pb::ScopedAuthorityCheckReply WorkspaceAuthority::scoped_check(const pb::ScopedAuthorityCheckRequest& request) {
    require(!request.workspace().empty() && request.workspace().size() <= 256 && request.workspace().find('\0') == std::string::npos,
            "Workspace authority identity is invalid");
    require(request.has_principal(), "Authenticated actor reference is required");
    principal(request.principal());
    require(request.components_size() <= 32, "Authority scope exceeds 32 components");
    std::vector<std::string> components;
    components.reserve(request.components_size());
    for (const auto& component : request.components()) {
        require(!component.empty() && component.size() <= 256, "Invalid opaque authority component");
        components.push_back(component);
    }
    constexpr auto registry = "workspace-registry";
    if (request.right() == a::WORKSPACE_CREATE) {
        require(request.workspace() == registry && components.empty(), "Workspace creation authority is registry-scoped");
    } else if (request.workspace() == registry) {
        require(request.right() == a::POLICY_ADMINISTER, "Registry authority cannot grant ordinary workspace rights");
    } else {
        require(request.right() != a::WORKSPACE_CREATE, "Workspace creation authority is registry-scoped");
    }
    try {
        impl_->prepare(request.workspace(), request.principal());
    } catch (const WorldError& error) {
        if (error.code != wire::UNAUTHORIZED || impl_->identity_active(request.principal())) throw;
    }
    a::Scope scope; *scope.mutable_domain() = domain(request.workspace());
    for (const auto& component : components) scope.add_components(component);
    const auto decision = impl_->store(request.workspace()).check(request.principal(), scope, request.right(),
        [this](const auto& actor) { return impl_->identity_active(actor); });
    pb::ScopedAuthorityCheckReply reply; reply.set_allowed(decision.allowed()); reply.set_revision(decision.revision());
    reply.set_incarnation(impl_->incarnation); reply.set_epoch(impl_->epoch);
    return reply;
}
pb::AuthorizationFence WorkspaceAuthority::authorization_fence(const a::ActorRef& actor) {
    principal(actor);
    if (actor.issuer_id() != impl_->issuer) throw WorldError(wire::FORBIDDEN, "Workspace issuer does not match actor identity");
    std::map<std::string, a::ActorRef> subjects;
    for (auto& [workspace, store] : impl_->stores) {
        (void)workspace;
        for (const auto& subject : store->subjects(actor)) subjects.emplace(subject.SerializeAsString(), subject);
    }
    std::vector<a::ActorRef> all; for (const auto& [key, subject] : subjects) { (void)key; all.push_back(subject); }
    impl_->prepare_subjects(actor, all);
    pb::AuthorizationFence reply; reply.set_incarnation(impl_->incarnation); reply.set_epoch(impl_->epoch); return reply;
}
pb::AuthorizationFence WorkspaceAuthority::fence_snapshot() const {
    if (!impl_->ready || impl_->broken || impl_->epoch == UINT64_MAX)
        throw WorldError(wire::UNAVAILABLE, "Workspace authorization fence is unavailable");
    pb::AuthorizationFence reply; reply.set_incarnation(impl_->incarnation); reply.set_epoch(impl_->epoch); return reply;
}
void WorkspaceAuthority::policy_changed() { impl_->bump_epoch(); }
unsigned WorkspaceAuthority::rights(const pb::AccessRequest& request, bool fresh) {
    if (request.workspace() == "workspace-registry")
        throw WorldError(wire::INVALID_ARGUMENT, "Reserved registry has no executable workspace access");
    if (!request.has_principal()) throw WorldError(wire::FORBIDDEN, "Workspace requires authenticated actor authority");
    principal(request.principal());
    require(request.actor() == actor_name(request.principal()), "Workspace actor name does not match authenticated principal");
    impl_->store(request.workspace());
    if (fresh) impl_->prepare(request.workspace(), request.principal());
    const auto has = [&](a::Right right) { return impl_->allowed(request.workspace(), request.principal(), right); };
    unsigned mask = 0;
    if (has(a::WORKSPACE_ACCESS)) mask |= 1u << wire::WORKSPACE_SELECT;
    if (has(a::FILE_BROWSE) && has(a::FILE_READ)) mask |= 1u << wire::FILES_READ;
    const std::array<a::Right, 8> mutation_rights{a::FILE_CREATE, a::FILE_EDIT, a::FILE_RENAME, a::FILE_MOVE, a::FILE_COPY,
                                               a::FILE_TRASH, a::FILE_RESTORE, a::FILE_PERMANENT_DELETE};
    if (std::all_of(mutation_rights.begin(), mutation_rights.end(), has)) mask |= 1u << wire::FILES_WRITE;
    if (has(a::PROCESS_EXECUTE)) mask |= 1u << wire::PROCESS_LAUNCH;
    return mask;
}
ApplicationPermissions WorkspaceAuthority::application_permissions(const pb::ApplicationRef& app,
                                                                   const a::ActorRef& actor, bool fresh) {
    pb::AccessRequest request; request.set_workspace(app.workspace()); request.set_actor(actor_name(actor));
    *request.mutable_principal() = actor;
    ApplicationPermissions result; result.rights = rights(request, fresh);
    const std::vector<std::string> scope{"applications", app.application()};
    result.observe = impl_->allowed(app.workspace(), actor, a::APPLICATION_OBSERVE, scope);
    result.control = impl_->allowed(app.workspace(), actor, a::APPLICATION_CONTROL, scope);
    return result;
}
a::GrantPage WorkspaceAuthority::list(const pb::GrantListRequest& request) {
    impl_->prepare(request.workspace(), request.principal());
    return impl_->store(request.workspace()).list(request.principal(), request.after_id(), request.limit());
}
a::GrantRecord WorkspaceAuthority::delegate(const pb::GrantDelegateRequest& request) {
    require(request.has_expected_revision(), "Expected authority revision is required");
    impl_->prepare(request.workspace(), request.principal(), &request.terms().subject());
    auto& store = impl_->store(request.workspace()); const auto before = store.revision();
    const auto record = store.delegate(request.principal(), request.parent_id(), request.terms(),
        request.expected_revision(), [this](const auto& actor) { return impl_->identity_active(actor); });
    if (store.revision() != before) impl_->bump_epoch(); else impl_->invalidate(); return record;
}
a::GrantRecord WorkspaceAuthority::revoke(const pb::GrantRevokeRequest& request) {
    require(request.has_expected_revision(), "Expected authority revision is required");
    impl_->prepare(request.workspace(), request.principal());
    auto& store = impl_->store(request.workspace()); const auto before = store.revision();
    const auto record = store.revoke(request.principal(), request.id(), request.expected_revision(),
        [this](const auto& actor) { return impl_->identity_active(actor); });
    if (store.revision() != before) impl_->bump_epoch(); else impl_->invalidate(); return record;
}
a::BootstrapBinding WorkspaceAuthority::bind_bootstrap(const pb::BootstrapBindRequest& request) {
    require(request.has_expected_revision(), "Expected authority revision is required");
    auto& store = impl_->store(request.workspace());
    Impl::BootstrapGuard guard(*impl_, request.credential(), request.principal());
    impl_->current(request.credential(), guard.claims);
    const auto item = impl_->bootstrap_template(request.selector());
    impl_->bootstrap_ready(request.credential(), guard.claims, request.principal(), store.subjects(request.principal()));
    const auto before = store.revision();
    const auto result = store.bind_bootstrap(request.principal(), request.selector(), request.parent_id(), item.terms(),
        request.expected_revision(), [this](const auto& actor) { return impl_->identity_active(actor); });
    if (store.revision() != before) impl_->bump_epoch(); else impl_->invalidate(); return result;
}
a::BootstrapBinding WorkspaceAuthority::revoke_bootstrap(const pb::BootstrapRevokeRequest& request) {
    require(request.has_expected_revision(), "Expected authority revision is required");
    auto& store = impl_->store(request.workspace());
    Impl::BootstrapGuard guard(*impl_, request.credential(), request.principal());
    impl_->bootstrap_ready(request.credential(), guard.claims, request.principal(), store.subjects(request.principal()));
    const auto before = store.revision();
    const auto result = store.revoke_bootstrap(request.principal(), request.id(), request.expected_revision(),
        [this](const auto& actor) { return impl_->identity_active(actor); });
    if (store.revision() != before) impl_->bump_epoch(); else impl_->invalidate(); return result;
}
a::BootstrapReport WorkspaceAuthority::apply_bootstrap(const pb::BootstrapApplyRequest& request) {
    auto& store = impl_->store(request.workspace());
    Impl::BootstrapGuard guard(*impl_, request.credential(), request.principal());
    impl_->current(request.credential(), guard.claims);
    a::BootstrapReport report; report.set_issuer_id(impl_->issuer); *report.mutable_domain() = domain(request.workspace());
    report.set_profile_id(guard.claims.bootstrap_profile()); report.set_profile_revision(guard.claims.bootstrap_profile_revision());
    id::BootstrapProfileState selected;
    try { selected = impl_->profile(report.profile_id(), report.profile_revision()); }
    catch (const WorldError& error) {
        if (error.code != wire::NOT_FOUND) throw;
        impl_->bootstrap_live(request.principal());
        report.mutable_error()->set_code(error.code); report.mutable_error()->set_message(error.what()); return report;
    }
    impl_->bootstrap_ready(request.credential(), guard.claims, request.principal(), store.subjects(request.principal()));
    for (const auto& item : selected.profile().grants()) {
        if (item.terms().scope().domain().kind() != a::WORKSPACE || item.terms().scope().domain().id() != request.workspace()) continue;
        auto* result = report.add_results(); result->set_template_id(item.id());
        a::BootstrapSelector selector; selector.set_issuer_id(impl_->issuer); selector.set_profile_id(report.profile_id());
        selector.set_profile_revision(report.profile_revision()); selector.set_template_id(item.id());
        try {
            const auto binding = store.bootstrap_binding(selector);
            impl_->bootstrap_ready(request.credential(), guard.claims, request.principal(),
                store.bootstrap_subjects({binding.id()}, request.principal()));
            const auto before = store.revision();
            *result->mutable_grant() = store.materialize_bootstrap(binding.id(), request.principal(), selector, item.terms(),
                [this](const auto& actor) { return impl_->identity_active(actor); });
            if (store.revision() != before) impl_->bump_epoch(); else impl_->invalidate();
        } catch (const WorldError& error) {
            if (error.code == wire::UNAUTHORIZED || error.code == wire::UNAVAILABLE) throw;
            result->mutable_error()->set_code(error.code); result->mutable_error()->set_message(error.what());
        }
    }
    impl_->bootstrap_live(request.principal()); return report;
}
void WorkspaceAuthority::tick() { impl_->tick(); }
void WorkspaceAuthority::settle(bool effects_settled) { impl_->settle(effects_settled); }
void WorkspaceAuthority::shutdown(const std::function<void()>& reconcile) { impl_->shutdown(reconcile); }
std::uint64_t WorkspaceAuthority::cancellation_sequence() const { return impl_->cancellation_sequence; }
void WorkspaceAuthority::keepalive() { impl_->keepalive(); }
bool WorkspaceAuthority::consume_changes() { const bool result = impl_->changed; impl_->changed = false; return result; }
void WorkspaceAuthority::set_observer(std::function<void()> observer) { impl_->observer = std::move(observer); }
void initialize_workspace_authority(const std::filesystem::path& path, ExecutionStore& execution) {
    pb::WorkspaceAuthoritySeed seed;
    require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(path), &seed), "Invalid workspace authority seed");
    validate_message(seed); require(seed.protocol_version() == 1, "Unsupported workspace authority seed version");
    if (seed.workspace() == "workspace-registry") {
        try {
            execution.workspace_get(seed.workspace());
            throw std::invalid_argument("Reserved workspace registry cannot have execution state");
        } catch (const WorldError& error) {
            if (error.code != wire::NOT_FOUND) throw;
        }
    } else execution.workspace_get(seed.workspace());
    authority::Store store(seed.authority_directory(), domain(seed.workspace()), true);
    store.install_roots({seed.roots().begin(), seed.roots().end()}, 0);
}
}
