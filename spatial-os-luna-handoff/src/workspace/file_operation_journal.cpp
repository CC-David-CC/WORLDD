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

namespace spatial {
namespace {
constexpr std::uint64_t kMaxRows = 65536;
constexpr int kMaxPages = 16384;  // 64 MiB at the fixed 4 KiB page size.
constexpr std::size_t kMaxOutcome = 8192;
constexpr std::size_t kMaxCopyJobRecord = 16 * 1024;
constexpr std::uint64_t kMaxCopyJobRows = 65536;
constexpr std::size_t kMaxCopyJobPruneBatch = 1024;
void check(int rc, sqlite3* db, const char* message) {
  (void)db;
  if (rc == SQLITE_FULL) throw std::length_error("File operation journal capacity exhausted");
  if (rc != SQLITE_OK && rc != SQLITE_ROW && rc != SQLITE_DONE)
    throw std::runtime_error(message);
}
class Statement {
 public:
  Statement(sqlite3* db, const char* sql) : db_(db) { check(sqlite3_prepare_v2(db, sql, -1, &value_, nullptr), db, "File journal query failed"); }
  ~Statement() { sqlite3_finalize(value_); }
  sqlite3_stmt* get() const { return value_; }
  void text(int at, const std::string& value) { check(sqlite3_bind_text(value_, at, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT), db_, "File journal bind failed"); }
  void blob(int at, const std::string& value) { check(sqlite3_bind_blob(value_, at, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT), db_, "File journal bind failed"); }
  void number(int at, sqlite3_int64 value) { check(sqlite3_bind_int64(value_, at, value), db_, "File journal bind failed"); }
  bool row() { const int rc = sqlite3_step(value_); check(rc, db_, "File journal read failed"); return rc == SQLITE_ROW; }
  void done() { check(sqlite3_step(value_), db_, "File journal write failed"); }
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
  char* error = nullptr;
  const int rc = sqlite3_exec(db, text, nullptr, nullptr, &error);
  const std::string detail=error?error:"";
  sqlite3_free(error);
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
  if(has_unknown_fields(r) || r.format_version()!=1 || r.workspace_id().empty() ||
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
       r.process_scope().launch_id().size()!=16 || r.process_scope().scope_name().empty() ||
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
  a.clear_drained_process_scope(); b.clear_drained_process_scope();
  a.clear_state(); a.clear_bytes_progress(); a.clear_entries_progress();
  a.clear_terminal_receipt(); a.clear_updated_at_unix_ms(); a.clear_prepared_result();
  b.clear_state(); b.clear_bytes_progress(); b.clear_entries_progress();
  b.clear_terminal_receipt(); b.clear_updated_at_unix_ms(); b.clear_prepared_result();
  return a.SerializeAsString()==b.SerializeAsString();
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
struct FileOperationJournal::Impl {
  using Task = std::function<void(sqlite3*)>;
  std::filesystem::path database;
  std::mutex mutex;
  std::condition_variable wake;
  std::deque<Task> tasks;
  bool stopping = false;
  std::thread worker;
  std::promise<void> ready;
  explicit Impl(const std::filesystem::path& state) : database(state / "file-operations" / "file-operations.db") {
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
    auto signal = ready.get_future();
    worker = std::thread([this] { run(); });
    try { signal.get(); } catch (...) { if(worker.joinable())worker.join(); throw; }
  }
  ~Impl() {
    { std::lock_guard lock(mutex); stopping = true; }
    wake.notify_one();
    if (worker.joinable()) worker.join();
  }
  void run() {
    sqlite3* db = nullptr;
    try {
      const int flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_NOMUTEX | SQLITE_OPEN_NOFOLLOW;
      int rc = sqlite3_open_v2(database.c_str(), &db, flags, nullptr);
      check(rc, db, "Cannot open file operation journal");
      if (chmod(database.c_str(), 0600) != 0) throw std::runtime_error("Cannot protect file operation journal database");
      sqlite3_busy_timeout(db, 5000);
      sql(db, "PRAGMA page_size=4096; PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; PRAGMA trusted_schema=OFF; PRAGMA max_page_count=16384");
      { Statement page(db,"PRAGMA page_size"); if(!page.row() || sqlite3_column_int(page.get(),0)!=4096) throw std::runtime_error("File journal page size is not 4 KiB"); }
      { Statement cap(db,"PRAGMA max_page_count"); if(!cap.row() || sqlite3_column_int(cap.get(),0)>kMaxPages) throw std::runtime_error("File journal size cap is unavailable"); }
      int version=0;
      { Statement q(db, "PRAGMA user_version");
        if (!q.row()) throw std::runtime_error("File journal version is unavailable");
        version=sqlite3_column_int(q.get(),0);
      }
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
        if(version!=7) throw std::runtime_error("Unsupported file operation journal format");
      { Statement q(db, "PRAGMA quick_check"); if (!q.row() || q.string(0) != "ok") throw std::runtime_error("File operation journal integrity failed"); }
      ready.set_value();
    } catch (...) {
      try { ready.set_exception(std::current_exception()); } catch (...) {}
      if (db) sqlite3_close(db);
      return;
    }
    for (;;) {
      Task task;
      { std::unique_lock lock(mutex); wake.wait(lock, [this] { return stopping || !tasks.empty(); });
        if (tasks.empty() && stopping) break;
        task = std::move(tasks.front()); tasks.pop_front(); }
      try { task(db); } catch (...) { /* packaged task transports failures to its waiter */ }
    }
    sqlite3_close(db);
  }
  template<class F> auto call(F&& operation, const Observer& observer) -> std::invoke_result_t<F, sqlite3*> {
    using Result = std::invoke_result_t<F, sqlite3*>;
    auto packaged=std::make_shared<std::packaged_task<Result(sqlite3*)>>(std::forward<F>(operation));
    auto result = packaged->get_future();
    { std::lock_guard lock(mutex); if (stopping) throw std::runtime_error("File operation journal is stopping");
      tasks.emplace_back([task=std::move(packaged)](sqlite3* db) mutable { (*task)(db); }); }
    wake.notify_one();
    while (result.wait_for(std::chrono::milliseconds(10)) != std::future_status::ready) if (observer) observer();
    if constexpr (std::is_void_v<Result>) result.get(); else return result.get();
  }
};
FileOperationJournal::FileOperationJournal(const std::filesystem::path& directory) : impl_(std::make_unique<Impl>(directory)) {}
FileOperationJournal::~FileOperationJournal() = default;
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
     !identity.ParseFromString(identity_bytes) || identity.scope_name()!="spatial-git-workers" ||
     identity.boot_id()!=boot_id || identity.device()==0 || identity.inode()==0 || identity.launch_id().size()!=16)
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
         !scope.device() || !scope.inode() || scope.launch_id().size()!=16)
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
