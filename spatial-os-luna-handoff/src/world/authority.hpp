#pragma once
#include "authority/store.hpp"
#include "issuer.pb.h"
#include <map>
#include <set>
#include <tuple>

namespace spatial {
// World grants never imply workspace, application, file, or process authority.
class WorldAuthority {
public:
    WorldAuthority(const std::filesystem::path&, const std::string& world, const std::string& issuer);
    std::vector<authority::v1::ActorRef> subjects(const authority::v1::ActorRef& caller);
    void update(const std::vector<identity::v1::ActorState>& states);
    void revoked(const std::string& actor);
    bool allowed(const authority::v1::ActorRef&, const std::string& right, const std::vector<std::string>& components = {});
    authority::v1::GrantPage list(const authority::v1::ActorRef&, const std::string& after, std::uint32_t limit);
    authority::v1::GrantRecord delegate(const authority::v1::ActorRef&, const std::string& parent,
                                      const authority::v1::GrantTerms&, std::uint64_t revision);
    authority::v1::GrantRecord revoke(const authority::v1::ActorRef&, const std::string& id, std::uint64_t revision);
private:
    bool active(const authority::v1::ActorRef&) const;
    authority::v1::Domain domain_;
    authority::Store store_;
    std::string issuer_;
    std::map<std::string, bool> active_;
    std::set<std::string> revoked_;
    using DecisionKey = std::tuple<std::string, std::string, std::vector<std::string>>;
    std::map<DecisionKey, bool> decisions_;
};
void initialize_world_authority(const std::filesystem::path& seed, const std::string& world);
}
