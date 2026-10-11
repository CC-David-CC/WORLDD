#include "identity/bootstrap.hpp"
#include "authority/store.hpp"
#include "core/protocol.hpp"
#include <algorithm>
#include <cstdint>
#include <set>
#include <stdexcept>

namespace spatial::identity {
namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::invalid_argument(message);
}
void identifier(const std::string& id) {
    require(!id.empty() && id.size() <= 128 && id.find('\0') == std::string::npos,
            "Bootstrap identity must contain one to 128 nonzero bytes");
}
}
void validate_bootstrap_profile(const v1::BootstrapProfile& profile) {
    validate_message(profile); identifier(profile.id());
    require(profile.grants_size() <= 32 && profile.ByteSizeLong() <= 32768,
            "Bootstrap profile exceeds template or byte limit");
    std::set<std::string> ids;
    for (const auto& grant : profile.grants()) {
        identifier(grant.id());
        require(ids.insert(grant.id()).second && grant.has_terms() && !grant.terms().has_subject(),
                "Bootstrap templates require unique identities and recipient-free terms");
        authority::validate_permissions(grant.terms());
    }
}
void validate_bootstrap_read(const v1::BootstrapProfileRead& request, bool exact) {
    validate_message(request); identifier(request.id());
    require(!exact || request.has_revision(), "An exact bootstrap revision is required");
    require(!request.has_revision() || (request.revision() > 0 && request.revision() <= INT64_MAX),
            "Bootstrap revision is outside the storage range");
}
bool bootstrap_has_administration(const v1::BootstrapProfile& profile) {
    for (const auto& grant : profile.grants())
        if (std::find(grant.terms().rights().begin(), grant.terms().rights().end(),
                      authority::v1::POLICY_ADMINISTER) != grant.terms().rights().end()) return true;
    return false;
}
}
