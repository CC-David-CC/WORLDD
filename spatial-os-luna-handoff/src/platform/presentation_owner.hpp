#pragma once
#include "presentation.pb.h"
#include "platform/process_handle.hpp"
#include <filesystem>
#include <memory>
namespace spatial {
// Private host-session API. No actor or world receives the control channel.
class PresentationOwner final {
public:
    PresentationOwner(const std::filesystem::path& executable,const std::filesystem::path& state,int borrowed_native);
    ~PresentationOwner();
    PresentationOwner(const PresentationOwner&)=delete;
    PresentationOwner& operator=(const PresentationOwner&)=delete;
    pid_t pid() const;
    int pidfd() const;
    render::v1::PresentationState attach(const render::v1::WorldPresentationBinding&,int borrowed_channel,const ProcessHandle& world,const workspace::v1::ControlOwner&,const supervisor::v1::ProcessScopeIdentity&);
    render::v1::PresentationState select(const render::v1::WorldPresentationBinding&);
    render::v1::PresentationState inspect(const render::v1::WorldPresentationBinding&);
    render::v1::PresentationState detach(const render::v1::WorldPresentationBinding&);
    render::v1::PresentationState recover(const render::v1::WorldPresentationBinding&,const supervisor::v1::ProcessScopeStopped&,const supervisor::v1::NativeScopeStopped&);
    void shutdown();
private:
    struct Impl;std::unique_ptr<Impl> impl_;
};
}
