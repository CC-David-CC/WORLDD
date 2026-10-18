#pragma once
#include "workspace/file_operation_journal.hpp"
#include "supervisor/process_scope.hpp"
#include <memory>

namespace spatial {
// Trusted lifecycle setup. No serialized message can construct this participant.
class BrokerCopyCleanupParticipant final {
public:
  // Install before starting the actual owned scope. The callback retains its participant.
  static std::shared_ptr<BrokerCopyCleanupParticipant> install(
      supervisor::ProcessScope& owned_broker, int execution_directory_fd);
  void pre_prune(const supervisor::OwnedDrainedTree&);
  const workspace::v1::FileCopyCleanupJournalBinding& journal_binding() const;
private:
  BrokerCopyCleanupParticipant(PinnedFileOperationJournal, supervisor::v1::ProcessScopeIdentity);
  PinnedFileOperationJournal journal_;
  supervisor::v1::ProcessScopeIdentity broker_;
};
}
