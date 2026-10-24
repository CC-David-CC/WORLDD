#pragma once
#include "control.pb.h"
#include <filesystem>

namespace spatial::control {
v1::ControlProfiles default_profiles();
void validate_profiles(const v1::ControlProfiles& profiles);
const v1::ControlProfile& active_profile(const v1::ControlProfiles& profiles);
// Binary Protobuf, private host-owned configuration. No actor credentials.
v1::ControlProfiles load_profiles(const std::filesystem::path& path);
void save_profiles(const std::filesystem::path& path, const v1::ControlProfiles& profiles);
}
