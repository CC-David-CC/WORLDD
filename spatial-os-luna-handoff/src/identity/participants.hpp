#pragma once
#include "core/protocol.hpp"
#include "issuer.pb.h"
#include <cstdint>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>

namespace spatial::identity {
namespace participant_detail {
inline void require(bool valid, const char* message) {
    if (!valid) throw std::invalid_argument(message);
}
inline auto domain_key(const authority::v1::Domain& domain) {
    return std::pair{static_cast<int>(domain.kind()), domain.id()};
}
}
inline void validate_participant(const v1::AuthorityParticipant& participant) {
    using participant_detail::require;
    validate_message(participant);
    require(participant.id().size() == 32 && participant.public_key().size() == 32,
            "Participant identity and service key must contain 32 bytes");
    require(participant.domains_size() > 0 && participant.domains_size() <= 64,
            "Participant requires one to 64 domains");
    std::set<std::pair<int, std::string>> domains;
    for (const auto& domain : participant.domains()) {
        require((domain.kind() == authority::v1::WORLD || domain.kind() == authority::v1::WORKSPACE) &&
                !domain.id().empty() && domain.id().size() <= 256 && domain.id().find('\0') == std::string::npos,
                "Participant requires a valid authority domain");
        require(domains.insert(participant_detail::domain_key(domain)).second, "Duplicate participant domain");
    }
}
inline void validate_registry_read(const v1::AuthorityRegistryRead& read) {
    validate_message(read);
    participant_detail::require((read.after_id().empty() || read.after_id().size() == 32) && read.limit() <= 16,
                                "Invalid registry cursor or page limit");
}
inline void validate_participant_record(const v1::AuthorityParticipantRecord& record, const std::string& issuer) {
    using participant_detail::require;
    validate_message(record);
    require(record.has_participant() && issuer.size() == 32 && record.issuer_id() == issuer &&
            record.registered_revision() > 0 && record.registered_revision() <= INT64_MAX,
            "Invalid participant record context");
    validate_participant(record.participant());
    for (int i = 1; i < record.participant().domains_size(); ++i)
        require(participant_detail::domain_key(record.participant().domains(i-1)) <
                participant_detail::domain_key(record.participant().domains(i)), "Participant domains are not canonical");
}
inline void validate_registry_page(const v1::AuthorityRegistryPage& page, const std::string& issuer) {
    using participant_detail::require;
    validate_message(page);
    require(issuer.size() == 32 && page.issuer_id() == issuer && page.registry_revision() <= INT64_MAX &&
            page.participants_size() <= 16, "Invalid authority registry context");
    std::string prior;
    std::set<std::string> keys;
    std::set<std::uint64_t> revisions;
    std::set<std::pair<int, std::string>> domains;
    for (const auto& record : page.participants()) {
        validate_participant_record(record, issuer);
        require(record.registered_revision() <= page.registry_revision() && (prior.empty() || prior < record.participant().id()) &&
                keys.insert(record.participant().public_key()).second && revisions.insert(record.registered_revision()).second,
                "Invalid registry record order or revision");
        prior = record.participant().id();
        for (const auto& domain : record.participant().domains())
            require(domains.insert(participant_detail::domain_key(domain)).second, "Conflicting registry domains");
    }
    require(page.next_after().empty() || (!prior.empty() && page.next_after() == prior), "Invalid registry continuation");
}
}
