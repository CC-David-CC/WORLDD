#pragma once
#include "workspace/file_operation_journal.hpp"
#include "supervisor/process_scope.hpp"
#include "supervisor/launch.hpp"
#include <optional>
#include <memory>

namespace spatial {
// Trusted lifecycle setup. No serialized message can construct this participant.
class BrokerCopyCleanupParticipant final {
public:
  // Install before starting the actual owned scope. The callback retains its participant.
  static std::shared_ptr<BrokerCopyCleanupParticipant> deferred(supervisor::AuthorityLaunch& owned_broker);
  static std::shared_ptr<BrokerCopyCleanupParticipant> install(
      supervisor::ProcessScope& owned_broker, int execution_directory_fd);
  void bind(PinnedFileOperationJournal journal);
  void pre_prune(const supervisor::OwnedDrainedTree&);
  const workspace::v1::FileCopyCleanupJournalBinding& journal_binding() const;
private:
  explicit BrokerCopyCleanupParticipant(supervisor::v1::ProcessScopeIdentity);
  std::optional<PinnedFileOperationJournal> journal_;
  supervisor::v1::ProcessScopeIdentity broker_;
};
}
