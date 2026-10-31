#pragma once
#include "supervisor/host_actor_controller.hpp"
#include "control/device_stream.hpp"
#include "core/stop_trace.hpp"
#include <atomic>
#include <condition_variable>
#include <deque>
#include <future>
#include <memory>
#include <thread>
#include <type_traits>
#include <stdexcept>
#include <utility>
#include <optional>

namespace spatial::supervisor {
class HostCaptureReceiver;
// An exact actor flight was interrupted. Its host and worlds remain owned.
class HostActorContextInterrupted final : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};
// One actor owner. Private host IO and capture must never perform actor exchanges.
class HostActorWorker final {
public:
    struct CaptureBinding final {
        const std::uint64_t actor_generation, capture_generation;
        const std::weak_ptr<const ActorSessionInterrupt> interrupt;
        bool retired() const noexcept { return retired_.load(); }
        std::uint64_t dispatched_sequence() const noexcept {
            return dispatched_sequence_.load(std::memory_order_acquire);
        }
        std::uint64_t presented_sequence() const noexcept {
            return presented_sequence_.load(std::memory_order_acquire);
        }
        std::uint64_t accepted_sequence() const noexcept {
            return accepted_sequence_.load(std::memory_order_acquire);
        }
        CaptureBinding(std::uint64_t actor, std::uint64_t capture,
                       std::weak_ptr<const ActorSessionInterrupt> handle)
            : actor_generation(actor), capture_generation(capture), interrupt(std::move(handle)) {}
    private:
        friend class HostActorWorker;
        std::atomic<bool> retired_{false};
        std::atomic<std::uint64_t> presented_sequence_{0};
        std::atomic<std::uint64_t> dispatched_sequence_{0};
        std::atomic<std::uint64_t> accepted_sequence_{0};
        std::atomic<std::uint64_t> observed_sequence_{0};
        std::atomic<bool> capture_active_{false};
        std::atomic<std::uint32_t> source_slot_{0};
        std::shared_ptr<control::MotionIntent> motion_intent_;
    };
    using Binding = std::shared_ptr<CaptureBinding>;
    struct CaptureEndpoint {std::unique_ptr<control::DeviceStream> stream;std::uint32_t source_slot=0;};
    using CaptureStart=std::function<CaptureEndpoint(const Binding&)>;
    using CaptureStop=std::function<void(const Binding&)>;
    using CaptureStopProgress=std::function<bool(const Binding&)>;
    explicit HostActorWorker(HostActorController&,CaptureStart = {},CaptureStop = {},CaptureStopProgress = {});
    ~HostActorWorker();
    HostActorWorker(const HostActorWorker&) = delete;
    HostActorWorker& operator=(const HostActorWorker&) = delete;
    bool failed() const noexcept { return failed_.load(); }
    Binding capture_binding() const;
    bool shutdown() noexcept;
    control::v1::ControlStatus control_status(std::uint64_t) const;

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
        work.execute = [this, promise, owned, cancellation] {
            stop_trace::Scope trace(cancellation?"host":"host_action",exchange_serial_);
            if(cancellation && stop_trace::enabled()) {
                std::lock_guard lock(watch_mutex_);
                if(flight_) {stop_trace::mark("flight_started_ns",flight_->started);
                    if(flight_->binding) {stop_trace::mark("actor_generation",flight_->binding->actor_generation);
                        stop_trace::mark("capture_generation",flight_->binding->capture_generation);}}
            }
            try {
                if constexpr (std::is_void_v<Result>) {
                    (*owned)();
                    stop_trace::mark("operation_returned");
                    settle_context();stop_trace::mark("settlement_returned");
                    synchronize_capture();stop_trace::mark("capture_returned");
                    refresh_status_snapshot();
                    if(!complete([&] {promise->set_value();},true)) {
                        stop_trace::mark("publication_interrupted");
                        settle_context();throw HostActorContextInterrupted("Actor result lost its interruption barrier");
                    }
                    stop_trace::mark("published");
                } else {
                    auto result = (*owned)();
                    stop_trace::mark("operation_returned");
                    settle_context();stop_trace::mark("settlement_returned");
                    synchronize_capture();stop_trace::mark("capture_returned");
                    refresh_status_snapshot();
                    if(!complete([&] {promise->set_value(std::move(result));},true)) {
                        stop_trace::mark("publication_interrupted");
                        settle_context();throw HostActorContextInterrupted("Actor result lost its interruption barrier");
                    }
                    stop_trace::mark("published");
                }
            } catch (...) {
                stop_trace::mark("failed");
                const auto failure = std::current_exception();
                try { settle_context(); } catch (...) {}
                complete_failure(failure,[&](std::exception_ptr error) {promise->set_exception(error);});
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
    void capture_failed(const Binding&) noexcept;

private:
    struct StatusSnapshot {
        std::uint64_t generation = 0;
        std::uint64_t revision = 0;
        control::v1::ControlStatus status;
    };
    struct Work {
        std::function<void()> execute;
        Binding urgent_binding;
        Binding capture_binding;
        bool cancellation = false;
        bool capture_heartbeat = false; // Diagnostic metadata. Never changes urgency.
        bool capture_motion = false;
        bool capture_relative_motion = false;
        std::string capture_source;
        std::uint64_t source_generation = 0, packet_sequence = 0;
        std::shared_ptr<control::v1::CapturedDeviceEvent> capture_packet;
    };
    struct Flight {
        std::uint64_t serial;
        std::int64_t started;
        Binding binding;
        bool urgent;
        const char* origin;
        const char* phase = "starting";
        bool release_satisfied = false;
        bool interrupted = false;
    };
    bool enqueue(Work);
    bool current(const Binding&) const;
    void run(std::promise<void> ready) noexcept;
    void run_presentation() noexcept;
    void watch() noexcept;
    void refresh_context();
    void settle_context();
    void synchronize_capture();
    bool retire_capture();
    bool complete(const std::function<void()>& publish, bool success);
    void complete_failure(std::exception_ptr, const std::function<void(std::exception_ptr)>& publish);
    void begin_exchange(const Binding& urgent = {}, bool cancellation = false, const char* origin = "maintenance");
    void set_flight_phase(const char* phase) noexcept;
    void end_exchange() noexcept;
    void stop() noexcept;
    void remember_status(std::uint64_t, std::uint64_t, const control::v1::ControlStatus&) const noexcept;
    void refresh_status_snapshot() noexcept;
    HostActorController& controller_;
    CaptureStart start_capture_;
    CaptureStop stop_capture_;
    CaptureStopProgress poll_stop_capture_;
    Binding capture_owner_;
    std::unique_ptr<HostCaptureReceiver> receiver_;
    std::atomic<bool> capture_cleanup_complete_{true};
    std::mutex queue_mutex_;
    mutable std::mutex watch_mutex_;
    std::condition_variable changed_;
    std::deque<Work> queue_;
    Binding binding_, pending_cancel_;
    std::optional<Flight> flight_;
    mutable std::atomic<std::shared_ptr<const StatusSnapshot>> status_snapshot_;
    std::uint64_t exchange_serial_ = 0;
    std::atomic<bool> stopping_{false}, failed_{false}, cancel_pending_{false};
    std::thread worker_, presentation_worker_, watchdog_;
};
}
