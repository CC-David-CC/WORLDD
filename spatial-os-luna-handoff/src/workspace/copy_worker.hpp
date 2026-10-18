#pragma once
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <stdexcept>
#include "process_scope.pb.h"
#include "copy_worker.pb.h"
#include <vector>
#include <string>
#include <string_view>
#include <sys/stat.h>
#include "storage.pb.h"
namespace spatial {
class WorkspaceResources;
class StorageClient;
class CopyWorkerError final : public std::runtime_error {
public:
    enum class Code { unavailable, invalid_argument, resource_exhausted };
    CopyWorkerError(Code code, const char* message) : std::runtime_error(message), code(code) {}
    Code code;
};
struct CopyWorkerReceipt { std::uint64_t bytes=0; std::uint32_t entries=0; std::string digest; std::string validation; };
void validate_copy_worker_progress(std::uint64_t old_bytes, std::uint32_t old_entries,
    std::uint64_t new_bytes, std::uint32_t new_entries,
    std::uint64_t maximum_bytes, std::uint32_t maximum_entries);
struct CopyWorkerPipeRead { std::string bytes; bool eof = false; };
// The caller supplies a nonblocking pipe. Hitting the byte budget is not EOF.
CopyWorkerPipeRead read_copy_worker_pipe(int fd, std::size_t limit);
class CopyWorkerStreamDecoder final {
public:
    static constexpr std::size_t kMaxInputBytes = 65536;
    static constexpr std::size_t kMaxBufferedBytes = 131072;
    CopyWorkerStreamDecoder(std::uint32_t protocol, std::uint64_t maximum_bytes,
        std::uint32_t maximum_entries);
    void feed(std::string_view bytes, bool end_of_stream = false);
    std::optional<CopyWorkerReceipt> take_progress();
    const std::optional<CopyWorkerReceipt>& latest_progress() const noexcept;
    const std::string& terminal_message() const noexcept;
    bool ready() const noexcept;
    std::size_t buffered_bytes() const noexcept;
private:
    void parse_available();
    std::uint32_t protocol_;
    std::uint64_t maximum_bytes_;
    std::uint32_t maximum_entries_;
    std::string buffered_;
    std::string terminal_message_;
    std::optional<CopyWorkerReceipt> latest_progress_;
    bool progress_changed_ = false;
    bool end_of_stream_ = false;
};
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
        bool cabinet_source_is_directory = false, const struct stat* cabinet_expected_source = nullptr,
        std::string reserved_scope_name = {},
        std::function<void(const supervisor::v1::ProcessScopeIdentity&,
                           const supervisor::v1::ProcessScopeIdentity&)> before_launch = {},
        std::function<void(const supervisor::v1::ProcessScopeIdentity&)> drained = {});
    CopyTreeJob(WorkspaceResources&, const std::vector<int>& staging_fds,
        const workspace::copyworker::v1::WorkerRequest& finalization);
    ~CopyTreeJob();
    CopyTreeJob(const CopyTreeJob&)=delete;
    CopyTreeJob& operator=(const CopyTreeJob&)=delete;
    std::optional<CopyWorkerReceipt> poll();
    std::optional<CopyWorkerReceipt> take_progress();
    supervisor::v1::ProcessScopeState identity() const;
    // Kill the owned worker and return only after ProcessScope proves it empty.
    void cancel();
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
