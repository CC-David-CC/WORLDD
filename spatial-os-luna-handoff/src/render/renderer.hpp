#pragma once
#include "core/protocol.hpp"
#include "compositor_native.pb.h"
#include <filesystem>
#include <memory>
namespace spatial {
class Renderer {
public:
    Renderer(const std::string& display, const std::filesystem::path& state, const render::v1::NativeCompositorConfig* native = nullptr);
    ~Renderer();
    wire::RendererReady ready() const;
    wire::FrameReply draw(const wire::RenderCommand& command);
    wire::RenderVisibility observe(const wire::RenderCommand& command);
    int connection() const;
    void events();
    wire::NativeSnapshot synchronize();
    wire::NativeActorInput input(const wire::RendererInputCommand& command);
    void bind_input(const render::v1::WorldPresentationBinding&, int sealed_config_fd);
    void retire_input(const render::v1::WorldPresentationBinding&);
    wire::NativeActorInput input(const render::v1::WorldPresentationBinding&, const wire::RendererInputCommand&);
    wire::NativeSnapshot synchronize(const render::v1::WorldPresentationBinding&);
private:
    wire::FrameReply draw_scene(const wire::RenderCommand& command, wire::RenderVisibility* visibility);
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
