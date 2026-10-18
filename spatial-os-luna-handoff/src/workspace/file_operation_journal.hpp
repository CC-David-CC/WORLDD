#pragma once
#include "file_actions.pb.h"
#include "file_copy_job_journal.pb.h"
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace spatial {
struct FileOperationBinding {
  std::string workspace;
  std::string operation_id;
  std::string principal;
  std::string worktree;
  std::uint64_t root_device = 0;
  std::uint64_t root_inode = 0;
  int right = 0;
  std::string request_sha256;
  std::string engine_incarnation;
};
struct FileOperationRecord {
  enum class State { absent, intent, complete, uncertain, conflict } state = State::absent;
  std::string engine_incarnation;
  workspace::v1::FileActionReply reply;
};
struct GitWorkerParentRecord {
  enum class State { absent, reserved, bound } state = State::absent;
  std::uint64_t root_device = 0, root_inode = 0;
  std::string boot_id;
  std::string root_path;
  std::string identity_bytes;
};
class FileOperationJournal final {
public:
  using Observer = std::function<void()>;
  using CopyJobRecord = workspace::v1::FileCopyJobJournalRecord;
  explicit FileOperationJournal(const std::filesystem::path& state_directory);
  ~FileOperationJournal();
  FileOperationJournal(const FileOperationJournal&) = delete;
  FileOperationJournal& operator=(const FileOperationJournal&) = delete;
  bool bind_root(const std::string& worktree, std::uint64_t device, std::uint64_t inode,
                 const Observer& observer);
  FileOperationRecord find(const FileOperationBinding&, const Observer& observer);
  void begin(const FileOperationBinding&, const Observer& observer);
  void finish(const FileOperationBinding&, FileOperationRecord::State,
              const workspace::v1::FileActionReply&, const Observer& observer);
  void mark_uncertain(const FileOperationBinding&, const workspace::v1::FileActionReply&,
                      const Observer& observer);

  // Copy-job records contain no live handles. Their root-relative paths and
  // filesystem identities support fail-closed restart reconciliation.
  void begin_copy_job(const CopyJobRecord&, const Observer& observer);
  void update_copy_job(const CopyJobRecord&, const Observer& observer);
  void request_copy_job_cancel(CopyJobRecord&, const Observer& observer);
  std::optional<CopyJobRecord> find_copy_job(const std::string& workspace,
      const std::string& job_id, const Observer& observer);
  std::optional<CopyJobRecord> find_copy_job_by_operation(const std::string& workspace,
      const std::string& initiating_operation_id, const Observer& observer);
  std::vector<CopyJobRecord> recovery_copy_jobs(const std::string& workspace,
      const Observer& observer);
  GitWorkerParentRecord reserve_git_worker_parent(std::uint64_t root_device,
      std::uint64_t root_inode, const std::string& root_path,
      const std::string& boot_id, const Observer& observer);
  void bind_git_worker_parent(std::uint64_t root_device, std::uint64_t root_inode,
      const std::string& root_path, const std::string& boot_id, const std::string& identity_bytes, const Observer& observer);
  GitWorkerParentRecord find_git_worker_parent(const Observer& observer);
  void clear_git_worker_parent(const GitWorkerParentRecord& expected, const Observer& observer);
  // Retire the active generation without discarding unresolved recovery evidence.
  void retain_git_worker_parent(const GitWorkerParentRecord& expected, const Observer& observer);
  std::vector<GitWorkerParentRecord> retained_git_worker_parents(const Observer& observer);
  std::vector<CopyJobRecord> recovery_copy_jobs_all(const Observer& observer);
  std::size_t prune_terminal_copy_jobs(const std::string& workspace,
      std::uint64_t older_than_unix_ms, std::size_t maximum_rows,
      const Observer& observer);
private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}  // namespace spatial
