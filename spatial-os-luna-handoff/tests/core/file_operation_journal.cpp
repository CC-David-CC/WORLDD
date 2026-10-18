#include "workspace/file_operation_journal.hpp"
#include "grants.pb.h"
#include <sqlite3.h>
#include <filesystem>
#include <array>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

using namespace spatial;
using namespace spatial::workspace::v1;

namespace {
void check(bool ok,const char* message) { if(!ok) throw std::runtime_error(message); }
template<class F> void rejects(F&& operation,const char* message) {
  bool rejected=false;
  try { operation(); } catch(const std::exception&) { rejected=true; }
  check(rejected,message);
}
std::filesystem::path make_temp() {
  std::array<char,128> path{};
  const auto pattern=(std::filesystem::temp_directory_path()/"spatial-copy-journal-XXXXXX").string();
  check(pattern.size()+1<=path.size(),"temporary state path too long");
  std::copy(pattern.begin(),pattern.end(),path.begin());
  char* created=mkdtemp(path.data());
  check(created!=nullptr,"could not create journal fixture directory");
  return created;
}
void exec_sql(sqlite3* db,const char* command) {
  char* error=nullptr;
  const int rc=sqlite3_exec(db,command,nullptr,nullptr,&error);
  const std::string detail=error?error:"";
  sqlite3_free(error);
  if(rc!=SQLITE_OK) throw std::runtime_error("legacy fixture SQL failed: "+detail);
}
std::filesystem::path make_v1_state() {
  auto state=make_temp();
  const auto directory=state/"file-operations";
  std::filesystem::create_directory(directory);
  check(chmod(directory.c_str(),0700)==0,"could not protect v1 fixture directory");
  sqlite3* db=nullptr;
  const auto database=directory/"file-operations.db";
  check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"could not create v1 fixture database");
  exec_sql(db,
    "CREATE TABLE file_roots(worktree TEXT PRIMARY KEY,device INTEGER NOT NULL,inode INTEGER NOT NULL);"
    "CREATE TABLE file_operations(workspace TEXT NOT NULL,operation BLOB NOT NULL CHECK(length(operation)=16),"
    "principal BLOB NOT NULL CHECK(length(principal)=64),worktree TEXT NOT NULL,device INTEGER NOT NULL,inode INTEGER NOT NULL,"
    "exact_right INTEGER NOT NULL,digest BLOB NOT NULL CHECK(length(digest)=32),engine BLOB NOT NULL CHECK(length(engine)=16),"
    "state INTEGER NOT NULL CHECK(state BETWEEN 1 AND 3),outcome BLOB NOT NULL,PRIMARY KEY(workspace,operation));"
    "INSERT INTO file_roots(worktree,device,inode) VALUES('tree-one',7,19);PRAGMA user_version=1;");
  sqlite3_close(db);
  check(chmod(database.c_str(),0600)==0,"could not protect v1 fixture database");
  return state;
}
FileOperationJournal::CopyJobRecord record(std::string job_id=std::string(16,'j')) {
  FileOperationJournal::CopyJobRecord value;
  value.set_format_version(2); value.set_workspace_id("workspace-one"); value.set_job_id(std::move(job_id));
  value.set_initiating_operation_id(value.job_id()); value.set_initiating_request_sha256(std::string(32,'d'));
  value.set_principal_issuer_id(std::string(32,'i')); value.set_principal_actor_id(std::string(32,'a'));
  value.set_worktree_id("tree-one"); value.set_worktree_root_device(7); value.set_worktree_root_inode(19);
  value.set_exact_right(spatial::authority::v1::FILE_COPY);
  value.add_source_path_components("directory"); value.add_source_path_components("source.bin");
  auto* source=value.mutable_source(); source->set_device(7); source->set_inode(30);
  source->set_generation(4); source->set_type(FileCopyFilesystemIdentity::REGULAR_FILE);
  value.add_destination_directory_components("directory");
  auto* destination=value.mutable_destination_directory(); destination->set_device(7);
  destination->set_inode(20); destination->set_generation(3); destination->set_type(FileCopyFilesystemIdentity::DIRECTORY);
  value.set_destination_basename("copy.bin"); value.set_staging_name(".spatial-copy-stage-job");
  auto* staging=value.mutable_staging(); staging->set_device(7); staging->set_inode(40);
  staging->set_generation(5); staging->set_type(FileCopyFilesystemIdentity::DIRECTORY);
  value.set_maximum_bytes(4096); value.set_maximum_entries(8);
  auto* stage_root=value.mutable_staging_root();stage_root->set_device(7);stage_root->set_inode(39);
  stage_root->set_type(FileCopyFilesystemIdentity::DIRECTORY);
  value.set_staging_subvolume_id(0);value.set_destination_subvolume_id(0);
  auto* parent=value.mutable_process_scope_parent();parent->set_launch_id(std::string(32,'p'));
  parent->set_scope_name("spatial-git-workers");parent->set_boot_id("12345678-1234-1234-1234-123456789012");
  parent->set_device(2);parent->set_inode(3);
  value.set_broker_root_device(2);value.set_broker_root_inode(3);
  value.set_broker_root_boot_id(parent->boot_id());value.set_reserved_process_scope_name("copy-"+value.job_id());
  auto* process=value.mutable_process_scope(); process->set_launch_id(std::string(32,'l'));
  process->set_scope_name("scope-job"); process->set_boot_id("boot-id");
  process->set_device(7); process->set_inode(50);
  value.set_state(FILE_COPY_JOB_JOURNAL_QUEUED);
  value.set_created_at_unix_ms(10); value.set_updated_at_unix_ms(10);
  return value;
}
FileOperationJournal::CopyJobRecord legacy_v1_record(std::string job_id=std::string(16,'v')) {
  auto value=record(std::move(job_id)); value.set_format_version(1);
  value.mutable_process_scope()->set_launch_id(std::string(16,'l'));
  value.mutable_process_scope_parent()->set_launch_id(std::string(16,'p'));
  return value;
}
void terminal(FileOperationJournal::CopyJobRecord& value,
              FileCopyJobJournalState state,FileCopyJobReceipt::State receipt_state,
              std::uint64_t tick) {
  value.set_state(state); value.set_updated_at_unix_ms(tick);
  auto* receipt=value.mutable_terminal_receipt(); receipt->set_job_id(value.job_id());
  receipt->set_state(receipt_state);
  receipt->set_bytes_copied(value.bytes_progress());
  receipt->set_entries_copied(value.entries_progress());
  if(receipt_state==FileCopyJobReceipt::COMPLETE) receipt->set_content_digest(std::string(32,'h'));
  if(receipt_state==FileCopyJobReceipt::FAILED) {
    receipt->set_failure_code(FileActionError::UNAVAILABLE);
    receipt->set_detail("copy failed before publication");
  }
}
std::string read_copy_job_bytes(const std::filesystem::path& state,const std::string& workspace,const std::string& job_id) {
  sqlite3* db=nullptr;const auto database=state/"file-operations"/"file-operations.db";
  check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"could not open legacy copy bytes");
  sqlite3_stmt* q=nullptr;
  check(sqlite3_prepare_v2(db,"SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?",-1,&q,nullptr)==SQLITE_OK,
        "could not prepare legacy copy bytes");
  sqlite3_bind_text(q,1,workspace.data(),static_cast<int>(workspace.size()),SQLITE_TRANSIENT);
  sqlite3_bind_blob(q,2,job_id.data(),static_cast<int>(job_id.size()),SQLITE_TRANSIENT);
  check(sqlite3_step(q)==SQLITE_ROW,"legacy copy row disappeared");
  const auto* data=static_cast<const char*>(sqlite3_column_blob(q,0));
  const int size=sqlite3_column_bytes(q,0);
  const std::string result(data,static_cast<std::size_t>(size));
  sqlite3_finalize(q);sqlite3_close(db);return result;
}
void seed_legacy_terminal(const std::filesystem::path& state,const FileOperationJournal::CopyJobRecord& job) {
  sqlite3* db=nullptr;
  const auto database=state/"file-operations"/"file-operations.db";
  check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"could not open legacy copy fixture");
  sqlite3_stmt* q=nullptr;
  check(sqlite3_prepare_v2(db,"INSERT INTO file_copy_jobs(workspace,job_id,initiating_operation,state,updated_at,record) VALUES(?,?,?,?,?,?)",-1,&q,nullptr)==SQLITE_OK,
        "could not prepare legacy copy fixture");
  const auto bytes=job.SerializeAsString();
  sqlite3_bind_text(q,1,job.workspace_id().data(),static_cast<int>(job.workspace_id().size()),SQLITE_TRANSIENT);
  sqlite3_bind_blob(q,2,job.job_id().data(),static_cast<int>(job.job_id().size()),SQLITE_TRANSIENT);
  sqlite3_bind_blob(q,3,job.initiating_operation_id().data(),static_cast<int>(job.initiating_operation_id().size()),SQLITE_TRANSIENT);
  sqlite3_bind_int(q,4,job.state()); sqlite3_bind_int64(q,5,static_cast<sqlite3_int64>(job.updated_at_unix_ms()));
  sqlite3_bind_blob(q,6,bytes.data(),static_cast<int>(bytes.size()),SQLITE_TRANSIENT);
  check(sqlite3_step(q)==SQLITE_DONE,"could not seed legacy terminal copy row");
  sqlite3_finalize(q); sqlite3_close(db);
}
}
namespace {
FileOperationBinding binding(std::string operation="operation-id-001") {
  FileOperationBinding value;
  value.workspace="workspace-one"; value.operation_id=std::move(operation);
  value.principal=std::string(64,'p'); value.worktree="tree-one";
  value.root_device=7; value.root_inode=19; value.right=spatial::authority::v1::FILE_CREATE;
  value.request_sha256=std::string(32,'d'); value.engine_incarnation=std::string(16,'e');
  return value;
}
FileActionReply outcome(const std::string& operation,FileActionError::Code code) {
  FileActionReply reply; reply.set_protocol_version(1); reply.set_operation_id(operation);
  reply.mutable_error()->set_code(code); reply.mutable_error()->set_recovery_operation_id(operation);
  return reply;
}
}
int verify_legacy_file_operations() {
  try {
    std::array<char,64> temporary{};
    const std::string pattern=(std::filesystem::temp_directory_path()/"spatial-file-journal-XXXXXX").string();
    if(pattern.size()+1>temporary.size()) throw std::runtime_error("temporary state path too long");
    std::copy(pattern.begin(),pattern.end(),temporary.begin());
    char* created=mkdtemp(temporary.data());
    if(!created) throw std::runtime_error("could not create journal state directory");
    auto path=std::filesystem::path(created);
    struct stat owned{};
    if(lstat(path.c_str(),&owned)!=0 || !S_ISDIR(owned.st_mode) || S_ISLNK(owned.st_mode) || path.filename().string().rfind("spatial-file-journal-",0)!=0)
      throw std::runtime_error("unsafe journal fixture directory");
    const FileOperationJournal::Observer observer=[]{};
    auto request=binding();
    {
      FileOperationJournal journal(path);
      check(journal.bind_root(request.worktree,request.root_device,request.root_inode,observer),"initial root pin failed");
      check(!journal.bind_root(request.worktree,request.root_device,request.root_inode+1,observer),"root rebinding was accepted");
      check(journal.find(request,observer).state==FileOperationRecord::State::absent,"new operation was not absent");
      journal.begin(request,observer);
    }
    {
      FileOperationJournal recovered(path);
      check(recovered.find(request,observer).state==FileOperationRecord::State::intent,"unresolved intent did not survive reopen");
      auto pending=outcome(request.operation_id,FileActionError::UNCERTAIN);
      recovered.finish(request,FileOperationRecord::State::uncertain,pending,observer);
      check(recovered.find(request,observer).state==FileOperationRecord::State::uncertain,"uncertain outcome did not persist");
      auto mismatch=request; mismatch.request_sha256=std::string(32,'x');
      check(recovered.find(mismatch,observer).state==FileOperationRecord::State::conflict,"changed request did not conflict");
      mismatch=request; mismatch.principal.back()='x';
      check(recovered.find(mismatch,observer).state==FileOperationRecord::State::conflict,"changed principal did not conflict");
    }
    auto terminal=binding("operation-id-002");
    {
      FileOperationJournal journal(path); journal.begin(terminal,observer);
      journal.finish(terminal,FileOperationRecord::State::complete,
                     outcome(terminal.operation_id,FileActionError::UNIMPLEMENTED),observer);
    }
    {
      FileOperationJournal recovered(path);
      auto prior=recovered.find(terminal,observer);
      check(prior.state==FileOperationRecord::State::complete && prior.reply.error().code()==FileActionError::UNIMPLEMENTED,
            "terminal historical outcome did not survive reopen");
    }
    std::filesystem::remove_all(path);
    return 0;
  } catch(const std::exception& error) {
    std::cerr<<error.what()<<'\n'; return 1;
  }
}

int main() {
    {
      auto state=make_v1_state();
      FileOperationJournal journal(state);
      auto current=record(std::string(16,'c'));
      auto wrong=current; wrong.set_format_version(1);
      rejects([&]{journal.begin_copy_job(wrong,{});},"new copy job accepted v1 with a current 32-byte identity");
      wrong=current; wrong.mutable_process_scope()->set_launch_id(std::string(16,'x'));
      rejects([&]{journal.begin_copy_job(wrong,{});},"new copy job accepted v2 with a legacy 16-byte identity");
      wrong=current; wrong.mutable_process_scope_parent()->set_launch_id(std::string(16,'p'));
      rejects([&]{journal.begin_copy_job(wrong,{});},"new v2 copy job accepted a legacy parent launch identity");
      wrong=current; wrong.mutable_process_scope_parent()->set_launch_id(std::string(33,'p'));
      rejects([&]{journal.begin_copy_job(wrong,{});},"new v2 copy job accepted an unsupported parent launch length");
      wrong=current; wrong.set_format_version(3);
      rejects([&]{journal.begin_copy_job(wrong,{});},"new copy job accepted an unsupported private format version");
      journal.begin_copy_job(current,{});
      auto reopened=journal.find_copy_job(current.workspace_id(),current.job_id(),{});
      check(reopened && reopened->process_scope().launch_id()==std::string(32,'l'),
            "current 32-byte ProcessScope identity did not survive journal reopen");
      std::filesystem::remove_all(state);
    }

  try {
    check(verify_legacy_file_operations()==0,"legacy file-operation journal regression failed");
    {
      const auto generations=make_v1_state();
      auto uncertain=record();
      const auto boot=uncertain.broker_root_boot_id();
      {
        FileOperationJournal journal(generations);
        (void)journal.reserve_git_worker_parent(2,3,"/sys/fs/cgroup/old-root",boot,{});
        journal.bind_git_worker_parent(2,3,"/sys/fs/cgroup/old-root",boot,
            uncertain.process_scope_parent().SerializeAsString(),{});
        journal.begin_copy_job(uncertain,{});
        terminal(uncertain,FILE_COPY_JOB_JOURNAL_UNCERTAIN,FileCopyJobReceipt::UNCERTAIN,11);
        journal.update_copy_job(uncertain,{});
        auto parent=journal.find_git_worker_parent({});
        auto stale=parent;stale.root_inode++;
        rejects([&]{journal.retain_git_worker_parent(stale,{});},"stale parent retirement succeeded");
        check(journal.find_git_worker_parent({}).identity_bytes==parent.identity_bytes &&
              journal.retained_git_worker_parents({}).empty(),"failed retirement was not atomic");
        journal.retain_git_worker_parent(parent,{});
      }
      for(int generation=0;generation<3;++generation) {
        FileOperationJournal reopened(generations);
        const auto retained=reopened.retained_git_worker_parents({});
        check(retained.size()==1 && retained.front().root_inode==3 &&
              retained.front().identity_bytes==uncertain.process_scope_parent().SerializeAsString(),
              "historical parent binding changed across generation restarts");
        check(reopened.find_copy_job(uncertain.workspace_id(),uncertain.job_id(),{})->SerializeAsString()==
              uncertain.SerializeAsString(),"uncertainty receipt changed across generation restarts");
        const auto active=reopened.reserve_git_worker_parent(2,100+generation,"/sys/fs/cgroup/new-root",boot,{});
        check(active.state==GitWorkerParentRecord::State::reserved,"historical uncertainty blocked a fresh generation");
        auto identity=uncertain.process_scope_parent();identity.set_inode(200+generation);
        reopened.bind_git_worker_parent(2,100+generation,"/sys/fs/cgroup/new-root",boot,identity.SerializeAsString(),{});
        const auto bound=reopened.find_git_worker_parent({});
        check(bound.identity_bytes==identity.SerializeAsString(),"new worker generation did not bind");
        rejects([&]{reopened.update_copy_job(record(),{});},"historical uncertainty became mutable");
        reopened.clear_git_worker_parent(bound,{});
      }
      std::filesystem::remove_all(generations);
    }
    {
      {
      const auto proof_state=make_v1_state();
      FileOperationJournal journal(proof_state);
      auto job=record();
      auto premature=job;*premature.mutable_drained_process_scope()=job.process_scope();
      rejects([&]{journal.begin_copy_job(premature,{});},"job began with a preexisting drain proof");
      journal.begin_copy_job(job,{});job.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
      job.set_updated_at_unix_ms(11);journal.update_copy_job(job,{});
      auto wrong=job;*wrong.mutable_drained_process_scope()=job.process_scope();
      wrong.mutable_drained_process_scope()->set_inode(job.process_scope().inode()+1);
      rejects([&]{journal.update_copy_job(wrong,{});},"drain proof accepted a different scope inode");
      job.set_updated_at_unix_ms(4102444800000ULL);
      *job.mutable_drained_process_scope()=job.process_scope();journal.update_copy_job(job,{});
      journal.request_copy_job_cancel(job,{});
      check(job.updated_at_unix_ms()==4102444800000ULL,
            "cancel acknowledgment timestamp moved backwards after clock rollback");
      auto erased=job;erased.clear_drained_process_scope();
      rejects([&]{journal.update_copy_job(erased,{});},"durable drain proof was removed");
      auto changed=job;changed.mutable_drained_process_scope()->set_launch_id(std::string(32,'x'));
      rejects([&]{journal.update_copy_job(changed,{});},"durable drain proof identity changed");
      FileOperationJournal reopened(proof_state);
      check(reopened.find_copy_job(job.workspace_id(),job.job_id(),{})->drained_process_scope().SerializeAsString()==
            job.process_scope().SerializeAsString(),"exact drain proof did not survive restart");
      std::filesystem::remove_all(proof_state);
    }
    const auto state=make_v1_state();
      FileOperationJournal journal(state);
      for(int cancel_requested=0;cancel_requested<2;++cancel_requested) {
        auto job=record(std::string(16,cancel_requested?'b':'a'));
        journal.begin_copy_job(job,{});
        job.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
        auto* prepared=job.mutable_prepared_result();
        prepared->set_staging_output_name(".spatial-copy-output-prepared");
        *prepared->mutable_output()=job.source();prepared->set_bytes_copied(1);
        prepared->set_entries_copied(1);prepared->set_content_sha256(std::string(32,'d'));
        job.set_updated_at_unix_ms(20);journal.update_copy_job(job,{});
        if(cancel_requested) journal.request_copy_job_cancel(job,{});
        auto recovered=*journal.find_copy_job(job.workspace_id(),job.job_id(),{});
        const auto identity=recovered.prepared_result().SerializeAsString();
        journal.request_copy_job_cancel(recovered,{});
        terminal(recovered,FILE_COPY_JOB_JOURNAL_CANCELLED,FileCopyJobReceipt::CANCELLED,
                 recovered.updated_at_unix_ms());
        journal.update_copy_job(recovered,{});
        check(journal.find_copy_job(job.workspace_id(),job.job_id(),{})->prepared_result().SerializeAsString()==identity,
              "prepared cancellation discarded immutable output identity");
      }
      std::filesystem::remove_all(state);
    }
    {
      auto state=make_v1_state();
      { FileOperationJournal schema(state); }
      auto legacy=legacy_v1_record(std::string(16,'v'));
      terminal(legacy,FILE_COPY_JOB_JOURNAL_FAILED,FileCopyJobReceipt::FAILED,20);
      const auto original_receipt=legacy.terminal_receipt().SerializeAsString();
      const auto original_record=legacy.SerializeAsString();
      seed_legacy_terminal(state,legacy);
      check(read_copy_job_bytes(state,legacy.workspace_id(),legacy.job_id())==original_record,
            "legacy terminal seed bytes differ before recovery");
      {
        FileOperationJournal recovered(state);
        auto loaded=recovered.find_copy_job(legacy.workspace_id(),legacy.job_id(),{});
        check(loaded && loaded->format_version()==1 && loaded->process_scope().launch_id().size()==16,
              "legacy v1 ProcessScope record did not reopen");
        check(loaded->terminal_receipt().SerializeAsString()==original_receipt,
              "legacy terminal receipt bytes changed during recovery");
        check(recovered.recovery_copy_jobs(legacy.workspace_id(),{}).empty() &&
              recovered.recovery_copy_jobs_all({}).empty(),
              "terminal legacy record leaked into active recovery results");
      }
      check(read_copy_job_bytes(state,legacy.workspace_id(),legacy.job_id())==original_record,
            "legacy terminal journal bytes changed after reopen/recovery scan");
      std::filesystem::remove_all(state);
    }
    const auto state=make_v1_state();
    const FileOperationJournal::Observer observer=[]{};
    auto job=record();
    {
      FileOperationJournal journal(state);
      sqlite3* version_db=nullptr;
      const auto version_path=state/"file-operations"/"file-operations.db";
      check(sqlite3_open(version_path.c_str(),&version_db)==SQLITE_OK,"could not inspect migrated database");
      sqlite3_stmt* version_statement=nullptr;
      check(sqlite3_prepare_v2(version_db,"PRAGMA user_version",-1,&version_statement,nullptr)==SQLITE_OK &&
            sqlite3_step(version_statement)==SQLITE_ROW && sqlite3_column_int(version_statement,0)==7,
            "journal did not migrate schema version 1 to 7");
      sqlite3_finalize(version_statement); sqlite3_close(version_db);
      check(journal.bind_root("tree-one",7,19,observer),"v1 root pin did not survive migration");
      check(!journal.bind_root("tree-one",7,20,observer),"v1 migration changed root identity");
      check(journal.bind_root("tree-one",7,19,observer),"migrated journal root could not reopen");
      constexpr auto boot="12345678-1234-1234-1234-123456789012";
      auto reserved=journal.reserve_git_worker_parent(2,3,"/sys/fs/cgroup/mock-root",boot,observer);
      check(reserved.state==GitWorkerParentRecord::State::reserved &&
            reserved.root_device==2 && reserved.root_inode==3 && reserved.root_path=="/sys/fs/cgroup/mock-root" && reserved.boot_id==boot,
            "Git parent reservation was not durable");
      rejects([&]{(void)journal.reserve_git_worker_parent(2,4,"/sys/fs/cgroup/mock-root",boot,observer);},
              "Git parent reservation accepted a changed root identity");
      rejects([&]{(void)journal.reserve_git_worker_parent(2,3,"/sys/fs/cgroup/other-root",boot,observer);},
              "Git parent reservation accepted a changed canonical root path");
      {
        FileOperationJournal reopened(state);
        const auto pending=reopened.find_git_worker_parent(observer);
        check(pending.state==GitWorkerParentRecord::State::reserved && pending.identity_bytes.empty(),
              "reserved parent intent did not survive a restart before identity bind");
      }
      supervisor::v1::ProcessScopeIdentity parent_identity;
      parent_identity.set_scope_name("spatial-git-workers");parent_identity.set_boot_id(boot);
      parent_identity.set_launch_id(std::string(32,'p'));parent_identity.set_device(9);parent_identity.set_inode(11);
      journal.bind_git_worker_parent(2,3,"/sys/fs/cgroup/mock-root",boot,parent_identity.SerializeAsString(),observer);
      auto bound=journal.find_git_worker_parent(observer);
      check(bound.state==GitWorkerParentRecord::State::bound &&
            bound.identity_bytes==parent_identity.SerializeAsString() &&
            bound.root_path=="/sys/fs/cgroup/mock-root",
            "Git parent identity did not survive durable bind");
      {
        FileOperationJournal reopened(state);
        auto durable=reopened.find_git_worker_parent(observer);
        check(durable.state==GitWorkerParentRecord::State::bound &&
              durable.identity_bytes==parent_identity.SerializeAsString() &&
              durable.root_path=="/sys/fs/cgroup/mock-root",
              "Git parent binding did not survive journal reopen");
        check(reopened.find_git_worker_parent(observer).state==GitWorkerParentRecord::State::bound,
              "Git parent binding changed during a restart with active work");
      }
      auto oversized=record(std::string(16,'z'));
      oversized.clear_source_path_components();
      for(int i=0;i<40;++i) oversized.add_source_path_components(std::string(255,'x'));
      oversized.add_source_path_components("source.bin");
      rejects([&]{journal.begin_copy_job(oversized,observer);},"oversized copy job path was accepted");
      auto wrong_device=job; wrong_device.set_job_id(std::string(16,'d'));
      wrong_device.set_initiating_operation_id(std::string(16,'d'));
      wrong_device.mutable_staging()->set_device(8);
      rejects([&]{journal.begin_copy_job(wrong_device,observer);},
              "staging on a different device from the destination was accepted");
      journal.begin_copy_job(job,observer);
      auto other_workspace=job;
      other_workspace.set_workspace_id("workspace-two");
      other_workspace.set_worktree_id("tree-two");
      other_workspace.set_job_id(std::string(16,'w'));
      other_workspace.set_initiating_operation_id(std::string(16,'w'));
      check(journal.bind_root("tree-two",7,19,observer),"second workspace root did not bind");
      journal.begin_copy_job(other_workspace,observer);
      {
        FileOperationJournal reopened(state);
        const auto lifecycle=reopened.find_git_worker_parent(observer);
        const auto active=reopened.recovery_copy_jobs_all(observer);
        check(lifecycle.state==GitWorkerParentRecord::State::bound && active.size()==2,
              "bound worker parent and active jobs did not survive a restart together");
      }
      const auto all_recovery=journal.recovery_copy_jobs_all(observer);
      check(all_recovery.size()==2,"global copy recovery omitted a workspace job");
      auto found=journal.find_copy_job(job.workspace_id(),job.job_id(),observer);
      auto by_operation=journal.find_copy_job_by_operation(
          job.workspace_id(),job.initiating_operation_id(),observer);
      check(by_operation && by_operation->job_id()==job.job_id(),
            "initiating operation lookup did not find persisted job");
      rejects([&]{journal.begin_copy_job(job,observer);},
              "duplicate initiating operation was accepted");
      check(found && found->SerializeAsString()==job.SerializeAsString(),"copy job did not round-trip");
      auto recovery=journal.recovery_copy_jobs(job.workspace_id(),observer);
      check(recovery.size()==1 && recovery[0].job_id()==job.job_id(),"queued job missing from recovery query");
      auto running=job; running.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
      running.set_bytes_progress(40); running.set_entries_progress(2); running.set_updated_at_unix_ms(11);
      journal.update_copy_job(running,observer);
      auto skipped_cancel=running;
      terminal(skipped_cancel,FILE_COPY_JOB_JOURNAL_CANCELLED,FileCopyJobReceipt::CANCELLED,12);
      rejects([&]{journal.update_copy_job(skipped_cancel,observer);},
              "running copy recovery skipped its durable cancel-requested state");
      auto canceling=running;
      journal.request_copy_job_cancel(canceling,observer);
      check(canceling.state()==FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED &&
            journal.find_copy_job(canceling.workspace_id(),canceling.job_id(),observer)->state()==
                FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED,
            "production cancellation helper did not durably journal intent before stop");
      auto cancelled=canceling;
      terminal(cancelled,FILE_COPY_JOB_JOURNAL_CANCELLED,FileCopyJobReceipt::CANCELLED,
          canceling.updated_at_unix_ms()+1);
      journal.update_copy_job(cancelled,observer);
      check(journal.recovery_copy_jobs(job.workspace_id(),observer).empty(),
            "cancelled job remained in recovery query");
      auto cancelled_by_operation=journal.find_copy_job_by_operation(
          cancelled.workspace_id(),cancelled.initiating_operation_id(),observer);
      check(cancelled_by_operation &&
            cancelled_by_operation->state()==FILE_COPY_JOB_JOURNAL_CANCELLED,
            "initiating operation lookup omitted terminal job");
      const auto prune_time=cancelled.updated_at_unix_ms()+1;
      auto changed=cancelled; changed.set_updated_at_unix_ms(prune_time);
      rejects([&]{journal.update_copy_job(changed,observer);},"terminal job mutation was accepted");
      check(journal.prune_terminal_copy_jobs(job.workspace_id(),prune_time,1,observer)==1,
            "eligible terminal job was not pruned");
      check(!journal.find_copy_job(job.workspace_id(),job.job_id(),observer),
            "pruned terminal job remains visible");
      auto other_running=other_workspace;
      other_running.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
      other_running.set_updated_at_unix_ms(15);
      journal.update_copy_job(other_running,observer);
      auto other_cancelling=other_running;
      other_cancelling.set_state(FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED);
      other_cancelling.set_updated_at_unix_ms(16);
      journal.update_copy_job(other_cancelling,observer);
      auto other_cancelled=other_cancelling;
      terminal(other_cancelled,FILE_COPY_JOB_JOURNAL_CANCELLED,FileCopyJobReceipt::CANCELLED,17);
      journal.update_copy_job(other_cancelled,observer);
      const auto parent_after_jobs=journal.find_git_worker_parent(observer);
      journal.clear_git_worker_parent(parent_after_jobs,observer);
      check(journal.find_git_worker_parent(observer).state==GitWorkerParentRecord::State::absent,
            "verified empty parent could not clear after all job rows became terminal");
    }
    {
      auto prepared=record(std::string(16,'i'));
      auto intent=prepared;
      intent.clear_staging(); intent.clear_process_scope();
      intent.set_state(FILE_COPY_JOB_JOURNAL_START_INTENT);
      auto partial_stage=intent; *partial_stage.mutable_staging()=prepared.staging();
      auto partial_scope=intent; *partial_scope.mutable_process_scope()=prepared.process_scope();
      {
        FileOperationJournal journal(state);
        journal.begin_copy_job(intent,observer);
        auto found=journal.find_copy_job(intent.workspace_id(),intent.job_id(),observer);
        auto recovery=journal.recovery_copy_jobs(intent.workspace_id(),observer);
        check(found && found->state()==FILE_COPY_JOB_JOURNAL_START_INTENT &&
              !found->has_staging() && !found->has_process_scope(),
              "provisional intent did not persist without created identities");
        check(recovery.size()==1 && recovery[0].job_id()==intent.job_id(),
              "provisional intent was omitted from recovery enumeration");
        rejects([&]{journal.begin_copy_job(partial_stage,observer);},
                "provisional intent accepted a staging identity before creation");
        rejects([&]{journal.begin_copy_job(partial_scope,observer);},
                "provisional intent accepted a ProcessScope identity before preparation");
      }
      {
        FileOperationJournal reopened(state);
        auto durable=reopened.find_copy_job(intent.workspace_id(),intent.job_id(),observer);
        check(durable && durable->state()==FILE_COPY_JOB_JOURNAL_START_INTENT,
              "provisional intent did not survive journal reopen");
        auto queued=intent;
        *queued.mutable_staging()=prepared.staging();
        *queued.mutable_process_scope()=prepared.process_scope();
        queued.set_state(FILE_COPY_JOB_JOURNAL_QUEUED);
        queued.set_updated_at_unix_ms(11);
        reopened.update_copy_job(queued,observer);
        auto bound=reopened.find_copy_job(intent.workspace_id(),intent.job_id(),observer);
        check(bound && bound->state()==FILE_COPY_JOB_JOURNAL_QUEUED &&
              bound->has_staging() && bound->has_process_scope(),
              "provisional intent did not bind both created identities");
        auto rebound=queued; rebound.mutable_staging()->set_inode(41);
        rebound.set_updated_at_unix_ms(12);
        rejects([&]{reopened.update_copy_job(rebound,observer);},
                "queued job allowed a second staging identity binding");
        auto changed=queued; changed.mutable_process_scope()->set_inode(51);
        changed.set_updated_at_unix_ms(12);
        rejects([&]{reopened.update_copy_job(changed,observer);},
                "queued job allowed a second ProcessScope identity binding");
      }
    }
    {
      FileCopyJobJournalRecord failed;
      failed=record(std::string(16,'f'));
      {
        FileOperationJournal journal(state);
        journal.begin_copy_job(failed,observer);
        failed.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
        failed.set_updated_at_unix_ms(11);
        journal.update_copy_job(failed,observer);
        terminal(failed,FILE_COPY_JOB_JOURNAL_FAILED,FileCopyJobReceipt::FAILED,12);
        journal.update_copy_job(failed,observer);
      }
      FileOperationJournal reopened(state);
      auto by_job=reopened.find_copy_job(failed.workspace_id(),failed.job_id(),observer);
      auto by_operation=reopened.find_copy_job_by_operation(
          failed.workspace_id(),failed.initiating_operation_id(),observer);
      check(by_job && by_operation &&
            by_job->state()==FILE_COPY_JOB_JOURNAL_FAILED &&
            by_operation->terminal_receipt().failure_code()==FileActionError::UNAVAILABLE,
            "failed receipt did not persist across journal reopen");
      check(reopened.prune_terminal_copy_jobs(failed.workspace_id(),100,1,observer)==1,
            "eligible failed job was not retained under terminal policy");
    }
    {
      FileOperationJournal recovered(state);
      auto progress=record(std::string(16,'p'));
      recovered.begin_copy_job(progress,observer);
      progress.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
      progress.set_bytes_progress(100); progress.set_entries_progress(3); progress.set_updated_at_unix_ms(11);
      recovered.update_copy_job(progress,observer);
      auto regressed=progress; regressed.set_bytes_progress(99); regressed.set_updated_at_unix_ms(12);
      rejects([&]{recovered.update_copy_job(regressed,observer);},"regressing progress was accepted");
      auto changed=progress; changed.set_destination_basename("other.bin"); changed.set_updated_at_unix_ms(12);
      rejects([&]{recovered.update_copy_job(changed,observer);},"changed destination identity was accepted");
      auto canceling=progress; canceling.set_state(FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED);
      canceling.set_updated_at_unix_ms(12); recovered.update_copy_job(canceling,observer);
      auto uncertain=canceling;
      terminal(uncertain,FILE_COPY_JOB_JOURNAL_UNCERTAIN,FileCopyJobReceipt::UNCERTAIN,13);
      recovered.update_copy_job(uncertain,observer);
      const auto recovery=recovered.recovery_copy_jobs(progress.workspace_id(),observer);
      bool found_uncertain=false;
      for(const auto& job:recovery)
        found_uncertain=found_uncertain ||
            (job.job_id()==progress.job_id() && job.state()==FILE_COPY_JOB_JOURNAL_UNCERTAIN);
      check(found_uncertain,"uncertain job was omitted from recovery query");
      auto rewritten_uncertain=uncertain;
      rewritten_uncertain.set_updated_at_unix_ms(14);
      rejects([&]{recovered.update_copy_job(rewritten_uncertain,observer);},
              "terminal uncertain copy receipt was mutable during recovery");
      check(recovered.prune_terminal_copy_jobs(progress.workspace_id(),100,10,observer)==0,
            "uncertain job was incorrectly pruned");
      auto complete=record(std::string(16,'c'));
      recovered.begin_copy_job(complete,observer);
      complete.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
      complete.set_bytes_progress(60); complete.set_entries_progress(2);
      complete.set_updated_at_unix_ms(11); recovered.update_copy_job(complete,observer);
      auto prepared=complete.mutable_prepared_result();
      prepared->set_staging_output_name(".spatial-copy-output-c");
      auto* output=prepared->mutable_output(); output->set_device(7); output->set_inode(60);
      output->set_generation(7); output->set_type(FileCopyFilesystemIdentity::REGULAR_FILE);
      prepared->set_bytes_copied(60); prepared->set_entries_copied(2);
      prepared->set_content_sha256(std::string(32,'h'));
      auto wrong_type=complete;
      wrong_type.mutable_prepared_result()->mutable_output()->set_type(FileCopyFilesystemIdentity::DIRECTORY);
      rejects([&]{recovered.update_copy_job(wrong_type,observer);},
              "prepared output type differing from the source was accepted");
      auto wrong_device=complete;
      wrong_device.mutable_prepared_result()->mutable_output()->set_device(8);
      rejects([&]{recovered.update_copy_job(wrong_device,observer);},
              "prepared output device differing from staging was accepted");
      complete.set_updated_at_unix_ms(12); recovered.update_copy_job(complete,observer);
      auto failed_after_prepare=complete;
      terminal(failed_after_prepare,FILE_COPY_JOB_JOURNAL_FAILED,FileCopyJobReceipt::FAILED,13);
      rejects([&]{recovered.update_copy_job(failed_after_prepare,observer);},
              "prepared job transitioned to failed after publication preparation");
      auto altered=complete; altered.mutable_prepared_result()->set_content_sha256(std::string(32,'x'));
      altered.set_updated_at_unix_ms(13);
      rejects([&]{recovered.update_copy_job(altered,observer);},"prepared result mutation was accepted");
      terminal(complete,FILE_COPY_JOB_JOURNAL_COMPLETE,FileCopyJobReceipt::COMPLETE,13);
      recovered.update_copy_job(complete,observer);
      auto complete_record=recovered.find_copy_job(complete.workspace_id(),complete.job_id(),observer);
      check(complete_record && complete_record->prepared_result().output().inode()==60 &&
            complete_record->terminal_receipt().content_digest()==std::string(32,'h'),
            "prepared publication identity did not survive journal load");
      auto complete_by_operation=recovered.find_copy_job_by_operation(
          complete.workspace_id(),complete.initiating_operation_id(),observer);
      check(complete_by_operation &&
            complete_by_operation->terminal_receipt().state()==FileCopyJobReceipt::COMPLETE,
            "initiating operation lookup did not recover terminal receipt");
      auto invalid_failure=record(std::string(16,'v'));
      recovered.begin_copy_job(invalid_failure,observer);
      invalid_failure.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
      invalid_failure.set_updated_at_unix_ms(11);
      recovered.update_copy_job(invalid_failure,observer);
      terminal(invalid_failure,FILE_COPY_JOB_JOURNAL_FAILED,FileCopyJobReceipt::FAILED,12);
      invalid_failure.mutable_terminal_receipt()->set_failure_code(
          static_cast<FileActionError::Code>(99));
      rejects([&]{recovered.update_copy_job(invalid_failure,observer);},
              "unknown failed-copy error code was accepted");
      auto missing_failure=record(std::string(16,'n'));
      recovered.begin_copy_job(missing_failure,observer);
      missing_failure.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
      missing_failure.set_updated_at_unix_ms(11);
      recovered.update_copy_job(missing_failure,observer);
      terminal(missing_failure,FILE_COPY_JOB_JOURNAL_FAILED,FileCopyJobReceipt::FAILED,12);
      missing_failure.mutable_terminal_receipt()->set_failure_code(FileActionError::CODE_UNSPECIFIED);
      rejects([&]{recovered.update_copy_job(missing_failure,observer);},
              "failed receipt without a known code was accepted");
      const auto reject_failure_code=[&](char id,FileActionError::Code code) {
        auto rejected=record(std::string(16,id));
        recovered.begin_copy_job(rejected,observer);
        rejected.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
        rejected.set_updated_at_unix_ms(11);
        recovered.update_copy_job(rejected,observer);
        terminal(rejected,FILE_COPY_JOB_JOURNAL_FAILED,FileCopyJobReceipt::FAILED,12);
        rejected.mutable_terminal_receipt()->set_failure_code(code);
        rejects([&]{recovered.update_copy_job(rejected,observer);},
                "conflict, uncertain, or unauthorized failure code was accepted");
      };
      reject_failure_code('1',FileActionError::CONFLICT);
      reject_failure_code('2',FileActionError::UNCERTAIN);
      reject_failure_code('3',FileActionError::UNAUTHORIZED);
      auto cancelling=record(std::string(16,'q'));
      recovered.begin_copy_job(cancelling,observer);
      cancelling.set_state(FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED);
      cancelling.set_updated_at_unix_ms(11);
      recovered.update_copy_job(cancelling,observer);
      terminal(cancelling,FILE_COPY_JOB_JOURNAL_FAILED,FileCopyJobReceipt::FAILED,12);
      rejects([&]{recovered.update_copy_job(cancelling,observer);},
              "cancel-requested job transitioned to failed");
      auto malformed=record(std::string(16,'m'));
      recovered.begin_copy_job(malformed,observer);
    }
    {
      auto indexed=record(std::string(16,'k'));
      { FileOperationJournal journal(state); journal.begin_copy_job(indexed,observer); }
      sqlite3* db=nullptr;
      const auto database=state/"file-operations"/"file-operations.db";
      check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"could not open indexed-key corruption fixture");
      sqlite3_stmt* statement=nullptr;
      check(sqlite3_prepare_v2(db,"UPDATE file_copy_jobs SET job_id=? WHERE job_id=?",-1,
                               &statement,nullptr)==SQLITE_OK,"could not prepare indexed job id corruption");
      sqlite3_bind_blob(statement,1,std::string(16,'z').data(),16,SQLITE_TRANSIENT);
      sqlite3_bind_blob(statement,2,indexed.job_id().data(),16,SQLITE_TRANSIENT);
      check(sqlite3_step(statement)==SQLITE_DONE && sqlite3_changes(db)==1,
            "could not corrupt indexed job id");
      sqlite3_finalize(statement); sqlite3_close(db);
      FileOperationJournal recovered(state);
      rejects([&]{(void)recovered.find_copy_job_by_operation(
          indexed.workspace_id(),indexed.initiating_operation_id(),observer);},
          "initiating operation lookup accepted a mismatched job id index");
      rejects([&]{(void)recovered.recovery_copy_jobs(indexed.workspace_id(),observer);},
          "recovery lookup accepted a mismatched job id index");
    }
    {
      sqlite3* db=nullptr;
      const auto database=state/"file-operations"/"file-operations.db";
      check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"could not reopen journal fixture");
      sqlite3_stmt* statement=nullptr;
      check(sqlite3_prepare_v2(db,"UPDATE file_copy_jobs SET record=x'ff' WHERE job_id=?",-1,&statement,nullptr)==SQLITE_OK,
            "could not prepare malformed-row injection");
      sqlite3_bind_blob(statement,1,std::string(16,'m').data(),16,SQLITE_TRANSIENT);
      check(sqlite3_step(statement)==SQLITE_DONE,"could not insert malformed row");
      sqlite3_finalize(statement); sqlite3_close(db);
      FileOperationJournal recovered(state);
      rejects([&]{(void)recovered.find_copy_job("workspace-one",std::string(16,'m'),observer);},
              "malformed stored protobuf was accepted");
    }
    {
      sqlite3* db=nullptr;
      const auto database=state/"file-operations"/"file-operations.db";
      check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"could not open oversized-row fixture");
      exec_sql(db,"PRAGMA ignore_check_constraints=ON;");
      sqlite3_stmt* statement=nullptr;
      check(sqlite3_prepare_v2(db,"UPDATE file_copy_jobs SET record=zeroblob(16385) WHERE job_id=?",
                               -1,&statement,nullptr)==SQLITE_OK,"could not prepare oversized-row fixture");
      sqlite3_bind_blob(statement,1,std::string(16,'m').data(),16,SQLITE_TRANSIENT);
      check(sqlite3_step(statement)==SQLITE_DONE && sqlite3_changes(db)==1,
            "could not inject oversized journal row");
      sqlite3_finalize(statement); sqlite3_close(db);
      rejects([&]{FileOperationJournal oversized_recovered(state);},
              "oversized stored copy job row was accepted");
    }
    std::filesystem::remove_all(state);
    return 0;
  } catch(const std::exception& error) {
    std::cerr<<error.what()<<std::endl; return 1;
  }
}
