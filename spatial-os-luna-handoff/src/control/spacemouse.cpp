#include "control/spacemouse.hpp"
#include <spnav.h>
#include <poll.h>
#include <stdexcept>

namespace spatial::control {
namespace { std::mutex spnav_owner; }
SpaceMouseSource::SpaceMouseSource(Emit emit)
    : emit_(std::move(emit)), library_owner_(spnav_owner, std::try_to_lock) {
    if (!emit_ || !library_owner_.owns_lock()) throw std::invalid_argument("SpaceMouse source needs exclusive library ownership");
}
SpaceMouseSource::~SpaceMouseSource() { close(); }
v1::DeviceEvent SpaceMouseSource::event() {
    v1::DeviceEvent result; result.set_protocol_version(1); result.set_source_id("spacenav");
    result.set_source_generation(generation_); result.set_event_sequence(++sequence_); return result;
}
void SpaceMouseSource::activate() {
    // Device queries and removal notifications require native protocol v1.
    char path[1024]{};
    if (spnav_dev_path(path, sizeof(path)) <= 0 || spnav_dev_axes() != 6) return;
    ++generation_; sequence_ = 0; active_ = true;
    auto connected = event(); connected.mutable_connected()->set_kind(v1::SPACEMOUSE); emit_(connected);
}
bool SpaceMouseSource::open() {
    if (opened_) return true;
    if (spnav_open() == -1) return false;
    opened_ = true;
    if (spnav_protocol() < 1 || spnav_evmask(SPNAV_EVMASK_INPUT | SPNAV_EVMASK_DEV) == -1) {
        close(); return false;
    }
    try { activate(); } catch (...) { close(); throw; }
    return true;
}
int SpaceMouseSource::descriptor() const { return opened_ ? spnav_fd() : -1; }
void SpaceMouseSource::disconnect() {
    if (!active_) return;
    active_ = false;
    auto disconnected = event(); disconnected.mutable_disconnected(); emit_(disconnected);
}
void SpaceMouseSource::close() noexcept {
    try { disconnect(); } catch (...) {}
    if (opened_) spnav_close();
    opened_ = false;
}
void SpaceMouseSource::pump(DeviceAdapters::Clock::time_point now) {
    if (!opened_) return;
    pollfd socket{spnav_fd(), POLLIN, 0};
    const int ready = poll(&socket, 1, 0);
    if (ready < 0 || socket.revents & (POLLERR | POLLHUP | POLLNVAL)) { close(); return; }
    spnav_event incoming{};
    // Bound one pass so a busy controller cannot starve keyboard or cancellation.
    for (unsigned count = 0; count < 256 && spnav_poll_event(&incoming); ++count) {
        if (incoming.type == SPNAV_EVENT_DEV) {
            // Rebind explicitly after any topology change. Never retain old buttons.
            disconnect();
            if (incoming.dev.op == SPNAV_DEV_ADD) activate();
            continue;
        }
        if (!active_) continue;
        auto value = event();
        if (incoming.type == SPNAV_EVENT_MOTION) {
            const int axes[]{incoming.motion.x, incoming.motion.y, incoming.motion.z,
                             incoming.motion.rx, incoming.motion.ry, incoming.motion.rz};
            for (const auto axis : axes) value.mutable_axes()->add_raw(axis);
        } else if (incoming.type == SPNAV_EVENT_BUTTON) {
            if (incoming.button.bnum < 0 || incoming.button.bnum >= 3) continue;
            value.mutable_button()->set_button(static_cast<unsigned>(incoming.button.bnum + 1));
            value.mutable_button()->set_pressed(incoming.button.press != 0);
        } else continue;
        emit_(value);
    }
    if (active_ && (heartbeat_ == DeviceAdapters::Clock::time_point{} || now - heartbeat_ >= std::chrono::milliseconds(100))) {
        auto heartbeat = event(); heartbeat.mutable_heartbeat(); emit_(heartbeat); heartbeat_ = now;
    }
}
}
