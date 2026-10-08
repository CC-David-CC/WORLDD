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
    void tick();
    void keepalive();
    bool consume_changes();
    void set_observer(std::function<void()> observer);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
void initialize_workspace_authority(const std::filesystem::path& seed, ExecutionStore& store);
}
