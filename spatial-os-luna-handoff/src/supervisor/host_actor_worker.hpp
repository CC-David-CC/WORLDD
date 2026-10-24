#pragma once
#include "supervisor/host_actor_controller.hpp"
#include <atomic>
#include <condition_variable>
#include <deque>
#include <future>
#include <thread>
#include <type_traits>
#include <stdexcept>
#include <utility>
#include <optional>

namespace spatial::supervisor {
// One actor owner. Private host IO and capture must never perform actor exchanges.
class HostActorWorker final {
public:
    struct CaptureBinding final {
        const std::uint64_t actor_generation, capture_generation;
        const std::weak_ptr<const ActorSessionInterrupt> interrupt;
        bool retired() const noexcept { return retired_.load(); }
        CaptureBinding(std::uint64_t actor, std::uint64_t capture,
                       std::weak_ptr<const ActorSessionInterrupt> handle)
            : actor_generation(actor), capture_generation(capture), interrupt(std::move(handle)) {}
    private:
        friend class HostActorWorker;
        std::atomic<bool> retired_{false};
    };
    using Binding = std::shared_ptr<CaptureBinding>;
    explicit HostActorWorker(HostActorController&);
    ~HostActorWorker();
    HostActorWorker(const HostActorWorker&) = delete;
    HostActorWorker& operator=(const HostActorWorker&) = delete;
    bool failed() const noexcept { return failed_.load(); }
    Binding capture_binding() const;

    template<class F> auto call(F&& function, const Binding& urgent = {}, bool cancellation = false) -> std::invoke_result_t<F> {
        if (std::this_thread::get_id() == worker_.get_id())
            throw std::runtime_error("Actor worker cannot wait on itself");
        return submit(std::forward<F>(function), urgent, cancellation).get();
    }
    template<class F> auto submit(F&& function, const Binding& urgent = {}, bool cancellation = false)
        -> std::future<std::invoke_result_t<F>> {
        using Result = std::invoke_result_t<F>;
        static_assert(!std::is_reference_v<Result>, "Queued actor calls must return owned values");
        auto promise = std::make_shared<std::promise<Result>>();
        auto future = promise->get_future();
        auto owned = std::make_shared<std::decay_t<F>>(std::forward<F>(function));
        Work work;
        work.urgent_binding = urgent; work.cancellation = cancellation;
        work.execute = [this, promise, owned] {
            try {
                if constexpr (std::is_void_v<Result>) {
                    (*owned)();
                    settle_context();
                    controller_.synchronize_controls_presentation();
                    if(!complete([&] {promise->set_value();},true)) {
                        settle_context();throw std::runtime_error("Actor result lost its interruption barrier");
                    }
                } else {
                    auto result = (*owned)();
                    settle_context();
                    controller_.synchronize_controls_presentation();
                    if(!complete([&] {promise->set_value(std::move(result));},true)) {
                        settle_context();throw std::runtime_error("Actor result lost its interruption barrier");
                    }
                }
            } catch (...) {
                const auto failure = std::current_exception();
                try { settle_context(); controller_.synchronize_controls_presentation(); } catch (...) {}
                (void)complete([&] {promise->set_exception(failure);},false);
            }
        };
        if (!enqueue(std::move(work))) throw std::runtime_error("Host actor queue is full or stopped");
        return future;
    }
    // Nonblocking receiver API. Keep the exact binding obtained at stream creation.
    bool post_device(const Binding&, control::v1::CapturedDeviceEvent);
    control::v1::ControlStatus device_event(const Binding&, const control::v1::CapturedDeviceEvent&);
    identity::v1::HostActorTransitionResult transition_proposed(std::uint64_t,
        const identity::v1::HostActorTransitionRequest&);
    bool request_cancel(const Binding&) noexcept;

private:
    struct Work { std::function<void()> execute; Binding urgent_binding; bool cancellation = false; };
    struct Flight {
        std::uint64_t serial;
        std::int64_t started;
        Binding binding;
        bool urgent;
        bool release_satisfied = false;
    };
    bool enqueue(Work);
    bool current(const Binding&) const;
    void run(std::promise<void> ready) noexcept;
    void watch() noexcept;
    void refresh_context();
    void settle_context();
    bool complete(const std::function<void()>& publish, bool success);
    void begin_exchange(const Binding& urgent = {}, bool cancellation = false);
    void end_exchange() noexcept;
    void stop() noexcept;
    HostActorController& controller_;
    std::mutex queue_mutex_;
    mutable std::mutex watch_mutex_;
    std::condition_variable changed_;
    std::deque<Work> queue_;
    Binding binding_, pending_cancel_;
    std::optional<Flight> flight_;
    std::uint64_t exchange_serial_ = 0;
    std::atomic<bool> stopping_{false}, failed_{false}, cancel_pending_{false};
    std::thread worker_, watchdog_;
};
}
