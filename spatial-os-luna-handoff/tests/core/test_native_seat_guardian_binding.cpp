#include "identity/native_seat_binding.hpp"
#include <functional>
#include <iostream>
#include <stdexcept>
#include <vector>

using spatial::identity::guardian::v1::NativeSeatBinding;
using spatial::identity::validate_native_seat_binding;
int main() {
    NativeSeatBinding good;
    good.set_issuer_id(std::string(32, 'i')); good.set_actor_id(std::string(32, 'a'));
    good.set_credential_id(std::string(32, 'c')); good.set_actor_entity_id("identity:actor");
    good.set_actor_session_instance(1); good.set_service_incarnation(std::string(32, 's'));
    good.set_authority_generation(2); good.set_native_server_id(std::string(32, 'n'));
    good.set_compositor_credential_id(std::string(32, 'p')); good.set_native_seat_id(3);
    validate_native_seat_binding(good);
    NativeSeatBinding decoded;
    if (!decoded.ParseFromString(good.SerializeAsString()) || decoded.SerializeAsString()!=good.SerializeAsString()) return 1;
    std::vector<std::function<void(NativeSeatBinding&)>> corruptions={
        [](auto& b){b.clear_issuer_id();}, [](auto& b){b.set_actor_id("short");},
        [](auto& b){b.clear_credential_id();}, [](auto& b){b.clear_actor_entity_id();},
        [](auto& b){b.set_actor_entity_id(std::string("a\0b",3));},
        [](auto& b){b.set_actor_session_instance(0);}, [](auto& b){b.clear_service_incarnation();},
        [](auto& b){b.set_authority_generation(0);}, [](auto& b){b.clear_native_server_id();},
        [](auto& b){b.clear_compositor_credential_id();}, [](auto& b){b.set_native_seat_id(0);}
    };
    for (const auto& corrupt : corruptions) {
        auto bad=good; corrupt(bad); bool rejected=false;
        try {validate_native_seat_binding(bad);} catch (const std::invalid_argument&) {rejected=true;}
        if (!rejected) {std::cerr << "Malformed native seat binding was accepted\n"; return 1;}
    }
    return 0;
}
