#pragma once
#include "broker.pb.h"
#include "core/protocol.hpp"
#include <climits>
#include <stdexcept>

namespace spatial {
// The same shape checks apply at both ends of the private control protocol.
inline void validate_control_owner(const workspace::v1::ControlOwner& owner) {
    validate_message(owner);
    const auto& launch = owner.launch();
    const auto& native = owner.native();
    if (!(owner.issuer_id().size() == 32 && launch.participant_id().size() == 32 &&
        launch.registered_revision() > 0 && launch.registered_revision() <= INT64_MAX &&
        launch.generation() > 0 && launch.generation() <= INT64_MAX &&
        launch.service_incarnation().size() == 32 && launch.launch_id().size() == 32 &&
        launch.supervision_revision() > 0 && launch.supervision_revision() <= INT64_MAX &&
        launch.authorization_id().size() == 32 && native.launch_id() == launch.launch_id() &&
        native.server_id().size() == 32 && native.credential_id().size() == 32 &&
        native.role() == security::v1::COMPOSITOR))
        throw std::invalid_argument("Invalid control owner binding");
}
inline void validate_control_recovery(const workspace::v1::ControlOwnerRecovery& recovery) {
    validate_message(recovery);
    validate_control_owner(recovery.owner());
    const auto& process = recovery.process_stopped().identity();
    std::string scope = "authority-";
    constexpr char hex[] = "0123456789abcdef";
    for (unsigned char byte : recovery.owner().launch().launch_id()) {
        scope += hex[byte >> 4]; scope += hex[byte & 15];
    }
    if (!(recovery.ByteSizeLong() <= 4096 && recovery.has_process_stopped() && recovery.has_native_stopped() &&
        process.launch_id() == recovery.owner().launch().launch_id() && process.scope_name() == scope &&
        process.boot_id().size() == 36 && process.boot_id().find('\0') == std::string::npos &&
        process.device() > 0 && process.inode() > 0 &&
        recovery.native_stopped().identity().SerializeAsString() == recovery.owner().native().SerializeAsString()))
        throw std::invalid_argument("Invalid control recovery cleanup binding");
}
}
