#pragma once
#include "broker.pb.h"

namespace spatial::supervisor {
// Validate before interpreting either outcome. A validated error remains an error.
// This checks protocol evidence, not transport authentication or native cleanup.
void validate_control_reply(const workspace::v1::ControlSupervisorRequest& request,
                            const workspace::v1::ControlSupervisorReply& reply);
}
