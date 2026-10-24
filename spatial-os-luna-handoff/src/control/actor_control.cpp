#include "control/actor_control.hpp"
#include "core/geometry.hpp"
#include "core/scene.hpp"
#include "core/protocol.hpp"
#include <limits>
#include <stdexcept>

namespace spatial::control {
namespace {
void need(bool value, const char* message) {
    if (!value) throw std::invalid_argument(message);
}
bool identity_valid(const std::string& value) {
    return !value.empty() && value.size() <= 256 && value.find('\0') == std::string::npos;
}
void checked_reply(const identity::v1::WorldSessionReply& reply) {
    validate_message(reply);
    need(reply.protocol_version() == 1, "Invalid actor-control reply version");
    if (reply.has_error()) {
        need(reply.error().code() != core::v1::ERROR_CODE_UNSPECIFIED, "Unspecified actor-control error");
        throw ActorSessionRejected(reply.error());
    }
}
}

ActorControl::ActorControl(ActorContext context, Action action, Inspect inspect, Close close)
    : context_(std::move(context)), action_(std::move(action)),
      inspect_(std::move(inspect)), close_(std::move(close)) {
    need(identity_valid(context_.world) && identity_valid(context_.actor), "Invalid authenticated actor context");
    need(bool(action_) && bool(inspect_) && bool(close_), "Actor control requires owned session callbacks");
}
ActorControl::~ActorControl() { close(); }
void ActorControl::require_context(std::uint64_t generation) const {
    need(!failed_ && generation == context_.generation, "Unavailable or stale actor-control context");
}
void ActorControl::fail_locked() noexcept {
    if (failed_) return;
    failed_ = true;
    // Mark failed before closing: a throwing cleanup cannot reopen this context.
    try { close_(); } catch (...) {}
}
void ActorControl::close() noexcept {
    std::lock_guard lock(mutex_);
    fail_locked();
}
bool ActorControl::available() const {
    std::lock_guard lock(mutex_);
    return !failed_;
}
core::v1::ActorActionReply ActorControl::propose(std::uint64_t generation,
                                              const core::v1::ActorActionRequest& proposal) {
    std::lock_guard lock(mutex_);
    require_context(generation);
    validate_message(proposal);
    need(proposal.token().empty() && !proposal.has_seq(), "Adapters cannot choose actor tokens or sequences");
    need(proposal.action_case() != core::v1::ActorActionRequest::ACTION_NOT_SET, "No proposed actor action");
    if (sequence_ == std::numeric_limits<std::uint64_t>::max()) {
        fail_locked();
        throw std::runtime_error("Actor-control sequence exhausted");
    }
    auto request = proposal;
    request.set_seq(++sequence_);
    try {
        const auto reply = action_(request);
        checked_reply(reply);
        need(reply.has_action() && reply.action().accepted(), "Invalid actor-control action outcome");
        const auto& result = reply.action();
        need(proposal.has_aim() == result.has_aim(), "Actor-control aim outcome mismatch");
        need(proposal.has_observe() == result.has_observation(), "Actor-control observation outcome mismatch");
        if (proposal.has_observe()) {
            const auto& observation = result.observation();
            const auto& requested = proposal.observe();
            need(observation.actor() == context_.actor && !observation.room().empty() &&
                 observation.frame_id() > 0 && observation.has_pose() &&
                 observation.pose().has_position() && observation.pose().has_orientation(),
                 "Observation changed the bound actor or omitted its frame");
            const auto width = requested.has_width() ? requested.width() : 512;
            const auto height = requested.has_height() ? requested.height() : 512;
            validate_observation_extent(width, height, requested.include_pixels());
            need(observation.viewport().width() == width && observation.viewport().height() == height &&
                 observation.has_image() == requested.include_pixels(), "Observation changed the requested viewport or pixel mode");
            const auto& p = observation.pose().position(); const auto& q = observation.pose().orientation();
            validate_pose({{p.x(), p.y(), p.z()}, {q.x(), q.y(), q.z(), q.w()}});
            if (requested.include_pixels()) {
                const auto& image = observation.image();
                need(image.width() == width && image.height() == height &&
                     image.rgb8().size() == std::uint64_t(width) * height * 3,
                     "Observation image does not match its checked frame");
            }
        }
        return reply.action();
    } catch (const ActorInputCancelled&) {
        // The checked controller transport confirmed cancellation. Continue only
        // with a fresh sequence. This is not permission to replay prior input.
        throw;
    } catch (const ActorSessionRejected&) {
        // Preserve ActorSessionClient's existing mutating-error contract.
        // A matching rejection is not proof that no external effect occurred.
        fail_locked();
        throw;
    } catch (...) {
        fail_locked();
        throw;
    }
}
core::v1::ActorStateReply ActorControl::inspect(std::uint64_t generation) {
    std::lock_guard lock(mutex_);
    require_context(generation);
    try {
        const auto reply = inspect_();
        checked_reply(reply);
        need(reply.has_state() && reply.state().id() == context_.actor &&
             !reply.state().room().empty() && reply.state().has_pose() &&
             reply.state().pose().has_position() && reply.state().pose().has_orientation() &&
             reply.state().axes_size() == 6, "Actor inspection changed authenticated context");
        need(reply.state().translation_frame() == core::v1::BODY_TRANSLATION ||
             reply.state().translation_frame() == core::v1::LEVEL_TRANSLATION, "Actor inspection changed the motion frame");
        const auto& pose = reply.state().pose();
        const auto& p = pose.position(); const auto& q = pose.orientation();
        validate_pose({{p.x(), p.y(), p.z()}, {q.x(), q.y(), q.z(), q.w()}});
        return reply.state();
    } catch (...) {
        fail_locked();
        throw;
    }
}
void ActorControl::cancel(std::uint64_t generation) {
    core::v1::ActorActionRequest stop;
    stop.mutable_stop();
    propose(generation, stop);
}
}
