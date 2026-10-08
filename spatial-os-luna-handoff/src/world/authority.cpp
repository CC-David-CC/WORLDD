#include "world/authority.hpp"
#include "identity/tls_support.hpp"
#include "world/store.hpp"
#include "world_session.pb.h"
#include <google/protobuf/text_format.h>

namespace spatial {
namespace {
namespace pb = authority::v1;
pb::Domain domain(const std::string& world) {
    pb::Domain value; value.set_kind(pb::WORLD); value.set_id(world); return value;
}
}
WorldAuthority::WorldAuthority(const std::filesystem::path& path, const std::string& world, const std::string& issuer)
    : domain_(domain(world)), store_(path, domain_, false), issuer_(issuer) {}
std::vector<pb::ActorRef> WorldAuthority::subjects(const pb::ActorRef& caller) { return store_.subjects(caller); }
bool WorldAuthority::active(const pb::ActorRef& actor) const {
    const auto found = active_.find(actor.actor_id());
    return actor.issuer_id() == issuer_ && !revoked_.contains(actor.actor_id()) && found != active_.end() && found->second;
}
void WorldAuthority::update(const std::vector<identity::v1::ActorState>& states) {
    for (const auto& state : states) {
        if (state.revoked()) revoked_.insert(state.actor_id());
        active_[state.actor_id()] = state.active() && !revoked_.contains(state.actor_id());
    }
    decisions_.clear();
}
void WorldAuthority::revoked(const std::string& actor) {
    revoked_.insert(actor); active_[actor] = false; decisions_.clear();
}
bool WorldAuthority::allowed(const pb::ActorRef& actor, const std::string& right, const std::vector<std::string>& components) {
    pb::Right selected;
    if (right == "enter") selected = pb::WORLD_ENTER;
    else if (right == "navigate") selected = pb::WORLD_NAVIGATE;
    else if (right == "observe") selected = pb::WORLD_OBSERVE;
    else if (right == "world.write") selected = pb::WORLD_MANIPULATE;
    else return false;
    const DecisionKey key{actor.SerializeAsString(), right, components};
    const auto cached = decisions_.find(key); if (cached != decisions_.end()) return cached->second;
    pb::Scope target; *target.mutable_domain() = domain_;
    for (const auto& component : components) target.add_components(component);
    const bool result = store_.check(actor, target, selected, [this](const auto& subject) { return active(subject); }).allowed();
    if (decisions_.size() >= 4096) decisions_.clear();
    decisions_.emplace(key, result); return result;
}
pb::GrantPage WorldAuthority::list(const pb::ActorRef& actor, const std::string& after, std::uint32_t limit) {
    return store_.list(actor, after, limit);
}
pb::GrantRecord WorldAuthority::delegate(const pb::ActorRef& actor, const std::string& parent, const pb::GrantTerms& terms,
                                       std::uint64_t revision) {
    const auto result = store_.delegate(actor, parent, terms, revision, [this](const auto& subject) { return active(subject); });
    decisions_.clear(); return result;
}
pb::GrantRecord WorldAuthority::revoke(const pb::ActorRef& actor, const std::string& id, std::uint64_t revision) {
    const auto result = store_.revoke(actor, id, revision, [this](const auto& subject) { return active(subject); });
    decisions_.clear(); return result;
}
void initialize_world_authority(const std::filesystem::path& path, const std::string& world) {
    identity::v1::WorldAuthoritySeed seed;
    if (!google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(path), &seed))
        throw std::invalid_argument("Invalid world authority seed");
    validate_message(seed);
    if (seed.protocol_version() != 1) throw std::invalid_argument("Unsupported world authority seed version");
    authority::Store store(seed.authority_directory(), domain(world), true);
    store.install_roots({seed.roots().begin(), seed.roots().end()}, 0);
}
}
