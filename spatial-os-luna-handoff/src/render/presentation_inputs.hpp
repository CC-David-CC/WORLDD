#pragma once
#include "compositor_native.pb.h"
#include "core/protocol.hpp"
#include <X11/Xlib.h>
#include <memory>
namespace spatial {
class XSurfaces;
// Private compositor state. Every world binding has independent native seats.
class PresentationInputs final {
public:
    PresentationInputs(Display*, XSurfaces&, std::string server_id);
    ~PresentationInputs();
    PresentationInputs(const PresentationInputs&) = delete;
    PresentationInputs& operator=(const PresentationInputs&) = delete;
    static void validate_binding(const render::v1::WorldPresentationBinding&);
    void reserve(const render::v1::WorldPresentationBinding&);
    void initialize(const render::v1::WorldPresentationBinding&, int sealed_config_fd);
    void bind(const render::v1::WorldPresentationBinding&, int sealed_config_fd);
    void retire(const render::v1::WorldPresentationBinding&);
    wire::NativeActorInput input(const render::v1::WorldPresentationBinding&, const wire::RendererInputCommand&);
    void snapshot(const render::v1::WorldPresentationBinding&, wire::NativeSnapshot&);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
