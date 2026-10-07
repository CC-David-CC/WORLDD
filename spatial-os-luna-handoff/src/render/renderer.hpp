#pragma once
#include "core/protocol.hpp"
#include <filesystem>
#include <memory>
namespace spatial {
class Renderer {
public:
    Renderer(const std::string& display, const std::filesystem::path& state);
    ~Renderer();
    wire::RendererReady ready() const;
    wire::FrameReply draw(const wire::RenderCommand& command);
    int connection() const;
    void events();
    wire::NativeSnapshot synchronize();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
