#pragma once
#include "process_scope.pb.h"
#include <span>

namespace spatial {
// Resolve actual queues from retained directories, including loop backing storage.
void validate_storage_device_handles(const supervisor::v1::ProcessScopeBudget& budget,
                                     std::span<const int> directories);
// Require finite kernel read/write ceilings for the current service's pinned volume.
void validate_current_storage_io(int directory);
}
