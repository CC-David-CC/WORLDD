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
#include <fcntl.h>

namespace spatial {
namespace {
constexpr std::uint64_t kMaxRows = 65536;
constexpr int kMaxPages = 16384;  // 64 MiB at the fixed 4 KiB page size.
constexpr std::size_t kMaxOutcome = 8192;
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
  sqlite3_free(error);
  check(rc, db, "File operation journal initialization failed");
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
      { Statement q(db, "PRAGMA user_version"); if (!q.row()) throw std::runtime_error("File journal version is unavailable");
        const int version = sqlite3_column_int(q.get(), 0);
        if (version == 0) {
          sql(db, "BEGIN EXCLUSIVE");
          sql(db, "CREATE TABLE file_roots(worktree TEXT PRIMARY KEY,device INTEGER NOT NULL,inode INTEGER NOT NULL);"
                  "CREATE TABLE file_operations(workspace TEXT NOT NULL,operation BLOB NOT NULL CHECK(length(operation)=16),"
                  "principal BLOB NOT NULL CHECK(length(principal)=64),worktree TEXT NOT NULL,device INTEGER NOT NULL,inode INTEGER NOT NULL,"
                  "exact_right INTEGER NOT NULL,digest BLOB NOT NULL CHECK(length(digest)=32),engine BLOB NOT NULL CHECK(length(engine)=16),"
                  "state INTEGER NOT NULL CHECK(state BETWEEN 1 AND 3),outcome BLOB NOT NULL,PRIMARY KEY(workspace,operation));"
                  "PRAGMA user_version=1");
          sql(db, "COMMIT");
        } else if (version != 1) throw std::runtime_error("Unsupported file operation journal format"); }
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
} // namespace spatial
