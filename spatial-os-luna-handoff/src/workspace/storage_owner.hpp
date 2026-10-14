#pragma once
#include "storage.pb.h"
#include <memory>

namespace spatial {
// Privileged quota administration for one fixed volume and one private broker.
// The caller must authenticate the transport before invoking handle().
class StorageOwner final {
public:
    explicit StorageOwner(workspace::v1::StorageOwnerConfig configuration, bool require_kernel_io = false, bool migrate_copy_catalog = false, unsigned migrate_schema_from = 0);
    ~StorageOwner();
    workspace::v1::StorageOwnerReply handle(const workspace::v1::StorageOwnerRequest& request);
    const workspace::v1::StorageOwnerConfig& configuration() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
void validate_storage_policy(const workspace::v1::StorageOwnerConfig& configuration, bool require_broker = true,
                             bool schema_only_legacy_state = false);
}
