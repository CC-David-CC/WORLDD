#pragma once
#include "control.pb.h"
#include <filesystem>
#include <optional>
#include <sys/types.h>

namespace spatial::control::physical_detail {
struct KernelIdentity {
    std::filesystem::path node;
    dev_t filesystem = 0;
    ino_t inode = 0;
    dev_t device = 0;
    bool operator==(const KernelIdentity&) const = default;
};
// Inspect kernel metadata without reading events or requiring evdev read access.
// A missing, replaced, inaccessible, or mismatched node returns no identity.
std::optional<KernelIdentity> kernel_identity(const std::filesystem::path& configured, v1::DeviceKind kind);
}
