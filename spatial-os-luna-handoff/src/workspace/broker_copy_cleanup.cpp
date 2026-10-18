#include "workspace/broker_copy_cleanup.hpp"
#include <sqlite3.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <cerrno>
#include <algorithm>
#include <stdexcept>

namespace spatial {
namespace {
using Record=workspace::v1::FileCopyJobJournalRecord;
using Outcome=workspace::v1::FileCopyScopeCleanupOutcome;
void require(bool value,const char* message) { if(!value) throw std::runtime_error(message); }
struct Fd { int value=-1; ~Fd(){if(value>=0)close(value);} };

}
BrokerCopyCleanupParticipant::BrokerCopyCleanupParticipant(PinnedFileOperationJournal journal,
    supervisor::v1::ProcessScopeIdentity broker):journal_(std::move(journal)),broker_(std::move(broker)) {}
std::shared_ptr<BrokerCopyCleanupParticipant> BrokerCopyCleanupParticipant::install(
    supervisor::ProcessScope& owned_broker,int execution_directory_fd) {
  auto participant=std::shared_ptr<BrokerCopyCleanupParticipant>(new BrokerCopyCleanupParticipant(
      FileOperationJournal::pin_existing(execution_directory_fd),owned_broker.identity()));
  owned_broker.set_pre_prune_cleanup([participant](const auto& tree){participant->pre_prune(tree);});
  return participant;
}
const workspace::v1::FileCopyCleanupJournalBinding& BrokerCopyCleanupParticipant::journal_binding() const {
  return journal_.binding();
}
void BrokerCopyCleanupParticipant::pre_prune(const supervisor::OwnedDrainedTree& tree) {
  require(tree.identity().SerializeAsString()==broker_.SerializeAsString(),"Cleanup requires its actual retained broker root");
  journal_.verify();
  auto journal=FileOperationJournal::open_existing(journal_);
  const auto root_path=std::filesystem::read_symlink("/proc/self/fd/"+std::to_string(tree.directory_fd()));
  const auto parent_path=std::filesystem::path("/proc/self/fd/"+std::to_string(tree.directory_fd()))/"spatial-git-workers";
  auto parents=journal->retained_git_worker_parents({});
  const auto active=journal->find_git_worker_parent({});
  if(active.state!=GitWorkerParentRecord::State::absent) parents.push_back(active);
  for(auto job:journal->recovery_copy_jobs_all({})) {
    if(job.broker_root_device()!=broker_.device() || job.broker_root_inode()!=broker_.inode() ||
        job.broker_root_boot_id()!=broker_.boot_id()) continue;
    journal_.verify();
    journal_.verify_workspace_binding(job);
    auto saved=journal->scope_cleanup_outcome(job.workspace_id(),job.job_id(),{});
    if(saved) {
      require(saved->journal().SerializeAsString()==journal_.binding().SerializeAsString() &&
          saved->broker_root().SerializeAsString()==broker_.SerializeAsString() &&
          saved->job_identity_sha256()==FileOperationJournal::copy_job_identity_sha256(job),
          "Saved cleanup disposition belongs to another execution or root");
      if(saved->kind()!=Outcome::RESERVED_SCOPE_RECONCILED) continue;
      // A previously checked reservation may have lost its empty parent during pruning.
    }
    const auto outcome=[&](Outcome::Kind kind,bool uncertain) {
      Outcome value;value.set_format_version(1);*value.mutable_journal()=journal_.binding();
      *value.mutable_broker_root()=broker_;value.set_workspace_id(job.workspace_id());
      value.set_worktree_id(job.worktree_id());value.set_job_id(job.job_id());
      value.set_job_identity_sha256(FileOperationJournal::copy_job_identity_sha256(job));value.set_kind(kind);
      journal->record_scope_cleanup_outcome(job,value,uncertain,{});
    };
    const auto parent=std::find_if(parents.begin(),parents.end(),[&](const auto& item) {
      return item.state==GitWorkerParentRecord::State::bound && item.root_device==broker_.device() &&
          item.root_inode==broker_.inode() && item.boot_id==broker_.boot_id() && item.root_path==root_path &&
          item.identity_bytes==job.process_scope_parent().SerializeAsString();
    });
    require(parent!=parents.end(),"Cleanup job lacks its exact durable Git parent generation");
    struct stat named{};
    const int parent_result=fstatat(tree.directory_fd(),"spatial-git-workers",&named,AT_SYMLINK_NOFOLLOW);
    if(parent_result<0 && errno!=ENOENT) throw std::runtime_error("Cannot inspect cleanup Git parent");
    if(parent_result<0) {
      if(saved && saved->kind()==Outcome::RESERVED_SCOPE_RECONCILED) continue;
      if(job.has_drained_process_scope()) continue;
      outcome(Outcome::INVALID_PARENT,true);continue;
    }
    if(!S_ISDIR(named.st_mode) || static_cast<std::uint64_t>(named.st_dev)!=job.process_scope_parent().device() ||
        static_cast<std::uint64_t>(named.st_ino)!=job.process_scope_parent().inode()) {
      outcome(Outcome::INVALID_PARENT,true);continue;
    }
    if(!job.has_process_scope()) {
      if(saved) {
        struct stat child{};
        const auto child_path=parent_path/job.reserved_process_scope_name();
        const int rc=lstat(child_path.c_str(),&child);
        require(rc<0 && errno==ENOENT,"Reconciled reserved scope was recreated");
        continue;
      }
      require(job.state()==workspace::v1::FILE_COPY_JOB_JOURNAL_START_INTENT,"Unbound cleanup job is not a start intent");
      supervisor::ProcessScope::remove_reserved_empty(parent_path,job.process_scope_parent(),job.reserved_process_scope_name());
      outcome(Outcome::RESERVED_SCOPE_RECONCILED,false);continue;
    }
    Fd parent_fd;parent_fd.value=openat(tree.directory_fd(),"spatial-git-workers",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    require(parent_fd.value>=0,"Cannot pin cleanup Git parent");
    const int child_result=fstatat(parent_fd.value,job.process_scope().scope_name().c_str(),&named,AT_SYMLINK_NOFOLLOW);
    if(child_result<0 && errno!=ENOENT) throw std::runtime_error("Cannot inspect recorded cleanup worker");
    if(child_result<0) {
      if(!job.has_drained_process_scope()) outcome(Outcome::MISSING_SCOPE,true);
      continue;
    }
    if(!S_ISDIR(named.st_mode) || static_cast<std::uint64_t>(named.st_dev)!=job.process_scope().device() ||
        static_cast<std::uint64_t>(named.st_ino)!=job.process_scope().inode()) {
      outcome(Outcome::REPLACED_SCOPE,true);continue;
    }
    const bool immutable=job.state()==workspace::v1::FILE_COPY_JOB_JOURNAL_UNCERTAIN;
    const bool stopped=supervisor::ProcessScope::stop_recovered(parent_path,job.process_scope_parent().device(),
        job.process_scope_parent().inode(),job.process_scope(),[&](const auto& identity) {
          journal_.verify();
          if(immutable) outcome(Outcome::IMMUTABLE_UNCERTAINTY,false);
          else {
            *job.mutable_drained_process_scope()=identity;
            journal->update_copy_job(job,{});
            journal_.verify();
          }
        });
    // ENOENT after our inspection is not a drain acknowledgment, even with old proof.
    if(!stopped) outcome(Outcome::MISSING_SCOPE,true);
  }
  journal_.verify();
  journal->finish_cleanup();
  journal_.verify();
}
}
