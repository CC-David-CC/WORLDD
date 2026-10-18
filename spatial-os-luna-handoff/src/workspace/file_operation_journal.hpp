#pragma once
#include "file_actions.pb.h"
#include "file_copy_job_journal.pb.h"
#include <cstdint>
#include <chrono>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace spatial {
using CleanupDeadline = std::chrono::steady_clock::time_point;
constexpr CleanupDeadline kNoCleanupDeadline = CleanupDeadline::max();
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
class PinnedFileOperationJournal final {
public:
  ~PinnedFileOperationJournal();
  PinnedFileOperationJournal(PinnedFileOperationJournal&&) noexcept;
  PinnedFileOperationJournal& operator=(PinnedFileOperationJournal&&) noexcept;
  PinnedFileOperationJournal(const PinnedFileOperationJournal&) = delete;
  const workspace::v1::FileCopyCleanupJournalBinding& binding(
      CleanupDeadline deadline = kNoCleanupDeadline) const;
  void verify(CleanupDeadline deadline = kNoCleanupDeadline) const;
  int execution_directory_fd() const;
  void verify_workspace_binding(const workspace::v1::FileCopyJobJournalRecord&,
      CleanupDeadline deadline = kNoCleanupDeadline) const;
private:
  friend class FileOperationJournal;
  struct Impl;
  explicit PinnedFileOperationJournal(std::shared_ptr<Impl>);
  std::shared_ptr<Impl> impl_;
};
class FileOperationJournal final {
public:
  using Observer = std::function<void()>;
  using CopyJobRecord = workspace::v1::FileCopyJobJournalRecord;
  explicit FileOperationJournal(const std::filesystem::path& state_directory);
  ~FileOperationJournal();
  // Trusted lifecycle setup only. Neither protobuf nor a pathname mints this capability.
  // Existing-only recovery may create bounded private hardlink aliases. It never
  // creates a database, migrates schema, changes modes, or replaces a database.
  static PinnedFileOperationJournal pin_existing(int execution_directory_fd);
  // Acquire execution.lock. Never create, migrate, chmod, or replace an existing journal.
  static std::unique_ptr<FileOperationJournal> open_existing(const PinnedFileOperationJournal&,
      CleanupDeadline deadline = kNoCleanupDeadline);
  static std::string copy_job_identity_sha256(const CopyJobRecord&);
  std::optional<workspace::v1::FileCopyScopeCleanupOutcome> scope_cleanup_outcome(
      const std::string& workspace, const std::string& job_id, const Observer& observer);
  // Validate a stored disposition against the exact current job and pinned
  // execution/journal binding before it can affect recovery decisions.
  std::optional<workspace::v1::FileCopyScopeCleanupOutcome> validated_scope_cleanup_outcome(
      const CopyJobRecord& expected, const GitWorkerParentRecord* parent,
      const PinnedFileOperationJournal& current, const Observer& observer);
  // Exact old bytes are checked in the same transaction as outcome insertion and optional uncertainty.
  void record_scope_cleanup_outcome(const CopyJobRecord& expected,
      const workspace::v1::FileCopyScopeCleanupOutcome&, bool mark_uncertain, const Observer& observer);
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
  friend class BrokerCopyCleanupParticipant;
  void finish_cleanup();
  struct Impl;
  explicit FileOperationJournal(std::unique_ptr<Impl>);
  std::unique_ptr<Impl> impl_;
};
}  // namespace spatial
