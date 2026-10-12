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

}

int main() {
    signal(SIGPIPE, SIG_IGN);
    try {
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
