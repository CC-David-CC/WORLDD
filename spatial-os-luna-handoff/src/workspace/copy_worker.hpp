#pragma once
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include "process_scope.pb.h"
#include "copy_worker.pb.h"
#include <vector>
#include <string>
#include "storage.pb.h"
namespace spatial {
class WorkspaceResources;
class StorageClient;
struct CopyWorkerReceipt { std::uint64_t bytes=0; std::uint32_t entries=0; std::string digest; std::string validation; };
// True only when destination_directory is inside the pinned source directory.
bool cabinet_copy_destination_inside_source(int source_directory_fd, int destination_directory_fd);
// All start/poll/finalize calls run on the broker owner thread.
// The child receives sealed protobuf input and pinned roots. It never receives SQLite.
class CopyTreeJob final {
public:
    CopyTreeJob(WorkspaceResources&, int source_fd, int destination_fd,
        std::uint64_t source_subvolume_id, std::uint64_t destination_subvolume_id,
        std::uint64_t maximum_bytes, std::uint32_t maximum_entries,
        const workspace::copyworker::v1::StateExportRequest* state_export = nullptr,
        std::string cabinet_source_name = {}, std::string cabinet_destination_name = {},
        bool cabinet_source_is_directory = false);
    CopyTreeJob(WorkspaceResources&, const std::vector<int>& staging_fds,
        const workspace::copyworker::v1::WorkerRequest& finalization);
    ~CopyTreeJob();
    CopyTreeJob(const CopyTreeJob&)=delete;
    CopyTreeJob& operator=(const CopyTreeJob&)=delete;
    std::optional<CopyWorkerReceipt> poll();
    supervisor::v1::ProcessScopeState identity() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
void validate_copy_stage_receipt(const workspace::v1::StorageCopyStageReceipt&,
    const workspace::v1::StorageCopyStageBegin&, const workspace::v1::StorageObjectState& source,
    const workspace::v1::StorageObjectState& destination, StorageClient&);
workspace::v1::StorageCopyStageReceipt copy_registered_snapshot_to_staging(StorageClient& storage, WorkspaceResources& resources,
    const workspace::v1::StorageCopyStageBegin& intent, const std::function<void()>& progress = {});
CopyWorkerReceipt run_copy_tree_worker(WorkspaceResources& resources,
    int immutable_source_fd, int writable_destination_fd, std::uint64_t source_subvolume_id,
    std::uint64_t destination_subvolume_id, std::uint64_t maximum_bytes, std::uint32_t maximum_entries,
    const std::function<void()>& progress = {});
}
