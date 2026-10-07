#include "world/actors.hpp"
#include "core/geometry.hpp"
#include <google/protobuf/text_format.h>
#include <sys/random.h>
#include <cerrno>
#include <cmath>
#include <fstream>
#include <iterator>

namespace spatial {
namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::invalid_argument(message);
}
std::string random_token() {
    std::array<unsigned char, 32> bytes{};
    std::size_t offset = 0;
    while (offset < bytes.size()) {
        const auto count = getrandom(bytes.data() + offset, bytes.size() - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw std::runtime_error("Cannot obtain session entropy");
        offset += static_cast<std::size_t>(count);
    }
    constexpr char hex[] = "0123456789abcdef";
    std::string result;
    for (const auto byte : bytes) { result += hex[byte >> 4]; result += hex[byte & 15]; }
    return result;
}
Pose as_pose(const wire::Pose& value) {
    const auto& p = value.position(); const auto& q = value.orientation();
    return {{p.x(), p.y(), p.z()}, {q.x(), q.y(), q.z(), q.w()}};
}
void set_pose(wire::Pose* out, const Pose& value) {
    auto* p = out->mutable_position(); p->set_x(value.p[0]); p->set_y(value.p[1]); p->set_z(value.p[2]);
    auto* q = out->mutable_orientation();
    q->set_x(value.q[0]); q->set_y(value.q[1]); q->set_z(value.q[2]); q->set_w(value.q[3]);
}
}
wire::RuntimeConfig load_runtime_config(const std::filesystem::path& path) {
    require(std::filesystem::file_size(path) <= 16384, "Runtime configuration exceeds size limit");
    std::ifstream input(path);
    if (!input) throw std::runtime_error("Cannot read runtime configuration");
    const std::string text((std::istreambuf_iterator<char>(input)), {});
    wire::RuntimeConfig result;
    require(google::protobuf::TextFormat::ParseFromString(text, &result), "Invalid runtime configuration protobuf");
    validate_message(result);
    require(result.has_linear_gain() && result.linear_gain() > 0 && result.linear_gain() <= 1000, "Invalid linear gain");
    require(result.has_angular_gain() && result.angular_gain() > 0 && result.angular_gain() <= 1000, "Invalid angular gain");
    require(result.has_deadzone() && result.deadzone() >= 0 && result.deadzone() < 1, "Invalid deadzone");
    require(result.has_input_timeout() && result.input_timeout() > 0 && result.input_timeout() <= 60, "Invalid input timeout");
    return result;
}
Actors::Actors(WorldStore& store, const wire::RuntimeConfig& config, bool manual_clock)
    : store_(store), config_(config), manual_clock_(manual_clock) {
    for (const auto& entity : store_.actors()) {
        Actor actor; actor.entity = entity; actors_.emplace(entity.id(), std::move(actor));
    }
}
Actors::Actor& Actors::authenticated(const std::string& token) {
    const auto found = sessions_.find(token);
    if (token.empty() || found == sessions_.end()) throw WorldError(wire::UNAUTHORIZED, "Invalid actor session");
    return actors_.at(found->second);
}
wire::ActorOpenReply Actors::open(const wire::ActorOpenRequest& request) {
    require(request.grants_size() <= 64, "Too many grants");
    const std::set<std::string> allowed{"navigate", "interact", "world.write", "git.write", "process.launch"};
    std::set<std::string> grants;
    for (const auto& grant : request.grants()) {
        require(allowed.contains(grant), "Unknown actor grant");
        require(grants.insert(grant).second, "Duplicate actor grant");
    }
    auto found = actors_.find(request.id());
    if (found != actors_.end() && !found->second.token.empty())
        throw WorldError(wire::IN_USE, "Actor already has a controller");
    const auto token = random_token();
    if (sessions_.contains(token)) throw std::runtime_error("Session entropy collision");
    if (found == actors_.end()) {
        wire::Entity entity; entity.set_id(request.id()); entity.set_kind(wire::ACTOR);
        entity.set_room(request.has_room() ? request.room() : store_.info().entry_room());
        entity.set_label(request.id());
        if (request.has_pose()) *entity.mutable_pose() = request.pose();
        else { entity.mutable_pose()->mutable_position(); entity.mutable_pose()->mutable_orientation()->set_w(1); }
        Actor actor; actor.entity = store_.create_actor(entity);
        found = actors_.emplace(entity.id(), std::move(actor)).first;
    }
    auto& actor = found->second;
    // An existing actor keeps its last valid pose, including a durable restore.
    actor.token = token; actor.grants = std::move(grants); actor.sequence = 0;
    actor.axes.fill(0); actor.expires = now_;
    sessions_.emplace(token, actor.entity.id());
    wire::ActorOpenReply result; result.set_id(actor.entity.id()); result.set_token(token); return result;
}
wire::ActorStateReply Actors::inspect(const std::string& token) {
    const auto& actor = authenticated(token);
    wire::ActorStateReply result; result.set_id(actor.entity.id()); result.set_room(actor.entity.room());
    *result.mutable_pose() = actor.entity.pose();
    for (const auto axis : actor.axes) result.add_axes(axis);
    return result;
}
wire::ActorCloseReply Actors::close(const std::string& token) {
    auto& actor = authenticated(token);
    actor.axes.fill(0); actor.expires = now_; actor.grants.clear(); actor.token.clear();
    sessions_.erase(token);
    wire::ActorCloseReply result; result.set_closed(true); return result;
}
wire::ActorActionReply Actors::act(const wire::ActorActionRequest& request) {
    auto& actor = authenticated(request.token());
    require(request.has_seq() && request.seq() > 0, "Missing positive actor sequence");
    if (request.seq() <= actor.sequence) throw WorldError(wire::STALE_SEQUENCE, "Actor sequence already consumed");
    actor.sequence = request.seq();
    switch (request.action_case()) {
    case wire::ActorActionRequest::kInput: {
        if (!actor.grants.contains("navigate")) throw WorldError(wire::FORBIDDEN, "Navigation grant required");
        require(request.input().axes_size() == 6, "Expected six motion axes");
        std::array<double, 6> axes{};
        for (std::size_t i = 0; i < axes.size(); ++i) {
            const double value = request.input().axes(static_cast<int>(i));
            require(std::isfinite(value) && std::abs(value) <= 1, "Axis must be finite and within [-1,1]");
            axes[i] = std::abs(value) <= config_.deadzone() ? 0 :
                std::copysign((std::abs(value) - config_.deadzone()) / (1 - config_.deadzone()), value);
        }
        actor.axes = axes; actor.expires = now_ + config_.input_timeout();
        break;
    }
    case wire::ActorActionRequest::kStop:
        actor.axes.fill(0); actor.expires = now_; break;
    case wire::ActorActionRequest::ACTION_NOT_SET:
        throw WorldError(wire::UNKNOWN_METHOD, "No recognized actor action");
    }
    wire::ActorActionReply result; result.set_accepted(true); return result;
}
void Actors::step(double target) {
    require(std::isfinite(target) && target >= now_, "Invalid monotonic time");
    for (auto& [id, actor] : actors_) {
        (void)id;
        const double end = std::min(target, actor.expires);
        if (end > now_) {
            const Vec3 linear{actor.axes[0] * config_.linear_gain(), actor.axes[1] * config_.linear_gain(), actor.axes[2] * config_.linear_gain()};
            const Vec3 angular{actor.axes[3] * config_.angular_gain(), actor.axes[4] * config_.angular_gain(), actor.axes[5] * config_.angular_gain()};
            set_pose(actor.entity.mutable_pose(), integrate(as_pose(actor.entity.pose()), linear, angular, end - now_));
        }
        if (target >= actor.expires) actor.axes.fill(0);
    }
    now_ = target;
}
void Actors::tick(double monotonic_seconds) { if (!manual_clock_) step(monotonic_seconds); }
wire::ClockReply Actors::advance(const wire::ClockAdvanceRequest& request) {
    if (!manual_clock_) throw WorldError(wire::FORBIDDEN, "Manual clock is disabled");
    require(request.has_seconds() && std::isfinite(request.seconds()) && request.seconds() >= 0 &&
            request.seconds() <= 3600, "Invalid clock interval");
    step(now_ + request.seconds());
    wire::ClockReply result; result.set_seconds(now_); return result;
}
wire::WorldCheckpointReply Actors::checkpoint() {
    std::vector<wire::Entity> entities;
    for (const auto& [id, actor] : actors_) { (void)id; entities.push_back(actor.entity); }
    for (const auto& entity : store_.checkpoint_actors(entities)) actors_.at(entity.id()).entity = entity;
    wire::WorldCheckpointReply result; result.set_world_revision(store_.info().world_revision()); return result;
}
const wire::Entity* Actors::find(const std::string& id) const {
    const auto found = actors_.find(id);
    return found == actors_.end() ? nullptr : &found->second.entity;
}
} // namespace spatial
