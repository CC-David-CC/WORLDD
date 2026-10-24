// Callback peers test the shared boundary. They do not qualify native execution.
#include "control/actor_control.hpp"
#include <atomic>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <vector>

using namespace spatial;
using control::ActorControl;
namespace {
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
template<class F> void rejects(F operation) {
    bool rejected = false;
    try { operation(); } catch (const std::exception&) { rejected = true; }
    check(rejected, "invalid operation accepted");
}
struct Peer {
    std::vector<std::uint64_t> sequences;
    unsigned closed = 0;
    bool deny = false, cancelled = false, uncertain = false, wrong_actor = false, wrong_outcome = false, unsolicited_observation = false;
    identity::v1::WorldSessionReply act(const core::v1::ActorActionRequest& request) {
        check(request.token().empty(), "adapter exported a token");
        sequences.push_back(request.seq());
        if (uncertain) throw std::runtime_error("uncertain delivery");
        if (cancelled) {
            core::v1::Error error; error.set_code(core::v1::NO_FOCUS);
            throw ActorInputCancelled(error);
        }
        identity::v1::WorldSessionReply reply; reply.set_protocol_version(1);
        if (deny) reply.mutable_error()->set_code(core::v1::FORBIDDEN);
        else if (wrong_outcome) reply.mutable_closed()->set_closed(true);
        else {
            reply.mutable_action()->set_accepted(true);
            if (request.has_observe() || unsolicited_observation) {
                auto* observation = reply.mutable_action()->mutable_observation();
                observation->set_actor(wrong_actor ? "other" : "actor"); observation->set_room("room");
                observation->set_frame_id(1); observation->mutable_viewport()->set_width(512);
                observation->mutable_viewport()->set_height(512); observation->mutable_pose()->mutable_position();
                observation->mutable_pose()->mutable_orientation()->set_w(1);
            }
        }
        return reply;
    }
    identity::v1::WorldSessionReply inspect() {
        identity::v1::WorldSessionReply reply; reply.set_protocol_version(1);
        auto* state = reply.mutable_state();
        state->set_id(wrong_actor ? "other" : "actor"); state->set_room("room");
        state->mutable_pose()->mutable_position();
        state->mutable_pose()->mutable_orientation()->set_w(1);
        for (int i = 0; i < 6; ++i) state->add_axes(0);
        return reply;
    }
    std::unique_ptr<ActorControl> controller() {
        return std::make_unique<ActorControl>(control::ActorContext{"world", "actor", 7},
            [this](const auto& r) { return act(r); }, [this] { return inspect(); }, [this] { ++closed; });
    }
};
core::v1::ActorActionRequest stop() { core::v1::ActorActionRequest r; r.mutable_stop(); return r; }
void identities_and_sequences() {
    Peer peer;
    {
        auto controller = peer.controller();
        rejects([&] { controller->propose(6, stop()); });
        auto forged = stop(); forged.set_token("foreign");
        rejects([&] { controller->propose(7, forged); });
        forged = stop(); forged.set_seq(0);
        rejects([&] { controller->propose(7, forged); });
        rejects([&] { controller->propose(7, {}); });
        check(peer.sequences.empty(), "invalid proposal reached session");
        controller->cancel(7);
        controller->cancel(7);
        check(peer.sequences == std::vector<std::uint64_t>{1, 2}, "sequence allocation changed");
        check(controller->inspect(7).id() == "actor", "inspection changed actor");
        peer.deny = true;
        rejects([&] { controller->propose(7, stop()); });
        peer.deny = false;
        rejects([&] { controller->propose(7, stop()); });
        check(peer.sequences == std::vector<std::uint64_t>{1, 2, 3} && !controller->available(),
              "mutating rejection reopened or replayed the session");
    }
    check(peer.closed == 1, "actor channel was not closed exactly once");
}
void uncertainty_is_terminal() {
    Peer peer; auto controller = peer.controller(); peer.uncertain = true;
    rejects([&] { controller->propose(7, stop()); });
    peer.uncertain = false;
    rejects([&] { controller->propose(7, stop()); });
    check(!controller->available() && peer.closed == 1 && peer.sequences.size() == 1,
          "uncertain action was replayed or left channel open");
    controller->close(); check(peer.closed == 1, "duplicate cleanup");
}
void confirmed_cancellation_allows_only_fresh_actions() {
    Peer peer; auto controller = peer.controller(); peer.cancelled = true;
    rejects([&] { controller->propose(7, stop()); });
    check(controller->available() && peer.closed == 0, "confirmed cancellation disabled the actor");
    peer.cancelled = false; controller->cancel(7);
    check(peer.sequences == std::vector<std::uint64_t>{1, 2}, "cancelled input was replayed");
}
void checked_outcomes_and_inspection() {
    for (bool wrong_actor : {true, false}) {
        Peer peer; auto controller = peer.controller();
        peer.wrong_actor = wrong_actor; peer.wrong_outcome = !wrong_actor;
        rejects([&] { if (wrong_actor) controller->inspect(7); else controller->propose(7, stop()); });
        check(!controller->available() && peer.closed == 1, "invalid successful reply left context usable");
    }
    for (bool unsolicited : {true, false}) {
        Peer peer; auto controller = peer.controller();
        peer.unsolicited_observation = unsolicited; peer.wrong_actor = !unsolicited;
        auto action = stop();
        if (!unsolicited) action.mutable_observe();
        rejects([&] { controller->propose(7, action); });
        check(!controller->available() && peer.closed == 1, "foreign or unsolicited observation left context usable");
    }
}
void concurrent_adapters_share_one_sequence() {
    Peer peer; auto controller = peer.controller();
    std::vector<std::thread> senders;
    std::atomic<unsigned> failures = 0;
    for (int i = 0; i < 12; ++i) senders.emplace_back([&] {
        try { for (int j = 0; j < 20; ++j) controller->cancel(7); }
        catch (...) { ++failures; }
    });
    for (auto& thread : senders) thread.join();
    check(failures == 0 && peer.sequences.size() == 240, "concurrent actions were lost");
    for (std::size_t i = 0; i < peer.sequences.size(); ++i)
        check(peer.sequences[i] == i + 1, "sequence order differs from delivery order");
    Peer independent; auto second = independent.controller(); second->cancel(7);
    check(independent.sequences == std::vector<std::uint64_t>{1}, "another actor inherited sequencing");
}
}
int main() {
    try {
        identities_and_sequences(); std::cout << "PASS identities-and-sequences\n";
        uncertainty_is_terminal(); std::cout << "PASS uncertainty-terminal-no-replay\n";
        confirmed_cancellation_allows_only_fresh_actions(); std::cout << "PASS confirmed-cancellation-fresh-sequence\n";
        checked_outcomes_and_inspection(); std::cout << "PASS checked-outcomes-and-actor-binding\n";
        concurrent_adapters_share_one_sequence(); std::cout << "PASS concurrent-adapters-independent-contexts\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
