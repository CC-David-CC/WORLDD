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
BrokerCopyCleanupParticipant::BrokerCopyCleanupParticipant(supervisor::v1::ProcessScopeIdentity broker)
    :broker_(std::move(broker)) {}
std::shared_ptr<BrokerCopyCleanupParticipant> BrokerCopyCleanupParticipant::deferred(
    supervisor::AuthorityLaunch& owned_broker) {
  const auto record=owned_broker.inspect();
  require(record.stage()==supervisor::v1::PREPARED && record.native().role()==security::v1::BROKER,
      "Deferred copy cleanup requires the prepared owned broker launch");
  auto participant=std::shared_ptr<BrokerCopyCleanupParticipant>(
      new BrokerCopyCleanupParticipant(record.process()));
  owned_broker.set_pre_prune_cleanup([participant](const auto& tree){participant->pre_prune(tree);});
  return participant;
}
std::shared_ptr<BrokerCopyCleanupParticipant> BrokerCopyCleanupParticipant::install(
    supervisor::ProcessScope& owned_broker,int execution_directory_fd) {
  auto participant=std::shared_ptr<BrokerCopyCleanupParticipant>(
      new BrokerCopyCleanupParticipant(owned_broker.identity()));
  participant->bind(FileOperationJournal::pin_existing(execution_directory_fd));
  owned_broker.set_pre_prune_cleanup([participant](const auto& tree){participant->pre_prune(tree);});
  return participant;
}
void BrokerCopyCleanupParticipant::bind(PinnedFileOperationJournal journal) {
  require(!journal_ && journal.execution_directory_fd()>=0,
      "Broker copy cleanup journal may bind exactly once");
  journal.verify(); journal_.emplace(std::move(journal));
}
const workspace::v1::FileCopyCleanupJournalBinding& BrokerCopyCleanupParticipant::journal_binding() const {
  require(journal_.has_value(),"Broker copy cleanup journal is not bound");
  return journal_->binding();
}
void BrokerCopyCleanupParticipant::pre_prune(const supervisor::OwnedDrainedTree& tree) {
  const auto deadline=tree.deadline();
  const auto check_deadline=[&] { if(std::chrono::steady_clock::now()>=deadline) throw std::runtime_error("Copy cleanup deadline expired"); };
  check_deadline();
  // Validate the exact retained broker root before considering the no-execution exception.
  require(tree.identity().SerializeAsString()==broker_.SerializeAsString(),"Cleanup requires its actual retained broker root");
  if(!journal_) {
    require(!tree.execution_may_have_run(),"Unbound broker cleanup cannot produce a stop receipt after execution may have run");
    return; // Exact prepared root, before any gate release or external payload attachment.
  }
  journal_->verify(deadline);
  auto journal=FileOperationJournal::open_existing(*journal_,deadline);
  check_deadline();
  const auto root_path=std::filesystem::read_symlink("/proc/self/fd/"+std::to_string(tree.directory_fd()));
  const auto parent_path=std::filesystem::path("/proc/self/fd/"+std::to_string(tree.directory_fd()))/"spatial-git-workers";
  auto parents=journal->retained_git_worker_parents({});
  check_deadline();
  const auto active=journal->find_git_worker_parent({});
  check_deadline();
  if(active.state!=GitWorkerParentRecord::State::absent) parents.push_back(active);
  for(auto job:journal->recovery_copy_jobs_all({})) {
    check_deadline();
    if(job.broker_root_device()!=broker_.device() || job.broker_root_inode()!=broker_.inode() ||
        job.broker_root_boot_id()!=broker_.boot_id()) continue;
    journal_->verify(deadline);
    journal_->verify_workspace_binding(job,deadline);
    check_deadline();
    auto saved=journal->scope_cleanup_outcome(job.workspace_id(),job.job_id(),{});
    check_deadline();
    if(saved) {
      require(saved->journal().SerializeAsString()==journal_->binding(deadline).SerializeAsString() &&
          saved->broker_root().SerializeAsString()==broker_.SerializeAsString() &&
          saved->job_identity_sha256()==FileOperationJournal::copy_job_identity_sha256(job),
          "Saved cleanup disposition belongs to another execution or root");
      if(saved->kind()!=Outcome::RESERVED_SCOPE_RECONCILED) continue;
      // A previously checked reservation may have lost its empty parent during pruning.
    }
    const auto outcome=[&](Outcome::Kind kind,bool uncertain) {
      check_deadline();
      Outcome value;value.set_format_version(1);*value.mutable_journal()=journal_->binding(deadline);
      *value.mutable_broker_root()=broker_;value.set_workspace_id(job.workspace_id());
      value.set_worktree_id(job.worktree_id());value.set_job_id(job.job_id());
      value.set_job_identity_sha256(FileOperationJournal::copy_job_identity_sha256(job));value.set_kind(kind);
      journal->record_scope_cleanup_outcome(job,value,uncertain,{});
      check_deadline();
    };
    const auto parent=std::find_if(parents.begin(),parents.end(),[&](const auto& item) {
      return item.state==GitWorkerParentRecord::State::bound && item.root_device==broker_.device() &&
          item.root_inode==broker_.inode() && item.boot_id==broker_.boot_id() && item.root_path==root_path &&
          item.identity_bytes==job.process_scope_parent().SerializeAsString();
    });
    require(parent!=parents.end(),"Cleanup job lacks its exact durable Git parent generation");
    check_deadline();
    struct stat named{};
    const int parent_result=fstatat(tree.directory_fd(),"spatial-git-workers",&named,AT_SYMLINK_NOFOLLOW);
    check_deadline();
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
      supervisor::ProcessScope::remove_reserved_empty(parent_path,job.process_scope_parent(),job.reserved_process_scope_name(),deadline);
      check_deadline();
      outcome(Outcome::RESERVED_SCOPE_RECONCILED,false);continue;
    }
    check_deadline();
    Fd parent_fd;parent_fd.value=openat(tree.directory_fd(),"spatial-git-workers",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    check_deadline();
    require(parent_fd.value>=0,"Cannot pin cleanup Git parent");
    check_deadline();
    const int child_result=fstatat(parent_fd.value,job.process_scope().scope_name().c_str(),&named,AT_SYMLINK_NOFOLLOW);
    check_deadline();
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
          check_deadline();
          journal_->verify(deadline);
          if(immutable) outcome(Outcome::IMMUTABLE_UNCERTAINTY,false);
          else {
            *job.mutable_drained_process_scope()=identity;
            journal->update_copy_job(job,{});
            check_deadline();
            journal_->verify(deadline);
          }
        },deadline);
    check_deadline();
    // ENOENT after our inspection is not a drain acknowledgment, even with old proof.
    if(!stopped) outcome(Outcome::MISSING_SCOPE,true);
  }
  check_deadline();
  journal_->verify(deadline);
  journal->finish_cleanup();
  check_deadline();
  journal_->verify(deadline);
}
}
