#pragma once
#include "core.pb.h"

namespace spatial::actor_input {
// Only cancellable input/observation primitives use the controlled-input reply.
// Filesystem, process, Git, durable object, and authority effects keep strict errors.
inline bool reversible(const core::v1::ActorActionRequest& action) {
    switch (action.action_case()) {
    case core::v1::ActorActionRequest::kInput:
    case core::v1::ActorActionRequest::kRelativeLook:
    case core::v1::ActorActionRequest::kStop:
    case core::v1::ActorActionRequest::kAim:
    case core::v1::ActorActionRequest::kPointer:
    case core::v1::ActorActionRequest::kKey:
    case core::v1::ActorActionRequest::kScroll:
    case core::v1::ActorActionRequest::kUnfocus:
    case core::v1::ActorActionRequest::kObserve:
        return true;
    default: return false;
    }
}
inline bool cancelled_rejection(core::v1::ErrorCode code) {
    switch (code) {
    case core::v1::FORBIDDEN:
    case core::v1::NO_TARGET:
    case core::v1::STALE_TARGET:
    case core::v1::NO_FOCUS:
    case core::v1::BUSY:
    case core::v1::NOT_VISIBLE:
    case core::v1::OUT_OF_REACH:
        return true;
    default: return false;
    }
}
}
