#pragma once
#include "broker.pb.h"
#include <filesystem>
#include <memory>
#include <functional>

namespace spatial {
class ExecutionStore;
struct ApplicationPermissions { unsigned rights = 0; bool observe = false, control = false; };
// Called only on the broker authority thread. Runtime callbacks perform no issuer I/O.
class WorkspaceAuthority {
public:
    WorkspaceAuthority(const std::filesystem::path& config, ExecutionStore& store);
    ~WorkspaceAuthority();
    bool bound(const std::string& workspace) const;
    unsigned rights(const workspace::v1::AccessRequest&, bool fresh);
    ApplicationPermissions application_permissions(const workspace::v1::ApplicationRef&,
        const authority::v1::ActorRef&, bool fresh);
    authority::v1::GrantPage list(const workspace::v1::GrantListRequest&);
    authority::v1::GrantRecord delegate(const workspace::v1::GrantDelegateRequest&);
    authority::v1::GrantRecord revoke(const workspace::v1::GrantRevokeRequest&);
    authority::v1::BootstrapBinding bind_bootstrap(const workspace::v1::BootstrapBindRequest&);
    authority::v1::BootstrapBinding revoke_bootstrap(const workspace::v1::BootstrapRevokeRequest&);
    authority::v1::BootstrapReport apply_bootstrap(const workspace::v1::BootstrapApplyRequest&);
    void tick();
    void settle(bool effects_settled);
    void shutdown(const std::function<void()>& reconcile);
    std::uint64_t cancellation_sequence() const;
    void keepalive();
    bool consume_changes();
    void set_observer(std::function<void()> observer);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
void initialize_workspace_authority(const std::filesystem::path& seed, ExecutionStore& store);
}
