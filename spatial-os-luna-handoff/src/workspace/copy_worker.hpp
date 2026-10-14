#pragma once
#include <cstdint>
#include <filesystem>
#include <functional>
#include <string>
#include "storage.pb.h"
namespace spatial {
class WorkspaceResources;
class StorageClient;
struct CopyWorkerReceipt { std::uint64_t bytes=0; std::uint32_t entries=0; std::string digest; };
workspace::v1::StorageCopyStageReceipt copy_registered_snapshot_to_staging(StorageClient& storage, WorkspaceResources& resources,
    const workspace::v1::StorageCopyStageBegin& intent, const std::function<void()>& progress = {});
CopyWorkerReceipt run_copy_tree_worker(WorkspaceResources& resources,
    const std::filesystem::path& immutable_source, const std::filesystem::path& writable_destination,
    std::uint64_t maximum_bytes, std::uint32_t maximum_entries,
    const std::function<void()>& progress = {});
}
