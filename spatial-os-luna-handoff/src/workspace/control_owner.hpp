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
}
