#include "workspace/file_operation_journal.hpp"
#include <sqlite3.h>
#include <sys/stat.h>
#include <unistd.h>
#include <algorithm>
#include <condition_variable>
#include <deque>
#include <cstdlib>
#include <atomic>
#include <future>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <type_traits>
#include <utility>
#include <chrono>
#include <limits>
#include <set>
#include <fcntl.h>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <limits.h>
#include <sys/file.h>
#include <sys/random.h>
#include <dirent.h>
#include <sys/statfs.h>
#include <sys/ioctl.h>
#include <linux/btrfs.h>
#include <openssl/evp.h>

namespace spatial {
namespace {
constexpr std::uint64_t kMaxRows = 65536;
constexpr int kMaxPages = 16384;  // 64 MiB at the fixed 4 KiB page size.
constexpr std::size_t kMaxOutcome = 8192;
constexpr std::size_t kMaxCopyJobRecord = 16 * 1024;
constexpr std::uint64_t kMaxCopyJobRows = 65536;
constexpr std::size_t kMaxCopyJobPruneBatch = 1024;
thread_local CleanupDeadline* active_cleanup_deadline = nullptr;
void check_deadline(CleanupDeadline deadline) {
  if(deadline!=kNoCleanupDeadline && std::chrono::steady_clock::now()>=deadline)
    throw std::runtime_error("Copy cleanup deadline expired");
}
void check_cleanup_deadline() {
  if(active_cleanup_deadline) check_deadline(*active_cleanup_deadline);
}
struct CleanupDeadlineContext {
  CleanupDeadline* previous;
  explicit CleanupDeadlineContext(CleanupDeadline& deadline):previous(active_cleanup_deadline){active_cleanup_deadline=&deadline;}
  ~CleanupDeadlineContext(){active_cleanup_deadline=previous;}
};
int cleanup_busy(void* context, int prior) {
  auto* deadline = static_cast<CleanupDeadline*>(context);
  if (!deadline || *deadline == kNoCleanupDeadline) return prior < 100 ? 1 : 0;
  const auto now=std::chrono::steady_clock::now();
  if(now>=*deadline) return 0;
  const auto remain=std::chrono::duration_cast<std::chrono::milliseconds>(*deadline-now).count();
  const auto delay=std::min<std::int64_t>(1,std::max<std::int64_t>(0,remain));
  if(delay) std::this_thread::sleep_for(std::chrono::milliseconds(delay));
  return std::chrono::steady_clock::now()<*deadline ? 1 : 0;
}
int cleanup_progress(void* context) {
  auto* deadline=static_cast<CleanupDeadline*>(context);
  return deadline && *deadline!=kNoCleanupDeadline && std::chrono::steady_clock::now()>=*deadline;
}
void check(int rc, sqlite3* db, const char* message) {
  (void)db;
  if (rc == SQLITE_FULL) throw std::length_error("File operation journal capacity exhausted");
  if (rc != SQLITE_OK && rc != SQLITE_ROW && rc != SQLITE_DONE)
    throw std::runtime_error(message);
}
class Statement {
 public:
  Statement(sqlite3* db, const char* sql) : db_(db) {
    check_cleanup_deadline();
    const int rc=sqlite3_prepare_v2(db,sql,-1,&value_,nullptr);
    if(rc!=SQLITE_OK){if(value_)sqlite3_finalize(value_);value_=nullptr;check(rc,db,"File journal query failed");}
    try { check_cleanup_deadline(); } catch(...) { sqlite3_finalize(value_);value_=nullptr;throw; }
  }
  ~Statement() { sqlite3_finalize(value_); }
  sqlite3_stmt* get() const { return value_; }
  void text(int at, const std::string& value) { check(sqlite3_bind_text(value_, at, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT), db_, "File journal bind failed"); }
  void blob(int at, const std::string& value) { check(sqlite3_bind_blob(value_, at, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT), db_, "File journal bind failed"); }
  void number(int at, sqlite3_int64 value) { check(sqlite3_bind_int64(value_, at, value), db_, "File journal bind failed"); }
  bool row() { check_cleanup_deadline(); const int rc = sqlite3_step(value_); check_cleanup_deadline(); check(rc, db_, "File journal read failed"); return rc == SQLITE_ROW; }
  void done() { check_cleanup_deadline(); check(sqlite3_step(value_), db_, "File journal write failed"); check_cleanup_deadline(); }
  std::string bytes(int column) const {
    const auto* p = static_cast<const char*>(sqlite3_column_blob(value_, column));
    const int n = sqlite3_column_bytes(value_, column);
    return p && n > 0 ? std::string(p, static_cast<std::size_t>(n)) : std::string{};
  }
  std::string string(int column) const {
    const auto* p = reinterpret_cast<const char*>(sqlite3_column_text(value_, column));
    const int n = sqlite3_column_bytes(value_, column);
    return p && n > 0 ? std::string(p, static_cast<std::size_t>(n)) : std::string{};
  }
 private:
  sqlite3* db_;
  sqlite3_stmt* value_ = nullptr;
};
void sql(sqlite3* db, const char* text);
class Transaction {
 public:
  explicit Transaction(sqlite3* db,const char* begin="BEGIN IMMEDIATE"):db_(db){sql(db_,begin);}
  ~Transaction(){if(!done_)sqlite3_exec(db_,"ROLLBACK",nullptr,nullptr,nullptr);}
  void commit(){sql(db_,"COMMIT");done_=true;}
 private: sqlite3* db_; bool done_=false;
};
void sql(sqlite3* db, const char* text) {
  check_cleanup_deadline();
  char* error = nullptr;
  const int rc = sqlite3_exec(db, text, nullptr, nullptr, &error);
  const std::string detail=error?error:"";
  sqlite3_free(error);
  check_cleanup_deadline();
  if(rc!=SQLITE_OK)
    throw std::runtime_error("File operation journal initialization failed: "+detail);
}
void validate_binding(const FileOperationBinding& b) {
  if (b.workspace.empty() || b.workspace.size() > 256 || b.operation_id.size() != 16 ||
      b.principal.size() != 64 || b.worktree.empty() || b.worktree.size() > 128 ||
      b.request_sha256.size() != 32 || b.engine_incarnation.size() != 16 ||
      b.root_device > INT64_MAX || b.root_inode > INT64_MAX || b.right <= 0)
    throw std::invalid_argument("Invalid file operation journal binding");
}
void bind_identity(Statement& q, const FileOperationBinding& b) {
  q.text(1, b.workspace); q.blob(2, b.operation_id); q.blob(3, b.principal);
  q.text(4, b.worktree); q.number(5, static_cast<sqlite3_int64>(b.root_device));
  q.number(6, static_cast<sqlite3_int64>(b.root_inode)); q.number(7, b.right);
  q.blob(8, b.request_sha256);
}
void validate_component(const std::string& value, const char* label) {
  if(value.empty() || value.size()>255 || value.find('\0')!=std::string::npos ||
     value.find('/')!=std::string::npos || value=="." || value=="..")
    throw std::invalid_argument(label);
}
bool has_unknown_fields(const google::protobuf::Message& message) {
  return message.GetReflection()->GetUnknownFields(message).field_count()!=0;
}
bool terminal_state(workspace::v1::FileCopyJobJournalState state) {
  using namespace workspace::v1;
  return state==FILE_COPY_JOB_JOURNAL_COMPLETE || state==FILE_COPY_JOB_JOURNAL_CANCELLED ||
         state==FILE_COPY_JOB_JOURNAL_CONFLICT || state==FILE_COPY_JOB_JOURNAL_UNCERTAIN ||
         state==FILE_COPY_JOB_JOURNAL_FAILED;
}
void validate_copy_identity(const workspace::v1::FileCopyFilesystemIdentity& id,
                            bool directory, const char* label) {
  using T=workspace::v1::FileCopyFilesystemIdentity;
  if(has_unknown_fields(id) || id.device()==0 || id.device()>INT64_MAX ||
     id.inode()==0 || id.inode()>INT64_MAX ||
     id.type()!=(directory?T::DIRECTORY:T::REGULAR_FILE))
    throw std::invalid_argument(label);
}
void validate_copy_job(const workspace::v1::FileCopyJobJournalRecord& r) {
  using namespace workspace::v1;
  if(has_unknown_fields(r) || (r.format_version()!=1 && r.format_version()!=2) || r.workspace_id().empty() ||
     r.workspace_id().size()>256 || r.job_id().size()!=16 ||
     r.initiating_operation_id().size()!=16 || r.initiating_request_sha256().size()!=32 ||
     r.principal_issuer_id().size()!=32 || r.principal_actor_id().size()!=32 ||
     r.worktree_id().empty() || r.worktree_id().size()>128 ||
     r.worktree_root_device()==0 || r.worktree_root_device()>INT64_MAX ||
     r.worktree_root_inode()==0 || r.worktree_root_inode()>INT64_MAX ||
     r.exact_right()!=spatial::authority::v1::FILE_COPY ||
     r.maximum_bytes()==0 || r.maximum_bytes()>4ULL*1024*1024*1024 ||
     r.maximum_entries()==0 || r.maximum_entries()>200000 ||
     r.created_at_unix_ms()==0 || r.created_at_unix_ms()>INT64_MAX ||
     r.updated_at_unix_ms()<r.created_at_unix_ms() || r.updated_at_unix_ms()>INT64_MAX ||
     r.broker_root_device()==0 || r.broker_root_device()>INT64_MAX ||
     r.broker_root_inode()==0 || r.broker_root_inode()>INT64_MAX ||
     r.broker_root_boot_id().size()!=36 || r.reserved_process_scope_name().empty() ||
     r.reserved_process_scope_name().size()>128 ||
     r.reserved_process_scope_name().find_first_of("/\\\\")!=std::string::npos ||
     r.reserved_process_scope_name().find('\0')!=std::string::npos)
    throw std::invalid_argument("Invalid file copy job journal record");
  validate_copy_identity(r.staging_root(),true,"Invalid broker-private copy staging root");
  if(r.staging_root().device()!=r.destination_directory().device() ||
     r.staging_subvolume_id()!=r.destination_subvolume_id())
    throw std::invalid_argument("Copy staging root and destination must share a filesystem subvolume");
  if(has_unknown_fields(r.process_scope_parent()) ||
     r.process_scope_parent().scope_name()!="spatial-git-workers" ||
     r.process_scope_parent().boot_id().size()!=36 ||
     r.process_scope_parent().boot_id()!=r.broker_root_boot_id() ||
     r.process_scope_parent().launch_id().size()!=(r.format_version()==1?16U:32U) ||
     r.process_scope_parent().device()==0 || r.process_scope_parent().inode()==0)
    throw std::invalid_argument("Invalid stable Git worker parent identity");
  if(r.source_path_components_size()==0 || r.source_path_components_size()>256 ||
     r.destination_directory_components_size()>256)
    throw std::invalid_argument("Invalid file copy job path depth");
  std::size_t path_bytes=0;
  for(const auto& part:r.source_path_components()) { validate_component(part,"Invalid source path component"); path_bytes+=part.size()+1; }
  for(const auto& part:r.destination_directory_components()) { validate_component(part,"Invalid destination path component"); path_bytes+=part.size()+1; }
  validate_component(r.destination_basename(),"Invalid destination basename");
  path_bytes+=r.destination_basename().size()+1;
  if(path_bytes>8192) throw std::invalid_argument("Copy job paths exceed their bound");
  validate_component(r.staging_name(),"Invalid staging name");
  if(r.staging_name().rfind(".spatial-copy-stage-",0)!=0)
    throw std::invalid_argument("Invalid copy staging name");
  if(r.source().type()!=FileCopyFilesystemIdentity::DIRECTORY &&
     r.source().type()!=FileCopyFilesystemIdentity::REGULAR_FILE)
    throw std::invalid_argument("Invalid copy source type");
  validate_copy_identity(r.source(),r.source().type()==FileCopyFilesystemIdentity::DIRECTORY,
                         "Invalid copy source identity");
  validate_copy_identity(r.destination_directory(),true,"Invalid copy destination identity");
  if(r.state()<FILE_COPY_JOB_JOURNAL_QUEUED ||
     r.state()>FILE_COPY_JOB_JOURNAL_START_INTENT)
    throw std::invalid_argument("Invalid file copy job journal state");
  const bool start_intent=r.state()==FILE_COPY_JOB_JOURNAL_START_INTENT;
  const bool prelaunch_terminal=terminal_state(r.state()) &&
      !r.has_staging() && !r.has_process_scope();
  if(start_intent) {
    if(r.has_staging() || r.has_process_scope() || r.bytes_progress()!=0 ||
       r.entries_progress()!=0 || r.has_prepared_result() || r.has_terminal_receipt())
      throw std::invalid_argument("Provisional copy intent contains post-creation state");
    return;
  }
  if(prelaunch_terminal) {
    if(r.bytes_progress()!=0 || r.entries_progress()!=0 || r.has_prepared_result())
      throw std::invalid_argument("Pre-launch terminal copy record contains worker state");
  } else {
    if(r.has_staging()!=r.has_process_scope())
      throw std::invalid_argument("Copy staging and ProcessScope identities must be bound together");
    validate_copy_identity(r.staging(),true,"Invalid copy staging identity");
    if(r.staging().device()!=r.destination_directory().device())
      throw std::invalid_argument("Copy staging and destination directory are on different devices");
    if(!r.has_process_scope() || has_unknown_fields(r.process_scope()) ||
       r.process_scope().launch_id().size()!=(r.format_version()==1?16U:32U) || r.process_scope().scope_name().empty() ||
       r.process_scope().scope_name().size()>128 || r.process_scope().boot_id().empty() ||
       r.process_scope().boot_id().size()>128 || r.process_scope().device()==0 ||
       r.process_scope().device()>INT64_MAX || r.process_scope().inode()==0 ||
       r.process_scope().inode()>INT64_MAX)
      throw std::invalid_argument("Invalid copy ProcessScope identity");
    validate_component(r.process_scope().scope_name(),"Invalid ProcessScope name");
  }
  if(r.has_drained_process_scope() &&
     (!r.has_process_scope() || r.drained_process_scope().SerializeAsString()!=r.process_scope().SerializeAsString()))
    throw std::invalid_argument("Drain acknowledgment differs from exact job scope");
  if(r.has_prepared_result()) {
    if(prelaunch_terminal)
      throw std::invalid_argument("Pre-launch terminal copy record has a prepared result");
    const auto& prepared=r.prepared_result();
    validate_component(prepared.staging_output_name(),"Invalid staged output name");
    if(prepared.staging_output_name().rfind(".spatial-copy-output-",0)!=0 ||
       !prepared.has_output() || has_unknown_fields(prepared) ||
       prepared.content_sha256().size()!=32 ||
       prepared.bytes_copied()>r.maximum_bytes() ||
       prepared.entries_copied()>r.maximum_entries())
      throw std::invalid_argument("Invalid prepared copy result");
    validate_copy_identity(prepared.output(),
        prepared.output().type()==FileCopyFilesystemIdentity::DIRECTORY,
        "Invalid prepared output identity");
    if(prepared.output().type()!=r.source().type() ||
       prepared.output().device()!=r.staging().device())
      throw std::invalid_argument("Prepared output does not match source type and staging device");
  }
  if(r.bytes_progress()>r.maximum_bytes() || r.entries_progress()>r.maximum_entries())
    throw std::invalid_argument("Copy job progress exceeds approved limits");
  const bool terminal=terminal_state(r.state());
  if(!terminal) {
    if(r.has_terminal_receipt()) throw std::invalid_argument("Nonterminal copy job has a terminal receipt");
    return;
  }
  if(!r.has_terminal_receipt() || has_unknown_fields(r.terminal_receipt()) ||
     r.terminal_receipt().job_id()!=r.job_id() ||
     r.terminal_receipt().detail().size()>1024 ||
     r.terminal_receipt().SerializeAsString().size()>kMaxOutcome)
    throw std::invalid_argument("Invalid terminal copy job receipt");
  using R=FileCopyJobReceipt;
  const auto state=r.terminal_receipt().state();
  if((r.state()==FILE_COPY_JOB_JOURNAL_COMPLETE && state!=R::COMPLETE) ||
     (r.state()==FILE_COPY_JOB_JOURNAL_CANCELLED && state!=R::CANCELLED) ||
     (r.state()==FILE_COPY_JOB_JOURNAL_CONFLICT && state!=R::CONFLICT) ||
     (r.state()==FILE_COPY_JOB_JOURNAL_UNCERTAIN && state!=R::UNCERTAIN) ||
     (r.state()==FILE_COPY_JOB_JOURNAL_FAILED && state!=R::FAILED) ||
     r.terminal_receipt().bytes_copied()>r.maximum_bytes() ||
     r.terminal_receipt().entries_copied()>r.maximum_entries())
    throw std::invalid_argument("Copy job terminal state and receipt disagree");
  if(r.state()==FILE_COPY_JOB_JOURNAL_FAILED) {
    if(r.terminal_receipt().failure_code()==FileActionError::CODE_UNSPECIFIED ||
       r.terminal_receipt().detail().size()>1024 || r.has_prepared_result())
      throw std::invalid_argument("Invalid failed copy job receipt");
    switch(r.terminal_receipt().failure_code()) {
      case FileActionError::INVALID_ARGUMENT: case FileActionError::NOT_FOUND:
      case FileActionError::EXHAUSTED: case FileActionError::UNAVAILABLE:
      case FileActionError::STALE_HANDLE: case FileActionError::UNIMPLEMENTED: break;
      default: throw std::invalid_argument("Disallowed failed copy job error code");
    }
  } else if(r.terminal_receipt().failure_code()!=FileActionError::CODE_UNSPECIFIED) {
    throw std::invalid_argument("Non-failed copy receipt has a failure code");
  }
  if(r.state()==FILE_COPY_JOB_JOURNAL_COMPLETE &&
     (!r.has_prepared_result() ||
      r.terminal_receipt().content_digest()!=r.prepared_result().content_sha256() ||
      r.terminal_receipt().bytes_copied()!=r.prepared_result().bytes_copied() ||
      r.terminal_receipt().entries_copied()!=r.prepared_result().entries_copied()))
    throw std::invalid_argument("Complete copy receipt does not match its prepared result");
}
void validate_copy_lookup(const std::string& workspace,const std::string& job_id) {
  if(workspace.empty() || workspace.size()>256 || job_id.size()!=16)
    throw std::invalid_argument("Invalid copy job lookup key");
}
workspace::v1::FileCopyJobJournalRecord normalized_copy_identity(workspace::v1::FileCopyJobJournalRecord value) {
  value.clear_drained_process_scope(); value.clear_state(); value.clear_bytes_progress(); value.clear_entries_progress();
  value.clear_terminal_receipt(); value.clear_updated_at_unix_ms(); value.clear_prepared_result();
  return value;
}
bool same_copy_job_identity(workspace::v1::FileCopyJobJournalRecord a,
                            workspace::v1::FileCopyJobJournalRecord b) {
  const bool bind_prepared_identity =
      a.state()==workspace::v1::FILE_COPY_JOB_JOURNAL_START_INTENT &&
      b.state()==workspace::v1::FILE_COPY_JOB_JOURNAL_QUEUED &&
      !a.has_staging() && !a.has_process_scope() &&
      b.has_staging() && b.has_process_scope();
  if(bind_prepared_identity) {
    a.clear_staging(); a.clear_process_scope();
    b.clear_staging(); b.clear_process_scope();
  }
  return normalized_copy_identity(std::move(a)).SerializeAsString()==
         normalized_copy_identity(std::move(b)).SerializeAsString();
}
bool allowed_copy_transition(workspace::v1::FileCopyJobJournalState from,
                             workspace::v1::FileCopyJobJournalState to) {
  using namespace workspace::v1;
  if(from==to) return from==FILE_COPY_JOB_JOURNAL_RUNNING ||
                       from==FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED ||
                       from==FILE_COPY_JOB_JOURNAL_QUEUED;
  if(from==FILE_COPY_JOB_JOURNAL_START_INTENT)
    return to==FILE_COPY_JOB_JOURNAL_QUEUED || to==FILE_COPY_JOB_JOURNAL_CANCELLED ||
           to==FILE_COPY_JOB_JOURNAL_CONFLICT || to==FILE_COPY_JOB_JOURNAL_UNCERTAIN ||
           to==FILE_COPY_JOB_JOURNAL_FAILED;
  if(from==FILE_COPY_JOB_JOURNAL_QUEUED)
    return to==FILE_COPY_JOB_JOURNAL_RUNNING || to==FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED ||
           to==FILE_COPY_JOB_JOURNAL_CONFLICT || to==FILE_COPY_JOB_JOURNAL_UNCERTAIN ||
           to==FILE_COPY_JOB_JOURNAL_FAILED;
  if(from==FILE_COPY_JOB_JOURNAL_RUNNING)
    return to==FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED || to==FILE_COPY_JOB_JOURNAL_COMPLETE ||
           to==FILE_COPY_JOB_JOURNAL_CONFLICT || to==FILE_COPY_JOB_JOURNAL_UNCERTAIN ||
           to==FILE_COPY_JOB_JOURNAL_FAILED;
  if(from==FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED)
    return to==FILE_COPY_JOB_JOURNAL_CANCELLED || to==FILE_COPY_JOB_JOURNAL_UNCERTAIN;
  return false;
}
workspace::v1::FileCopyJobJournalRecord decode_copy_job(const std::string& bytes) {
  workspace::v1::FileCopyJobJournalRecord record;
  if(bytes.empty() || bytes.size()>kMaxCopyJobRecord || !record.ParseFromString(bytes))
    throw std::runtime_error("File copy job journal record is malformed");
  try { validate_copy_job(record); }
  catch(const std::exception&) { throw std::runtime_error("File copy job journal record failed validation"); }
  return record;
}
}  // namespace
namespace {
void pin_require(bool value, const char* message) { if(!value) throw std::runtime_error(message); }
std::string filesystem_identity(int fd) {
  struct statfs info{}; pin_require(fstatfs(fd,&info)==0,"Cannot inspect journal filesystem");
  btrfs_ioctl_fs_info_args btrfs{};
  if(ioctl(fd,BTRFS_IOC_FS_INFO,&btrfs)==0)
    return std::string("btrfs:")+std::string(reinterpret_cast<const char*>(btrfs.fsid),sizeof(btrfs.fsid));
  return std::string(reinterpret_cast<const char*>(&info.f_type),sizeof(info.f_type))+
      std::string(reinterpret_cast<const char*>(&info.f_fsid),sizeof(info.f_fsid));
}
struct PinnedFd {
  int value=-1;
  ~PinnedFd() { if(value>=0) close(value); }
};
constexpr char kAliasPrefix[]=".spatial-pinned-sqlite-";
struct AliasDirectory {
  PinnedFd parent,directory,source;
  std::string name;
  struct stat original{};
  bool linked=false;
  static void require_same(int fd,int named_parent,const char* leaf) {
    struct stat held{},named{};
    pin_require(fstat(fd,&held)==0 && fstatat(named_parent,leaf,&named,AT_SYMLINK_NOFOLLOW)==0 &&
        held.st_dev==named.st_dev && held.st_ino==named.st_ino && S_ISDIR(named.st_mode) &&
        named.st_uid==geteuid() && (named.st_mode&0777)==0700,"Pinned alias directory changed");
  }
  bool matching_leaf(int fd) const {
    struct stat named{};
    return fstatat(fd,"database",&named,AT_SYMLINK_NOFOLLOW)==0 && S_ISREG(named.st_mode) &&
        named.st_uid==geteuid() && !(named.st_mode&0077) &&
        named.st_dev==original.st_dev && named.st_ino==original.st_ino;
  }
  struct CloseDirectory { void operator()(DIR* value) const { closedir(value); } };
  static std::vector<std::string> entries(int fd) {
    PinnedFd duplicate;duplicate.value=openat(fd,".",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    pin_require(duplicate.value>=0,"Cannot enumerate pinned alias directory");
    DIR* raw=fdopendir(duplicate.value);pin_require(raw!=nullptr,"Cannot inspect pinned alias directory");
    duplicate.value=-1;
    std::unique_ptr<DIR,CloseDirectory> stream(raw);
    std::vector<std::string> result;
    errno=0;
    while(auto* entry=readdir(raw)) {
      if(std::strcmp(entry->d_name,".") && std::strcmp(entry->d_name,"..")) result.emplace_back(entry->d_name);
      pin_require(result.size()<=66,"Pinned alias directory exceeds bounded layout");
    }
    pin_require(errno==0,"Cannot finish pinned alias scan");return result;
  }
  void prune_orphans() {
    unsigned retained=0;
    // Bound inspection independently from all unrelated execution-root entries.
    PinnedFd duplicate;duplicate.value=openat(parent.value,".",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    pin_require(duplicate.value>=0,"Cannot inspect pinned alias namespace");
    DIR* raw=fdopendir(duplicate.value);pin_require(raw!=nullptr,"Cannot open pinned alias namespace");duplicate.value=-1;
    std::unique_ptr<DIR,CloseDirectory> stream(raw);
    std::vector<std::string> names;
    unsigned scanned=0;
    errno=0;
    while(auto* entry=readdir(raw)) {
      pin_require(++scanned<=4096,"Pinned database parent exceeds bounded alias scan");
      if(std::strncmp(entry->d_name,kAliasPrefix,sizeof(kAliasPrefix)-1)!=0) continue;
      names.emplace_back(entry->d_name);pin_require(names.size()<=64,"Pinned alias namespace is full");
    }
    pin_require(errno==0,"Cannot finish pinned alias namespace scan");
    for(const auto& candidate:names) {
      const auto suffix=candidate.substr(sizeof(kAliasPrefix)-1);
      pin_require(suffix.size()==32 && suffix.find_first_not_of("0123456789abcdef")==std::string::npos,
          "Unexpected pinned alias name");
      PinnedFd held;held.value=openat(parent.value,candidate.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
      pin_require(held.value>=0,"Unsafe pinned alias entry");require_same(held.value,parent.value,candidate.c_str());
      if(flock(held.value,LOCK_EX|LOCK_NB)!=0) {
        pin_require(errno==EWOULDBLOCK || errno==EAGAIN,"Cannot inspect pinned alias owner");++retained;continue;
      }
      const auto leaves=entries(held.value);
      pin_require(leaves.empty() || (leaves.size()==1 && leaves.front()=="database" && matching_leaf(held.value)),
          "Foreign data in pinned alias namespace");
      require_same(held.value,parent.value,candidate.c_str());
      if(!leaves.empty()) pin_require(unlinkat(held.value,"database",0)==0 && fsync(held.value)==0,"Cannot unlink orphan database alias");
      pin_require(unlinkat(parent.value,candidate.c_str(),AT_REMOVEDIR)==0,"Cannot remove orphan alias directory");
    }
    pin_require(retained<64,"Pinned alias namespace has too many live connections");
    pin_require(fsync(parent.value)==0,"Cannot sync pinned alias namespace");
  }
  AliasDirectory(int parent_fd,int database_fd) {
    source.value=fcntl(database_fd,F_DUPFD_CLOEXEC,3);
    parent.value=openat(parent_fd,".",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    struct stat expected{},actual{};
    pin_require(source.value>=0 && parent.value>=0 && fstat(source.value,&original)==0 && S_ISREG(original.st_mode) &&
        original.st_uid==geteuid() && !(original.st_mode&0077) &&
        (fcntl(source.value,F_GETFL)&O_PATH)==O_PATH &&
        fstat(parent_fd,&expected)==0 && fstat(parent.value,&actual)==0 &&
        expected.st_dev==actual.st_dev && expected.st_ino==actual.st_ino,
        "Invalid pinned alias source or parent");
    pin_require(flock(parent.value,LOCK_EX|LOCK_NB)==0,"Pinned alias namespace is busy");
    prune_orphans();
    unsigned char random[16]{};std::size_t at=0;
    while(at<sizeof(random)) {
      const auto count=getrandom(random+at,sizeof(random)-at,0);
      if(count<0 && errno==EINTR) continue;
      pin_require(count>0,"Cannot name pinned database alias");at+=static_cast<std::size_t>(count);
    }
    name=kAliasPrefix;constexpr char digits[]="0123456789abcdef";
    for(const auto byte:random) { name+=digits[byte>>4];name+=digits[byte&15]; }
    pin_require(mkdirat(parent.value,name.c_str(),0700)==0,"Cannot create private database alias directory");
    // Any construction failure leaves a strictly bounded orphan for the next opener.
    directory.value=openat(parent.value,name.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    pin_require(directory.value>=0,"Cannot hold database alias directory");require_same(directory.value,parent.value,name.c_str());
    pin_require(flock(directory.value,LOCK_EX|LOCK_NB)==0,"Cannot own database alias directory");
    const auto from="/proc/self/fd/"+std::to_string(source.value);
    pin_require(linkat(AT_FDCWD,from.c_str(),directory.value,"database",AT_SYMLINK_FOLLOW)==0,
        "Cannot link exact pinned database inode");linked=true;
    pin_require(matching_leaf(directory.value),"Database alias does not match pinned inode");
    require_same(directory.value,parent.value,name.c_str());
    pin_require(fsync(directory.value)==0 && fsync(parent.value)==0,"Cannot sync database alias");
    pin_require(flock(parent.value,LOCK_UN)==0,"Cannot release database alias namespace");
  }
  ~AliasDirectory() {
    if(directory.value<0 || name.empty()) return;
    try {
      pin_require(flock(parent.value,LOCK_EX|LOCK_NB)==0,"Cannot lock alias namespace for close");
      require_same(directory.value,parent.value,name.c_str());
      const auto leaves=entries(directory.value);
      pin_require(leaves.size()==1 && leaves.front()=="database" && linked && matching_leaf(directory.value),
          "Cannot remove substituted database alias");
      pin_require(unlinkat(directory.value,"database",0)==0 && fsync(directory.value)==0 &&
          unlinkat(parent.value,name.c_str(),AT_REMOVEDIR)==0 && fsync(parent.value)==0,"Cannot remove closed database alias");
      flock(parent.value,LOCK_UN);
    } catch(...) {
      // Retain evidence; future bounded orphan scanning must revalidate every entry.
      sqlite3_log(SQLITE_WARNING,"Retained pinned database alias after close");
    }
  }
};

// SQLite's ordinary Unix xFullPathname resolves /proc descriptor links. Keep
// this private, fixed descriptor path intact so side files share the pinned directory.
// MAIN opens an exact-inode regular hardlink below the same pinned parent.
// Side files retain their original fixed names and final-entry NOFOLLOW.
struct AnchoredJournalVfs {
  sqlite3_vfs api{};
  sqlite3_vfs* base=nullptr;
  char name[96]{};
  char filename[PATH_MAX]{};
  sqlite3_filename descriptor_filename=nullptr;
  AliasDirectory* alias=nullptr;
  static AnchoredJournalVfs* self(sqlite3_vfs* value) {
    return reinterpret_cast<AnchoredJournalVfs*>(value);
  }
  bool allows(const char* path) const noexcept {
    if(!path) return false;
    const auto size=std::strlen(filename);
    if(std::strncmp(path,filename,size)!=0) return false;
    const char* suffix=path+size;
    return *suffix=='\0' || std::strcmp(suffix,"-journal")==0 ||
        std::strcmp(suffix,"-wal")==0 || std::strcmp(suffix,"-shm")==0;
  }
  AnchoredJournalVfs(int directory,int database_fd,const char* leaf) {
    static std::atomic<unsigned long> sequence{0};
    base=sqlite3_vfs_find(nullptr);pin_require(base!=nullptr,"SQLite Unix VFS is unavailable");
    api=*base;
    std::snprintf(name,sizeof(name),"spatial-pinned-journal-%lu",++sequence);
    auto owned_alias=std::make_unique<AliasDirectory>(directory,database_fd);
    const int count=std::snprintf(filename,sizeof(filename),"/proc/self/fd/%d/%s",owned_alias->parent.value,leaf);
    pin_require(count>0 && static_cast<std::size_t>(count)<sizeof(filename) && count<api.mxPathname,
        "Pinned SQLite descriptor path is too long");
    const auto descriptor_path="/proc/self/fd/"+std::to_string(owned_alias->directory.value)+"/database";
    const auto journal=std::string(filename)+"-journal",wal=std::string(filename)+"-wal";
    descriptor_filename=sqlite3_create_filename(descriptor_path.c_str(),journal.c_str(),wal.c_str(),0,nullptr);
    pin_require(descriptor_filename!=nullptr,"Cannot retain pinned SQLite filename");
    api.zName=name;api.pNext=nullptr;
    api.xFullPathname=[](sqlite3_vfs* v,const char* path,int size,char* output) {
      const auto* owned=self(v);
      if(!owned->allows(path) || std::strlen(path)>=static_cast<std::size_t>(size)) return SQLITE_CANTOPEN;
      std::memcpy(output,path,std::strlen(path)+1);return SQLITE_OK;
    };
    api.xOpen=[](sqlite3_vfs* v,const char* path,sqlite3_file* file,int flags,int* result) {
      file->pMethods=nullptr;
      auto* owned=self(v);
      if(!owned->allows(path)) return SQLITE_CANTOPEN;
      if(std::strcmp(path,owned->filename)==0) {
        if(!(flags&SQLITE_OPEN_MAIN_DB) || (flags&(SQLITE_OPEN_CREATE|SQLITE_OPEN_DELETEONCLOSE|SQLITE_OPEN_EXCLUSIVE)))
          return SQLITE_CANTOPEN;
        const int opened=owned->base->xOpen(owned->base,owned->descriptor_filename,file,flags|SQLITE_OPEN_NOFOLLOW,result);
        if(opened!=SQLITE_OK) return opened;
        unsigned char header[100]{};
        const int read=file->pMethods->xRead(file,header,sizeof(header),0);
        if(read!=SQLITE_OK || std::memcmp(header,"SQLite format 3",16)!=0 || header[18]!=1 || header[19]!=1) {
          // Use only SQLite's own managed descriptor. No independent close may drop locks.
          const int closed=file->pMethods->xClose(file);
          if(closed!=SQLITE_OK) return closed;
          file->pMethods=nullptr;return SQLITE_CANTOPEN;
        }
        return SQLITE_OK;
      }
      const auto* suffix=path+std::strlen(owned->filename);
      if(std::strcmp(suffix,"-journal")!=0 || !(flags&SQLITE_OPEN_MAIN_JOURNAL)) return SQLITE_CANTOPEN;
      return owned->base->xOpen(owned->base,path,file,flags|SQLITE_OPEN_NOFOLLOW,result);
    };
    api.xDelete=[](sqlite3_vfs* v,const char* path,int sync) {
      auto* owned=self(v);
      return owned->allows(path) && std::strcmp(path+std::strlen(owned->filename),"-journal")==0?
          owned->base->xDelete(owned->base,path,sync):SQLITE_IOERR_DELETE;
    };
    api.xAccess=[](sqlite3_vfs* v,const char* path,int flags,int* result) {
      auto* owned=self(v);
      if(!owned->allows(path)) { *result=0;return SQLITE_OK; }
      const auto* suffix=path+std::strlen(owned->filename);
      if(std::strcmp(suffix,"-wal")==0 || std::strcmp(suffix,"-shm")==0) { *result=0;return SQLITE_OK; }
      return owned->base->xAccess(owned->base,path,flags,result);
    };
    if(sqlite3_vfs_register(&api,0)!=SQLITE_OK) {
      sqlite3_free_filename(descriptor_filename);descriptor_filename=nullptr;
      throw std::runtime_error("Cannot register pinned SQLite VFS");
    }
    alias=owned_alias.release();
  }
  ~AnchoredJournalVfs() { sqlite3_vfs_unregister(&api);sqlite3_free_filename(descriptor_filename);delete alias; }
};
static_assert(std::is_standard_layout_v<AnchoredJournalVfs>);
// Every handle and statement is private. Retain the complete capability if close
// cannot finish; freeing its VFS/filename/alias under a live DB is never safe.
void close_private_connection(sqlite3*& db,std::unique_ptr<AnchoredJournalVfs>& vfs) {
  if(!db) return;
  int result=sqlite3_close(db);
  if(result==SQLITE_BUSY) {
    while(auto* statement=sqlite3_next_stmt(db,nullptr)) sqlite3_finalize(statement);
    result=sqlite3_close(db);
  }
  if(result!=SQLITE_OK) {
    (void)vfs.release();db=nullptr;
    sqlite3_log(SQLITE_ERROR,"Retained pinned SQLite capability after failed close");
    throw std::runtime_error("Pinned SQLite connection could not close; capability retained");
  }
  db=nullptr;
}
struct ExistingConnection {
  std::unique_ptr<AnchoredJournalVfs> vfs;
  sqlite3* db=nullptr;
  ExistingConnection(int directory,int file,const char* leaf):
      vfs(std::make_unique<AnchoredJournalVfs>(directory,file,leaf)) {}
  void close() { close_private_connection(db,vfs); }
  ~ExistingConnection() {
    try { close(); } catch(...) { sqlite3_log(SQLITE_ERROR,"Pinned SQLite close failed during unwinding"); }
  }
};
void check_file_identity(int fd,int parent,const char* name, bool directory) {
  struct stat held{},named{};
  pin_require(fstat(fd,&held)==0 && fstatat(parent,name,&named,AT_SYMLINK_NOFOLLOW)==0 &&
      held.st_dev==named.st_dev && held.st_ino==named.st_ino && named.st_uid==geteuid() &&
      (directory?S_ISDIR(named.st_mode):S_ISREG(named.st_mode)) && !(named.st_mode&0022),
      "Pinned execution or journal identity changed");
}
}
struct PinnedFileOperationJournal::Impl {
  PinnedFd root,parent,journal,database,execution;
  std::string name;
  workspace::v1::FileCopyCleanupJournalBinding binding;
  void verify() const {
    check_file_identity(root.value,parent.value,name.c_str(),true);
    check_file_identity(journal.value,root.value,"file-operations",true);
    check_file_identity(database.value,journal.value,"file-operations.db",false);
    check_file_identity(execution.value,root.value,"execution.db",false);
    for(const auto* name:{"file-operations.db-journal","file-operations.db-wal","file-operations.db-shm"}) {
      struct stat side{};
      const int rc=fstatat(journal.value,name,&side,AT_SYMLINK_NOFOLLOW);
      const bool rollback=std::strcmp(name,"file-operations.db-journal")==0;
      pin_require((rc<0 && errno==ENOENT) || (rollback && rc==0 && S_ISREG(side.st_mode) &&
          side.st_uid==geteuid() && !(side.st_mode&0077)),"Unsafe or unsupported pinned SQLite side file");
    }
    for(const auto* name:{"execution.db-journal","execution.db-wal","execution.db-shm"}) {
      struct stat side{};const int rc=fstatat(root.value,name,&side,AT_SYMLINK_NOFOLLOW);
      const bool rollback=std::strcmp(name,"execution.db-journal")==0;
      pin_require((rc<0 && errno==ENOENT) || (rollback && rc==0 && S_ISREG(side.st_mode) &&
          side.st_uid==geteuid() && !(side.st_mode&0077)),"Unsafe or unsupported execution SQLite side file");
    }
    for(const int fd:{journal.value,database.value,execution.value}) {
      struct stat info{};
      pin_require(fstat(fd,&info)==0 && !(info.st_mode&0077),"Pinned journal metadata is not private");
    }
    pin_require(filesystem_identity(root.value)==binding.filesystem_identity(),"Pinned execution filesystem changed");
  }
};
PinnedFileOperationJournal::PinnedFileOperationJournal(std::shared_ptr<Impl> impl):impl_(std::move(impl)) {}
PinnedFileOperationJournal::~PinnedFileOperationJournal()=default;
PinnedFileOperationJournal::PinnedFileOperationJournal(PinnedFileOperationJournal&&) noexcept=default;
PinnedFileOperationJournal& PinnedFileOperationJournal::operator=(PinnedFileOperationJournal&&) noexcept=default;
const workspace::v1::FileCopyCleanupJournalBinding& PinnedFileOperationJournal::binding(CleanupDeadline deadline) const { verify(deadline); return impl_->binding; }
void PinnedFileOperationJournal::verify(CleanupDeadline deadline) const {
  pin_require(bool(impl_),"Missing pinned journal ownership");
  if(deadline!=kNoCleanupDeadline && std::chrono::steady_clock::now()>=deadline) throw std::runtime_error("Copy cleanup deadline expired");
  impl_->verify();
  if(deadline!=kNoCleanupDeadline && std::chrono::steady_clock::now()>=deadline) throw std::runtime_error("Copy cleanup deadline expired");
}
int PinnedFileOperationJournal::execution_directory_fd() const { verify(); return impl_->root.value; }
void PinnedFileOperationJournal::verify_workspace_binding(const workspace::v1::FileCopyJobJournalRecord& job, CleanupDeadline deadline) const {
  verify(deadline);
  CleanupDeadlineContext deadline_context(deadline);
  try {
  ExistingConnection connection(impl_->root.value,impl_->execution.value,"execution.db");
  const int result=sqlite3_open_v2(connection.vfs->filename,&connection.db,SQLITE_OPEN_READONLY|SQLITE_OPEN_NOFOLLOW,connection.vfs->name);
  check(result,connection.db,"Cannot inspect pinned workspace registry");
  if(deadline==kNoCleanupDeadline) sqlite3_busy_timeout(connection.db,500);
  else { sqlite3_busy_handler(connection.db,cleanup_busy,&deadline); sqlite3_progress_handler(connection.db,1000,cleanup_progress,&deadline); }
  {
  Statement row(connection.db,"SELECT w.worktree,t.state,t.path FROM workspaces w JOIN worktrees t ON t.id=w.worktree WHERE w.id=?");
  row.text(1,job.workspace_id());
  pin_require(row.row() && row.string(0)==job.worktree_id() && row.string(1)=="ready",
      "Cleanup workspace/worktree binding changed");
  PinnedFd worktree,staging;
  check_cleanup_deadline();
  worktree.value=open(row.string(2).c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
  check_cleanup_deadline();
  struct stat info{};
  pin_require(worktree.value>=0 && fstat(worktree.value,&info)==0 &&
      static_cast<std::uint64_t>(info.st_dev)==job.worktree_root_device() &&
      static_cast<std::uint64_t>(info.st_ino)==job.worktree_root_inode(),"Cleanup worktree root changed");
  check_cleanup_deadline();
  staging.value=openat(worktree.value,".spatial-copy-staging",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
  check_cleanup_deadline();
  pin_require(staging.value>=0 && fstat(staging.value,&info)==0 &&
      static_cast<std::uint64_t>(info.st_dev)==job.staging_root().device() &&
      static_cast<std::uint64_t>(info.st_ino)==job.staging_root().inode(),"Cleanup staging root changed");
  check_cleanup_deadline();
  }
  verify(deadline);connection.close();verify(deadline);
  } catch(...) { throw; }
}
PinnedFileOperationJournal FileOperationJournal::pin_existing(int root_fd) {
  auto pin=std::make_shared<PinnedFileOperationJournal::Impl>();
  pin->root.value=fcntl(root_fd,F_DUPFD_CLOEXEC,3);
  pin_require(pin->root.value>=3,"Cannot retain execution root descriptor");
  const auto path=std::filesystem::read_symlink("/proc/self/fd/"+std::to_string(pin->root.value));
  pin_require(path.is_absolute() && path==path.lexically_normal(),"Execution root has no stable absolute name");
  pin->name=path.filename().string();
  pin_require(!pin->name.empty(),"Execution root must not be the filesystem root");
  pin->parent.value=open(path.parent_path().c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
  pin->journal.value=openat(pin->root.value,"file-operations",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
  pin->database.value=openat(pin->journal.value,"file-operations.db",O_PATH|O_NOFOLLOW|O_CLOEXEC);
  pin->execution.value=openat(pin->root.value,"execution.db",O_PATH|O_NOFOLLOW|O_CLOEXEC);
  pin_require(pin->parent.value>=0 && pin->journal.value>=0 && pin->database.value>=0 && pin->execution.value>=0,
      "Existing execution and file journals are required");
  auto& binding=pin->binding; binding.set_format_version(1);
  struct stat info{};
  pin_require(fstat(pin->root.value,&info)==0,"Cannot identify execution root");
  binding.set_execution_device(info.st_dev); binding.set_execution_inode(info.st_ino);
  binding.set_filesystem_identity(filesystem_identity(pin->root.value));
  pin_require(fstat(pin->execution.value,&info)==0,"Cannot identify execution database");
  binding.set_execution_database_device(info.st_dev); binding.set_execution_database_inode(info.st_ino);
  pin_require(fstat(pin->journal.value,&info)==0,"Cannot identify journal directory");
  binding.set_journal_directory_device(info.st_dev); binding.set_journal_directory_inode(info.st_ino);
  pin_require(fstat(pin->database.value,&info)==0,"Cannot identify journal database");
  binding.set_journal_database_device(info.st_dev); binding.set_journal_database_inode(info.st_ino);
  pin->verify();
  ExistingConnection connection(pin->journal.value,pin->database.value,"file-operations.db");
  const int result=sqlite3_open_v2(connection.vfs->filename,&connection.db,SQLITE_OPEN_READONLY|SQLITE_OPEN_NOFOLLOW,connection.vfs->name);
  check(result,connection.db,"Cannot inspect existing journal identity");
  {
  Statement version(connection.db,"PRAGMA user_version");
  pin_require(version.row() && sqlite3_column_int(version.get(),0)==8,"Cleanup requires initialized journal schema 8");
  Statement id(connection.db,"SELECT identity FROM cleanup_journal_identity WHERE id=1");
  pin_require(id.row() && id.bytes(0).size()==32,"Missing cleanup journal identity");
  binding.set_journal_id(id.bytes(0));
  }
  pin->verify();connection.close();pin->verify();
  return PinnedFileOperationJournal(std::move(pin));
}
struct FileOperationJournal::Impl {
  using Task = std::function<void(sqlite3*)>;
  std::filesystem::path database;
  std::mutex mutex;
  std::condition_variable wake;
  std::deque<Task> tasks;
  bool stopping = false;
  std::thread worker;
  std::promise<void> ready;
  std::shared_ptr<PinnedFileOperationJournal::Impl> pins;
  CleanupDeadline deadline = kNoCleanupDeadline;
  PinnedFd execution_lock;
  std::unique_ptr<AnchoredJournalVfs> anchored;
  std::exception_ptr close_failure;
  explicit Impl(const std::filesystem::path& state,
                std::shared_ptr<PinnedFileOperationJournal::Impl> pinned = {},
                CleanupDeadline operation_deadline = kNoCleanupDeadline)
      : database(state / "file-operations" / "file-operations.db"), pins(std::move(pinned)), deadline(operation_deadline) {
    if(pins) {
      check_deadline(deadline);pins->verify();check_deadline(deadline);
      execution_lock.value=openat(pins->root.value,"execution.lock",O_RDWR|O_NOFOLLOW|O_CLOEXEC);
      check_deadline(deadline);
      pin_require(execution_lock.value>=0,"Existing execution lock is required");
      check_file_identity(execution_lock.value,pins->root.value,"execution.lock",false);check_deadline(deadline);
      pin_require(flock(execution_lock.value,LOCK_EX|LOCK_NB)==0,"Execution is still owned by a broker");check_deadline(deadline);
      anchored=std::make_unique<AnchoredJournalVfs>(pins->journal.value,pins->database.value,"file-operations.db");
      check_deadline(deadline);database=anchored->filename;
    } else {
    const auto directory=database.parent_path();
    if(std::filesystem::is_symlink(state) || !std::filesystem::is_directory(state))
      throw std::runtime_error("File operation journal state root is unsafe");
    std::filesystem::create_directories(directory);
    struct stat parent{};
    if (lstat(directory.c_str(), &parent) != 0 || !S_ISDIR(parent.st_mode) || S_ISLNK(parent.st_mode))
      throw std::runtime_error("File operation journal directory is unsafe");
    if (chmod(directory.c_str(), 0700) != 0 || lstat(directory.c_str(), &parent) != 0 ||
        !S_ISDIR(parent.st_mode) || S_ISLNK(parent.st_mode) || (parent.st_mode & 077) != 0)
      throw std::runtime_error("Cannot protect file operation journal directory");
    for (const auto* suffix : {"", "-journal", "-wal", "-shm"}) {
      auto path = database; path += suffix; struct stat st{};
      if (lstat(path.c_str(), &st) == 0 && S_ISLNK(st.st_mode))
        throw std::runtime_error("File operation journal path must not be a symlink");
    }
    }
    check_deadline(deadline);
    auto signal = ready.get_future();
    worker = std::thread([this] { run(); });
    try { signal.get(); } catch (...) { if(worker.joinable())worker.join(); throw; }
  }
  void finish() {
    { std::lock_guard lock(mutex); stopping = true; }
    wake.notify_one();
    if (worker.joinable()) worker.join();
  }
  ~Impl() { finish(); }
  void close_worker(sqlite3*& db) noexcept {
    try { close_private_connection(db,anchored); }
    catch(...) { close_failure=std::current_exception(); }
  }
  void run() {
    sqlite3* db = nullptr;
    active_cleanup_deadline=&deadline;
    try {
      check_cleanup_deadline();
      const int flags = SQLITE_OPEN_READWRITE | (pins?0:SQLITE_OPEN_CREATE) | SQLITE_OPEN_NOMUTEX | SQLITE_OPEN_NOFOLLOW;
      check_cleanup_deadline();
      int rc = sqlite3_open_v2(database.c_str(), &db, flags, anchored?anchored->name:nullptr);
      check_cleanup_deadline();
      check(rc, db, "Cannot open file operation journal");
      if(pins) pin_require(sqlite3_db_readonly(db,"main")==0,"Cleanup journal is not writable");
      if (!pins && chmod(database.c_str(), 0600) != 0) throw std::runtime_error("Cannot protect file operation journal database");
      if(deadline==kNoCleanupDeadline) sqlite3_busy_timeout(db,5000);
      else { sqlite3_busy_handler(db,cleanup_busy,&deadline); sqlite3_progress_handler(db,1000,cleanup_progress,&deadline); }
      if(!pins) sql(db, "PRAGMA page_size=4096; PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; PRAGMA trusted_schema=OFF; PRAGMA max_page_count=16384");
      else {
        pins->verify();
        sql(db,"PRAGMA synchronous=FULL; PRAGMA trusted_schema=OFF; PRAGMA max_page_count=16384");
        Statement mode(db,"PRAGMA journal_mode");
        pin_require(mode.row() && mode.string(0)=="delete","Existing cleanup journal must use DELETE mode");
      }
      { Statement page(db,"PRAGMA page_size"); if(!page.row() || sqlite3_column_int(page.get(),0)!=4096) throw std::runtime_error("File journal page size is not 4 KiB"); }
      { Statement cap(db,"PRAGMA max_page_count"); if(!cap.row() || sqlite3_column_int(cap.get(),0)>kMaxPages) throw std::runtime_error("File journal size cap is unavailable"); }
      int version=0;
      { Statement q(db, "PRAGMA user_version");
        if (!q.row()) throw std::runtime_error("File journal version is unavailable");
        version=sqlite3_column_int(q.get(),0);
      }
        if(pins && version!=8) throw std::runtime_error("Existing-only cleanup cannot migrate journal schema");
        if (version == 0) {
          sql(db, "BEGIN EXCLUSIVE");
          sql(db, "CREATE TABLE file_roots(worktree TEXT PRIMARY KEY,device INTEGER NOT NULL,inode INTEGER NOT NULL);"
                  "CREATE TABLE file_operations(workspace TEXT NOT NULL,operation BLOB NOT NULL CHECK(length(operation)=16),"
                  "principal BLOB NOT NULL CHECK(length(principal)=64),worktree TEXT NOT NULL,device INTEGER NOT NULL,inode INTEGER NOT NULL,"
                  "exact_right INTEGER NOT NULL,digest BLOB NOT NULL CHECK(length(digest)=32),engine BLOB NOT NULL CHECK(length(engine)=16),"
                  "state INTEGER NOT NULL CHECK(state BETWEEN 1 AND 3),outcome BLOB NOT NULL,PRIMARY KEY(workspace,operation));"
                  "PRAGMA user_version=1");
          sql(db, "COMMIT");
          version=1;
        }
        if(version==1) {
          sql(db,"BEGIN EXCLUSIVE");
          sql(db,"CREATE TABLE file_copy_jobs(workspace TEXT NOT NULL,job_id BLOB NOT NULL CHECK(length(job_id)=16),"
                 "initiating_operation BLOB NOT NULL CHECK(length(initiating_operation)=16),"
                 "state INTEGER NOT NULL CHECK(state BETWEEN 1 AND 7),updated_at INTEGER NOT NULL CHECK(updated_at>0),"
                 "record BLOB NOT NULL CHECK(length(record)>0 AND length(record)<=16384),"
                 "PRIMARY KEY(workspace,job_id),UNIQUE(workspace,initiating_operation));"
                 "CREATE INDEX file_copy_jobs_recovery ON file_copy_jobs(workspace,state,updated_at);"
                 "PRAGMA user_version=2");
          sql(db,"COMMIT");
          version=2;
        }
        if(version==2) {
          sql(db,"BEGIN EXCLUSIVE");
          sql(db,"ALTER TABLE file_copy_jobs RENAME TO file_copy_jobs_v2;"
                 "DROP INDEX file_copy_jobs_recovery;"
                 "CREATE TABLE file_copy_jobs(workspace TEXT NOT NULL,job_id BLOB NOT NULL CHECK(length(job_id)=16),"
                 "initiating_operation BLOB NOT NULL CHECK(length(initiating_operation)=16),"
                 "state INTEGER NOT NULL CHECK(state BETWEEN 1 AND 8),updated_at INTEGER NOT NULL CHECK(updated_at>0),"
                 "record BLOB NOT NULL CHECK(length(record)>0 AND length(record)<=16384),"
                 "PRIMARY KEY(workspace,job_id),UNIQUE(workspace,initiating_operation));"
                 "INSERT INTO file_copy_jobs SELECT * FROM file_copy_jobs_v2;"
                 "DROP TABLE file_copy_jobs_v2;"
                 "CREATE INDEX file_copy_jobs_recovery ON file_copy_jobs(workspace,state,updated_at);"
                 "PRAGMA user_version=3");
          sql(db,"COMMIT");
          version=3;
        }
        if(version==3) {
          sql(db,"BEGIN EXCLUSIVE");
          sql(db,"ALTER TABLE file_copy_jobs RENAME TO file_copy_jobs_v3;"
                 "DROP INDEX file_copy_jobs_recovery;"
                 "CREATE TABLE file_copy_jobs(workspace TEXT NOT NULL,job_id BLOB NOT NULL CHECK(length(job_id)=16),"
                 "initiating_operation BLOB NOT NULL CHECK(length(initiating_operation)=16),"
                 "state INTEGER NOT NULL CHECK(state BETWEEN 1 AND 9),updated_at INTEGER NOT NULL CHECK(updated_at>0),"
                 "record BLOB NOT NULL CHECK(length(record)>0 AND length(record)<=16384),"
                 "PRIMARY KEY(workspace,job_id),UNIQUE(workspace,initiating_operation));"
                 "INSERT INTO file_copy_jobs SELECT * FROM file_copy_jobs_v3;"
                 "DROP TABLE file_copy_jobs_v3;"
                 "CREATE INDEX file_copy_jobs_recovery ON file_copy_jobs(workspace,state,updated_at);"
                 "PRAGMA user_version=4");
          sql(db,"COMMIT");
          version=4;
        }
        if(version==4) {
          sql(db,"BEGIN EXCLUSIVE");
          sql(db,"CREATE TABLE git_worker_parent(id INTEGER PRIMARY KEY CHECK(id=1),"
                 "state INTEGER NOT NULL CHECK(state IN (1,2)),root_device INTEGER NOT NULL CHECK(root_device>0),"
                 "root_inode INTEGER NOT NULL CHECK(root_inode>0),boot_id TEXT NOT NULL CHECK(length(boot_id)=36),"
                 "identity BLOB NOT NULL);"
                 "PRAGMA user_version=5");
          sql(db,"COMMIT");
          version=5;
        }
        if(version==5) {
          sql(db,"BEGIN EXCLUSIVE");
          sql(db,"ALTER TABLE git_worker_parent ADD COLUMN root_path TEXT NOT NULL DEFAULT '';"
                 "PRAGMA user_version=6");
          sql(db,"COMMIT");
          version=6;
        }
        if(version==6) {
          sql(db,"BEGIN EXCLUSIVE");
          sql(db,"CREATE TABLE retained_git_worker_parents("
                 "state INTEGER NOT NULL CHECK(state=2),root_device INTEGER NOT NULL CHECK(root_device>0),"
                 "root_inode INTEGER NOT NULL CHECK(root_inode>0),boot_id TEXT NOT NULL CHECK(length(boot_id)=36),"
                 "identity BLOB NOT NULL,root_path TEXT NOT NULL,"
                 "PRIMARY KEY(root_device,root_inode,boot_id,identity,root_path));"
                 "PRAGMA user_version=7");
          sql(db,"COMMIT");
          version=7;
        }
        if(version==7) {
          sql(db,"BEGIN EXCLUSIVE");
          sql(db,"CREATE TABLE cleanup_journal_identity(id INTEGER PRIMARY KEY CHECK(id=1),identity BLOB NOT NULL CHECK(length(identity)=32));"
                 "INSERT INTO cleanup_journal_identity VALUES(1,randomblob(32));"
                 "CREATE TABLE copy_scope_cleanup(workspace TEXT NOT NULL,job_id BLOB NOT NULL CHECK(length(job_id)=16),"
                 "record BLOB NOT NULL CHECK(length(record)>0 AND length(record)<=8192),PRIMARY KEY(workspace,job_id));"
                 "PRAGMA user_version=8; COMMIT");
          version=8;
        }
        if(version!=8) throw std::runtime_error("Unsupported file operation journal format");
        if(pins) {
          Statement id(db,"SELECT identity FROM cleanup_journal_identity WHERE id=1");
          pin_require(id.row() && id.bytes(0)==pins->binding.journal_id(),"Existing journal identity changed");
          pins->verify();
        }
      { Statement q(db, "PRAGMA quick_check"); if (!q.row() || q.string(0) != "ok") throw std::runtime_error("File operation journal integrity failed"); }
      check_cleanup_deadline();
      ready.set_value();
    } catch (...) {
      try { ready.set_exception(std::current_exception()); } catch (...) {}
      close_worker(db);
      active_cleanup_deadline=nullptr;
      return;
    }
    for (;;) {
      Task task;
      { std::unique_lock lock(mutex); wake.wait(lock, [this] { return stopping || !tasks.empty(); });
        if (tasks.empty() && stopping) break;
        task = std::move(tasks.front()); tasks.pop_front(); }
      try { task(db); } catch (...) { /* packaged task transports failures to its waiter */ }
    }
    close_worker(db);
    try { check_cleanup_deadline(); } catch(...) { if(!close_failure) close_failure=std::current_exception(); }
    active_cleanup_deadline=nullptr;
  }
  template<class F> auto call(F&& operation, const Observer& observer) -> std::invoke_result_t<F, sqlite3*> {
    using Result = std::invoke_result_t<F, sqlite3*>;
    auto packaged=std::make_shared<std::packaged_task<Result(sqlite3*)>>(
        [this,operation=std::forward<F>(operation)](sqlite3* db) mutable -> Result {
          check_cleanup_deadline();
          if(pins) pins->verify();
          check_cleanup_deadline();
          if constexpr(std::is_void_v<Result>) {
            operation(db);
            check_cleanup_deadline();
          } else {
            auto result=operation(db);
            check_cleanup_deadline();
            return result;
          }
        });
    auto result = packaged->get_future();
    { std::lock_guard lock(mutex); if (stopping) throw std::runtime_error("File operation journal is stopping");
      tasks.emplace_back([task=std::move(packaged)](sqlite3* db) mutable { (*task)(db); }); }
    wake.notify_one();
    while (result.wait_for(std::chrono::milliseconds(10)) != std::future_status::ready) {
      if(deadline!=kNoCleanupDeadline && std::chrono::steady_clock::now()>=deadline) {
        // Wait for cooperative SQLite cancellation and task completion before returning.
      }
      if(observer) observer();
    }
    if constexpr (std::is_void_v<Result>) result.get(); else return result.get();
  }
};
FileOperationJournal::FileOperationJournal(const std::filesystem::path& directory) : impl_(std::make_unique<Impl>(directory)) {}
FileOperationJournal::~FileOperationJournal() = default;
void FileOperationJournal::finish_cleanup() {
  pin_require(bool(impl_->pins),"Only pinned cleanup owns explicit finish");
  if(impl_->deadline!=kNoCleanupDeadline && std::chrono::steady_clock::now()>=impl_->deadline) throw std::runtime_error("Copy cleanup deadline expired");
  impl_->finish();
  if(impl_->close_failure) std::rethrow_exception(impl_->close_failure);
  if(impl_->deadline!=kNoCleanupDeadline && std::chrono::steady_clock::now()>=impl_->deadline) throw std::runtime_error("Copy cleanup deadline expired");
}
FileOperationJournal::FileOperationJournal(std::unique_ptr<Impl> impl):impl_(std::move(impl)) {}
std::unique_ptr<FileOperationJournal> FileOperationJournal::open_existing(const PinnedFileOperationJournal& pin, CleanupDeadline deadline) {
  if(deadline!=kNoCleanupDeadline && std::chrono::steady_clock::now()>=deadline)
    throw std::runtime_error("Copy cleanup deadline expired");
  pin.verify(deadline);
  return std::unique_ptr<FileOperationJournal>(new FileOperationJournal(std::make_unique<Impl>(
      std::filesystem::path{},pin.impl_,deadline)));
}
std::string FileOperationJournal::copy_job_identity_sha256(const CopyJobRecord& record) {
  validate_copy_job(record);
  const auto bytes=normalized_copy_identity(record).SerializeAsString();
  std::string digest(32,'\0'); unsigned size=0;
  pin_require(EVP_Digest(bytes.data(),bytes.size(),reinterpret_cast<unsigned char*>(digest.data()),
      &size,EVP_sha256(),nullptr)==1 && size==32,"Cannot hash copy job identity");
  return digest;
}
std::optional<workspace::v1::FileCopyScopeCleanupOutcome> FileOperationJournal::scope_cleanup_outcome(
    const std::string& workspace,const std::string& job_id,const Observer& observer) {
  validate_copy_lookup(workspace,job_id);
  return impl_->call([workspace,job_id](sqlite3* db)->std::optional<workspace::v1::FileCopyScopeCleanupOutcome> {
    Statement q(db,"SELECT record FROM copy_scope_cleanup WHERE workspace=? AND job_id=?");
    q.text(1,workspace);q.blob(2,job_id);
    if(!q.row()) return std::nullopt;
    workspace::v1::FileCopyScopeCleanupOutcome value;
    const auto bytes=q.bytes(0);
    pin_require(bytes.size()<=kMaxOutcome && value.ParseFromString(bytes) && !has_unknown_fields(value) &&
        value.format_version()==1 && value.workspace_id()==workspace && value.job_id()==job_id &&
        value.job_identity_sha256().size()==32 && value.kind()>=workspace::v1::FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED &&
        value.kind()<=workspace::v1::FileCopyScopeCleanupOutcome::IMMUTABLE_UNCERTAINTY,
        "Malformed copy scope cleanup outcome");
    return value;
  },observer);
}
void FileOperationJournal::record_scope_cleanup_outcome(const CopyJobRecord& expected,
    const workspace::v1::FileCopyScopeCleanupOutcome& outcome,bool mark_uncertain,const Observer& observer) {
  using Outcome=workspace::v1::FileCopyScopeCleanupOutcome;
  validate_copy_job(expected);
  pin_require(bool(impl_->pins),"Scope outcomes require retained existing-only journal ownership");
  impl_->pins->verify();
  pin_require(outcome.format_version()==1 && !has_unknown_fields(outcome) &&
      outcome.journal().SerializeAsString()==impl_->pins->binding.SerializeAsString() &&
      outcome.workspace_id()==expected.workspace_id() && outcome.worktree_id()==expected.worktree_id() &&
      outcome.job_id()==expected.job_id() && outcome.job_identity_sha256()==copy_job_identity_sha256(expected) &&
      outcome.broker_root().device()==expected.broker_root_device() &&
      outcome.broker_root().inode()==expected.broker_root_inode() &&
      outcome.broker_root().boot_id()==expected.broker_root_boot_id() &&
      outcome.broker_root().launch_id().size()==32 &&
      outcome.kind()>=Outcome::RESERVED_SCOPE_RECONCILED && outcome.kind()<=Outcome::IMMUTABLE_UNCERTAINTY,
      "Cleanup outcome differs from pinned journal or exact job");
  validate_component(outcome.broker_root().scope_name(),"Invalid cleanup root name");
  pin_require(outcome.kind()!=Outcome::RESERVED_SCOPE_RECONCILED ||
      (expected.state()==workspace::v1::FILE_COPY_JOB_JOURNAL_START_INTENT && !expected.has_process_scope() && !mark_uncertain),
      "Reserved cleanup outcome must not invent a worker identity");
  auto next=expected;
  if(mark_uncertain && !terminal_state(expected.state())) {
    pin_require(expected.updated_at_unix_ms()<INT64_MAX,"Copy cleanup timestamp overflow");
    next.set_state(workspace::v1::FILE_COPY_JOB_JOURNAL_UNCERTAIN);
    next.set_updated_at_unix_ms(expected.updated_at_unix_ms()+1);
    auto* receipt=next.mutable_terminal_receipt();receipt->set_job_id(expected.job_id());
    receipt->set_state(workspace::v1::FileCopyJobReceipt::UNCERTAIN);
    receipt->set_bytes_copied(expected.bytes_progress());receipt->set_entries_copied(expected.entries_progress());
    receipt->set_detail("Pre-prune exact scope recovery was not proven; staged evidence retained");
    validate_copy_job(next);
    pin_require(same_copy_job_identity(expected,next) && allowed_copy_transition(expected.state(),next.state()),
        "Invalid cleanup uncertainty transition");
  }
  const auto old_bytes=expected.SerializeAsString(),new_bytes=next.SerializeAsString(),bytes=outcome.SerializeAsString();
  pin_require(bytes.size()<=kMaxOutcome,"Copy cleanup outcome exceeds its bound");
  auto pins=impl_->pins;
  impl_->call([=](sqlite3* db) {
    pins->verify(); Transaction tx(db);
    Statement row(db,"SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?");
    row.text(1,expected.workspace_id());row.blob(2,expected.job_id());
    pin_require(row.row(),"Cleanup job disappeared");
    const auto current=row.bytes(0);
    Statement saved(db,"SELECT record FROM copy_scope_cleanup WHERE workspace=? AND job_id=?");
    saved.text(1,expected.workspace_id());saved.blob(2,expected.job_id());
    if(saved.row()) {
      pin_require(saved.bytes(0)==bytes && (current==old_bytes || current==new_bytes),
          "Cleanup outcome retry changed its identity or expected job");
      pins->verify();tx.commit();return;
    }
    pin_require(current==old_bytes,"Cleanup job changed before outcome commit");
    Statement count(db,"SELECT count(*) FROM copy_scope_cleanup");
    pin_require(count.row() && sqlite3_column_int64(count.get(),0)<static_cast<sqlite3_int64>(kMaxCopyJobRows),
        "Copy cleanup outcome row limit reached");
    Statement insert(db,"INSERT INTO copy_scope_cleanup(workspace,job_id,record) VALUES(?,?,?)");
    insert.text(1,expected.workspace_id());insert.blob(2,expected.job_id());insert.blob(3,bytes);insert.done();
    if(new_bytes!=old_bytes) {
      Statement update(db,"UPDATE file_copy_jobs SET state=?,updated_at=?,record=? WHERE workspace=? AND job_id=? AND record=?");
      update.number(1,next.state());update.number(2,next.updated_at_unix_ms());update.blob(3,new_bytes);
      update.text(4,expected.workspace_id());update.blob(5,expected.job_id());update.blob(6,old_bytes);update.done();
      pin_require(sqlite3_changes(db)==1,"Cleanup job lost its compare-and-swap");
    }
    pins->verify();tx.commit();
  },observer);
}

bool FileOperationJournal::bind_root(const std::string& worktree, std::uint64_t device, std::uint64_t inode,
                 const Observer& observer) {
  return impl_->call([=](sqlite3* db) {
    Transaction tx(db); bool found=false, matches=true;
    { Statement q(db, "SELECT device,inode FROM file_roots WHERE worktree=?"); q.text(1, worktree);
      if (q.row()) { found=true;
        matches=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),0))==device &&
          static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),1))==inode;
      }
    }
    if(!matches) return false;
    if(!found) { Statement add(db, "INSERT INTO file_roots(worktree,device,inode) VALUES(?,?,?)");
      add.text(1,worktree); add.number(2,static_cast<sqlite3_int64>(device)); add.number(3,static_cast<sqlite3_int64>(inode)); add.done(); }
    tx.commit(); return true;
  }, observer);
}
FileOperationRecord FileOperationJournal::find(const FileOperationBinding& binding, const Observer& observer) {
  validate_binding(binding);
  return impl_->call([binding](sqlite3* db) {
    FileOperationRecord record;
    Statement q(db, "SELECT principal,worktree,device,inode,exact_right,digest,engine,state,outcome FROM file_operations WHERE workspace=? AND operation=?");
    q.text(1,binding.workspace); q.blob(2,binding.operation_id);
    if (!q.row()) return record;
    const bool exact = q.bytes(0)==binding.principal && q.string(1)==binding.worktree &&
      static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),2))==binding.root_device &&
      static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),3))==binding.root_inode &&
      sqlite3_column_int(q.get(),4)==binding.right && q.bytes(5)==binding.request_sha256;
    if (!exact) { record.state=FileOperationRecord::State::conflict; return record; }
    record.engine_incarnation=q.bytes(6);
    const int state=sqlite3_column_int(q.get(),7);
    if(state==1) record.state=FileOperationRecord::State::intent;
    else if(state==2) record.state=FileOperationRecord::State::complete;
    else record.state=FileOperationRecord::State::uncertain;
    const auto bytes=q.bytes(8);
    if(!bytes.empty() && !record.reply.ParseFromString(bytes)) throw std::runtime_error("File operation journal outcome is malformed");
    return record;
  },observer);
}
void FileOperationJournal::begin(const FileOperationBinding& b, const Observer& observer) {
  validate_binding(b);
  impl_->call([b](sqlite3* db) {
    Transaction tx(db);
    std::uint64_t rows=0;
    { Statement count(db,"SELECT count(*) FROM file_operations");
      if(!count.row()) throw std::runtime_error("File journal row count unavailable");
      rows=static_cast<std::uint64_t>(sqlite3_column_int64(count.get(),0)); }
    if(rows>=kMaxRows) throw std::length_error("File operation journal row limit reached");
    Statement add(db,"INSERT INTO file_operations(workspace,operation,principal,worktree,device,inode,exact_right,digest,engine,state,outcome) VALUES(?,?,?,?,?,?,?,?,?,1,x'')");
    bind_identity(add,b); add.blob(9,b.engine_incarnation); add.done();
    tx.commit();
  },observer);
}
void FileOperationJournal::finish(const FileOperationBinding& b, FileOperationRecord::State state,
    const workspace::v1::FileActionReply& reply, const Observer& observer) {
  if(state!=FileOperationRecord::State::complete && state!=FileOperationRecord::State::uncertain)
    throw std::invalid_argument("Invalid terminal file journal state");
  const auto outcome=reply.SerializeAsString();
  if(outcome.size()>kMaxOutcome) throw std::length_error("File journal outcome exceeds its bound");
#ifdef SPATIAL_TEST_FILE_JOURNAL_FAULTS
  if(state==FileOperationRecord::State::complete &&
     std::getenv("SPATIAL_TEST_FILE_JOURNAL_FAIL_FINISH_ONCE")) {
    static std::atomic<bool> injected{false};
    bool expected=false;
    if(injected.compare_exchange_strong(expected,true))
      throw std::runtime_error("fixture-injected file journal terminal commit failure");
  }
#endif
  const int terminal=state==FileOperationRecord::State::complete?2:3;
  impl_->call([b,outcome,terminal](sqlite3* db) {
#ifdef SPATIAL_TEST_FILE_JOURNAL_FAULTS
    if(const char* marker=std::getenv("SPATIAL_TEST_FILE_JOURNAL_FINISH_NOTIFY")) {
      const int fd=open(marker,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
      if(fd>=0) close(fd);
    }
    if(const char* delay=std::getenv("SPATIAL_TEST_FILE_JOURNAL_FINISH_DELAY_MS")) {
      char* end=nullptr; const long ms=std::strtol(delay,&end,10);
      if(end && *end=='\0' && ms>0 && ms<=5000) std::this_thread::sleep_for(std::chrono::milliseconds(ms));
    }
#endif
    Transaction tx(db);
    Statement q(db,"UPDATE file_operations SET state=?,outcome=? WHERE workspace=? AND operation=? AND digest=? AND state=1");
    q.number(1,terminal); q.blob(2,outcome); q.text(3,b.workspace); q.blob(4,b.operation_id); q.blob(5,b.request_sha256); q.done();
    if(sqlite3_changes(db)!=1) throw std::runtime_error("File operation journal intent is missing");
    tx.commit();
  },observer);
}
void FileOperationJournal::mark_uncertain(const FileOperationBinding& b,
    const workspace::v1::FileActionReply& reply, const Observer& observer) {
  const auto outcome=reply.SerializeAsString();
  if(outcome.size()>kMaxOutcome) throw std::length_error("File journal outcome exceeds its bound");
  impl_->call([b,outcome](sqlite3* db) {
    Transaction tx(db);
    Statement q(db,"UPDATE file_operations SET state=3,outcome=? WHERE workspace=? AND operation=? AND digest=? AND state IN (1,2)");
    q.blob(1,outcome); q.text(2,b.workspace); q.blob(3,b.operation_id); q.blob(4,b.request_sha256); q.done();
    if(sqlite3_changes(db)!=1) throw std::runtime_error("File operation journal cannot retain uncertainty");
    tx.commit();
  },observer);
}
void FileOperationJournal::begin_copy_job(const CopyJobRecord& record,const Observer& observer) {
  validate_copy_job(record);
  if(record.format_version()!=2 || (record.has_process_scope() && record.process_scope().launch_id().size()!=32))
    throw std::invalid_argument("New copy jobs require current format and 32-byte launch identity");
  if(record.has_drained_process_scope()) throw std::invalid_argument("A new copy job cannot begin with a drain acknowledgment");
  if(record.state()!=workspace::v1::FILE_COPY_JOB_JOURNAL_START_INTENT &&
     record.state()!=workspace::v1::FILE_COPY_JOB_JOURNAL_QUEUED &&
     record.state()!=workspace::v1::FILE_COPY_JOB_JOURNAL_RUNNING)
    throw std::invalid_argument("Copy job must begin at start intent, queued, or running");
  const auto bytes=record.SerializeAsString();
  if(bytes.empty() || bytes.size()>kMaxCopyJobRecord)
    throw std::length_error("File copy job journal record exceeds its bound");
  impl_->call([record,bytes](sqlite3* db) {
    Transaction tx(db);
    std::uint64_t rows=0;
    { Statement q(db,"SELECT count(*) FROM file_copy_jobs");
      if(!q.row()) throw std::runtime_error("Copy job journal row count unavailable");
      rows=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),0)); }
    if(rows>=kMaxCopyJobRows) throw std::length_error("File copy job journal row limit reached");
    { Statement root(db,"SELECT device,inode FROM file_roots WHERE worktree=?");
      root.text(1,record.worktree_id());
      if(!root.row() || static_cast<std::uint64_t>(sqlite3_column_int64(root.get(),0))!=record.worktree_root_device() ||
         static_cast<std::uint64_t>(sqlite3_column_int64(root.get(),1))!=record.worktree_root_inode())
        throw std::runtime_error("Copy job root identity is not registered");
    }
    Statement add(db,"INSERT INTO file_copy_jobs(workspace,job_id,initiating_operation,state,updated_at,record) VALUES(?,?,?,?,?,?)");
    add.text(1,record.workspace_id()); add.blob(2,record.job_id()); add.blob(3,record.initiating_operation_id());
    add.number(4,record.state()); add.number(5,static_cast<sqlite3_int64>(record.updated_at_unix_ms()));
    add.blob(6,bytes); add.done(); tx.commit();
  },observer);
}
void FileOperationJournal::request_copy_job_cancel(CopyJobRecord& record,const Observer& observer) {
  using namespace workspace::v1;
  if(record.state()==FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED) return;
  if(record.state()!=FILE_COPY_JOB_JOURNAL_QUEUED && record.state()!=FILE_COPY_JOB_JOURNAL_RUNNING)
    throw std::logic_error("Copy job cannot enter cancellation from its current state");
  record.set_state(FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED);
  record.set_updated_at_unix_ms(std::max(record.updated_at_unix_ms(),static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count())));
  update_copy_job(record,observer);
}
void FileOperationJournal::update_copy_job(const CopyJobRecord& record,const Observer& observer) {
  validate_copy_job(record);
  const auto bytes=record.SerializeAsString();
  if(bytes.empty() || bytes.size()>kMaxCopyJobRecord)
    throw std::length_error("File copy job journal record exceeds its bound");
  impl_->call([record,bytes](sqlite3* db) {
    Transaction tx(db);
    Statement q(db,"SELECT state,updated_at,record FROM file_copy_jobs WHERE workspace=? AND job_id=?");
    q.text(1,record.workspace_id()); q.blob(2,record.job_id());
    if(!q.row()) throw std::runtime_error("File copy job journal record is missing");
    const auto old_bytes=q.bytes(2);
    const auto old=decode_copy_job(old_bytes);
    if(old_bytes==bytes) { tx.commit(); return; }
    if(terminal_state(old.state())) throw std::runtime_error("Terminal copy job journal record is immutable");
    if((old.has_drained_process_scope() &&
        old.drained_process_scope().SerializeAsString()!=record.drained_process_scope().SerializeAsString()) ||
       !same_copy_job_identity(old,record) ||
       (old.has_prepared_result() &&
        old.prepared_result().SerializeAsString()!=record.prepared_result().SerializeAsString()) ||
       record.updated_at_unix_ms()<old.updated_at_unix_ms() ||
       record.bytes_progress()<old.bytes_progress() ||
       record.entries_progress()<old.entries_progress() ||
       !allowed_copy_transition(old.state(),record.state()))
      throw std::runtime_error("Invalid file copy job journal transition");
    Statement update(db,"UPDATE file_copy_jobs SET state=?,updated_at=?,record=? WHERE workspace=? AND job_id=? AND state=? AND updated_at=?");
    update.number(1,record.state()); update.number(2,static_cast<sqlite3_int64>(record.updated_at_unix_ms()));
    update.blob(3,bytes); update.text(4,record.workspace_id()); update.blob(5,record.job_id());
    update.number(6,old.state()); update.number(7,static_cast<sqlite3_int64>(old.updated_at_unix_ms())); update.done();
    if(sqlite3_changes(db)!=1) throw std::runtime_error("File copy job journal update lost its compare-and-swap");
    tx.commit();
  },observer);
}
std::optional<FileOperationJournal::CopyJobRecord> FileOperationJournal::find_copy_job_by_operation(
    const std::string& workspace,const std::string& operation_id,const Observer& observer) {
  validate_copy_lookup(workspace,operation_id);
  return impl_->call([workspace,operation_id](sqlite3* db)->std::optional<CopyJobRecord> {
    Statement q(db,"SELECT state,updated_at,initiating_operation,job_id,record FROM file_copy_jobs "
                   "WHERE workspace=? AND initiating_operation=?");
    q.text(1,workspace); q.blob(2,operation_id);
    if(!q.row()) return std::nullopt;
    auto record=decode_copy_job(q.bytes(4));
    if(record.workspace_id()!=workspace || record.initiating_operation_id()!=operation_id ||
       record.job_id()!=q.bytes(3) ||
       record.state()!=sqlite3_column_int(q.get(),0) ||
       record.updated_at_unix_ms()!=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),1)) ||
       q.bytes(2)!=operation_id)
      throw std::runtime_error("File copy job initiating operation index does not match its record");
    return record;
  },observer);
}
std::optional<FileOperationJournal::CopyJobRecord> FileOperationJournal::find_copy_job(
    const std::string& workspace,const std::string& job_id,const Observer& observer) {
  validate_copy_lookup(workspace,job_id);
  return impl_->call([workspace,job_id](sqlite3* db)->std::optional<CopyJobRecord> {
    Statement q(db,"SELECT state,updated_at,record FROM file_copy_jobs WHERE workspace=? AND job_id=?");
    q.text(1,workspace); q.blob(2,job_id);
    if(!q.row()) return std::nullopt;
    auto record=decode_copy_job(q.bytes(2));
    if(record.workspace_id()!=workspace || record.job_id()!=job_id ||
       record.state()!=sqlite3_column_int(q.get(),0) ||
       record.updated_at_unix_ms()!=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),1)))
      throw std::runtime_error("File copy job journal index does not match its record");
    return record;
  },observer);
}
std::vector<FileOperationJournal::CopyJobRecord> FileOperationJournal::recovery_copy_jobs(
    const std::string& workspace,const Observer& observer) {
  if(workspace.empty() || workspace.size()>256) throw std::invalid_argument("Invalid copy job workspace");
  return impl_->call([workspace](sqlite3* db) {
    std::vector<CopyJobRecord> records;
    Statement q(db,"SELECT state,updated_at,job_id,record FROM file_copy_jobs WHERE workspace=? AND state IN (1,2,3,7,9) ORDER BY updated_at,job_id");
    q.text(1,workspace);
    while(q.row()) {
      auto record=decode_copy_job(q.bytes(3));
      if(record.workspace_id()!=workspace || record.job_id()!=q.bytes(2) ||
         record.state()!=sqlite3_column_int(q.get(),0) ||
         record.updated_at_unix_ms()!=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),1)))
        throw std::runtime_error("File copy job recovery index is inconsistent");
      records.push_back(std::move(record));
      if(records.size()>kMaxCopyJobRows) throw std::runtime_error("File copy job recovery result exceeded its bound");
    }
    return records;
  },observer);
}
GitWorkerParentRecord FileOperationJournal::reserve_git_worker_parent(
    std::uint64_t root_device,std::uint64_t root_inode,const std::string& root_path,const std::string& boot_id,const Observer& observer) {
  const std::filesystem::path parsed_root(root_path);
  if(root_device==0 || root_inode==0 || root_device>INT64_MAX || root_inode>INT64_MAX || boot_id.size()!=36 ||
     root_path.empty() || root_path.size()>4096 || root_path.find('\0')!=std::string::npos ||
     !parsed_root.is_absolute() || parsed_root.lexically_normal()!=parsed_root)
    throw std::invalid_argument("Invalid Git worker root reservation");
  return impl_->call([=](sqlite3* db) {
    Transaction tx(db);
    GitWorkerParentRecord result;
    Statement q(db,"SELECT state,root_device,root_inode,boot_id,identity,root_path FROM git_worker_parent WHERE id=1");
    if(q.row()) {
      result.state=sqlite3_column_int(q.get(),0)==1?GitWorkerParentRecord::State::reserved:GitWorkerParentRecord::State::bound;
      result.root_device=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),1));
      result.root_inode=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),2));
      result.boot_id=q.string(3); result.identity_bytes=q.bytes(4); result.root_path=q.string(5);
      if(result.root_device!=root_device || result.root_inode!=root_inode || result.boot_id!=boot_id || result.root_path!=root_path)
        throw std::runtime_error("Git worker parent reservation belongs to another broker root or boot");
      if((result.state==GitWorkerParentRecord::State::reserved && !result.identity_bytes.empty()) ||
         (result.state==GitWorkerParentRecord::State::bound && result.identity_bytes.empty()))
        throw std::runtime_error("Git worker parent reservation state is malformed");
      if(result.state==GitWorkerParentRecord::State::bound) {
        supervisor::v1::ProcessScopeIdentity identity;
        if(!identity.ParseFromString(result.identity_bytes) || has_unknown_fields(identity) ||
           identity.scope_name()!="spatial-git-workers" || identity.boot_id()!=result.boot_id ||
           !identity.device() || !identity.inode() ||
           (identity.launch_id().size()!=16 && identity.launch_id().size()!=32))
          throw std::runtime_error("Bound Git worker parent identity is malformed");
      }
    } else {
      Statement add(db,"INSERT INTO git_worker_parent(id,state,root_device,root_inode,boot_id,identity,root_path) VALUES(1,1,?,?,?,x'',?)");
      add.number(1,static_cast<sqlite3_int64>(root_device));add.number(2,static_cast<sqlite3_int64>(root_inode));add.text(3,boot_id);add.text(4,root_path);add.done();
      result.state=GitWorkerParentRecord::State::reserved;result.root_device=root_device;result.root_inode=root_inode;result.boot_id=boot_id;result.root_path=root_path;
    }
    tx.commit();return result;
  },observer);
}
void FileOperationJournal::bind_git_worker_parent(std::uint64_t root_device,std::uint64_t root_inode,const std::string& root_path,
    const std::string& boot_id,const std::string& identity_bytes,const Observer& observer) {
  supervisor::v1::ProcessScopeIdentity identity;
  const std::filesystem::path parsed_root(root_path);
  if(root_device==0 || root_inode==0 || boot_id.size()!=36 || root_path.empty() || root_path.size()>4096 ||
     root_path.find('\0')!=std::string::npos || !parsed_root.is_absolute() || parsed_root.lexically_normal()!=parsed_root || identity_bytes.empty() ||
     !identity.ParseFromString(identity_bytes) || has_unknown_fields(identity) || identity.scope_name()!="spatial-git-workers" ||
     identity.boot_id()!=boot_id || identity.device()==0 || identity.inode()==0 || identity.launch_id().size()!=32)
    throw std::invalid_argument("Invalid bound Git worker parent identity");
  impl_->call([=](sqlite3* db) {
    Transaction tx(db);
    Statement q(db,"SELECT state,root_device,root_inode,boot_id,identity,root_path FROM git_worker_parent WHERE id=1");
    if(!q.row() || sqlite3_column_int(q.get(),0)!=1 ||
       static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),1))!=root_device ||
       static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),2))!=root_inode || q.string(3)!=boot_id || q.string(5)!=root_path)
      throw std::runtime_error("Git worker parent reservation changed before identity bind");
    Statement update(db,"UPDATE git_worker_parent SET state=2,identity=? WHERE id=1 AND state=1");
    update.blob(1,identity_bytes);update.done();
    if(sqlite3_changes(db)!=1) throw std::runtime_error("Git worker parent identity bind was not durable");
    tx.commit();
  },observer);
}
GitWorkerParentRecord FileOperationJournal::find_git_worker_parent(const Observer& observer) {
  return impl_->call([](sqlite3* db) {
    GitWorkerParentRecord result;
    Statement q(db,"SELECT state,root_device,root_inode,boot_id,identity,root_path FROM git_worker_parent WHERE id=1");
    if(!q.row()) return result;
    const int state=sqlite3_column_int(q.get(),0);
    if(state!=1 && state!=2) throw std::runtime_error("Git worker parent journal state is invalid");
    result.state=state==1?GitWorkerParentRecord::State::reserved:GitWorkerParentRecord::State::bound;
    result.root_device=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),1));
    result.root_inode=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),2));
    result.boot_id=q.string(3);result.identity_bytes=q.bytes(4);result.root_path=q.string(5);
    const std::filesystem::path parsed_root(result.root_path);
    if(result.root_device==0 || result.root_inode==0 || result.boot_id.size()!=36 || result.root_path.empty() ||
       result.root_path.size()>4096 || result.root_path.find('\0')!=std::string::npos ||
       !parsed_root.is_absolute() || parsed_root.lexically_normal()!=parsed_root ||
       (result.state==GitWorkerParentRecord::State::reserved && !result.identity_bytes.empty()) ||
       (result.state==GitWorkerParentRecord::State::bound && result.identity_bytes.empty()))
      throw std::runtime_error("Git worker parent journal record is malformed");
    if(result.state==GitWorkerParentRecord::State::bound) {
      supervisor::v1::ProcessScopeIdentity identity;
      if(!identity.ParseFromString(result.identity_bytes) || has_unknown_fields(identity) ||
         identity.scope_name()!="spatial-git-workers" || identity.boot_id()!=result.boot_id ||
         !identity.device() || !identity.inode() ||
         (identity.launch_id().size()!=16 && identity.launch_id().size()!=32))
        throw std::runtime_error("Bound Git worker parent identity is malformed");
    }
    return result;
  },observer);
}
void FileOperationJournal::clear_git_worker_parent(const GitWorkerParentRecord& expected,const Observer& observer) {
  if(expected.state==GitWorkerParentRecord::State::absent) throw std::invalid_argument("Cannot clear absent Git worker parent record");
  impl_->call([expected](sqlite3* db) {
    Transaction tx(db);
    Statement q(db,"DELETE FROM git_worker_parent WHERE id=1 AND state=? AND root_device=? AND root_inode=? AND boot_id=? AND identity=? AND root_path=?");
    q.number(1,expected.state==GitWorkerParentRecord::State::reserved?1:2);
    q.number(2,static_cast<sqlite3_int64>(expected.root_device));q.number(3,static_cast<sqlite3_int64>(expected.root_inode));
    q.text(4,expected.boot_id);q.blob(5,expected.identity_bytes);q.text(6,expected.root_path);q.done();
    if(sqlite3_changes(db)!=1) throw std::runtime_error("Git worker parent journal record changed before clear");
    tx.commit();
  },observer);
}
void FileOperationJournal::retain_git_worker_parent(const GitWorkerParentRecord& expected,const Observer& observer) {
  if(expected.state!=GitWorkerParentRecord::State::bound)
    throw std::invalid_argument("Only a bound Git parent can retain uncertainty evidence");
  impl_->call([expected](sqlite3* db) {
    Transaction tx(db);
    Statement save(db,"INSERT INTO retained_git_worker_parents(state,root_device,root_inode,boot_id,identity,root_path) "
                      "SELECT state,root_device,root_inode,boot_id,identity,root_path FROM git_worker_parent "
                      "WHERE id=1 AND state=2 AND root_device=? AND root_inode=? AND boot_id=? AND identity=? AND root_path=?");
    save.number(1,static_cast<sqlite3_int64>(expected.root_device));save.number(2,static_cast<sqlite3_int64>(expected.root_inode));
    save.text(3,expected.boot_id);save.blob(4,expected.identity_bytes);save.text(5,expected.root_path);save.done();
    if(sqlite3_changes(db)!=1) throw std::runtime_error("Git worker parent changed before evidence retention");
    sql(db,"DELETE FROM git_worker_parent WHERE id=1");
    tx.commit();
  },observer);
}
std::vector<GitWorkerParentRecord> FileOperationJournal::retained_git_worker_parents(const Observer& observer) {
  return impl_->call([](sqlite3* db) {
    std::vector<GitWorkerParentRecord> result;
    Statement q(db,"SELECT state,root_device,root_inode,boot_id,identity,root_path FROM retained_git_worker_parents "
                   "ORDER BY root_device,root_inode,boot_id,identity,root_path");
    while(q.row()) {
      GitWorkerParentRecord parent;
      parent.state=GitWorkerParentRecord::State::bound;
      parent.root_device=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),1));
      parent.root_inode=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),2));
      parent.boot_id=q.string(3);parent.identity_bytes=q.bytes(4);parent.root_path=q.string(5);
      supervisor::v1::ProcessScopeIdentity scope;
      const std::filesystem::path path(parent.root_path);
      if(sqlite3_column_int(q.get(),0)!=2 || !parent.root_device || !parent.root_inode ||
         parent.boot_id.size()!=36 || parent.root_path.size()>4096 || parent.root_path.find('\0')!=std::string::npos ||
         !path.is_absolute() || path.lexically_normal()!=path || !scope.ParseFromString(parent.identity_bytes) ||
         scope.scope_name()!="spatial-git-workers" || scope.boot_id()!=parent.boot_id ||
         !scope.device() || !scope.inode() || (scope.launch_id().size()!=16 && scope.launch_id().size()!=32))
        throw std::runtime_error("Retained Git worker parent is malformed");
      result.push_back(std::move(parent));
      if(result.size()>kMaxCopyJobRows) throw std::runtime_error("Retained Git parent count exceeds copy job bound");
    }
    return result;
  },observer);
}
std::vector<FileOperationJournal::CopyJobRecord> FileOperationJournal::recovery_copy_jobs_all(const Observer& observer) {
  return impl_->call([](sqlite3* db) {
    std::vector<CopyJobRecord> records;
    Statement q(db,"SELECT state,updated_at,job_id,record FROM file_copy_jobs WHERE state IN (1,2,3,7,9) ORDER BY updated_at,job_id");
    while(q.row()) {
      auto record=decode_copy_job(q.bytes(3));
      if(record.job_id()!=q.bytes(2) || record.state()!=sqlite3_column_int(q.get(),0) ||
         record.updated_at_unix_ms()!=static_cast<std::uint64_t>(sqlite3_column_int64(q.get(),1)))
        throw std::runtime_error("File copy job global recovery index is inconsistent");
      records.push_back(std::move(record));
      if(records.size()>kMaxCopyJobRows) throw std::runtime_error("File copy global recovery result exceeded its bound");
    }
    return records;
  },observer);
}
std::size_t FileOperationJournal::prune_terminal_copy_jobs(const std::string& workspace,
    std::uint64_t older_than_unix_ms,std::size_t maximum_rows,const Observer& observer) {
  if(workspace.empty() || workspace.size()>256 || older_than_unix_ms>INT64_MAX ||
     maximum_rows==0 || maximum_rows>kMaxCopyJobPruneBatch)
    throw std::invalid_argument("Invalid copy job retention request");
  return impl_->call([workspace,older_than_unix_ms,maximum_rows](sqlite3* db) {
    Transaction tx(db);
    Statement remove(db,"DELETE FROM file_copy_jobs WHERE rowid IN (SELECT rowid FROM file_copy_jobs "
        "WHERE workspace=? AND state IN (4,5,6,8) AND updated_at<? ORDER BY updated_at LIMIT ?)");
    remove.text(1,workspace); remove.number(2,static_cast<sqlite3_int64>(older_than_unix_ms));
    remove.number(3,static_cast<sqlite3_int64>(maximum_rows)); remove.done();
    const auto count=static_cast<std::size_t>(sqlite3_changes(db)); tx.commit(); return count;
  },observer);
}
} // namespace spatial
