#pragma once
#include "control.pb.h"
namespace spatial::control {
// Metadata preflight only. Never reads events or grabs a device.
void validate_native_capture(const v1::NativeCaptureConfig&);
}
