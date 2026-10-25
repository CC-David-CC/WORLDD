#define main actor_transition_fixture_main
#include "actor_transition_coordinator_probe.cpp"
#undef main
#include "supervisor/host_actor_controller.hpp"
#include "supervisor/host_actor_worker.hpp"
#include "platform/presentation_stream.hpp"
#include <sys/un.h>
#include <future>
#include <signal.h>
#include <limits>
#include <sys/socket.h>

namespace {
using namespace spatial;
using namespace std::chrono_literals;

render::v1::PresentationState presentation(const std::string& world, render::v1::PresentationStage stage,
                                           bool selected = false) {
    render::v1::PresentationState state;
    state.mutable_binding()->set_world_id(world);
    state.set_stage(stage);
    state.set_selected(selected);
    return state;
}

identity::v1::HostActorTransitionRequest request_for(const Fixture& source) {
    identity::v1::HostActorTransitionRequest request;
    request.set_protocol_version(1);
    request.set_transition_id(source.transition);
    request.set_destination_world_id("world-b");
    request.set_departure_sequence(17);
    return request;
}

void controller_case(bool deny_preparation, bool fail_selection, const std::string& fault = {}) {
    Fixture source_fixture; source_fixture.world = "world-a";
    Fixture destination_fixture; destination_fixture.world = "world-b";
    Endpoint source(source_fixture), destination(destination_fixture);
    std::atomic<bool> prepared{false}, departed{false}, activated{false};
    source.start([&](int fd) {
        handshake(fd, source_fixture);
        state(fd, source_fixture, 3);
        if (deny_preparation) {
            state(fd, source_fixture, 4);
            receive(fd, pb::WorldSessionRequest::kAction, 5);
            auto reply = reply_for(5); reply.mutable_action()->set_accepted(true); send_reply(fd, reply);
            closed(fd); return;
        }
        const auto call = receive(fd, pb::WorldSessionRequest::kDepart, 4);
        check(call.depart().seq() == 17, "host controller changed departure sequence");
        if (fault == "departure") { denial(fd, 4); closed(fd); return; }
        auto reply = reply_for(4); auto* receipt = reply.mutable_departed();
        receipt->set_actor_id(source_fixture.actor_id);
        receipt->set_world_id(source_fixture.world);
        receipt->set_transition_id(source_fixture.transition);
        departed = true;
        send_reply(fd, reply); closed(fd);
    });
    destination.start([&](int fd) {
        handshake(fd, destination_fixture, true, false, [&](auto& reply) {
            if (deny_preparation) {
                reply.clear_prepared();
                reply.mutable_error()->set_code(core::v1::FORBIDDEN);
                reply.mutable_error()->set_message("fixture admission denial");
            }
            prepared = true;
        });
        if (deny_preparation || fault == "departure") { closed(fd); return; }
        receive(fd, pb::WorldSessionRequest::kActivate, 3);
        if (fault == "activation") { denial(fd, 3); closed(fd); return; }
        auto reply = reply_for(3); opened(destination_fixture, reply.mutable_opened());
        activated = true; send_reply(fd, reply); closed(fd);
    });
    source.client->authenticate();
    int inspect_calls = 0;
    int select_calls = 0;
    const auto inspect = [&](const workspace::v1::ControlOwner&) {
        ++inspect_calls;
        return presentation("world-b", render::v1::ATTACHED);
    };
    const auto select = [&](const workspace::v1::ControlOwner& owner) {
        ++select_calls;
        check(prepared && (!deny_preparation) && departed && activated,
              "foreground selected before destination activation");
        check(owner.issuer_id() == std::string("registered-owner"), "wrong retained destination owner selected");
        if (fault == "selection-mismatch") return presentation("wrong-world", render::v1::READY, true);
        if (fault == "selection-nonstd") throw 7;
        if (fail_selection) throw std::runtime_error("fixture selection transport uncertain");
        return presentation("world-b", render::v1::READY, true);
    };
    // These opaque bytes represent the exact retained ControlOwner chosen by trusted setup.
    workspace::v1::ControlOwner owner;
    owner.set_issuer_id("registered-owner");
    {
    supervisor::HostActorController controller(inspect, select, "world-a", std::move(source.client));
    controller.register_world("world-b", owner, [&] { return std::move(destination.client); });
    const auto result = controller.transition(request_for(source_fixture));
    check(result.protocol_version() == 1 && result.operation_generation() == 1, "host result version/generation invalid");
    if (deny_preparation) {
        check(result.actor_transition().phase() == identity::v1::PREPARATION_REJECTED &&
              result.host_selection() == identity::v1::HOST_SELECTION_NOT_ATTEMPTED &&
              result.actor_control_available() && controller.control_available() && select_calls == 0,
              "preparation denial did not preserve source control");
        controller.inspect(controller.generation());
        core::v1::ActorActionRequest stop; stop.set_seq(18); stop.mutable_stop();
        check(controller.action(controller.generation(), stop).action().accepted(), "denial lost source action usability");
    } else if (fault == "departure" || fault == "activation") {
        check(result.actor_transition().effect_uncertain() && result.actor_transition().has_server_error() &&
              result.host_selection() == identity::v1::HOST_SELECTION_NOT_ATTEMPTED &&
              !result.actor_control_available() && !controller.control_available() && select_calls == 0,
              "uncertain actor effect exposed control or selected foreground");
        check(result.actor_transition().source_departure_confirmed() == (fault == "activation"), "departure fact lost");
        const auto retry = controller.transition(request_for(source_fixture));
        check(retry.operation_generation() == 0 && !retry.actor_control_available(), "uncertain operation replayed");
    } else if (fail_selection || fault == "selection-mismatch" || fault == "selection-nonstd") {
        check(result.actor_transition().destination_activation_confirmed() &&
              result.host_selection() == identity::v1::HOST_SELECTION_UNCERTAIN &&
              !result.actor_control_available() && !controller.control_available() &&
              select_calls == 1 && departed && activated,
              "selection uncertainty did not preserve activation facts and fence control");
        const auto retry = controller.transition(request_for(source_fixture));
        check(!retry.actor_control_available() && select_calls == 1 && retry.operation_generation() == 0,
              "fenced retry replayed effects");
        rejects([&] { controller.inspect(1); });
    } else {
        check(result.actor_transition().destination_activation_confirmed() &&
              result.host_selection() == identity::v1::HOST_SELECTION_CONFIRMED &&
              !result.actor_transition().host_presentation_selection_pending() &&
              result.actor_control_available() && controller.control_available() &&
              controller.current_world() == "world-b" && select_calls == 1,
              "successful host selection did not adopt destination client");
    }
    check(inspect_calls == 1, "destination mapping was not checked exactly once");
    } // Release active/quarantined channels before joining EOF witnesses.
    source.finish(); destination.finish();
}

void wrong_mapping() {
    Fixture fixture; fixture.world = "world-a";
    Endpoint source(fixture);
    source.start([&](int fd) { handshake(fd, fixture); closed(fd); });
    source.client->authenticate();
    {
    supervisor::HostActorController controller(
        [](const workspace::v1::ControlOwner&) { return presentation("other-world", render::v1::ATTACHED); },
        [](const workspace::v1::ControlOwner&) { throw std::runtime_error("must not select"); return render::v1::PresentationState{}; },
        "world-a", std::move(source.client));
    workspace::v1::ControlOwner owner; owner.set_issuer_id("owner");
    rejects([&] { controller.register_world("world-b", owner, [] { return std::unique_ptr<ActorSessionClient>{}; }); });
    check(controller.control_available(), "wrong destination mapping disturbed source client");
    }
    source.finish();
}
struct ThrowingFactory {
    std::shared_ptr<bool> fail;
    explicit ThrowingFactory(std::shared_ptr<bool> value) : fail(std::move(value)) {}
    ThrowingFactory(const ThrowingFactory& other) : fail(other.fail) {
        if (*fail) throw std::runtime_error("fixture factory copy failure");
    }
    std::unique_ptr<ActorSessionClient> operator()() const { return {}; }
};
void setup_exception_does_not_wedge() {
    Fixture a; a.world = "world-a"; Endpoint source(a);
    source.start([&](int fd) { handshake(fd, a); state(fd, a, 3); state(fd, a, 4); closed(fd); });
    source.client->authenticate();
    auto fail = std::make_shared<bool>(false);
    {
        supervisor::HostActorController controller(
            [](const auto&) { return presentation("world-b", render::v1::ATTACHED); },
            [](const auto&) { throw std::runtime_error("unexpected selection"); return render::v1::PresentationState{}; },
            "world-a", std::move(source.client));
        workspace::v1::ControlOwner owner; owner.set_issuer_id("owner-b");
        controller.register_world("world-b", owner, ThrowingFactory(fail));
        *fail = true;
        rejects([&] { controller.transition(request_for(a)); });
        check(controller.control_available() && controller.generation() == 0, "setup exception wedged transition guard");
        *fail = false;
        const auto result = controller.transition(request_for(a));
        check(result.actor_control_available() && result.operation_generation() == 1 &&
              result.actor_transition().phase() == identity::v1::PREPARATION_REJECTED,
              "transition unavailable after setup exception");
        check(controller.inspect(1).state().id() == a.actor_id, "setup exception lost source usability");
    }
    source.finish();
}

void inspect_fences_transition() {
    Fixture a; a.world = "world-a"; Endpoint source(a);
    std::promise<void> entered, release;
    auto waiting = entered.get_future(); auto released = release.get_future().share();
    source.start([&](int fd) {
        handshake(fd, a); receive(fd, pb::WorldSessionRequest::kInspect, 3); entered.set_value();
        check(released.wait_for(1s) == std::future_status::ready, "inspect witness release timed out");
        auto reply = reply_for(3); reply.mutable_state()->set_id(a.actor_id); send_reply(fd, reply);
        receive(fd, pb::WorldSessionRequest::kAction, 4);
        reply = reply_for(4); reply.mutable_action()->set_accepted(true); send_reply(fd, reply); closed(fd);
    });
    source.client->authenticate();
    {
        supervisor::HostActorController controller(
            [](const auto&) { return presentation("world-b", render::v1::ATTACHED); },
            [](const auto&) { throw std::runtime_error("unexpected selection"); return render::v1::PresentationState{}; },
            "world-a", std::move(source.client));
        workspace::v1::ControlOwner owner; owner.set_issuer_id("owner-b");
        controller.register_world("world-b", owner, [] { throw std::runtime_error("unexpected channel creation"); return std::unique_ptr<ActorSessionClient>{}; });
        auto operation = std::async(std::launch::async, [&] { return controller.inspect(0); });
        const bool ready = waiting.wait_for(1s) == std::future_status::ready;
        const auto transition = controller.transition(request_for(a));
        core::v1::ActorActionRequest stop; stop.set_seq(1); stop.mutable_stop();
        bool rejected = false; try { controller.action(0, stop); } catch (...) { rejected = true; }
        release.set_value(); const auto inspected = operation.get();
        check(ready && rejected && transition.operation_generation() == 0 && !transition.has_actor_transition() &&
              inspected.state().id() == a.actor_id && controller.generation() == 0,
              "transition or action raced an in-flight inspection");
        check(controller.action(0, stop).action().accepted(), "serialized semantic action failed");
    }
    source.finish();
}

void aba_and_concurrency() {
    Fixture a; a.world = "world-a";
    Fixture b; b.world = "world-b";
    Fixture resumed; resumed.world = "world-a";
    // Same persistent actor, fresh authentication channel for each destination.
    Endpoint initial(a), destination(b), returning(resumed);
    const auto depart = [](int fd, const Fixture& fixture, std::uint64_t id) {
        receive(fd, pb::WorldSessionRequest::kDepart, id);
        auto reply = reply_for(id);
        auto* receipt = reply.mutable_departed();
        receipt->set_actor_id(fixture.actor_id); receipt->set_world_id(fixture.world);
        receipt->set_transition_id(fixture.transition); send_reply(fd, reply); closed(fd);
    };
    initial.start([&](int fd) { handshake(fd, a); state(fd, a, 3); depart(fd, a, 4); });
    destination.start([&](int fd) {
        handshake(fd, b, true); receive(fd, pb::WorldSessionRequest::kActivate, 3);
        auto reply = reply_for(3); opened(b, reply.mutable_opened()); send_reply(fd, reply);
        state(fd, b, 4); depart(fd, b, 5);
    });
    returning.start([&](int fd) {
        handshake(fd, resumed, true); receive(fd, pb::WorldSessionRequest::kActivate, 3);
        auto reply = reply_for(3); opened(resumed, reply.mutable_opened()); send_reply(fd, reply);
        state(fd, resumed, 4); closed(fd);
    });
    initial.client->authenticate();
    workspace::v1::ControlOwner owner_a, owner_b;
    owner_a.set_issuer_id("exact-a"); owner_b.set_issuer_id("exact-b");
    int selections = 0, factories = 0;
    std::promise<void> entered, release;
    auto entered_future = entered.get_future(); auto released = release.get_future().share();
    {
        supervisor::HostActorController controller(
            [&](const auto& owner) { return presentation(owner.issuer_id() == "exact-a" ? "world-a" : "world-b", render::v1::ATTACHED); },
            [&](const auto& owner) {
                const auto& expected = ++selections == 1 ? owner_b : owner_a;
                check(owner.SerializeAsString() == expected.SerializeAsString(), "wrong exact retained owner");
                return presentation(selections == 1 ? "world-b" : "world-a", render::v1::READY, true);
            }, "world-a", std::move(initial.client));
        controller.register_world("world-a", owner_a, [&] { ++factories; return std::move(returning.client); });
        controller.register_world("world-b", owner_b, [&] {
            ++factories; entered.set_value();
            check(released.wait_for(1s) == std::future_status::ready, "concurrency release timed out");
            return std::move(destination.client);
        });
        auto request = request_for(a);
        auto invalid = request; invalid.set_protocol_version(2);
        check(controller.transition(invalid).request_error().code() == core::v1::UNSUPPORTED_VERSION, "unsupported version lost");
        invalid = request; invalid.GetReflection()->MutableUnknownFields(&invalid)->AddVarint(99, 1);
        check(controller.transition(invalid).request_error().code() == core::v1::INVALID_ARGUMENT, "unknown request field accepted");
        invalid = request; invalid.mutable_destination_pose()->mutable_position();
        invalid.mutable_destination_pose()->mutable_orientation()->set_w(1);
        invalid.mutable_destination_pose()->GetReflection()->MutableUnknownFields(invalid.mutable_destination_pose())->AddVarint(99, 1);
        check(controller.transition(invalid).has_request_error(), "unknown nested field accepted");
        invalid = request; invalid.mutable_destination_pose()->mutable_position();
        invalid.mutable_destination_pose()->mutable_orientation()->set_w(2);
        check(controller.transition(invalid).has_request_error(), "nonunit pose accepted");
        invalid.mutable_destination_pose()->mutable_orientation()->set_w(std::numeric_limits<double>::infinity());
        check(controller.transition(invalid).has_request_error(), "nonfinite pose accepted");
        invalid = request; invalid.mutable_destination_pose();
        check(controller.transition(invalid).has_request_error(), "missing pose components accepted");
        invalid = request; invalid.set_destination_world_id("world-a");
        check(controller.transition(invalid).has_request_error(), "current world transition accepted");
        check(factories == 0 && selections == 0 && controller.generation() == 0 && controller.control_available(),
              "invalid request caused effects or wedged guard");
        auto operation = std::async(std::launch::async, [&] { return controller.transition(request); });
        const bool ready = entered_future.wait_for(1s) == std::future_status::ready;
        // Always release the worker, including assertion failure paths.
        core::v1::ActorActionRequest action; action.set_seq(18); action.mutable_stop();
        bool inspect_rejected = false, action_rejected = false;
        try { controller.inspect(0); } catch (...) { inspect_rejected = true; }
        try { controller.action(0, action); } catch (...) { action_rejected = true; }
        auto concurrent = controller.transition(request);
        release.set_value();
        const auto first = operation.get();
        check(ready && inspect_rejected && action_rejected && concurrent.operation_generation() == 0,
              "concurrent operation crossed transition fence");
        check(first.host_selection() == identity::v1::HOST_SELECTION_CONFIRMED, "A-B failed");
        rejects([&] { controller.action(0, action); });
        rejects([&] { controller.inspect(0); });
        request.set_destination_world_id("world-a"); request.set_departure_sequence(19);
        const auto second = controller.transition(request);
        check(second.host_selection() == identity::v1::HOST_SELECTION_CONFIRMED && second.operation_generation() == 2 &&
              controller.current_world() == "world-a" && factories == 2 && selections == 2, "B-A failed");
        rejects([&] { controller.action(1, action); });
        check(controller.inspect(2).state().id() == a.actor_id, "resumed actor unusable");
    }
    initial.finish(); destination.finish(); returning.finish();
}

std::unique_ptr<supervisor::HostActorController> shared_host(Endpoint& source, const Fixture& fixture,
    control::ControlsMenu::Persist persist = [](const auto&) {},
    supervisor::HostActorController::ControlsPresentation controls = {}) {
    source.client->authenticate();
    auto result = std::make_unique<supervisor::HostActorController>(
        [](const auto&) { return presentation("world-b", render::v1::ATTACHED); },
        [](const auto&) { return presentation("world-b", render::v1::READY, true); },
        fixture.world, std::move(source.client),std::move(controls));
    result->start_controls(control::default_profiles(), std::move(persist));
    return result;
}
core::v1::ActorActionRequest controlled_accept(int fd, std::uint64_t request_id, std::uint64_t sequence,
    core::v1::ActorActionRequest::ActionCase kind) {
    const auto request = receive(fd, pb::WorldSessionRequest::kControlAction, request_id);
    check(request.control_action().seq() == sequence && request.control_action().token().empty() &&
          request.control_action().action_case() == kind, "host changed shared actor sequence, identity, or action");
    auto reply = reply_for(request_id); auto* outcome = reply.mutable_control_action();
    outcome->set_sequence(sequence); outcome->mutable_accepted()->set_accepted(true); send_reply(fd, reply);
    return request.control_action();
}
core::v1::ActorActionRequest shared_stop() { core::v1::ActorActionRequest result; result.mutable_stop(); return result; }
void shared_proposals_and_denial() {
    Fixture fixture; Endpoint source(fixture);
    source.start([&](int fd) {
        handshake(fd, fixture);
        const auto first = receive(fd, pb::WorldSessionRequest::kControlAction, 3);
        check(first.control_action().seq() == 1 && first.control_action().has_pointer(), "first shared input did not reach its actor");
        auto reply = reply_for(3); auto* outcome = reply.mutable_control_action(); outcome->set_sequence(1);
        outcome->mutable_rejected()->mutable_error()->set_code(core::v1::NO_TARGET);
        outcome->mutable_rejected()->set_cancellation_confirmed(true); send_reply(fd, reply);
        controlled_accept(fd, 4, 2, core::v1::ActorActionRequest::kStop); closed(fd);
    });
    auto host = shared_host(source, fixture);
    core::v1::ActorActionRequest pointer; pointer.mutable_pointer()->set_button(1); pointer.mutable_pointer()->set_pressed(true);
    bool cancelled = false;
    try { (void)host->propose(0, pointer); } catch (const ActorInputCancelled& error) { cancelled = error.error().code() == core::v1::NO_TARGET; }
    check(cancelled && host->control_available(), "checked rejection disabled or changed shared actor context");
    auto forged = shared_stop(); forged.set_seq(0); rejects([&] { host->propose(0, forged); });
    forged = shared_stop(); forged.set_token("foreign"); rejects([&] { host->propose(0, forged); });
    rejects([&] { host->propose(1, shared_stop()); });
    auto legacy = shared_stop(); legacy.set_seq(99); rejects([&] { host->action(0, legacy); });
    rejects([&] { host->transition(request_for(fixture)); });
    rejects([&] { host->start_controls(control::default_profiles(), [](const auto&) {}); });
    check(host->propose(0, shared_stop()).accepted(), "fresh proposal failed after checked cancellation");
    host.reset(); source.finish();
}
void shared_durable_errors_remain_terminal() {
    Fixture fixture; Endpoint source(fixture);
    source.start([&](int fd) {
        handshake(fd, fixture); const auto request = receive(fd, pb::WorldSessionRequest::kAction, 3);
        check(request.action().seq() == 1 && request.action().has_object_release(), "durable action used the input cancellation wrapper");
        denial(fd, 3); closed(fd);
    });
    auto host = shared_host(source, fixture); const auto pin = host->interrupt_handle(0).lock();
    core::v1::ActorActionRequest release; release.mutable_object_release()->set_commit(false);
    rejects([&] { host->propose(0, release); });
    check(!host->control_available() && !host->control_status(0).available() && pin->requested(), "durable error retained shared control");
    rejects([&] { host->propose(0, shared_stop()); }); host.reset(); source.finish();
}
void shared_capture_menu_and_stop_cache() {
    Fixture fixture; Endpoint source(fixture);
    source.start([&](int fd) {
        handshake(fd, fixture);
        const auto first = controlled_accept(fd, 3, 1, core::v1::ActorActionRequest::kInput);
        check(first.input().axes_size() == 6 && first.input().axes(2) == -1 &&
              first.input().translation_frame() == core::v1::LEVEL_TRANSLATION, "host changed level keyboard input");
        controlled_accept(fd, 4, 2, core::v1::ActorActionRequest::kStop);
        controlled_accept(fd, 5, 3, core::v1::ActorActionRequest::kStop);
        controlled_accept(fd, 6, 4, core::v1::ActorActionRequest::kStop);
        controlled_accept(fd, 7, 5, core::v1::ActorActionRequest::kStop);
        controlled_accept(fd, 8, 6, core::v1::ActorActionRequest::kInput);
        controlled_accept(fd, 9, 7, core::v1::ActorActionRequest::kStop); closed(fd);
    });
    unsigned saved = 0; auto host = shared_host(source, fixture, [&](const auto&) { ++saved; });
    const auto epoch = host->control_status(0).capture_generation(); check(epoch > 0, "missing capture epoch");
    std::uint64_t sequence = 0;
    auto packet = [&] {
        control::v1::CapturedDeviceEvent value; value.set_protocol_version(1); value.set_capture_generation(epoch);
        value.set_packet_sequence(++sequence); value.set_monotonic_nanoseconds(static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()));
        auto* event = value.mutable_event(); event->set_protocol_version(1); event->set_source_id("keyboard");
        event->set_source_generation(1); event->set_event_sequence(sequence); return value;
    };
    auto connection = packet(); connection.mutable_event()->mutable_connected()->set_kind(control::v1::KEYBOARD);
    host->device_event(0, connection);
    auto forward = packet(); forward.mutable_event()->mutable_key()->set_keysym("w"); forward.mutable_event()->mutable_key()->set_pressed(true);
    auto wrong = forward; wrong.set_capture_generation(epoch + 1); rejects([&] { host->device_event(0, wrong); });
    host->device_event(0, forward); rejects([&] { host->device_event(0, forward); }); host->tick_controls(0);
    host->propose(0, shared_stop()); host->tick_controls(0);
    auto key = [&](const std::string& name, bool down) {
        auto event = packet(); event.mutable_event()->mutable_key()->set_keysym(name);
        event.mutable_event()->mutable_key()->set_pressed(down); return host->device_event(0, event);
    };
    key("F10", true); check(host->controls_menu(0).visible(), "host did not expose its in-session menu state");
    key("Right", true); key("Right", false); check(saved == 1, "host did not persist the active menu choice");
    key("F10", false); key("F10", true); check(!host->controls_menu(0).visible(), "host menu did not close");
    host->tick_controls(0); key("w", false); key("w", true); host->tick_controls(0);
    host->propose(0, shared_stop()); host.reset(); source.finish();
}
void shared_transition_context(bool denied) {
    Fixture a, b; a.world = "world-a"; b.world = "world-b"; Endpoint source(a), destination(b);
    source.start([&](int fd) {
        handshake(fd, a); controlled_accept(fd, 3, 1, core::v1::ActorActionRequest::kStop); state(fd, a, 4);
        if (denied) { controlled_accept(fd, 5, 3, core::v1::ActorActionRequest::kStop); closed(fd); return; }
        const auto depart = receive(fd, pb::WorldSessionRequest::kDepart, 5);
        check(depart.depart().seq() == 2, "departure bypassed the shared sequence owner");
        auto reply = reply_for(5); auto* receipt = reply.mutable_departed(); receipt->set_actor_id(a.actor_id);
        receipt->set_world_id(a.world); receipt->set_transition_id(a.transition); send_reply(fd, reply); closed(fd);
    });
    destination.start([&](int fd) {
        handshake(fd, b, true, false, [&](auto& reply) {
            if (denied) { reply.clear_prepared(); reply.mutable_error()->set_code(core::v1::FORBIDDEN); }
        });
        if (denied) { closed(fd); return; }
        receive(fd, pb::WorldSessionRequest::kActivate, 3); auto reply = reply_for(3); opened(b, reply.mutable_opened()); send_reply(fd, reply);
        controlled_accept(fd, 4, 1, core::v1::ActorActionRequest::kStop); closed(fd);
    });
    auto host = shared_host(source, a); const auto old = host->interrupt_handle(0).lock();
    const auto old_epoch = host->control_status(0).capture_generation();
    host->register_remote_world(b.world, [&] { return std::move(destination.client); });
    auto request = request_for(a); request.clear_departure_sequence();
    const auto result = host->transition_proposed(0, request);
    check(result.actor_control_available() && host->generation() == 1 && host->control_available(), "shared transition lost its adopted context");
    check(host->control_status(1).capture_generation() > old_epoch, "world transition retained the capture epoch");
    old->request(); check(host->control_available(), "retired source watchdog interrupted the new context");
    rejects([&] { host->propose(0, shared_stop()); });
    check(host->propose(1, shared_stop()).accepted(), "new context was closed by retired control teardown");
    if (denied) check(result.actor_transition().phase() == pb::PREPARATION_REJECTED && host->current_world() == a.world && !old->requested(),
                      "denied preparation lost source identity or interruption revocation");
    else check(result.actor_transition().destination_activation_confirmed() && host->current_world() == b.world,
               "successful transition did not adopt destination actor context");
    host.reset(); source.finish(); destination.finish();
}

}

namespace {
control::v1::CapturedDeviceEvent worker_packet(const supervisor::HostActorWorker::Binding& binding,
                                             std::uint64_t sequence, bool connected = false) {
    control::v1::CapturedDeviceEvent packet;
    packet.set_protocol_version(1);packet.set_capture_generation(binding->capture_generation);
    packet.set_packet_sequence(sequence);packet.set_monotonic_nanoseconds(static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()));
    auto* event=packet.mutable_event();event->set_protocol_version(1);event->set_source_id("worker-keyboard");
    event->set_source_generation(1);event->set_event_sequence(sequence);
    if(connected)event->mutable_connected()->set_kind(control::v1::KEYBOARD);
    else event->mutable_heartbeat();
    return packet;
}
control::v1::CapturedDeviceEvent worker_mouse_motion_packet(
        const supervisor::HostActorWorker::Binding& binding,std::uint64_t sequence,double dx,double dy,
        bool connected=false) {
    control::v1::CapturedDeviceEvent packet;
    packet.set_protocol_version(1);packet.set_capture_generation(binding->capture_generation);
    packet.set_packet_sequence(sequence);packet.set_monotonic_nanoseconds(static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()));
    auto* event=packet.mutable_event();event->set_protocol_version(1);event->set_source_id("worker-mouse");
    event->set_source_generation(1);event->set_event_sequence(sequence);
    if(connected)event->mutable_connected()->set_kind(control::v1::MOUSE);
    else {event->mutable_motion()->set_dx(dx);event->mutable_motion()->set_dy(dy);}
    return packet;
}
void worker_relative_motion_coalesces_without_losing_counts() {
    Fixture fixture;Endpoint source(fixture,2s);
    source.start([&](int fd) {
        handshake(fd,fixture);
        const auto action=controlled_accept(fd,3,1,core::v1::ActorActionRequest::kRelativeLook);
        const auto& delta=action.relative_look().angular_delta();
        check(std::abs(delta.x()+.005)<1e-9 && std::abs(delta.y()+.0025)<1e-9,
              "Coalescing dropped or repeated relative mouse counts");
        closed(fd);
    });
    auto host=shared_host(source,fixture);
    {
        supervisor::HostActorWorker worker(*host);
        const auto binding=worker.capture_binding();
        check(worker.post_device(binding,worker_mouse_motion_packet(binding,1,0,0,true)),"Mouse source did not connect");
        const auto connected_deadline=std::chrono::steady_clock::now()+1s;
        while(binding->processed_sequence()<1 && std::chrono::steady_clock::now()<connected_deadline)
            std::this_thread::sleep_for(1ms);
        check(binding->processed_sequence()==1,"Mouse source connection was not processed");
        std::promise<void> entered,release;auto gate=release.get_future().share();
        auto blocked=worker.submit([&] {entered.set_value();gate.wait();});entered.get_future().wait();
        constexpr std::uint64_t samples=1000;
        const auto started=std::chrono::steady_clock::now();
        for(std::uint64_t index=0;index<samples;++index)
            check(worker.post_device(binding,worker_mouse_motion_packet(binding,index+2,.001,.002)),
                  "Coalesced motion queue applied backpressure below its bounded workload");
        const auto elapsed=std::chrono::steady_clock::now()-started;
        const double rate=samples/std::chrono::duration<double>(elapsed).count();
        std::cout<<"M10 coalesced motion samples per second: "<<rate<<'\n';
        release.set_value();blocked.get();
        const auto processed_deadline=std::chrono::steady_clock::now()+1s;
        while(binding->processed_sequence()<samples+1 && std::chrono::steady_clock::now()<processed_deadline)
            std::this_thread::sleep_for(1ms);
        check(binding->processed_sequence()==samples+1,"Coalesced motion lost the newest capture sequence");
        check(rate>=1000,"Motion queue did not accept 1000 samples per second");
        check(host->control_available(),"Motion coalescing retired healthy actor authority");
    }
    host.reset();source.finish();
}
void worker_serializes_concurrent_callers() {
    Fixture fixture; Endpoint source(fixture);
    source.start([&](int fd) {
        handshake(fd, fixture);
        for(unsigned i=0;i<16;++i)controlled_accept(fd,3+i,1+i,core::v1::ActorActionRequest::kStop);
        closed(fd);
    });
    auto host=shared_host(source,fixture);
    {
        supervisor::HostActorWorker worker(*host);
        std::vector<std::future<bool>> replies;
        for(unsigned i=0;i<16;++i)replies.push_back(std::async(std::launch::async,[&] {
            return worker.call([&] {return host->propose(0,shared_stop()).accepted();});
        }));
        for(auto& reply:replies)check(reply.get(),"Concurrent queued proposal was refused");
    }
    host.reset();source.finish();
}
void worker_ordinary_exception_preserves_type_and_context() {
    Fixture fixture;Endpoint source(fixture);
    source.start([&](int fd) {
        handshake(fd,fixture);
        controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);
        closed(fd);
    });
    auto host=shared_host(source,fixture);
    {
        supervisor::HostActorWorker worker(*host);bool original=false;
        try {worker.call([] {throw std::invalid_argument("ordinary worker failure");});}
        catch(const std::invalid_argument& error) {original=std::string(error.what())=="ordinary worker failure";}
        catch(...) {}
        check(original,"An ordinary exception was relabelled as actor interruption");
        check(host->control_available(),"An ordinary exception retired healthy actor authority");
        check(worker.call([&] {return host->propose(0,shared_stop()).accepted();}),
              "Healthy actor did not remain usable after an ordinary exception");
    }
    host.reset();source.finish();
}
void worker_queue_bound_and_overflow_cancellation() {
    Fixture fixture;Endpoint source(fixture,2s);
    source.start([&](int fd) {
        handshake(fd,fixture);
        controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);
        controlled_accept(fd,4,2,core::v1::ActorActionRequest::kStop);closed(fd);
    });
    auto host=shared_host(source,fixture);
    {
        supervisor::HostActorWorker worker(*host);
        const auto binding=worker.capture_binding();const auto pin=binding->interrupt.lock();
        check(worker.post_device(binding,worker_packet(binding,1,true)),"Current capture did not connect");
        worker.call([] {});
        std::promise<void> started,release,second_started,second_release;
        auto gate=release.get_future().share(),second_gate=second_release.get_future().share();
        auto blocked=worker.submit([&] {started.set_value();gate.wait();});
        started.get_future().wait();
        auto following=worker.submit([&] {second_started.set_value();second_gate.wait();});
        for(unsigned i=0;i<127;++i)check(worker.post_device(binding,worker_packet(binding,2+i)),"Queue refused before its documented bound");
        check(!worker.post_device(binding,worker_packet(binding,129)),"Queue exceeded its documented bound");
        release.set_value();blocked.get();
        auto second_ready=second_started.get_future();
        if(second_ready.wait_for(2s)!=std::future_status::ready) {
            second_release.set_value();following.get();throw std::runtime_error("Bound cancellation did not reach its following operation");
        }
        (void)following.wait_for(450ms);
        const bool interrupted=pin->requested();
        second_release.set_value();following.get();
        check(binding->retired() && !interrupted,"Retired queued input interrupted a later healthy actor operation");
        check(worker.call([&] {return host->propose(0,shared_stop()).accepted();}),"Overflow cancellation destroyed ordinary actor authority");
    }
    host.reset();source.finish();
}
void worker_retired_receiver_cannot_cancel_or_interrupt_adopted_context() {
    Fixture a,b;a.world="world-a";b.world="world-b";
    Endpoint source(a,2s),destination(b,2s);
    source.start([&](int fd) {
        handshake(fd,a);controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);state(fd,a,4);
        controlled_accept(fd,5,3,core::v1::ActorActionRequest::kStop);closed(fd);
    });
    destination.start([&](int fd) {
        handshake(fd,b,true,false,[&](auto& reply) {reply.clear_prepared();reply.mutable_error()->set_code(core::v1::FORBIDDEN);});
        closed(fd);
    });
    auto host=shared_host(source,a);
    host->register_remote_world(b.world,[&] {return std::move(destination.client);});
    {
        supervisor::HostActorWorker worker(*host);
        const auto old=worker.capture_binding();
        auto request=request_for(a);request.clear_departure_sequence();
        const auto transition=worker.call([&] {return host->transition_proposed(0,request);});
        check(transition.actor_control_available(),"Denied destination lost its retained source");
        const auto adopted=worker.capture_binding();const auto pin=adopted->interrupt.lock();
        check(old!=adopted && old->retired() && adopted->actor_generation==1 &&
            adopted->capture_generation>old->capture_generation,"Worker did not publish its adopted capture epoch");
        std::promise<void> started,release;auto gate=release.get_future().share();
        auto blocked=std::async(std::launch::async,[&] {worker.call([&] {started.set_value();gate.wait();});});
        started.get_future().wait();
        bool rejected=true;
        for(unsigned i=0;i<129;++i)rejected=!worker.post_device(old,worker_packet(old,1+i))&&rejected;
        rejected=!worker.request_cancel(old)&&rejected;
        auto stale_rpc=worker.submit([] {return true;},old);
        (void)blocked.wait_for(450ms);
        const bool interrupted=pin->requested();
        release.set_value();blocked.get();check(stale_rpc.get(),"Stale urgency test did not finish its harmless queued operation");
        check(rejected && !interrupted,"Retired capture affected the adopted actor or its watchdog");
        check(worker.call([&] {return host->propose(1,shared_stop()).accepted();}),"Adopted actor was closed by old receiver traffic");
    }
    host.reset();source.finish();destination.finish();
}
void worker_malformed_current_capture_retires_before_more_delivery() {
    Fixture fixture;Endpoint source(fixture,2s);
    source.start([&](int fd) {
        handshake(fd,fixture);controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);closed(fd);
    });
    auto host=shared_host(source,fixture);
    {
        supervisor::HostActorWorker worker(*host);const auto binding=worker.capture_binding();
        // A heartbeat cannot precede the source's Connected event.
        check(worker.post_device(binding,worker_packet(binding,1)),"Current packet did not reach semantic validation");
        worker.call([] {}); // Bound cancellation and its checked reply precede this barrier.
        check(binding->retired(),"Malformed current capture remained active");
        check(!worker.post_device(binding,worker_packet(binding,2)),"Retired malformed source delivered more input");
        bool rejected=false;
        try {worker.call([&] {return worker.device_event(binding,worker_packet(binding,2,true));});}
        catch(const std::invalid_argument&) {rejected=true;}
        check(rejected,"Synchronous human RPC bypassed retired capture fencing");
        check(host->control_available(),"Checked capture cancellation destroyed ordinary actor authority");
    }
    host.reset();source.finish();
}
void worker_queue_empty_cancellation_has_its_own_watchdog_urgency() {
    Fixture fixture;Endpoint source(fixture,2s);
    source.start([&](int fd) {
        handshake(fd,fixture);const auto request=receive(fd,pb::WorldSessionRequest::kControlAction,3);
        check(request.control_action().has_stop(),"Capture failure did not use ordinary actor cancellation");
        closed(fd); // Deliberately withhold the Stop reply.
    });
    auto host=shared_host(source,fixture);
    {
        supervisor::HostActorWorker worker(*host);const auto binding=worker.capture_binding();const auto pin=binding->interrupt.lock();
        const auto start=std::chrono::steady_clock::now();
        check(worker.request_cancel(binding),"Current empty-queue cancellation was refused");
        source.finish(); // EOF must arrive while the worker and controller still exist.
        const auto elapsed=std::chrono::steady_clock::now()-start;
        check(elapsed>=300ms && elapsed<1500ms && pin->requested(),"Cancellation flight lacked its own watchdog urgency");
        check(!worker.call([&] {return host->control_available();}),"Uncertain cancellation left actor control available");
    }
    host.reset();
}
void worker_presentation_receipt_cannot_publish_after_actor_interruption() {
    Fixture fixture;Endpoint source(fixture,2s);
    source.start([&](int fd) {
        handshake(fd,fixture);controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);closed(fd);
    });
    std::promise<void> presenting;
    auto host=shared_host(source,fixture,[](const auto&) {},[&](auto,const auto& view) {
        if(view.visible()) {presenting.set_value();std::this_thread::sleep_for(500ms);}
    });
    {
        supervisor::HostActorWorker worker(*host);const auto binding=worker.capture_binding();const auto pin=binding->interrupt.lock();
        check(worker.post_device(binding,worker_packet(binding,1,true)),"Publication test capture did not connect");
        worker.call([] {});
        auto opening=worker.submit([&] {
            auto packet=worker_packet(binding,2);packet.mutable_event()->mutable_key()->set_keysym("F10");
            packet.mutable_event()->mutable_key()->set_pressed(true);
            return worker.device_event(binding,packet).menu_open();
        },binding);
        check(presenting.get_future().wait_for(1s)==std::future_status::ready,"Menu publication did not reach its real host callback");
        source.finish(); // Exact actor EOF occurs during the blocked presentation receipt.
        bool rejected=false;
        try {(void)opening.get();}catch(const std::exception&) {rejected=true;}
        check(rejected && pin->requested(),"A healthy result escaped after actor interruption during presentation");
        check(!worker.call([&] {return host->control_available();}),"Interrupted presentation retained healthy actor status");
    }
    host.reset();
}
void worker_checked_transition_release_disarms_old_capture_urgency() {
    for (const bool menu : {false, true}) {
        Fixture a, b; a.world="world-a"; b.world="world-b";
        Endpoint source(a,2s), destination(b,2s);
        std::promise<void> entered, release; auto gate=release.get_future().share();
        std::atomic<bool> armed{false};
        source.start([&](int fd) {
            handshake(fd,a);std::uint64_t id=3,sequence=1;
            if(menu)controlled_accept(fd,id++,sequence++,core::v1::ActorActionRequest::kStop);
            controlled_accept(fd,id++,sequence++,core::v1::ActorActionRequest::kStop);
            if(menu)controlled_accept(fd,id++,sequence++,core::v1::ActorActionRequest::kStop);
            state(fd,a,id++);
            const auto departure=receive(fd,pb::WorldSessionRequest::kDepart,id);
            check(departure.depart().seq()==sequence,"Released transition changed departure sequencing");
            auto reply=reply_for(id);auto* receipt=reply.mutable_departed();
            receipt->set_actor_id(a.actor_id);receipt->set_world_id(a.world);receipt->set_transition_id(a.transition);
            send_reply(fd,reply);closed(fd);
        });
        destination.start([&](int fd) {
            handshake(fd,b,true);receive(fd,pb::WorldSessionRequest::kActivate,3);
            auto reply=reply_for(3);opened(b,reply.mutable_opened());send_reply(fd,reply);
            controlled_accept(fd,4,1,core::v1::ActorActionRequest::kStop);closed(fd);
        });
        auto host=shared_host(source,a,[](const auto&) {},[&](auto,const auto& view) {
            if(menu && !view.visible() && armed.exchange(false)){entered.set_value();gate.wait();}
        });
        host->register_remote_world(b.world,[&] {
            if(!menu){entered.set_value();gate.wait();}
            return std::move(destination.client);
        });
        {
            supervisor::HostActorWorker worker(*host);const auto binding=worker.capture_binding();
            const auto pin=binding->interrupt.lock();
            if(menu) {
                check(worker.post_device(binding,worker_packet(binding,1,true)),"Menu source did not connect");
                worker.call([] {});
                worker.call([&] {
                    auto packet=worker_packet(binding,2);packet.mutable_event()->mutable_key()->set_keysym("F10");
                    packet.mutable_event()->mutable_key()->set_pressed(true);
                    check(worker.device_event(binding,packet).menu_open(),"Transition menu did not open");
                },binding);
            }
            armed.store(menu);auto proposal=request_for(a);proposal.clear_departure_sequence();
            auto transition=worker.submit([&] {return worker.transition_proposed(0,proposal);},binding,true);
            const bool reached=entered.get_future().wait_for(1s)==std::future_status::ready;
            if(!reached){release.set_value();(void)transition.get();throw std::runtime_error("Released transition did not reach its gate");}
            // A queued old Stop must not rearm the release deadline.
            auto queued=worker.submit([] {return true;},binding,true);
            const bool waiting=transition.wait_for(500ms)==std::future_status::timeout;
            const bool interrupted=pin->requested();
            const bool retired=binding->retired();
            release.set_value();const auto result=transition.get();
            check(waiting && !interrupted,"Checked release retained old capture urgency during a healthy transition");
            check(retired!=menu,"Capture retirement did not follow validated generation advancement");
            check(result.actor_control_available() && result.actor_transition().destination_activation_confirmed(),
                "Released transition did not complete checked departure and activation");
            check(queued.get() && binding->retired(),"Retired queued cancellation affected the adopted context");
            check(worker.call([&] {return host->propose(1,shared_stop()).accepted();}),"Adopted actor lost ordinary authority");
        }
        host.reset();source.finish();destination.finish();
    }
}
void worker_transition_withheld_release_keeps_watchdog() {
    Fixture fixture;Endpoint source(fixture,2s);std::atomic<bool> destination_called{false};
    source.start([&](int fd) {
        handshake(fd,fixture);const auto request=receive(fd,pb::WorldSessionRequest::kControlAction,3);
        check(request.control_action().has_stop(),"Transition did not request ordinary Stop first");closed(fd);
    });
    auto host=shared_host(source,fixture);
    host->register_remote_world("world-b",[&]() -> std::unique_ptr<ActorSessionClient> {destination_called.store(true);return {};});
    {
        supervisor::HostActorWorker worker(*host);const auto binding=worker.capture_binding();const auto pin=binding->interrupt.lock();
        auto proposal=request_for(fixture);proposal.clear_departure_sequence();const auto started=std::chrono::steady_clock::now();
        auto result=worker.submit([&] {return worker.transition_proposed(0,proposal);},binding,true);
        source.finish();bool rejected=false;try{(void)result.get();}catch(const std::exception&){rejected=true;}
        const auto elapsed=std::chrono::steady_clock::now()-started;
        check(rejected && pin->requested() && elapsed>=300ms && elapsed<1500ms && !destination_called,
            "Withheld transition release escaped its350ms input watchdog");
    }
    host.reset();
}
void worker_watchdog_interrupts_without_actor_locks() {
    Fixture fixture; Endpoint source(fixture,2s);
    std::promise<void> waiting;
    source.start([&](int fd) {
        handshake(fd,fixture);
        const auto request=receive(fd,pb::WorldSessionRequest::kControlAction,3);
        check(request.control_action().has_stop() && request.control_action().seq()==1,"Watchdog test changed the ordinary actor request");
        waiting.set_value();closed(fd); // No reply. The exact transport must be interrupted.
    });
    auto host=shared_host(source,fixture);const auto pin=host->interrupt_handle(0).lock();
    {
        supervisor::HostActorWorker worker(*host);
        const auto start=std::chrono::steady_clock::now();
        auto result=std::async(std::launch::async,[&] {
            try {worker.call([&] {return host->propose(0,shared_stop());},worker.capture_binding(),true);return false;}
            catch(const supervisor::HostActorContextInterrupted&) {return true;}
            catch(...) {return false;}
        });
        waiting.get_future().wait();
        check(result.wait_for(2s)==std::future_status::ready && result.get(),"Watchdog did not interrupt blocked actor work");
        const auto elapsed=std::chrono::steady_clock::now()-start;
        check(elapsed>=300ms && elapsed<2s && pin->requested(),"Watchdog bypassed its exact-session deadline");
        check(!host->control_available(),"Uncertain interrupted actor remained available");
        // Confirm peer EOF while the worker and controller still exist.
        source.finish();
    }
    host.reset();
}
supervisor::HostActorWorker::CaptureStart capture_pair_factory(std::unique_ptr<control::DeviceStream>& producer) {
    return [&producer](const auto& binding) {
        int pair[2];check(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,pair)==0,"Capture fixture socketpair failed");
        PresentationStream::File first(pair[0]),second(pair[1]);
        producer=std::make_unique<control::DeviceStream>(first.release(),getpid(),binding->capture_generation,control::DeviceStream::Role::Capture);
        return supervisor::HostActorWorker::CaptureEndpoint{
            std::make_unique<control::DeviceStream>(second.release(),getpid(),binding->capture_generation,control::DeviceStream::Role::Controller),1};
    };
}
control::v1::DeviceEvent receiver_event(std::uint64_t sequence,bool connect=false) {
    control::v1::DeviceEvent event;event.set_protocol_version(1);event.set_source_id("independent-keyboard");
    event.set_source_generation(1);event.set_event_sequence(sequence);
    if(connect)event.mutable_connected()->set_kind(control::v1::KEYBOARD);else event.mutable_heartbeat();
    return event;
}
void worker_heartbeat_only_wait_keeps_real_cancellation(unsigned cause) {
    // Cause1 is a fresh F12 gesture;2 is actual source silence;3 is a malformed source heartbeat.
    Fixture fixture;Endpoint source(fixture,3s);
    source.start([&](int fd){handshake(fd,fixture);closed(fd);});
    auto host=shared_host(source,fixture);std::unique_ptr<control::DeviceStream> producer;
    {
        supervisor::HostActorWorker worker(*host,capture_pair_factory(producer),[](const auto&){});
        const auto binding=worker.capture_binding();const auto pin=binding->interrupt.lock();
        std::atomic<unsigned> requested{0};std::atomic<bool> issued{false},premature_failure{false};
        std::jthread emitter([&](std::stop_token stop) {
            try {
                std::uint64_t sequence=1;
                producer->emit(receiver_event(sequence,true),std::chrono::steady_clock::now());
                while(!stop.stop_requested()) {
                    std::this_thread::sleep_for(100ms);if(stop.stop_requested())break;
                    const auto failure=requested.load();
                    if(failure==2){issued.store(true);break;} // Keep the endpoint open for real silence.
                    auto event=receiver_event(++sequence);
                    if(failure==1) {
                        event.mutable_key()->set_keysym("F12");event.mutable_key()->set_pressed(true);
                    } else if(failure==3)event.set_source_generation(2);
                    producer->emit(event,std::chrono::steady_clock::now());
                    if(failure){issued.store(true);break;}
                }
            }catch(...){if(!requested.load())premature_failure.store(true);}
        });
        const auto connected=std::chrono::steady_clock::now()+1s;
        while(binding->processed_sequence()<1 && std::chrono::steady_clock::now()<connected)
            std::this_thread::sleep_for(5ms);
        check(binding->processed_sequence()>=1,"Heartbeat watchdog test did not confirm its source connection");
        worker.call([]{}); // Initial Connected work cannot supply urgency in the gated phase.
        std::promise<void> entered,release;auto gate=release.get_future().share();
        struct Release {std::promise<void>& value;~Release(){try{value.set_value();}catch(...){}}} recovery{release};
        auto blocked=worker.submit([&]{entered.set_value();gate.wait();});
        check(entered.get_future().wait_for(1s)==std::future_status::ready,"Heartbeat watchdog foreground did not start");
        std::this_thread::sleep_for(450ms);
        const bool heartbeat_healthy=!pin->requested() && !binding->retired() && !premature_failure.load();
        requested.store(cause);
        const auto cancelled=std::chrono::steady_clock::now()+1500ms;
        while((!pin->requested() || !binding->retired() || !issued.load()) && std::chrono::steady_clock::now()<cancelled)
            std::this_thread::sleep_for(5ms);
        const bool exact_cancel=pin->requested() && binding->retired() && issued.load();
        release.set_value();
        bool healthy_result=false;try{blocked.get();healthy_result=true;}catch(const std::exception&){}
        emitter.request_stop();emitter.join();
        const bool clean=worker.shutdown();source.finish();
        check(heartbeat_healthy,"Heartbeat-only traffic promoted unrelated foreground work to cancellation urgency");
        check(exact_cancel,"Real input/source failure lost exact watchdog interruption and retirement");
        check(!healthy_result,"Interrupted foreground work published a healthy result");
        check(clean,"Heartbeat watchdog test left capture cleanup unresolved");
        check(!host->control_available(),"Interrupted actor context remained available");
    }
    producer.reset();host.reset();
}
void worker_pending_capture_retirement_keeps_root_calls_available() {
    Fixture fixture;Endpoint source(fixture,2s);
    source.start([&](int fd){handshake(fd,fixture);
        controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);closed(fd);});
    auto host=shared_host(source,fixture);std::unique_ptr<control::DeviceStream> producer;
    std::atomic<bool> release{false};std::atomic<unsigned> polls{0};
    {
        supervisor::HostActorWorker worker(*host,capture_pair_factory(producer),
            [](const auto&){throw std::runtime_error("Pending capture used synchronous fallback");},
            [&](const auto&){++polls;return release.load();});
        const auto binding=worker.capture_binding();check(worker.request_cancel(binding),"Capture cancellation was not queued");
        const auto deadline=std::chrono::steady_clock::now()+1s;
        while(!polls.load() && std::chrono::steady_clock::now()<deadline)std::this_thread::sleep_for(2ms);
        check(polls.load()>0,"Capture retirement did not enter its actual pending path");
        auto status=worker.submit([&]{return host->control_available();});
        check(status.wait_for(300ms)==std::future_status::ready && status.get(),
            "Pending capture retirement blocked root calls or retired actor authority");
        auto stopping=std::async(std::launch::async,[&]{return worker.shutdown();});
        check(stopping.wait_for(50ms)==std::future_status::timeout,"Capture cleanup was reported before its receipt");
        release.store(true);check(stopping.get(),"Checked capture completion did not settle retained cleanup");
        source.finish();
    }
    producer.reset();host.reset();
}
void worker_shutdown_cannot_publish_a_cached_nonvoid_result() {
    Fixture fixture;Endpoint source(fixture,2s);
    source.start([&](int fd){handshake(fd,fixture);closed(fd);});
    auto host=shared_host(source,fixture);
    {
        supervisor::HostActorWorker worker(*host);
        const auto binding=worker.capture_binding();const auto pin=binding->interrupt.lock();
        std::promise<void> entered,release;auto gate=release.get_future().share();
        struct Release {std::promise<void>& value;~Release(){try{value.set_value();}catch(...){}}} recovery{release};
        auto blocked=worker.submit([&]{entered.set_value();gate.wait();return true;});
        check(entered.get_future().wait_for(1s)==std::future_status::ready,"Shutdown test did not enter its cached operation");
        auto stopping=std::async(std::launch::async,[&]{return worker.shutdown();});
        const auto deadline=std::chrono::steady_clock::now()+1s;
        while(!pin->requested() && std::chrono::steady_clock::now()<deadline)std::this_thread::sleep_for(5ms);
        const bool interrupted=pin->requested();release.set_value();
        bool healthy=false;try{healthy=blocked.get();}catch(const std::exception&){}
        const bool clean=stopping.get();source.finish();
        check(interrupted && !healthy && clean && !host->control_available(),
            "Worker shutdown published a cached nonvoid result after interruption");
    }
    host.reset();
}
void worker_independent_capture_and_silence() {
    Fixture fixture;Endpoint source(fixture,2s);
    std::atomic<unsigned> checked_stops{0};
    source.start([&](int fd) {
        handshake(fd,fixture);
        for(unsigned sequence=1;sequence<=2;++sequence) {
            controlled_accept(fd,sequence+2,sequence,core::v1::ActorActionRequest::kStop);
            checked_stops.store(sequence);
            pollfd input{fd,POLLIN,0};check(poll(&input,1,1500)>0,"Silence fixture did not reach cancellation or EOF");
            char byte;const auto size=recv(fd,&byte,1,MSG_PEEK|MSG_DONTWAIT);
            if(size==0)return;
            check(size>0 && sequence<2,"Silence produced more than two fresh checked Stops");
        }
    });
    auto host=shared_host(source,fixture);std::unique_ptr<control::DeviceStream> producer;unsigned stopped=0;
    {
        supervisor::HostActorWorker worker(*host,capture_pair_factory(producer),[&](const auto&){++stopped;});
        const auto binding=worker.capture_binding();std::atomic<bool> done{false},emitter_failed{false};std::atomic<std::uint64_t> emitted{0};
        std::jthread emitter([&](std::stop_token stop) {
            try {
                std::uint64_t sequence=1;producer->emit(receiver_event(sequence,true),std::chrono::steady_clock::now());emitted.store(sequence);
                while(!done.load() && !stop.stop_requested()) {
                    std::this_thread::sleep_for(100ms);if(done.load() || stop.stop_requested())break;
                    producer->emit(receiver_event(++sequence),std::chrono::steady_clock::now());emitted.store(sequence);
                }
            }catch(const std::exception&){emitter_failed.store(true);}
        });
        // The parent can wait while this receiver continues accepting100ms capture.
        std::this_thread::sleep_for(450ms);
        worker.call([]{std::this_thread::sleep_for(180ms);});
        const auto status=worker.call([&]{return worker.control_status(0);});
        done.store(true);emitter.join();
        check(!emitter_failed.load() && status.capture_active() && !status.capture_retired() && status.capture_source_slot()==1 &&
            status.processed_capture_sequence()>=3 && emitted.load()>=5,"Independent capture did not remain healthy while the parent waited");
        // Keep the endpoint open. Actual source silence must still retire it.
        const auto deadline=std::chrono::steady_clock::now()+1500ms;
        while(!binding->retired() && std::chrono::steady_clock::now()<deadline)std::this_thread::sleep_for(10ms);
        check(binding->retired(),"Independent receiver ignored350ms active-source silence");
        worker.call([]{});
        const bool available=host->control_available();const bool clean=worker.shutdown();
        source.finish(); // Surface peer errors before testing cached pre-shutdown availability.
        check(available && clean && stopped==1 && checked_stops.load()>=1 && checked_stops.load()<=2,
            "Silence lost checked ordinary cancellation or capture cleanup");
    }
    producer.reset();host.reset();
}
void worker_both_silence_detector_orders_keep_checked_authority() {
    for(const bool adapter_first:{false,true}) {
        Fixture fixture;Endpoint source(fixture,2s);
        source.start([&](int fd) {
            handshake(fd,fixture);controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);
            if(adapter_first)controlled_accept(fd,4,2,core::v1::ActorActionRequest::kStop);
            closed(fd);
        });
        auto host=shared_host(source,fixture);
        {
            supervisor::HostActorWorker worker(*host);const auto binding=worker.capture_binding();
            worker.call([&] {
                auto packet=worker_packet(binding,1,true);
                packet.set_monotonic_nanoseconds(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                    (std::chrono::steady_clock::now()-400ms).time_since_epoch()).count()));
                (void)worker.device_event(binding,packet);
                if(adapter_first)(void)host->tick_controls(0); // Actual adapter expiry before receiver retirement.
                worker.capture_failed(binding); // Actual receiver failure entrypoint.
            },binding);
            check(binding->retired(),"Receiver did not publish ingress retirement");
            worker.call([]{}); // Retirement publication must precede pending cancellation.
            const bool available=host->control_available();worker.shutdown();source.finish();
            check(available,"A valid detector ordering lost ordinary actor authority");
        }
        host.reset();
    }
}
void worker_drains_buffered_capture_before_source_expiry() {
    Fixture fixture;Endpoint source(fixture,3s);
    source.start([&](int fd) {
        handshake(fd,fixture);
        // A false local timeout must not manufacture a Stop while capture is healthy.
        for(unsigned sequence=1;sequence<=2;++sequence) {
            pollfd item{fd,POLLIN,0};check(poll(&item,1,3000)>0,"Buffered capture peer did not close");
            char byte;const auto count=recv(fd,&byte,1,MSG_PEEK|MSG_DONTWAIT);
            if(count==0)return;
            controlled_accept(fd,sequence+2,sequence,core::v1::ActorActionRequest::kStop);
        }
        closed(fd);
    });
    auto host=shared_host(source,fixture);std::unique_ptr<control::DeviceStream> producer;
    {
        supervisor::HostActorWorker worker(*host,capture_pair_factory(producer),[](const auto&){});
        const auto binding=worker.capture_binding();std::atomic<bool> failed{false};
        std::jthread emitter([&](std::stop_token stop) {
            try {
                std::uint64_t sequence=1;
                auto emit=[&](bool connected) {
                    for(const auto* name:{"buffered-keyboard","buffered-mouse"}) {
                        auto event=receiver_event(sequence,connected);event.set_source_id(name);
                        if(connected)event.mutable_connected()->set_kind(std::string(name)=="buffered-keyboard" ? control::v1::KEYBOARD : control::v1::MOUSE);
                        producer->emit(event,std::chrono::steady_clock::now());
                    }
                    ++sequence;
                };
                emit(true);
                while(!stop.stop_requested()) {std::this_thread::sleep_for(50ms);if(!stop.stop_requested())emit(false);}
            }catch(...){failed.store(true);}
        });
        const auto deadline=std::chrono::steady_clock::now()+1s;
        while(binding->processed_sequence()<2 && std::chrono::steady_clock::now()<deadline)std::this_thread::sleep_for(5ms);
        check(binding->processed_sequence()>=2,"Buffered capture did not connect both independent sources");
        // Ordinary work owns no input urgency. The receiver continues validating fresh heartbeats.
        worker.call([] {std::this_thread::sleep_for(900ms);});
        const auto status=worker.call([&] {return worker.control_status(0);});
        const bool healthy=status.capture_active() && !status.capture_retired() && !failed.load();
        emitter.request_stop();emitter.join();
        const auto silence_deadline=std::chrono::steady_clock::now()+1500ms;
        while(!binding->retired() && std::chrono::steady_clock::now()<silence_deadline)std::this_thread::sleep_for(5ms);
        const bool silence_retired=binding->retired();
        worker.call([]{});
        check(worker.shutdown(),"Buffered capture lost checked shutdown");source.finish();
        check(healthy,"Buffered healthy capture was expired before its queued heartbeats were consumed");
        check(silence_retired,"Buffered liveness masked later actual source silence");
    }
    producer.reset();host.reset();
}
void worker_capture_start_failure_preserves_actor_and_cleanup_obligations() {
    for(const bool cleanup_failure:{false,true}) {
        Fixture fixture;Endpoint source(fixture,2s);
        source.start([&](int fd){handshake(fd,fixture);controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);closed(fd);});
        auto host=shared_host(source,fixture);unsigned retirements=0;
        {
            supervisor::HostActorWorker worker(*host,[](const auto&) -> supervisor::HostActorWorker::CaptureEndpoint {
                throw std::runtime_error("Fixture capture activation failure");
            },[&](const auto&){++retirements;if(cleanup_failure)throw std::runtime_error("Fixture capture cleanup uncertainty");});
            const auto binding=worker.capture_binding();worker.call([]{});
            const auto status=worker.call([&]{return worker.control_status(0);});
            check(binding && status.capture_retired() && !status.capture_active() && status.available(),
                "Capture activation failure lost actor authority or claimed live input");
            const bool clean=worker.shutdown();source.finish();
            check(clean!=cleanup_failure && retirements>=1,"Capture startup failure lost its checked cleanup obligation");
        }
        host.reset();
    }
}
void worker_capture_without_sources_does_not_expire() {
    Fixture fixture;Endpoint source(fixture,2s);source.start([&](int fd){handshake(fd,fixture);closed(fd);});
    auto host=shared_host(source,fixture);std::unique_ptr<control::DeviceStream> producer;unsigned stopped=0;
    {
        supervisor::HostActorWorker worker(*host,capture_pair_factory(producer),[&](const auto&){++stopped;});
        std::this_thread::sleep_for(500ms);
        const auto status=worker.call([&]{return worker.control_status(0);});
        check(status.capture_active() && !status.capture_retired() && status.processed_capture_sequence()==0,
            "An empty native source population became a false source-loss failure");
        check(worker.shutdown() && stopped==1,"Empty capture did not retire once with checked cleanup");
    }
    producer.reset();host.reset();source.finish();
}
void worker_capture_eof_and_malformed_are_bound() {
    for(const bool malformed:{false,true}) {
        Fixture fixture;Endpoint source(fixture,2s);
        source.start([&](int fd){handshake(fd,fixture);controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);closed(fd);});
        auto host=shared_host(source,fixture);std::unique_ptr<control::DeviceStream> producer;
        {
            supervisor::HostActorWorker worker(*host,capture_pair_factory(producer),[](const auto&){});
            const auto binding=worker.capture_binding();producer->emit(receiver_event(1,true),std::chrono::steady_clock::now());
            const auto connected=std::chrono::steady_clock::now()+1s;
            while(binding->processed_sequence()<1 && std::chrono::steady_clock::now()<connected)std::this_thread::sleep_for(5ms);
            check(binding->processed_sequence()==1,"Capture connection lacked its checked progress receipt");
            if(malformed)check(send(producer->descriptor(),"\x80",1,MSG_NOSIGNAL)==1,"Malformed capture was not sent");
            else producer.reset();
            const auto deadline=std::chrono::steady_clock::now()+1500ms;
            while(!binding->retired() && std::chrono::steady_clock::now()<deadline)std::this_thread::sleep_for(5ms);
            check(binding->retired(),"EOF or malformed capture did not retire its exact binding");
            worker.call([]{});
            check(binding->processed_sequence()==1 && host->control_available() && worker.shutdown(),
                "Capture failure acknowledged malformed input or lost checked cleanup");
        }
        producer.reset();host.reset();source.finish();
    }
}
void worker_capture_progress_waits_for_output_barrier() {
    Fixture fixture;Endpoint source(fixture,2s);
    source.start([&](int fd){handshake(fd,fixture);controlled_accept(fd,3,1,core::v1::ActorActionRequest::kStop);closed(fd);});
    std::promise<void> presenting;
    auto host=shared_host(source,fixture,[](const auto&){},[&](auto,const auto& view){
        if(view.visible()){presenting.set_value();std::this_thread::sleep_for(500ms);}
    });
    std::unique_ptr<control::DeviceStream> producer;
    {
        supervisor::HostActorWorker worker(*host,capture_pair_factory(producer),[](const auto&){});
        const auto binding=worker.capture_binding();producer->emit(receiver_event(1,true),std::chrono::steady_clock::now());
        const auto connected=std::chrono::steady_clock::now()+1s;
        while(binding->processed_sequence()<1 && std::chrono::steady_clock::now()<connected)std::this_thread::sleep_for(5ms);
        check(binding->processed_sequence()==1,"Output test did not confirm its connection");
        auto event=receiver_event(2);event.mutable_key()->set_keysym("F10");event.mutable_key()->set_pressed(true);
        producer->emit(event,std::chrono::steady_clock::now());
        check(presenting.get_future().wait_for(1s)==std::future_status::ready,"Output test did not reach the presentation callback");
        check(binding->processed_sequence()==1,"Raw capture reception acknowledged unfinished native output");
        source.finish();worker.call([]{});
        check(binding->processed_sequence()==1 && !host->control_available(),"Interrupted output published a processed capture receipt");
        check(worker.shutdown(),"Interrupted output lost capture retirement");
    }
    producer.reset();host.reset();
}
}

int main(int argc, char** argv) {
    signal(SIGPIPE, SIG_IGN);
    try {
        if(argc==2 && std::string(argv[1])=="--worker-only") {
            worker_serializes_concurrent_callers();std::cout<<"PASS worker-serializes-concurrent-callers\n";
            worker_ordinary_exception_preserves_type_and_context();std::cout<<"PASS worker-ordinary-exception-preserves-type-and-context\n";
            worker_queue_bound_and_overflow_cancellation();std::cout<<"PASS worker-bounded-queue-overflow-and-retired-input-urgency\n";
            worker_relative_motion_coalesces_without_losing_counts();std::cout<<"PASS worker-relative-motion-coalescing-preserves-counts-at-1kHz\n";
            worker_retired_receiver_cannot_cancel_or_interrupt_adopted_context();std::cout<<"PASS worker-retired-receiver-cannot-affect-adopted-context\n";
            worker_malformed_current_capture_retires_before_more_delivery();std::cout<<"PASS worker-malformed-current-capture-retires-and-cancels\n";
            worker_queue_empty_cancellation_has_its_own_watchdog_urgency();std::cout<<"PASS worker-queue-empty-cancellation-watchdog\n";
            worker_presentation_receipt_cannot_publish_after_actor_interruption();std::cout<<"PASS worker-presentation-receipt-interruption-barrier\n";
            worker_checked_transition_release_disarms_old_capture_urgency();std::cout<<"PASS worker-checked-transition-release-disarms-all-old-urgency\n";
            worker_transition_withheld_release_keeps_watchdog();std::cout<<"PASS worker-transition-withheld-release-keeps-watchdog\n";
            worker_watchdog_interrupts_without_actor_locks();std::cout<<"PASS worker-exact-session-watchdog-without-actor-locks\n";
            worker_independent_capture_and_silence();std::cout<<"PASS worker-independent-capture-and-source-silence\n";
            worker_drains_buffered_capture_before_source_expiry();std::cout<<"PASS worker-buffered-capture-before-source-expiry\n";
            worker_both_silence_detector_orders_keep_checked_authority();std::cout<<"PASS worker-both-silence-detector-orders-checked\n";
            worker_capture_start_failure_preserves_actor_and_cleanup_obligations();std::cout<<"PASS worker-capture-start-failure-authority-and-cleanup\n";
            worker_capture_without_sources_does_not_expire();std::cout<<"PASS worker-empty-capture-does-not-expire\n";
            worker_capture_eof_and_malformed_are_bound();std::cout<<"PASS worker-capture-eof-and-malformed-exact-binding\n";
            worker_capture_progress_waits_for_output_barrier();std::cout<<"PASS worker-capture-progress-output-interruption-barrier\n";
            worker_heartbeat_only_wait_keeps_real_cancellation(1);std::cout<<"PASS worker-heartbeat-only-wait-then-fresh-key-watchdog\n";
            worker_heartbeat_only_wait_keeps_real_cancellation(2);std::cout<<"PASS worker-heartbeat-only-wait-then-real-source-silence\n";
            worker_heartbeat_only_wait_keeps_real_cancellation(3);std::cout<<"PASS worker-heartbeat-only-wait-then-malformed-source-heartbeat\n";
            worker_pending_capture_retirement_keeps_root_calls_available();std::cout<<"PASS worker-pending-capture-retirement-keeps-root-calls-available\n";
            worker_shutdown_cannot_publish_a_cached_nonvoid_result();std::cout<<"PASS worker-shutdown-interrupted-cached-nonvoid-result\n";
            return 0;
        }
        if (argc == 2 && std::string(argv[1]) == "--shared-only") {
            shared_proposals_and_denial(); std::cout << "PASS shared-proposals-sequences-checked-denial-and-legacy-refusal\n";
            shared_durable_errors_remain_terminal(); std::cout << "PASS shared-durable-errors-terminal\n";
            shared_capture_menu_and_stop_cache(); std::cout << "PASS shared-capture-epochs-menu-stop-cache\n";
            shared_transition_context(true); std::cout << "PASS shared-denied-preparation-source-sequence-watchdog\n";
            shared_transition_context(false); std::cout << "PASS shared-transition-destination-sequence-and-teardown\n";
            return 0;
        }
        setup_exception_does_not_wedge();
        std::cout << "PASS controller-setup-exception-does-not-wedge\n";
        inspect_fences_transition();
        std::cout << "PASS controller-inspect-fences-transition-and-action\n";
        aba_and_concurrency();
        std::cout << "PASS controller-A-B-A-exact-owners-validation-and-concurrency\n";
        controller_case(false, false);
        std::cout << "PASS controller-success-and-no-premature-selection\n";
        controller_case(true, false);
        std::cout << "PASS controller-denied-preparation-preserves-source\n";
        wrong_mapping();
        std::cout << "PASS controller-wrong-destination-mapping\n";
        for (const auto* fault : {"departure", "activation", "selection-mismatch", "selection-nonstd"}) {
            controller_case(false, false, fault);
            std::cout << "PASS controller-uncertainty-no-replay-" << fault << '\n';
        }
        controller_case(false, true);
        std::cout << "PASS controller-activation-with-selection-uncertainty\n";
        std::cout << "FIXTURE ONLY: actor channels are local wire peers; SessionCoordinator and native presentation are untested\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
