#pragma once
#include "core/protocol.hpp"
#include "platform/render_process.hpp"
#include <filesystem>
#include <chrono>
#include <functional>
#include <memory>
#include <sys/types.h>
namespace spatial {
// Owned production compositor; one bounded in-flight scene, scheduler serviced while waiting.
class RenderWorker {
public:
    RenderWorker(const std::filesystem::path& executable, const std::filesystem::path& state, std::string display, int native_fd = -1);
    // Borrows descriptors. The world owns only its channel, never the compositor.
    RenderWorker(const std::filesystem::path& state, std::string display, int channel, int compositor_pidfd,
                 pid_t compositor_pid, pid_t channel_creator, int native_fd);
    ~RenderWorker();
    RenderWorker(const RenderWorker&) = delete;
    RenderWorker& operator=(const RenderWorker&) = delete;
    wire::FrameReply render(const wire::RenderScene& scene, std::uint32_t width, std::uint32_t height, bool capture);
    wire::RenderVisibility observe(const wire::RenderScene& scene, std::uint32_t width, std::uint32_t height, bool include_pixels = false);
    wire::NativeSnapshot synchronize();
    wire::NativeActorInput input(const wire::RendererInputCommand& command, const std::function<void()>& before_send = {});
    void check_alive() const;
    bool busy() const { return busy_; }
    void set_tick(std::function<void()> tick) { tick_ = std::move(tick); }
    pid_t pid() const;
    const std::string& display() const;
private:
    void wait(short events);
    void read_all(char* out, std::size_t size);
    wire::RenderResult receive();
    wire::RenderResult exchange(wire::RenderCommand command, const std::function<void()>& before_send = {}, int capture_fd = -1);
    struct Shared;
    std::unique_ptr<Shared> shared_;
    std::unique_ptr<RenderProcess> process_;
    // Borrowed endpoints. RenderProcess closes and reaps them.
    int socket_ = -1, pidfd_ = -1;
    std::uint64_t sequence_ = 0;
    std::uint64_t frame_id_ = 0;
    bool busy_ = false;
    std::function<void()> tick_ = [] {};
    std::chrono::steady_clock::time_point deadline_;
};
}
