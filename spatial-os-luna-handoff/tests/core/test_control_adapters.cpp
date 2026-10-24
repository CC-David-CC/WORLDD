// Deterministic adapter tests. Real world/native effects have separate suites.
#include "control/adapters.hpp"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

using namespace spatial;
namespace c = spatial::control;
namespace p = spatial::control::v1;
namespace {
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
template<class F> void rejects(F operation) {
    bool rejected = false;
    try { operation(); } catch (const std::exception&) { rejected = true; }
    check(rejected, "invalid adapter input accepted");
}
struct Fixture {
    std::vector<core::v1::ActorActionRequest> actions;
    unsigned closes = 0;
    std::chrono::milliseconds action_delay{0};
    bool aim_empty = false, aim_geometry = false, cancel_next = false;
    c::ActorControl actor{{"world", "actor", 0},
        [this](const auto& request) {
            actions.push_back(request);
            if (cancel_next) {
                cancel_next = false;
                core::v1::Error error; error.set_code(core::v1::NO_FOCUS);
                throw ActorInputCancelled(error);
            }
            identity::v1::WorldSessionReply reply; reply.set_protocol_version(1);
            auto* action = reply.mutable_action(); action->set_accepted(true);
            if (request.has_aim()) {
                auto* aim = action->mutable_aim();
                if (!aim_empty) { auto* hit = aim->mutable_hit(); hit->set_id("surface"); hit->set_generation(1); hit->mutable_pixel(); }
            }
            if (request.has_observe()) {
                auto* observation = action->mutable_observation(); observation->set_actor("actor");
                observation->set_room("room"); observation->set_frame_id(1);
                observation->mutable_pose()->mutable_position(); observation->mutable_pose()->mutable_orientation()->set_w(1);
                observation->mutable_viewport()->set_width(512); observation->mutable_viewport()->set_height(512);
                auto* visible = observation->add_visible(); visible->set_id("surface"); visible->set_generation(1);
                if (aim_geometry) visible->set_kind(core::v1::PANEL);
            }
            now += action_delay;
            return reply;
        }, [] {
            identity::v1::WorldSessionReply reply; reply.set_protocol_version(1);
            auto* state = reply.mutable_state(); state->set_id("actor"); state->set_room("room");
            state->mutable_pose()->mutable_position(); state->mutable_pose()->mutable_orientation()->set_w(1);
            for (int i = 0; i < 6; ++i) state->add_axes(0);
            return reply;
        }, [this] { ++closes; }};
    c::DeviceAdapters::Clock::time_point now{};
    c::DeviceAdapters adapter{actor, c::default_profiles(), [this] { return now; }};
    std::map<std::string, std::uint64_t> sequence;
    p::DeviceEvent event(const std::string& source) {
        p::DeviceEvent e; e.set_protocol_version(1); e.set_source_id(source); e.set_source_generation(1);
        e.set_event_sequence(++sequence[source]); return e;
    }
    void connect(const std::string& source, p::DeviceKind kind) {
        auto e = event(source); e.mutable_connected()->set_kind(kind); adapter.event(e, now);
    }
    void key(const std::string& key, bool down, bool repeat = false) {
        auto e = event("keyboard"); e.mutable_key()->set_keysym(key); e.mutable_key()->set_pressed(down);
        e.mutable_key()->set_repeat(repeat); adapter.event(e, now);
    }
    void pointer(bool down) {
        auto e = event("mouse"); e.mutable_button()->set_button(1); e.mutable_button()->set_pressed(down);
        adapter.event(e, now);
    }
    void axes(const std::array<double, 6>& raw) {
        auto e = event("spacemouse"); for (const auto value : raw) e.mutable_axes()->add_raw(value);
        adapter.event(e, now);
    }
    const core::v1::ActorInputRequest& last_input() const {
        check(!actions.empty() && actions.back().has_input(), "expected canonical input action");
        return actions.back().input();
    }
};
void presets_and_transactional_edits() {
    Fixture f; check(f.adapter.profiles().profiles_size() == 6, "six presets missing");
    const auto presets = f.adapter.profiles();
    for (const auto& profile : presets.profiles()) {
        f.adapter.select_profile(profile.id());
        check(f.adapter.status().navigating() == (profile.interaction() != p::HOLD_TO_NAVIGATE), "wrong initial mode");
    }
    const auto before = f.adapter.profiles().SerializeAsString(); const auto count = f.actions.size();
    auto invalid = f.adapter.profiles();
    *invalid.mutable_profiles(0)->add_bindings() = invalid.profiles(0).bindings(0);
    rejects([&] { f.adapter.set_profiles(invalid); });
    check(f.adapter.profiles().SerializeAsString() == before && f.actions.size() == count,
          "invalid profile changed configuration or actor state");
    rejects([&] { f.adapter.select_profile("missing"); });
    auto unknown_key = f.adapter.profiles();
    unknown_key.mutable_profiles(0)->mutable_bindings(0)->set_keysym("not_a_real_X_keysym");
    rejects([&] { f.adapter.set_profiles(unknown_key); });
    check(f.adapter.profiles().SerializeAsString() == before && f.actions.size() == count,
          "unknown physical keysym replaced a usable profile");
}
void movement_and_fresh_input_after_modes() {
    Fixture f; f.connect("keyboard", p::KEYBOARD);
    f.key("w", true); f.adapter.tick(f.now); check(f.last_input().axes(2) == -1, "forward mapping");
    f.adapter.set_menu(true); check(f.actions.back().has_stop(), "menu did not cancel");
    f.adapter.set_menu(false); const auto before = f.actions.size();
    f.key("w", true, true); f.adapter.tick(f.now);
    check(f.actions.size() == before, "held key resumed after mode change");
    f.key("w", false); f.key("w", true); f.adapter.tick(f.now);
    check(f.last_input().axes(2) == -1, "fresh key did not resume");
    f.key("F9", true); check(!f.adapter.status().navigating(), "mode switch failed");
    const auto switched = f.actions.size(); f.key("F9", true);
    check(f.actions.size() == switched && !f.adapter.status().navigating(), "duplicate switch toggled mode");
    f.key("F9", false); f.key("F9", true);
    check(f.adapter.status().navigating(), "fresh switch failed");
}
void click_keeps_only_fresh_application_focus() {
    Fixture f; f.connect("mouse", p::MOUSE); f.connect("keyboard", p::KEYBOARD);
    f.pointer(true); f.pointer(false);
    check(f.actions.size() == 5 && f.actions[0].has_stop() && f.actions[1].has_aim() && f.actions[2].has_observe() &&
          f.actions[3].has_pointer() && f.actions[3].pointer().pressed() &&
          f.actions[4].has_pointer() && !f.actions[4].pointer().pressed(), "click/cancellation ordering changed");
    check(!f.adapter.status().navigating(), "completed click did not enter application mode");
    f.key("a", true); check(f.actions.back().has_key(), "application key became navigation");
    f.key("F12", true); check(f.actions.back().has_stop() && f.adapter.status().navigating(), "cancel failed");
}
void hold_navigation_balances_both_edges() {
    Fixture f; f.adapter.select_profile("level-hold"); f.connect("keyboard", p::KEYBOARD);
    check(!f.adapter.status().navigating(), "hold preset initial mode");
    f.key("F9", true); check(f.adapter.status().navigating(), "hold did not navigate");
    f.key("F9", false); check(!f.adapter.status().navigating(), "release did not return");
    check(f.actions.back().has_stop(), "hold release left native state");
}
void empty_and_world_geometry_clicks_do_not_become_application_input() {
    for (bool empty : {true, false}) {
        Fixture f; f.aim_empty = empty; f.aim_geometry = !empty; f.connect("mouse", p::MOUSE);
        f.pointer(true); f.pointer(false);
        check(f.adapter.status().navigating(), "world geometry entered application control");
        check(std::none_of(f.actions.begin(), f.actions.end(), [](const auto& a) { return a.has_pointer(); }),
              "non-application target produced native input");
        check(f.actor.available(), "ordinary empty click disabled the actor");
    }
}
void checked_cancellation_requires_fresh_device_input() {
    Fixture f; f.connect("keyboard", p::KEYBOARD); f.connect("mouse", p::MOUSE);
    f.key("w", true); f.adapter.tick(f.now);
    f.cancel_next = true;
    auto motion = f.event("mouse"); motion.mutable_motion()->set_dx(10);
    rejects([&] { f.adapter.event(motion, f.now); });
    const auto before = f.actions.size(); f.adapter.tick(f.now);
    check(f.actor.available() && f.actions.size() == before, "cancelled cached motion was renewed");
    f.key("w", true, true); f.adapter.tick(f.now);
    check(f.actions.size() == before, "repeat resumed cancelled movement");
    f.key("w", false); f.key("w", true); f.adapter.tick(f.now);
    check(f.last_input().axes(2) == -1, "fresh key failed after checked cancellation");
}
void all_signed_axes_combination_and_expiry() {
    Fixture f; auto profiles = f.adapter.profiles();
    for (auto& profile : *profiles.mutable_profiles()) {
        profile.set_spacemouse_calibrated(true);
        for (int i = 0; i < 6; ++i) {
            auto* axis = profile.add_spacemouse_axes(); axis->set_raw_axis(i + 1); axis->set_full_scale(100);
        }
    }
    f.adapter.set_profiles(profiles); f.connect("spacemouse", p::SPACEMOUSE);
    for (unsigned index = 0; index < 6; ++index) for (double sign : {-1., 1.}) {
        std::array<double, 6> raw{}; raw[index] = sign * 100;
        f.axes(raw); f.adapter.tick(f.now);
        for (unsigned axis = 0; axis < 6; ++axis) check(f.last_input().axes(axis) == (axis == index ? sign : 0), "signed axis changed");
    }
    f.axes({100, 50, -100, 0, 100, -50}); f.adapter.tick(f.now);
    check(f.last_input().axes(0) == 1 && f.last_input().axes(1) == .5 && f.last_input().axes(2) == -1,
          "combined axes were normalized");
    f.now += std::chrono::milliseconds(250);
    auto heartbeat = f.event("spacemouse"); heartbeat.mutable_heartbeat(); f.adapter.event(heartbeat, f.now);
    f.adapter.tick(f.now); for (auto axis : f.last_input().axes()) check(axis == 0, "heartbeat refreshed stale axes");
}
void stale_sources_and_disconnect() {
    Fixture f; f.connect("keyboard", p::KEYBOARD); f.key("w", true); f.adapter.tick(f.now);
    auto removed = f.event("keyboard"); removed.mutable_disconnected(); f.adapter.event(removed, f.now);
    check(f.actions.back().has_stop(), "device loss did not stop actor");
    auto old = f.event("keyboard"); old.mutable_connected()->set_kind(p::KEYBOARD);
    rejects([&] { f.adapter.event(old, f.now); });
    old.set_source_generation(2); f.adapter.event(old, f.now);
    old = f.event("keyboard"); old.mutable_key()->set_keysym("w"); old.mutable_key()->set_pressed(true);
    rejects([&] { f.adapter.event(old, f.now); });
    f.now += std::chrono::milliseconds(351); f.adapter.tick(f.now);
    check(f.actions.back().has_stop(), "capture heartbeat loss did not cancel");
}
void mouse_counts_do_not_depend_on_event_rate() {
    for (unsigned rate : {30U, 60U, 144U}) {
        Fixture f; f.connect("mouse", p::MOUSE);
        for (unsigned i = 0; i < rate; ++i) {
            auto event = f.event("mouse"); event.mutable_motion()->set_dx(240. / rate); f.adapter.event(event, f.now);
        }
        double total = 0;
        for (const auto& action : f.actions) total += action.relative_look().angular_delta().y();
        check(std::abs(total + .6) < 1e-12, "mouse counts changed with event rate");
    }
}
void oversized_mouse_samples_do_not_start_an_unbounded_action_batch() {
    Fixture f; f.connect("mouse", p::MOUSE);
    auto event = f.event("mouse"); event.mutable_motion()->set_dx(100000);
    rejects([&] { f.adapter.event(event, f.now); });
    check(f.actions.empty(), "oversized pointer sample partially rotated the actor");
}
void queued_press_cannot_cross_a_mode_barrier() {
    Fixture f; f.connect("keyboard", p::KEYBOARD);
    auto queued = f.event("keyboard"); queued.mutable_key()->set_keysym("w"); queued.mutable_key()->set_pressed(true);
    const auto captured = f.now + std::chrono::milliseconds(5);
    f.now += std::chrono::milliseconds(10); f.adapter.set_menu(true);
    f.now += std::chrono::milliseconds(10); f.adapter.set_menu(false);
    const auto before = f.actions.size();
    check(!f.adapter.event(queued, captured, f.now), "queued press crossed a mode barrier");
    f.adapter.tick(f.now); f.key("w", true); f.adapter.tick(f.now);
    check(f.actions.size() == before, "queued press or duplicate renewed old movement");
    f.key("w", false); f.key("w", true); f.adapter.tick(f.now);
    check(f.last_input().axes(2) == -1, "stale press bookkeeping lost the fresh release");
}
void old_release_cancels_active_motion_and_allows_fresh_input() {
    Fixture f; f.connect("keyboard", p::KEYBOARD); f.key("w", true); f.adapter.tick(f.now);
    auto released = f.event("keyboard"); released.mutable_key()->set_keysym("w"); released.mutable_key()->set_pressed(false);
    f.now += std::chrono::milliseconds(300);
    check(!f.adapter.event(released, c::DeviceAdapters::Clock::time_point(std::chrono::milliseconds(1)), f.now),
          "expired release was treated as a fresh action");
    check(f.actions.back().has_stop(), "expired input did not cancel active motion");
    f.now += std::chrono::milliseconds(1); f.key("w", true); f.adapter.tick(f.now);
    check(f.last_input().axes(2) == -1, "expired release left the physical key stuck");
}
void expired_axes_and_motion_do_not_gain_a_new_receipt_timestamp() {
    Fixture f; f.connect("mouse", p::MOUSE); f.connect("spacemouse", p::SPACEMOUSE);
    auto axes = f.event("spacemouse"); for (double x : {100.,0.,0.,0.,0.,0.}) axes.mutable_axes()->add_raw(x);
    auto motion = f.event("mouse"); motion.mutable_motion()->set_dx(200);
    f.now += std::chrono::milliseconds(251);
    check(!f.adapter.event(axes, c::DeviceAdapters::Clock::time_point{}, f.now), "expired axes became fresh");
    check(!f.adapter.event(motion, c::DeviceAdapters::Clock::time_point{}, f.now), "expired motion became fresh");
    f.adapter.tick(f.now); check(f.actions.empty(), "expired samples changed the actor");
    check(f.adapter.last_raw_axes() == std::array<double,6>{}, "expired sample became calibration input");
    f.now += std::chrono::milliseconds(100); f.adapter.tick(f.now);
    check(f.actions.back().has_stop(), "receipt time hid source heartbeat loss");
}
void future_and_decreasing_capture_times_are_refused() {
    Fixture f; f.connect("mouse", p::MOUSE);
    auto first = f.event("mouse"); first.mutable_motion()->set_dx(1);
    f.now += std::chrono::milliseconds(10); f.adapter.event(first, f.now);
    const auto before = f.actions.size(); auto old = f.event("mouse"); old.mutable_motion()->set_dx(2);
    rejects([&] { f.adapter.event(old, f.now - std::chrono::milliseconds(1), f.now); });
    rejects([&] { f.adapter.event(old, f.now + std::chrono::milliseconds(1), f.now); });
    check(f.actions.size() == before, "invalid capture time changed the actor");
}
void slow_mouse_batch_cancels_before_delivering_more_old_counts() {
    Fixture f; f.connect("mouse", p::MOUSE); f.action_delay = std::chrono::milliseconds(100);
    auto motion = f.event("mouse"); motion.mutable_motion()->set_dx(1600);
    check(!f.adapter.event(motion, f.now), "slow gesture renewed its age for each action");
    check(f.actions.size() == 4 && f.actions.back().has_stop(), "expired batch did not stop after three accepted look actions");
    for (unsigned i = 0; i < 3; ++i) check(f.actions[i].has_relative_look(), "slow fixture did not reach real adapter look proposals");
    check(f.actor.available(), "checked gesture cancellation fenced the actor");
    f.action_delay = std::chrono::milliseconds(0);
    motion = f.event("mouse"); motion.mutable_motion()->set_dx(1);
    check(f.adapter.event(motion, f.now) && f.actions.back().has_relative_look(), "fresh gesture did not follow expired batch cancellation");
}
void private_preferences_roundtrip_and_refusals() {
    char path[] = "/tmp/spatial-control-profiles-XXXXXX";
    check(mkdtemp(path) != nullptr, "temporary preferences directory");
    const std::filesystem::path root(path);
    try {
        const auto file = root / "controls.pb"; auto profiles = c::default_profiles();
        c::save_profiles(file, profiles);
        check(c::load_profiles(file).SerializeAsString() == profiles.SerializeAsString(), "profile roundtrip");
        auto invalid = profiles; invalid.set_active_profile("missing");
        rejects([&] { c::save_profiles(file, invalid); });
        check(c::load_profiles(file).SerializeAsString() == profiles.SerializeAsString(), "invalid profile replaced valid file");
        std::filesystem::create_symlink(file, root / "link");
        rejects([&] { c::load_profiles(root / "link"); });
        rejects([&] { c::save_profiles(root / "link", profiles); });
        check(chmod(file.c_str(), 0644) == 0, "chmod fixture");
        rejects([&] { c::load_profiles(file); });
        rejects([&] { c::save_profiles(file, profiles); });
        std::filesystem::remove_all(root);
    } catch (...) { std::filesystem::remove_all(root); throw; }
}
}
int main() {
    try {
        presets_and_transactional_edits(); std::cout << "PASS six-presets-transactional-edits\n";
        movement_and_fresh_input_after_modes(); std::cout << "PASS fresh-input-after-mode-change\n";
        click_keeps_only_fresh_application_focus(); std::cout << "PASS click-acquisition-order\n";
        hold_navigation_balances_both_edges(); std::cout << "PASS hold-navigation-edges\n";
        empty_and_world_geometry_clicks_do_not_become_application_input(); std::cout << "PASS non-application-click-filtering\n";
        checked_cancellation_requires_fresh_device_input(); std::cout << "PASS checked-cancellation-fresh-device-input\n";
        all_signed_axes_combination_and_expiry(); std::cout << "PASS signed-axes-combination-expiry\n";
        stale_sources_and_disconnect(); std::cout << "PASS stale-source-disconnect-cancellation\n";
        mouse_counts_do_not_depend_on_event_rate(); std::cout << "PASS mouse-event-rate-independence\n";
        oversized_mouse_samples_do_not_start_an_unbounded_action_batch(); std::cout << "PASS bounded-mouse-action-batches\n";
        queued_press_cannot_cross_a_mode_barrier(); std::cout << "PASS queued-input-mode-barrier\n";
        old_release_cancels_active_motion_and_allows_fresh_input(); std::cout << "PASS expired-release-fresh-input\n";
        expired_axes_and_motion_do_not_gain_a_new_receipt_timestamp(); std::cout << "PASS capture-age-not-receipt-time\n";
        future_and_decreasing_capture_times_are_refused(); std::cout << "PASS invalid-capture-time-refusal\n";
        slow_mouse_batch_cancels_before_delivering_more_old_counts(); std::cout << "PASS mouse-batch-absolute-gesture-age\n";
        private_preferences_roundtrip_and_refusals(); std::cout << "PASS private-preference-storage\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
