#pragma once
#include <chrono>
#include <cstdint>
#include <functional>
#include <sys/stat.h>
#include <memory>
#include <future>
#include <string>
#include <string_view>
#include <vector>

#include "broker.pb.h"
#include "cabinet.pb.h"
#include "workspace/file_operation_journal.hpp"

namespace spatial {
class WorkspaceResources;
namespace file_engine_detail {
// Real directory durability helper used before acknowledging trash-directory creation.
bool sync_directory(int descriptor) noexcept;
// Opens only a pinned regular inode. FIFO/device paths never receive a data open.
// Returns an owned descriptor, or -1 with errno set. Nested mounts are rejected.
int open_regular_file(int directory, const char* basename,
                      std::uint64_t expected_generation = 0) noexcept;
// A changed broker-root inode does not prove that a same-boot worker exited.
bool can_skip_recovered_scope_cleanup(bool same_owner_root,
                                      std::string_view recorded_boot_id,
                                      std::string_view current_boot_id) noexcept;
}  // namespace file_engine_detail
// Internal adapter. Implement this with a fresh broker scoped-authority call.
// It must not return a cached decision for a consequential file operation.
class FileAuthority {
 public:
  virtual ~FileAuthority() = default;
  virtual workspace::v1::ScopedAuthorityCheckReply check_fresh(
      const workspace::v1::ScopedAuthorityCheckRequest&) = 0;
};

// One bounded filesystem syscall or chunk. Results cross back to the broker thread
// by value; the worker never touches engine maps, authority, or SQLite.
struct FileIoResult {
  int value = -1;
  int error = 0;
  bool effect_may_have_occurred = false;
  bool owns_descriptor = false;
  struct stat metadata{};
  std::string bytes;
};
FileIoResult wait_file_io_task(std::future<FileIoResult>&,
                               const std::function<void()>& observer,
                               bool effectful) noexcept;
class FileEffectExecutor {
 public:
  using Step = std::function<FileIoResult()>;
  virtual ~FileEffectExecutor() = default;
  virtual FileIoResult run_step(const workspace::v1::FileActionRequest&,
      authority::v1::Right right, bool effectful, Step step) = 0;
};

// Private worldd-side backend. root_fd must be the broker-authorized project
// directory descriptor. A usable descriptor to that inode is pinned for its lifetime.
class FileEngine final {
 public:
  using MonotonicClock = std::chrono::steady_clock;
  using MonotonicNow = std::function<MonotonicClock::time_point()>;
  FileEngine(
      int root_fd, std::string workspace_id, std::vector<std::string> opaque_scope_components,
      FileAuthority& authority, MonotonicNow now = [] { return MonotonicClock::now(); },
      FileEffectExecutor* effects = nullptr, WorkspaceResources* resources = nullptr,
      int staging_parent_fd = -1, std::string staging_root_name = {},
      FileOperationJournal* journal = nullptr,
      FileOperationJournal::Observer journal_observer = {});
  ~FileEngine();
  FileEngine(const FileEngine&) = delete;
  FileEngine& operator=(const FileEngine&) = delete;
  workspace::v1::FileActionReply handle(const workspace::v1::FileActionRequest&);
  // Stop only the matching owner's active copy job after an outer authority denial.
  void stop_copy_job_for_authority_loss(const std::string& job_id, const std::string& principal_identity) noexcept;
  // Recheck active owners when the broker consumes an authority change.
  void reconcile_copy_job_authority(const std::function<bool(const std::string&)>& allowed) noexcept;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}  // namespace spatial
