#include "workspace/authority.hpp"
#include "workspace/store.hpp"
#include "authority/store.hpp"
#include "identity/issuer_client.hpp"
#include "identity/issuer_watch.hpp"
#include "identity/tls_support.hpp"
#include "world/store.hpp"
#include <google/protobuf/text_format.h>
#include <algorithm>
#include <array>
#include <map>
#include <optional>
#include <set>
#include <thread>
#include <tuple>

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
    std::unique_ptr<identity::IssuerWatcher> watcher;
    std::string issuer;
    std::map<std::string, bool> active;
    std::set<std::string> revoked;
    std::map<std::tuple<std::string, std::string, int, std::vector<std::string>>, bool> decisions;
    std::uint64_t observed_revision = 0;
    bool ready = false, broken = false, changed = true;
    Clock::time_point last_exchange = Clock::now();
    std::function<void()> observer = [] {};

    Impl(const std::filesystem::path& path, ExecutionStore& execution) {
        pb::WorkspaceAuthorityConfig config;
        require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(path), &config), "Invalid workspace authority configuration");
        validate_message(config);
        require(config.protocol_version() == 1 && config.bindings_size() > 0 && config.bindings_size() <= 64, "Configure one to 64 workspace authorities");
        for (const auto& binding : config.bindings()) {
            require(!stores.contains(binding.workspace()), "Duplicate workspace authority binding");
            execution.workspace_get(binding.workspace());
            stores.emplace(binding.workspace(), std::make_unique<authority::Store>(binding.authority_directory(), domain(binding.workspace()), false));
        }
        client = std::make_unique<identity::IssuerClient>(config.issuer_client_config());
        id::IssuerRequest request; request.set_protocol_version(1); request.mutable_info();
        const auto reply = client->call(request);
        require(reply.has_info(), "Workspace issuer is unavailable"); issuer = reply.info().issuer_id();
        watcher = std::make_unique<identity::IssuerWatcher>(config.issuer_client_config());
        client->set_tick([this] { tick(); });
        const auto deadline = Clock::now()+client->timeout()+std::chrono::milliseconds(100);
        while (!ready && !broken && Clock::now() < deadline) { tick(); std::this_thread::sleep_for(std::chrono::milliseconds(1)); }
        require(ready && !broken, "Workspace revocation baseline is unavailable");
    }
    void invalidate() { decisions.clear(); changed = true; }
    void fail() { broken = true; active.clear(); invalidate(); }
    void tick() {
        if (broken || !watcher) { observer(); return; }
        if (watcher->failed()) { fail(); observer(); return; }
        while (const auto update = watcher->poll()) {
            if (update->has_error() || update->batch().issuer_id() != issuer) { fail(); observer(); return; }
            const auto& batch = update->batch(); observed_revision = std::max(observed_revision, batch.head_revision());
            if (batch.initial()) ready = true;
            for (const auto& event : batch.events()) {
                if (event.target().kind() == id::ACTOR_TARGET) {
                    revoked.insert(event.target().id()); active[event.target().id()] = false; invalidate();
                }
            }
        }
        observer();
    }
    id::IssuerReply exchange(id::IssuerRequest request) {
        if (broken) throw WorldError(wire::UNAVAILABLE, "Workspace identity service is disabled");
        request.set_protocol_version(1); id::IssuerReply reply;
        try { reply = client->call(std::move(request)); }
        catch (const std::exception&) { fail(); throw WorldError(wire::UNAVAILABLE, "Workspace issuer connection failed"); }
        if (broken) throw WorldError(wire::UNAVAILABLE, "Workspace identity service is disabled");
        last_exchange = Clock::now();
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
        return !broken && actor.issuer_id() == issuer && !revoked.contains(actor.actor_id()) && found != active.end() && found->second;
    }
    void prepare(const std::string& workspace, const a::ActorRef& actor, const a::ActorRef* extra = nullptr) {
        principal(actor);
        if (actor.issuer_id() != issuer) throw WorldError(wire::FORBIDDEN, "Workspace issuer does not match actor identity");
        auto subjects = store(workspace).subjects(actor);
        if (extra && extra->actor_id().size() == 32 && extra->issuer_id().size() == 32) subjects.push_back(*extra);
        std::set<std::string> ids;
        for (const auto& subject : subjects) if (subject.issuer_id() == issuer) ids.insert(subject.actor_id());
        std::vector<id::ActorState> states; std::optional<std::uint64_t> revision;
        auto next = ids.begin();
        while (next != ids.end()) {
            id::IssuerRequest request; auto* query = request.mutable_actor_status();
            while (next != ids.end() && query->actor_ids_size() < 128) query->add_actor_ids(*next++);
            const auto batch = exchange(std::move(request)).actor_status();
            if ((revision && *revision != batch.security_revision()) || batch.security_revision() < observed_revision) {
                fail(); throw WorldError(wire::UNAVAILABLE, "Actor status changed during workspace authority preparation");
            }
            revision = batch.security_revision(); states.insert(states.end(), batch.actors().begin(), batch.actors().end());
        }
        if (revision) observed_revision = std::max(observed_revision, *revision);
        for (const auto& state : states) {
            if (state.revoked()) revoked.insert(state.actor_id());
            active[state.actor_id()] = state.active() && !revoked.contains(state.actor_id());
        }
        invalidate();
        if (!identity_active(actor)) throw WorldError(wire::UNAUTHORIZED, "Workspace actor identity is inactive");
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
unsigned WorkspaceAuthority::rights(const pb::AccessRequest& request, bool fresh) {
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
    const auto record = impl_->store(request.workspace()).delegate(request.principal(), request.parent_id(), request.terms(),
        request.expected_revision(), [this](const auto& actor) { return impl_->identity_active(actor); });
    impl_->invalidate(); return record;
}
a::GrantRecord WorkspaceAuthority::revoke(const pb::GrantRevokeRequest& request) {
    require(request.has_expected_revision(), "Expected authority revision is required");
    impl_->prepare(request.workspace(), request.principal());
    const auto record = impl_->store(request.workspace()).revoke(request.principal(), request.id(), request.expected_revision(),
        [this](const auto& actor) { return impl_->identity_active(actor); });
    impl_->invalidate(); return record;
}
void WorkspaceAuthority::tick() { impl_->tick(); }
void WorkspaceAuthority::keepalive() {
    if (!impl_->broken && Clock::now()-impl_->last_exchange >= std::chrono::seconds(1)) {
        id::IssuerRequest request; request.mutable_info();
        try { impl_->exchange(std::move(request)); } catch (const WorldError&) { impl_->fail(); }
    }
}
bool WorkspaceAuthority::consume_changes() { const bool result = impl_->changed; impl_->changed = false; return result; }
void WorkspaceAuthority::set_observer(std::function<void()> observer) { impl_->observer = std::move(observer); }
void initialize_workspace_authority(const std::filesystem::path& path, ExecutionStore& execution) {
    pb::WorkspaceAuthoritySeed seed;
    require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(path), &seed), "Invalid workspace authority seed");
    validate_message(seed); require(seed.protocol_version() == 1, "Unsupported workspace authority seed version");
    execution.workspace_get(seed.workspace());
    authority::Store store(seed.authority_directory(), domain(seed.workspace()), true);
    store.install_roots({seed.roots().begin(), seed.roots().end()}, 0);
}
}
