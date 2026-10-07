#pragma once
#include "core/protocol.hpp"
#include <filesystem>
#include <chrono>
#include <functional>
#include <sys/types.h>
namespace spatial {
// Owned production compositor; one bounded in-flight scene, scheduler serviced while waiting.
class RenderWorker {
public:
    RenderWorker(const std::filesystem::path& executable, const std::filesystem::path& state, std::string display);
    ~RenderWorker();
    RenderWorker(const RenderWorker&) = delete;
    RenderWorker& operator=(const RenderWorker&) = delete;
    wire::FrameReply render(const wire::RenderScene& scene, std::uint32_t width, std::uint32_t height, bool capture);
    wire::NativeSnapshot synchronize();
    wire::NativeActorInput input(const wire::RendererInputCommand& command);
    void check_alive() const;
    void set_tick(std::function<void()> tick) { tick_ = std::move(tick); }
    pid_t pid() const { return pid_; }
    const std::string& display() const { return display_; }
private:
    void wait(short events);
    void read_all(char* out, std::size_t size);
    wire::RenderResult receive();
    wire::RenderResult exchange(wire::RenderCommand command);
    int socket_ = -1, pidfd_ = -1;
    pid_t pid_ = -1;
    std::string display_;
    std::uint64_t sequence_ = 0;
    std::uint64_t frame_id_ = 0;
    std::function<void()> tick_ = [] {};
    std::chrono::steady_clock::time_point deadline_;
};
}
