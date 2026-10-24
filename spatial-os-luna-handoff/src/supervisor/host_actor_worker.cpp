#include "supervisor/host_actor_worker.hpp"
#include <chrono>
#include <algorithm>
#include <iostream>

namespace spatial::supervisor {
namespace {
using Clock = std::chrono::steady_clock;
constexpr auto interval = std::chrono::milliseconds(16);
constexpr auto watchdog_limit = std::chrono::milliseconds(350);
std::int64_t nanoseconds() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
}
}
HostActorWorker::HostActorWorker(HostActorController& controller) : controller_(controller) {
    std::promise<void> ready;
    auto future = ready.get_future();
    worker_ = std::thread([this, ready = std::move(ready)]() mutable { run(std::move(ready)); });
    try {
        future.get();
        watchdog_ = std::thread([this] { watch(); });
    } catch (...) {
        stop();
        if (worker_.joinable()) worker_.join();
        throw;
    }
}
HostActorWorker::~HostActorWorker() {
    stop();
    if (worker_.joinable()) worker_.join();
    if (watchdog_.joinable()) watchdog_.join();
}
HostActorWorker::Binding HostActorWorker::capture_binding() const {
    std::lock_guard lock(watch_mutex_); return binding_;
}
bool HostActorWorker::current(const Binding& binding) const {
    std::lock_guard lock(watch_mutex_);
    return binding && binding_ == binding && !binding->retired();
}
void HostActorWorker::stop() noexcept {
    stopping_.store(true);
    changed_.notify_all();
    std::shared_ptr<const ActorSessionInterrupt> interrupt;
    {
        std::lock_guard lock(watch_mutex_);
        const auto context = binding_ ? binding_ : flight_ ? flight_->binding : Binding{};
        if (context) { context->retired_.store(true); interrupt = context->interrupt.lock(); }
    }
    if (interrupt) interrupt->request();
}
bool HostActorWorker::enqueue(Work work) {
    std::lock_guard lock(queue_mutex_);
    if (stopping_ || queue_.size() >= 128) return false;
    queue_.push_back(std::move(work));
    changed_.notify_one();
    return true;
}
bool HostActorWorker::post_device(const Binding& binding, control::v1::CapturedDeviceEvent packet) {
    if (!current(binding)) return false;
    if (packet.capture_generation() != binding->capture_generation) {
        (void)request_cancel(binding); return false;
    }
    Work work; work.urgent_binding = binding;
    work.execute = [this, binding, packet = std::move(packet)] {
        if (!current(binding) || !controller_.control_available()) return;
        try { (void)device_event(binding, packet); }
        catch (const std::exception&) {
            // Malformed current capture is terminal for that binding. A stale
            // binding cannot enter this path or cancel an adopted actor.
            (void)request_cancel(binding);
            std::cerr << "Captured actor input retired its binding\n";
        }
        controller_.synchronize_controls_presentation();
    };
    if (enqueue(std::move(work))) return true;
    (void)request_cancel(binding);
    return false;
}
control::v1::ControlStatus HostActorWorker::device_event(const Binding& binding,
        const control::v1::CapturedDeviceEvent& packet) {
    if(std::this_thread::get_id()!=worker_.get_id() || !current(binding) ||
        packet.capture_generation()!=binding->capture_generation)
        throw std::invalid_argument("Retired or mismatched human capture binding");
    return controller_.device_event(binding->actor_generation,packet);
}
bool HostActorWorker::request_cancel(const Binding& binding) noexcept {
    std::lock_guard lock(watch_mutex_);
    if (!binding || binding_ != binding) return false;
    if (binding->retired_.exchange(true)) return true;
    pending_cancel_ = binding;
    cancel_pending_.store(true);
    changed_.notify_all();
    return true;
}
identity::v1::HostActorTransitionResult HostActorWorker::transition_proposed(std::uint64_t generation,
        const identity::v1::HostActorTransitionRequest& proposal) {
    if (std::this_thread::get_id() != worker_.get_id())
        throw std::invalid_argument("Actor transition must run on its worker");
    const auto binding = capture_binding();
    if (!binding || binding->actor_generation != generation)
        throw std::invalid_argument("Stale transition capture binding");
    return controller_.transition_proposed(generation, proposal, [this, binding](auto phase) {
        std::lock_guard lock(watch_mutex_);
        if (binding_ != binding || !flight_ || flight_->binding != binding)
            throw std::runtime_error("Transition lost its exact capture flight");
        if (const auto interrupt = binding->interrupt.lock(); interrupt && interrupt->requested())
            throw std::runtime_error("Transition input release was interrupted");
        if (phase == HostActorController::TransitionPhase::InputReleased) {
            // Only a checked Stop reaches this hook. Remaining exchanges keep
            // their ordinary transport deadlines, without old capture urgency.
            flight_->release_satisfied = true;
        } else {
            if (!flight_->release_satisfied)
                throw std::runtime_error("Capture retirement preceded checked release");
            binding->retired_.store(true);
        }
    });
}
void HostActorWorker::refresh_context() {
    Binding next;
    if (controller_.control_available()) {
        const auto generation = controller_.generation();
        const auto status = controller_.control_status(generation);
        next = std::make_shared<CaptureBinding>(generation, status.capture_generation(), controller_.interrupt_handle(generation));
    }
    std::lock_guard lock(watch_mutex_);
    if (binding_ && next && binding_->actor_generation == next->actor_generation &&
        binding_->capture_generation == next->capture_generation) return;
    if (binding_) binding_->retired_.store(true);
    binding_ = std::move(next);
}
void HostActorWorker::settle_context() {
    refresh_context();
    const auto binding=capture_binding();
    if(binding)if(const auto interrupt=binding->interrupt.lock();interrupt && interrupt->requested()) {
        // A socket-shutdown request may precede the next actor exchange. Do not
        // publish a healthy result from cached status after that interruption.
        core::v1::ActorActionRequest stop;stop.mutable_stop();
        (void)controller_.propose(binding->actor_generation,stop);
        throw std::runtime_error("Interrupted actor context requires checked recovery");
    }
}
bool HostActorWorker::complete(const std::function<void()>& publish, bool success) {
    std::lock_guard lock(watch_mutex_);
    // A departed source can close normally during adoption. Inspect the actual
    // current binding, then publish the result and retire this flight atomically.
    bool interrupted=false;
    if(success && binding_)if(const auto interrupt=binding_->interrupt.lock())interrupted=interrupt->requested();
    flight_.reset();
    if(interrupted)return false;
    publish();return true;
}
void HostActorWorker::begin_exchange(const Binding& urgent, bool cancellation) {
    refresh_context();
    std::lock_guard lock(watch_mutex_);
    if (exchange_serial_ == UINT64_MAX) throw std::runtime_error("Actor worker exchange sequence exhausted");
    flight_ = Flight{++exchange_serial_, nanoseconds(), binding_, urgent && urgent == binding_ && (cancellation || !urgent->retired())};
}
void HostActorWorker::end_exchange() noexcept {
    std::lock_guard lock(watch_mutex_); flight_.reset();
}
void HostActorWorker::watch() noexcept {
    while (!stopping_) {
        {
            // Timestamp, context, and handle belong to one publication. Keep the
            // flight pinned through request(), so an old decision cannot select
            // a new flight's handle or outlive its completion barrier.
            std::lock_guard lock(watch_mutex_);
            if (flight_ && !flight_->release_satisfied && flight_->binding && nanoseconds() - flight_->started >=
                std::chrono::duration_cast<std::chrono::nanoseconds>(watchdog_limit).count()) {
                const auto context = flight_->binding;
                bool urgent = flight_->urgent || pending_cancel_ == context;
                if (!urgent) {
                    std::lock_guard queue_lock(queue_mutex_);
                    urgent = std::any_of(queue_.begin(), queue_.end(), [&](const Work& work) {
                        return work.urgent_binding == context && (work.cancellation || !context->retired());
                    });
                }
                if (urgent) if (const auto interrupt = context->interrupt.lock()) {
                    interrupt->request();
                    if(interrupt->requested() && binding_==context && !context->retired_.exchange(true)) {
                        pending_cancel_=context;cancel_pending_.store(true);changed_.notify_all();
                    }
                }
            }
        }
        std::unique_lock lock(queue_mutex_);
        changed_.wait_for(lock, std::chrono::milliseconds(10), [this] { return stopping_.load(); });
    }
}
void HostActorWorker::run(std::promise<void> ready) noexcept {
    bool announced = false;
    try {
        refresh_context(); ready.set_value(); announced = true;
        auto tick = Clock::now(), render = tick;
        unsigned burst = 0;
        while (!stopping_) {
            Work work;
            {
                std::unique_lock lock(queue_mutex_);
                if (queue_.empty() && !cancel_pending_)
                    changed_.wait_until(lock, std::min(tick, render), [this] {
                        return stopping_ || cancel_pending_ || !queue_.empty();
                    });
                if (stopping_) break;
                if (!queue_.empty() && burst < 8 && !cancel_pending_) {
                    work = std::move(queue_.front()); queue_.pop_front();
                }
            }
            if (work.execute) {
                begin_exchange(work.urgent_binding,work.cancellation);
                try { work.execute(); }
                catch (...) { (void)request_cancel(work.urgent_binding); }
                end_exchange(); ++burst;
                continue;
            }
            burst = 0;
            Binding cancelled;
            {
                std::lock_guard lock(watch_mutex_);
                cancelled = std::move(pending_cancel_); cancel_pending_.store(false);
            }
            begin_exchange(cancelled,true);
            try {
                // Compare the actual adopted context, after refresh_context().
                if (cancelled && cancelled == capture_binding() && controller_.control_available()) {
                    core::v1::ActorActionRequest stop; stop.mutable_stop();
                    (void)controller_.propose(cancelled->actor_generation, stop);
                }
                if (Clock::now() >= tick) {
                    const auto capture=capture_binding();
                    if (controller_.control_available() && capture && !capture->retired())
                        (void)controller_.tick_controls(controller_.generation());
                    tick = Clock::now() + interval;
                }
                if (Clock::now() >= render) {
                    controller_.pump_geometry_presentation();
                    render = Clock::now() + interval;
                }
                controller_.synchronize_controls_presentation();
            } catch (const std::exception&) {
                std::cerr << "Actor worker operation failed; checked context remains required\n";
            }
            end_exchange();
        }
    } catch (...) {
        failed_.store(true);
        if (!announced) ready.set_exception(std::current_exception());
        else std::cerr << "Actor worker stopped after an unexpected failure\n";
        try { refresh_context(); } catch (...) {}
        stop(); // Close the exact actor transport before notifying an idle root reader.
    }
    end_exchange(); stopping_.store(true); changed_.notify_all();
    std::lock_guard lock(queue_mutex_); queue_.clear();
}
}
