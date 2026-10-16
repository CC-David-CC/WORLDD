#pragma once
#include "file_actions.pb.h"
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <string>

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
class FileOperationJournal final {
public:
  using Observer = std::function<void()>;
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
private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}  // namespace spatial
