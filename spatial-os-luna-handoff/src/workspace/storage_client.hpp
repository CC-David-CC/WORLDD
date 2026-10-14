#pragma once
#include "storage.pb.h"
#include <filesystem>
#include <functional>
#include <memory>

namespace spatial {
class StorageClient final {
public:
    // Consume the private descriptor. Verify the volume before broker filesystem effects.
    StorageClient(int descriptor, const std::filesystem::path& volume);
    ~StorageClient();
    void set_observer(std::function<void()> observer);
    void check_alive() const;
    void fence_unavailable() noexcept;
    workspace::v1::StorageObjectState call(workspace::v1::StorageOwnerRequest request,
                                         const std::function<void()>& tick = {});
    int open_object(const workspace::v1::StorageObjectState& registered) const; // Caller owns returned descriptor.
    workspace::v1::StorageCopyDestinationReceipt prepare_copy_destination(const workspace::v1::StorageCopyDestinationPrepare&);
    workspace::v1::StorageCopyDestinationReceipt publish_copy_destination(const workspace::v1::StorageCopyDestinationPublish&);
    workspace::v1::StorageCopyDestinationReceipt inspect_copy_destination(const workspace::v1::StorageCopyDestinationInspect&);
    workspace::v1::StorageConfiguredApplicationAllocation prepare_configured_application(const workspace::v1::StorageConfiguredApplicationPrepare& request);
    workspace::v1::StorageConfiguredApplicationReply query_configured_application(const workspace::v1::StorageConfiguredApplicationQuery& request);
    workspace::v1::StorageCopyCatalogReply query_copy_catalog(const workspace::v1::StorageCopyCatalogQuery& request);
    workspace::v1::StorageCopyStageReceipt begin_copy_stage(const workspace::v1::StorageCopyStageBegin& request);
    workspace::v1::StorageCopyStageReceipt start_copy_stage(const workspace::v1::StorageCopyStageStart& request);
    workspace::v1::StorageCopyStageReceipt seal_copy_stage(const workspace::v1::StorageCopyStageSeal& request);
    workspace::v1::StorageCopyStageReceipt mark_copy_stage_uncertain(const workspace::v1::StorageCopyStageUncertain& request);
    workspace::v1::StorageCopyStageReceipt inspect_copy_stage(const workspace::v1::StorageCopyStageInspect& request);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
