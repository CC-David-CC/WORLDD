#pragma once
#include "storage.pb.h"
#include "core/protocol.hpp"
#include <set>
#include <stdexcept>
#include <string>

namespace spatial::copy_collections {
namespace pb=workspace::v1;
constexpr unsigned maximum_applications=32;
inline void require(bool value,const char* reason) {if(!value)throw std::invalid_argument(reason);}
inline bool key(const std::string& value) {
    return !value.empty() && value.size()<=64 &&
        value.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")==std::string::npos;
}
inline bool profile_name(const std::string& value) {return !value.empty() && value.size()<=128 && value.find('\0')==std::string::npos;}
inline bool object_id(const std::string& value) {
    return value.size()==32 && value.find_first_not_of("0123456789abcdef")==std::string::npos;
}
inline void contract(const pb::ApplicationStateContract& value) {
    validate_message(value);
    require(value.schema_id()=="spatial.application.quiesced-files" && value.schema_version()==1 &&
            value.subdomains_size()>0 && value.subdomains_size()<=2,"Unsupported application-owned persistent state contract");
    std::set<int> domains;
    for(const auto domain:value.subdomains())
        require((domain==pb::PERSISTENT_DATA || domain==pb::PERSISTENT_CONFIG) && domains.insert(domain).second,
                "Unknown or duplicate persistent state subdomain");
}
inline void definition(const pb::CopyApplicationDefinition& value) {
    validate_message(value);
    require(value.schema_version()==1 && !value.name().empty() && value.name().size()<=128 &&
            value.name().find('\0')==std::string::npos && !value.executable().empty() &&
            value.executable().front()=='/' && value.executable().size()<=4096 &&
            value.executable().find('\0')==std::string::npos && value.arguments_size()<=128,
            "Unsupported application definition");
    std::size_t bytes=value.executable().size()+1;
    for(const auto& argument:value.arguments()) {
        require(argument.size()<=4096 && argument.find('\0')==std::string::npos,"Invalid configured application argument");
        bytes+=argument.size()+1;
    }
    require(bytes<=32768,"Configured application argv exceeds launch bound");
}
inline void definitions(const pb::CopyApplicationDefinitionCollection& value) {
    validate_message(value);
    require(value.schema_version()==2 && value.applications_size()>0 &&
            value.applications_size()<=static_cast<int>(maximum_applications),"Invalid bounded application definition collection");
    std::set<std::string> keys;
    for(const auto& app:value.applications()) {
        require(key(app.key()) && keys.insert(app.key()).second && profile_name(app.profile()),
                "Invalid or duplicate configured application key/profile");
        definition(app.definition());contract(app.state_contract());
    }
}
inline void states(const pb::CopyApplicationStateCollection& value,bool published) {
    validate_message(value);
    require(value.schema_version()==2 && value.slots_size()>0 &&
            value.slots_size()<=static_cast<int>(maximum_applications),"Invalid bounded application state collection");
    std::set<std::string> keys,objects;
    std::uint64_t bytes=0;std::uint32_t entries=0;
    for(const auto& slot:value.slots()) {
        require(key(slot.key()) && keys.insert(slot.key()).second,"Invalid or duplicate application state key");
        contract(slot.contract());
        if(published) {
            require(slot.has_state_object() && slot.state_object().kind()==pb::StorageObject::APPLICATION_STATE &&
                    object_id(slot.state_object().id()) && objects.insert(slot.state_object().id()).second &&
                    slot.content_digest().size()==32 && slot.actual_bytes()<=(4ULL<<30)-bytes &&
                    slot.actual_entries()<=200000-entries,"Invalid destination application state slot");
            bytes+=slot.actual_bytes();entries+=slot.actual_entries();
        } else {
            require(!slot.has_state_object() && slot.content_digest().empty() &&
                    slot.actual_bytes()==0 && slot.actual_entries()==0,"Source registration must not contain stale state bytes or identities");
        }
    }
}
inline void mapping(const pb::ConfiguredStateMapping& value) {
    validate_message(value);
    require(!value.workspace_id().empty() && value.revision()>0 && value.entries_size()>0 &&
            value.entries_size()<=static_cast<int>(maximum_applications),"Invalid configured state mapping");
    std::set<std::string> keys,objects;std::string previous;
    for(const auto& entry:value.entries()) {
        require(previous.empty() || previous<entry.key(),"Configured mapping keys are not in canonical order");previous=entry.key();
        require(key(entry.key()) && keys.insert(entry.key()).second && entry.has_state_object() &&
                entry.state_object().kind()==pb::StorageObject::APPLICATION_STATE &&
                object_id(entry.state_object().id()) && objects.insert(entry.state_object().id()).second,
                "Duplicate, missing, or substituted configured state mapping");
        contract(entry.contract());
    }
}
}
