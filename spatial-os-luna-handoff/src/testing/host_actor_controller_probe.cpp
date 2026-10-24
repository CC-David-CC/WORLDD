#define main actor_transition_fixture_main
#include "actor_transition_coordinator_probe.cpp"
#undef main
#include "supervisor/host_actor_controller.hpp"
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
    control::ControlsMenu::Persist persist = [](const auto&) {}) {
    source.client->authenticate();
    auto result = std::make_unique<supervisor::HostActorController>(
        [](const auto&) { return presentation("world-b", render::v1::ATTACHED); },
        [](const auto&) { return presentation("world-b", render::v1::READY, true); },
        fixture.world, std::move(source.client));
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

int main(int argc, char** argv) {
    signal(SIGPIPE, SIG_IGN);
    try {
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
