#include "supervisor/host_actor_worker.hpp"
#include "supervisor/host_capture_receiver.hpp"
#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string_view>

namespace spatial::supervisor {
namespace {
using Clock = std::chrono::steady_clock;
constexpr auto interval = std::chrono::milliseconds(16);
constexpr auto watchdog_limit = std::chrono::milliseconds(350);
constexpr unsigned normal_burst = 8;
constexpr unsigned queue_limit = 128;
std::int64_t nanoseconds() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
}
}
HostActorWorker::HostActorWorker(HostActorController& controller,CaptureStart start,CaptureStop stop_callback,
        CaptureStopProgress progress)
    : controller_(controller),start_capture_(std::move(start)),stop_capture_(std::move(stop_callback)),
      poll_stop_capture_(std::move(progress)) {
    if(bool(start_capture_)!=bool(stop_capture_))throw std::invalid_argument("Capture requires checked start and stop callbacks");
    std::promise<void> ready;
    auto future = ready.get_future();
    worker_ = std::thread([this, ready = std::move(ready)]() mutable { run(std::move(ready)); });
    try {
        future.get();
        presentation_worker_ = std::thread([this] { run_presentation(); });
        watchdog_ = std::thread([this] { watch(); });
    } catch (...) {
        stop();
        if (worker_.joinable()) worker_.join();
        if (presentation_worker_.joinable()) presentation_worker_.join();
        if (watchdog_.joinable()) watchdog_.join();
        throw;
    }
}
HostActorWorker::~HostActorWorker() {
    (void)shutdown();
}
bool HostActorWorker::shutdown() noexcept {
    stop();
    if (worker_.joinable()) worker_.join();
    if (presentation_worker_.joinable()) presentation_worker_.join();
    if (watchdog_.joinable()) watchdog_.join();
    return capture_cleanup_complete_.load();
}
HostActorWorker::Binding HostActorWorker::capture_binding() const {
    std::lock_guard lock(watch_mutex_); return binding_;
}
control::v1::ControlStatus HostActorWorker::control_status(std::uint64_t generation) const {
    auto result=controller_.control_status(generation);
    const auto binding=capture_binding();
    if(binding && binding->actor_generation==generation) {
        result.set_processed_capture_sequence(binding->processed_sequence());
        result.set_capture_active(binding->capture_active_.load() && !binding->retired());
        result.set_capture_source_slot(binding->source_slot_.load());
        result.set_capture_retired(binding->retired());
    }
    return result;
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
        if(flight_)flight_->interrupted=true;
        const auto context = binding_ ? binding_ : flight_ ? flight_->binding : Binding{};
        if (context) { context->retired_.store(true); interrupt = context->interrupt.lock(); }
    }
    if (interrupt) interrupt->request();
}
bool HostActorWorker::enqueue(Work work) {
    std::lock_guard lock(queue_mutex_);
    if (stopping_) return false;
    if (work.capture_motion && !queue_.empty()) {
        auto& previous = queue_.back();
        if (previous.capture_motion && previous.urgent_binding == work.urgent_binding &&
            previous.capture_source == work.capture_source && previous.source_generation == work.source_generation) {
            if (!work.capture_relative_motion) {
                // Axes are state samples. The newest sample is authoritative.
                previous = std::move(work);
                changed_.notify_one();
                return true;
            }
            if (previous.capture_relative_motion && previous.capture_packet && work.capture_packet) {
                const auto& old_motion=previous.capture_packet->event().motion();
                const auto& new_motion=work.capture_packet->event().motion();
                const double dx=old_motion.dx()+new_motion.dx();
                const double dy=old_motion.dy()+new_motion.dy();
                if (std::isfinite(dx) && std::isfinite(dy) && std::abs(dx)<=100000 && std::abs(dy)<=100000) {
                    *previous.capture_packet=*work.capture_packet;
                    previous.capture_packet->mutable_event()->mutable_motion()->set_dx(dx);
                    previous.capture_packet->mutable_event()->mutable_motion()->set_dy(dy);
                    previous.packet_sequence=work.packet_sequence;
                    changed_.notify_one();
                    return true;
                }
            }
        }
    }
    if (queue_.size() >= queue_limit) return false;
    queue_.push_back(std::move(work));
    changed_.notify_one();
    return true;
}
bool HostActorWorker::post_device(const Binding& binding, control::v1::CapturedDeviceEvent packet) {
    if (!current(binding)) return false;
    if (packet.capture_generation() != binding->capture_generation) {
        (void)request_cancel(binding); return false;
    }
    if (packet.protocol_version() != 1 || !packet.has_event() || !packet.packet_sequence() || !packet.monotonic_nanoseconds() ||
        packet.monotonic_nanoseconds() > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max())) {
        (void)request_cancel(binding); return false;
    }
    auto observed = binding->observed_sequence_.load(std::memory_order_acquire);
    if (packet.packet_sequence() <= observed || !binding->observed_sequence_.compare_exchange_strong(
            observed, packet.packet_sequence(), std::memory_order_acq_rel)) {
        (void)request_cancel(binding); return false;
    }
    if (binding->motion_intent_) {
        const auto captured = control::DeviceAdapters::Clock::time_point(std::chrono::duration_cast<
            control::DeviceAdapters::Clock::duration>(std::chrono::nanoseconds(
                static_cast<std::int64_t>(packet.monotonic_nanoseconds()))));
        if (!binding->motion_intent_->observe(packet.event(), captured, control::DeviceAdapters::Clock::now())) {
            (void)request_cancel(binding); return false;
        }
    }
    const auto& event=packet.event();
    const bool capture_cancellation=event.has_disconnected() ||
        (event.has_key() && event.key().pressed() && event.key().keysym()=="F12") ||
        (event.has_button() && !event.button().pressed());
    Work work; work.capture_heartbeat = event.has_heartbeat();work.cancellation=capture_cancellation;
    work.capture_motion = packet.event().has_motion() || packet.event().has_axes();
    work.capture_relative_motion = packet.event().has_motion();
    if (work.capture_motion) {
        work.capture_source = packet.event().source_id();
        work.source_generation = packet.event().source_generation();
        work.packet_sequence = packet.packet_sequence();
    }
    // Liveness traffic does not request an action or release. Independent source
    // failure still publishes exact pending cancellation with watchdog urgency.
    if(!work.capture_heartbeat)work.urgent_binding = binding;
    work.capture_packet=std::make_shared<control::v1::CapturedDeviceEvent>(std::move(packet));
    auto captured_packet=work.capture_packet;
    work.execute = [this, binding, captured_packet] {
        const auto& packet=*captured_packet;
        try {
        if (!current(binding) || !controller_.control_available()) return;
        try { (void)device_event(binding, packet); }
        catch (const std::exception& error) {
            // Malformed current capture is terminal for that binding. A stale
            // binding cannot enter this path or cancel an adopted actor.
            (void)request_cancel(binding);
            std::cerr << "Captured actor input retired its binding: " << error.what() << '\n';
        }
        synchronize_capture();
        if(!complete([&] {
            if(binding_==binding && !binding->retired())
                binding->dispatched_sequence_.store(packet.packet_sequence(),std::memory_order_release);
        },true))settle_context();
        }catch(...) {
            // Failure ownership remains bound even when liveness traffic is nonurgent.
            (void)request_cancel(binding);throw;
        }
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
    {
        std::lock_guard lock(watch_mutex_);
        if (!binding || binding_ != binding) return false;
        if (binding->retired()) return true;
        pending_cancel_ = binding;
        cancel_pending_.store(true);
        binding->retired_.store(true); // Observed retirement implies cancellation is already pending.
        changed_.notify_all();
    }
    if (binding->motion_intent_) binding->motion_intent_->clear();
    return true;
}
void HostActorWorker::capture_failed(const Binding& binding) noexcept {
    // Ordinary checked cancellation runs first. The independent watchdog can
    // interrupt its exact flight without taking controller or actor locks.
    (void)request_cancel(binding);
}
bool HostActorWorker::retire_capture() {
    if(!capture_owner_)return true;
    capture_owner_->capture_active_.store(false);
    receiver_.reset();
    if(poll_stop_capture_) {
        if(!poll_stop_capture_(capture_owner_))return false;
    }else stop_capture_(capture_owner_);
    capture_owner_.reset();capture_cleanup_complete_.store(true);return true;
}
void HostActorWorker::synchronize_capture() {
    if(!start_capture_)return;
    const auto binding=capture_binding();
    try {
        if(capture_owner_ && (capture_owner_!=binding || capture_owner_->retired()) && !retire_capture())return;
        if(!binding || binding->retired() || capture_owner_)return;
        capture_owner_=binding;capture_cleanup_complete_.store(false);
        binding->motion_intent_ = controller_.motion_intent(binding->actor_generation);
        auto endpoint=start_capture_(binding);
        if(!endpoint.stream)throw std::runtime_error("Capture startup returned no private endpoint");
        binding->source_slot_=endpoint.source_slot;
        receiver_=std::make_unique<HostCaptureReceiver>(*this,binding,std::move(endpoint.stream));
        binding->capture_active_.store(true);
    } catch(...) {
        // Capture availability does not own world or workspace execution.
        capture_failed(binding);
        try {throw;}
        catch(const std::exception& error){std::cerr<<"Capture unavailable for its actor context: "<<error.what()<<'\n';}
        catch(...){std::cerr<<"Capture unavailable for its actor context\n";}
        try {retire_capture();}
        catch(...){capture_cleanup_complete_.store(false);std::cerr<<"Capture cleanup remains unconfirmed\n";}
    }
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
        try { (void)controller_.propose(binding->actor_generation,stop); }
        catch (...) {
            throw HostActorContextInterrupted("Interrupted actor context has no confirmed input release");
        }
        throw HostActorContextInterrupted("Interrupted actor context requires checked recovery");
    }
}
bool HostActorWorker::complete(const std::function<void()>& publish, bool success) {
    std::lock_guard lock(watch_mutex_);
    // Refresh may remove an interrupted binding. Keep the exact flight's
    // interruption until publication, without treating normal source departure
    // as interruption of an adopted destination.
    bool interrupted=success && flight_ && flight_->interrupted;
    if(success && binding_)if(const auto interrupt=binding_->interrupt.lock())interrupted|=interrupt->requested();
    flight_.reset();
    if(interrupted)return false;
    publish();return true;
}
void HostActorWorker::complete_failure(std::exception_ptr failure,
                                      const std::function<void(std::exception_ptr)>& publish) {
    std::lock_guard lock(watch_mutex_);
    // A transport exception can retire binding_ before settlement. Preserve the
    // exact interrupted flight through the same publication barrier as success.
    bool interrupted=flight_ && flight_->interrupted;
    if(binding_)if(const auto interrupt=binding_->interrupt.lock())interrupted|=interrupt->requested();
    if(interrupted)failure=std::make_exception_ptr(
        HostActorContextInterrupted("Interrupted actor context has no confirmed input release"));
    flight_.reset();
    publish(std::move(failure));
}
void HostActorWorker::begin_exchange(const Binding& urgent, bool cancellation, const char* origin) {
    refresh_context();
    std::lock_guard lock(watch_mutex_);
    if (exchange_serial_ == UINT64_MAX) throw std::runtime_error("Actor worker exchange sequence exhausted");
    flight_ = Flight{++exchange_serial_, nanoseconds(), binding_, urgent && urgent == binding_ && (cancellation || !urgent->retired()), origin};
}
void HostActorWorker::end_exchange() noexcept {
    std::lock_guard lock(watch_mutex_); flight_.reset();
}
void HostActorWorker::set_flight_phase(const char* phase) noexcept {
    std::lock_guard lock(watch_mutex_);
    if (flight_) flight_->phase = phase;
}
void HostActorWorker::run_presentation() noexcept {
    const auto* trace_method=std::getenv("SPATIAL_TEST_METHOD");
    const bool trace_pending=trace_method && std::string_view(trace_method)=="presentation-liveness-trace";
    auto pending_since=Clock::time_point{};
    auto last_pending_report=Clock::time_point{};
    while (!stopping_) {
        bool pending = false;
        bool cleanup_before=false,frame_pending=false,cleanup_after=false;
        const char* phase="start";
        const auto presentation_binding=capture_binding();
        const auto presentation_sequence=presentation_binding ?
            presentation_binding->dispatched_sequence_.load(std::memory_order_acquire) : 0;
        try {
            controller_.synchronize_controls_presentation();
            cleanup_before = controller_.pump_deferred_cleanup();
            frame_pending = controller_.pump_geometry_presentation([&](const char* value){phase=value;});
            cleanup_after = controller_.pump_deferred_cleanup();
            pending = cleanup_before || frame_pending || cleanup_after;
            // Only acknowledge input whose state preceded this presentation
            // cycle. A later dispatch needs a later output receipt.
            if (!pending && presentation_binding && current(presentation_binding))
                presentation_binding->processed_sequence_.store(presentation_sequence,std::memory_order_release);
        } catch (const std::exception& error) {
            pending = true;
            std::cerr << "Host presentation work remains pending: " << error.what() << '\n';
        } catch (...) {
            pending = true;
            std::cerr << "Host presentation work remains pending after an unknown failure\n";
        }
        const auto now=Clock::now();
        if(pending) {
            if(pending_since==Clock::time_point{})pending_since=now;
            if(trace_pending && now-pending_since>=std::chrono::milliseconds(250) &&
                (last_pending_report==Clock::time_point{} || now-last_pending_report>=std::chrono::milliseconds(250))) {
                std::cerr<<"Host presentation trace elapsed_ms="
                    <<std::chrono::duration_cast<std::chrono::milliseconds>(now-pending_since).count()
                    <<" cleanup_before="<<cleanup_before<<" frame_pending="<<frame_pending
                    <<" cleanup_after="<<cleanup_after<<" phase="<<phase
                    <<" dispatched="<<(presentation_binding?presentation_binding->dispatched_sequence_.load(std::memory_order_acquire):0)
                    <<" processed="<<(presentation_binding?presentation_binding->processed_sequence_.load(std::memory_order_acquire):0)
                    <<" binding_current="<<current(presentation_binding)<<'\n';
                last_pending_report=now;
            }
        } else {pending_since=Clock::time_point{};last_pending_report=Clock::time_point{};}
        std::unique_lock lock(queue_mutex_);
        changed_.wait_for(lock, pending ? std::chrono::milliseconds(2) : interval);
    }
}
void HostActorWorker::watch() noexcept {
    struct Decision {
        std::uint64_t serial=0,actor=0,capture=0;
        std::int64_t elapsed_ms=0;
        std::uint64_t diagnostic_time=0;
        bool flight=false,cancellation=false,heartbeat=false,event=false;
        bool queued_cancellation=false,queued_capture=false;
        const char* origin="unknown";
        const char* phase="unknown";
    };
    std::uint64_t logged_serial=0;
    while (!stopping_) {
        Decision decision;
        bool log_decision=false;
        {
            // Timestamp, context, and handle belong to one publication. Keep the
            // flight pinned through request(), so an old decision cannot select
            // a new flight's handle or outlive its completion barrier.
            std::lock_guard lock(watch_mutex_);
            if (flight_ && !flight_->release_satisfied && flight_->binding && nanoseconds() - flight_->started >=
                std::chrono::duration_cast<std::chrono::nanoseconds>(watchdog_limit).count()) {
                const auto context = flight_->binding;
                bool queued_heartbeat=false,queued_event=false;
                bool queued_cancellation=false,queued_capture=false;
                bool queued_host_event=false;
                {
                    std::lock_guard queue_lock(queue_mutex_);
                    for(const auto& work:queue_)if(work.urgent_binding==context &&
                            (work.cancellation || !context->retired())) {
                        if(work.capture_heartbeat)queued_heartbeat=true;else queued_event=true;
                        queued_cancellation |= work.cancellation;
                        queued_capture |= bool(work.capture_packet);
                        queued_host_event |= !work.capture_packet && !work.capture_heartbeat;
                    }
                }
                const bool controls_tick=flight_->origin==std::string_view("maintenance") &&
                    flight_->phase==std::string_view("controls_tick");
                // Capture ingress observes movement before queueing it. Let that
                // buffered input reach the checked stale-event/Stop path after a
                // slow controls tick; unrelated ordinary capture must not retire
                // the actor's authenticated channel. Explicit cancellation,
                // host requests, and an urgent flight retain watchdog behavior.
                const bool queued_event_urgent=queued_event &&
                    !(controls_tick && queued_capture && !queued_host_event && !queued_cancellation);
                const bool urgent=flight_->urgent || pending_cancel_==context || queued_heartbeat || queued_event_urgent;
                if (urgent) if (const auto interrupt = context->interrupt.lock()) {
                    if(!interrupt->requested() && logged_serial!=flight_->serial) {
                        decision.serial=flight_->serial;
                        decision.actor=context->actor_generation;decision.capture=context->capture_generation;
                        decision.elapsed_ms=(nanoseconds()-flight_->started)/1000000;
                        decision.flight=flight_->urgent;decision.cancellation=pending_cancel_==context;
                        decision.origin=flight_->origin;
                        decision.phase=flight_->phase;
                        decision.heartbeat=queued_heartbeat;decision.event=queued_event;
                        decision.queued_cancellation=queued_cancellation;decision.queued_capture=queued_capture;
                        decision.diagnostic_time=nanoseconds();
                        log_decision=true;logged_serial=flight_->serial;
                    }
                    interrupt->request();
                    if(interrupt->requested())flight_->interrupted=true;
                    if(interrupt->requested() && binding_==context && !context->retired()) {
                        pending_cancel_=context;cancel_pending_.store(true);context->retired_.store(true);changed_.notify_all();
                    }
                }
            }
        }
        if(log_decision) {
            // Slow diagnostic output cannot hold either operation/publication lock.
            try {
                std::cerr<<"Actor watchdog requested exact interruption flight="<<decision.serial
                    <<" monotonic_ns="<<decision.diagnostic_time<<" elapsed_ms="<<decision.elapsed_ms<<" actor_generation="<<decision.actor
                    <<" capture_generation="<<decision.capture<<" flight_urgent="<<decision.flight
                    <<" pending_cancel="<<decision.cancellation<<" queued_heartbeat="<<decision.heartbeat
                    <<" queued_event="<<decision.event<<" queued_cancellation="<<decision.queued_cancellation
                    <<" queued_capture="<<decision.queued_capture<<" flight_origin="<<decision.origin
                    <<" flight_phase="<<decision.phase<<'\n';
            }catch(...) {}
        }
        std::unique_lock lock(queue_mutex_);
        changed_.wait_for(lock, std::chrono::milliseconds(10), [this] { return stopping_.load(); });
    }
}
void HostActorWorker::run(std::promise<void> ready) noexcept {
    bool announced = false;
    try {
        refresh_context();synchronize_capture();
        ready.set_value(); announced = true;
        auto tick = Clock::now();
        unsigned burst = 0;
        while (!stopping_) {
            Work work;
            {
                std::unique_lock lock(queue_mutex_);
                if (queue_.empty() && !cancel_pending_)
                    changed_.wait_until(lock, tick, [this] {
                        return stopping_ || cancel_pending_ || !queue_.empty();
                    });
                if (stopping_) break;
                // A completed ordinary exchange can leave fresh source liveness
                // behind older heartbeats. Consume that bounded FIFO backlog
                // before checking source expiry. Gestures retain the normal
                // fairness bound and their original capture-age checks.
                const bool take_work = !queue_.empty() && (burst < normal_burst ||
                    (burst < normal_burst + queue_limit && queue_.front().capture_heartbeat));
                if (take_work && !cancel_pending_) {
                    work = std::move(queue_.front()); queue_.pop_front();
                }
            }
            if (work.execute) {
                begin_exchange(work.urgent_binding,work.cancellation,work.capture_packet ? "capture" : "host_request");
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
                    set_flight_phase("cancel_stop");
                    core::v1::ActorActionRequest stop; stop.mutable_stop();
                    (void)controller_.propose(cancelled->actor_generation, stop);
                    std::lock_guard lock(watch_mutex_);
                    if(flight_ && flight_->binding==cancelled)flight_->release_satisfied=true;
                }
                if (Clock::now() >= tick) {
                    set_flight_phase("controls_tick");
                    const auto capture=capture_binding();
                    if (controller_.control_available() && capture && !capture->retired())
                        (void)controller_.tick_controls(controller_.generation());
                    tick = Clock::now() + interval;
                }
                set_flight_phase("capture_synchronization");
                synchronize_capture();
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
    try {
        const auto deadline=Clock::now()+std::chrono::seconds(10);
        while(!retire_capture() && Clock::now()<deadline) {
            (void)controller_.pump_deferred_cleanup();std::this_thread::sleep_for(interval);
        }
        if(capture_owner_)std::cerr<<"Capture cleanup remains unconfirmed\n";
    }catch(...) {capture_cleanup_complete_.store(false);std::cerr<<"Capture cleanup remains unconfirmed\n";}
    end_exchange(); stopping_.store(true); changed_.notify_all();
    std::lock_guard lock(queue_mutex_); queue_.clear();
}
}
