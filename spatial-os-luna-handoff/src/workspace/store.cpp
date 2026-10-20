#include "workspace/store.hpp"
#include "workspace/copy_collections.hpp"
#include "workspace/copy_journal.hpp"
#include "workspace/copy_worker.hpp"
#include "workspace/file_engine.hpp"
#include "workspace/file_action_policy.hpp"
#include "workspace/file_operation_journal.hpp"
#include "copy_worker.pb.h"
#include "workspace/git_command.hpp"
#include "workspace/status.hpp"
#include "workspace/process.hpp"
#include "workspace/resources.hpp"
#include "workspace/storage_client.hpp"
#include "workspace/native_launch.hpp"
#include "workspace/authority.hpp"
#include "world/store.hpp"
#include <sqlite3.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <linux/fs.h>
#include <linux/openat2.h>
#include <unistd.h>
#include <sys/random.h>
#include <algorithm>
#include <cerrno>
#include <optional>
#include <set>
#include <fstream>
#include <iostream>
#include <future>
#include <exception>
#include <map>
#include <cmath>
#include <cstdlib>
#include <string_view>
#include <utility>

namespace spatial {
namespace {
namespace copy = workspace::copy::v1;
namespace auth = authority::v1;
void require(bool value, const char* message) { if (!value) throw std::invalid_argument(message); }
workspace::v1::StorageObject storage_object(workspace::v1::StorageObject::Kind kind, const std::filesystem::path& path) {
    workspace::v1::StorageObject object; object.set_kind(kind);
    auto id = path.filename().string();
    if (kind == workspace::v1::StorageObject::REPOSITORY) {
        require(id.ends_with(".git"), "Expected backing repository path"); id.resize(id.size()-4);
    }
    require(id.size() == 32 && id.find_first_not_of("0123456789abcdef") == std::string::npos, "Expected managed storage path identity");
    object.set_id(id); return object;
}
void valid_text(const std::string& value, std::size_t limit = 256) {
    require(!value.empty() && value.size() <= limit && value.find('\0') == std::string::npos, "Invalid Git identifier or argument");
}
void check(int result) {
    if (result != SQLITE_OK && result != SQLITE_ROW && result != SQLITE_DONE)
        throw WorldError(wire::STORAGE_ERROR, "Execution registry write or read failed");
}
struct File { int fd = -1; ~File() { if (fd >= 0) close(fd); } };
struct IndexLock {
    std::filesystem::path path;
    File file;
    explicit IndexLock(const std::filesystem::path& admin) : path(admin / "index.lock") {
        file.fd = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (file.fd < 0) throw WorldError(wire::IN_USE, "Worktree index is busy or cannot be locked");
    }
    ~IndexLock() {
        struct stat held{}, current{};
        if (fstat(file.fd, &held) == 0 && lstat(path.c_str(), &current) == 0 &&
            held.st_dev == current.st_dev && held.st_ino == current.st_ino) unlink(path.c_str());
    }
};
bool matches_bytes(const std::filesystem::path& path, const std::string& expected) {
    File file; file.fd = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    struct stat status{};
    if (file.fd < 0 || fstat(file.fd, &status) != 0 || !S_ISREG(status.st_mode)) return false;
    std::string bytes(expected.size() + 1, '\0'); std::size_t used = 0;
    while (used < bytes.size()) {
        const auto count = read(file.fd, bytes.data() + used, bytes.size() - used);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) return false;
        if (count == 0) break;
        used += static_cast<std::size_t>(count);
    }
    return used == expected.size() && bytes.substr(0, used) == expected;
}
struct Query {
    sqlite3_stmt* value = nullptr;
    Query(sqlite3* db, const char* sql) { check(sqlite3_prepare_v2(db, sql, -1, &value, nullptr)); }
    ~Query() { sqlite3_finalize(value); }
    void text(int index, const std::string& value) { check(sqlite3_bind_text(this->value, index, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT)); }
    void blob(int index, const std::string& value) { check(sqlite3_bind_blob(this->value, index, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT)); }
    int step() { const auto result = sqlite3_step(value); check(result); return result; }
    std::string text(int index) const {
        const auto* bytes = sqlite3_column_blob(value, index);
        return bytes ? std::string(static_cast<const char*>(bytes), sqlite3_column_bytes(value, index)) : "";
    }
};
void sql(sqlite3* db, const char* value) { check(sqlite3_exec(db, value, nullptr, nullptr, nullptr)); }
struct Transaction {
    sqlite3* db; bool done = false;
    explicit Transaction(sqlite3* db) : db(db) { sql(db, "BEGIN IMMEDIATE"); }
    ~Transaction() { if (!done) sqlite3_exec(db, "ROLLBACK", nullptr, nullptr, nullptr); }
    void commit() { sql(db, "COMMIT"); done = true; }
};
std::string line(std::string value) { if (!value.empty() && value.back() == '\n') value.pop_back(); return value; }
std::vector<std::string> tokens(const std::string& value) {
    std::vector<std::string> out;
    std::size_t start = 0;
    while (start < value.size()) {
        const auto end = value.find('\0', start);
        if (end == std::string::npos) throw WorldError(wire::STORAGE_ERROR, "Invalid Git machine-readable output");
        out.push_back(value.substr(start, end - start)); start = end + 1;
    }
    return out;
}
void sync_path(const std::filesystem::path& path, bool directory) {
    File file;
    file.fd = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | (directory ? O_DIRECTORY : 0));
    if (file.fd < 0 || fsync(file.fd) != 0) throw WorldError(wire::STORAGE_ERROR, "Cannot flush Git filesystem effect");
}
void sync_tree(const std::filesystem::path& path) {
    const auto status = std::filesystem::symlink_status(path);
    if (std::filesystem::is_symlink(status)) return; // Flush its parent, never its external target.
    if (std::filesystem::is_directory(status)) {
        for (const auto& entry : std::filesystem::directory_iterator(path)) sync_tree(entry.path());
        sync_path(path, true);
    } else if (std::filesystem::is_regular_file(status)) sync_path(path, false);
    else throw WorldError(wire::STORAGE_ERROR, "Unexpected filesystem entry in Git effect");
}
const char* removal_schema =
    "CREATE TABLE removals(id TEXT PRIMARY KEY,payload BLOB NOT NULL,record BLOB NOT NULL,"
    "worktree TEXT NOT NULL REFERENCES worktrees(id),retired TEXT UNIQUE NOT NULL,state TEXT NOT NULL);"
    "PRAGMA user_version=2";
const char* workspace_schema =
    "CREATE TABLE workspaces(id TEXT PRIMARY KEY,worktree TEXT UNIQUE NOT NULL REFERENCES worktrees(id),"
    "repo TEXT UNIQUE NOT NULL REFERENCES repositories(id),policy INTEGER NOT NULL CHECK(policy>=0 AND policy<=30),"
    "revision INTEGER NOT NULL CHECK(revision>0));"
    "CREATE TABLE workspace_members(workspace TEXT NOT NULL REFERENCES workspaces(id),actor TEXT NOT NULL,"
    "rights INTEGER NOT NULL CHECK(rights>=0 AND rights<=30),PRIMARY KEY(workspace,actor));"
    "PRAGMA user_version=3";
const char* process_schema =
    "CREATE TABLE processes(id TEXT PRIMARY KEY,actor TEXT NOT NULL,operation TEXT NOT NULL,payload BLOB NOT NULL,"
    "workspace TEXT NOT NULL REFERENCES workspaces(id),rights INTEGER NOT NULL,state TEXT NOT NULL,exit_code INTEGER,"
    "UNIQUE(actor,operation));PRAGMA user_version=4";
unsigned rights_mask(const google::protobuf::RepeatedField<int>& rights) {
    unsigned mask = 0;
    for (const auto value : rights) {
        require(value >= wire::WORKSPACE_SELECT && value <= wire::PROCESS_LAUNCH, "Unknown workspace right");
        const unsigned bit = 1u << value;
        require(!(mask & bit), "Duplicate workspace right"); mask |= bit;
    }
    return mask;
}
}

namespace {
using FileActionRequest = workspace::v1::FileActionRequest;
using FileActionReply = workspace::v1::FileActionReply;
using FileRight = authority::v1::Right;
class StoreFileAuthority final : public FileAuthority {
 public:
  explicit StoreFileAuthority(ExecutionStore& store):store_(store){}
  workspace::v1::ScopedAuthorityCheckReply check_fresh(
      const workspace::v1::ScopedAuthorityCheckRequest& request) override {
    return store_.scoped_authority_check(request);
  }
 private: ExecutionStore& store_;
};
FileIoResult file_io_failure(int error) { FileIoResult result; result.value=-1; result.error=error; return result; }
class StoreFileEffectExecutor final : public FileEffectExecutor {
 public:
  StoreFileEffectExecutor(ExecutionStore& store, FileOperationJournal& journal,
      std::function<void()> observer)
      :store_(store),journal_(journal),observer_(std::move(observer)){}
  void set_operation(const FileActionRequest& request, FileOperationBinding binding) {
    request_=&request; binding_=std::move(binding); intent_started_=false; effect_started_=false; journal_exhausted_=false;
  }
  FileIoResult run_step(const FileActionRequest& request, FileRight right,
      bool effectful, Step step) override {
    if(request_!=&request || !request_)return file_io_failure(EINVAL);
    workspace::v1::ScopedAuthorityCheckRequest check;
    check.set_workspace(request.workspace_id()); *check.mutable_principal()=request.principal(); check.set_right(right);
    for(const auto& component:request.authority_components())check.add_components(component);
    try {
      const auto decision=store_.scoped_authority_check(check);
      if(!decision.allowed())return file_io_failure(EACCES);
    } catch (...) { return file_io_failure(EIO); }
    if(effectful && !intent_started_) {
      try { journal_.begin(binding_,observer_); intent_started_=true; }
      catch(const std::length_error&) { journal_exhausted_=true; return file_io_failure(ENOSPC); }
      catch(...) { return file_io_failure(EIO); }
      // Journal I/O pumps issuer events. Recheck after durability and before dispatch.
      try {
        const auto after_intent=store_.scoped_authority_check(check);
        if(!after_intent.allowed()) return file_io_failure(EACCES);
      } catch(...) { return file_io_failure(EIO); }
    }
    std::future<FileIoResult> future;
    try { future=std::async(std::launch::async,std::move(step)); }
    catch(...) { return file_io_failure(EIO); }
    auto result=wait_file_io_task(future,observer_,effectful);
    if(result.effect_may_have_occurred)effect_started_=true;
    return result;
  }
  bool intent_started() const { return intent_started_; }
  bool effect_started() const { return effect_started_; }
  bool exhausted() const { return journal_exhausted_; }
  const FileOperationBinding& binding() const { return binding_; }
 private:
  ExecutionStore& store_; FileOperationJournal& journal_; FileOperationBinding binding_;
  const FileActionRequest* request_=nullptr;
  std::function<void()> observer_; bool intent_started_=false,effect_started_=false,journal_exhausted_=false;
};
authority::v1::Right file_right(const FileActionRequest& request) {
  using A=FileActionRequest::ActionCase;
  switch(request.action_case()) {
  case A::kBrowse: return authority::v1::FILE_BROWSE;
  case A::kRead: return authority::v1::FILE_READ;
  case A::kCreate: return authority::v1::FILE_CREATE;
  case A::kEdit: return authority::v1::FILE_EDIT;
  case A::kRename: return authority::v1::FILE_RENAME;
  case A::kMove: return authority::v1::FILE_MOVE;
  case A::kTrash: return authority::v1::FILE_TRASH;
  case A::kRestore: return authority::v1::FILE_RESTORE;
  case A::kPermanentDelete: return authority::v1::FILE_PERMANENT_DELETE;
  case A::kCopyJob: return authority::v1::FILE_COPY;
  case A::kTrashInspect: return authority::v1::FILE_BROWSE;
  default: return authority::v1::RIGHT_UNSPECIFIED;
  }
}
class FileRootStale final : public std::runtime_error {
 public:
  explicit FileRootStale(const char* source)
      : std::runtime_error("Registered project root binding is stale"), source(source) {}
  const char* source;
};
FileActionReply file_action_error(const std::string& id,
    workspace::v1::FileActionError::Code code,const char* detail,bool recovery=false) {
  FileActionReply reply;reply.set_protocol_version(1);reply.set_operation_id(id);
  auto* error=reply.mutable_error();error->set_code(code);error->set_detail(detail);
  if(recovery && id.size()==16)error->set_recovery_operation_id(id);
  return reply;
}
#if defined(SPATIAL_TEST_FILE_ACTION_DIAGNOSTICS)
std::string diagnostic_hex(std::string_view value) {
  constexpr char digits[] = "0123456789abcdef";
  std::string result;
  result.reserve(value.size() * 2);
  for (const unsigned char byte : value) {
    result.push_back(digits[byte >> 4]);
    result.push_back(digits[byte & 15]);
  }
  return result;
}
void record_file_action_diagnostic(const FileActionReply& reply, std::string_view source) noexcept {
  try {
    if (!reply.has_error() || reply.error().code() != workspace::v1::FileActionError::STALE_HANDLE ||
        source.empty() || source.size() > 96 || reply.error().detail().size() > 256 ||
        reply.operation_id().size() != 16)
      return;
    for (const char c : source)
      if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-'))
        return;
    const char* path = std::getenv("SPATIAL_SECURITY_EVIDENCE");
    if (!path || path[0] != '/')
      return;
    File directory;
    directory.fd = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat directory_stat {};
    if (directory.fd < 0 || fstat(directory.fd, &directory_stat) != 0 ||
        !S_ISDIR(directory_stat.st_mode) || directory_stat.st_uid != geteuid() ||
        (directory_stat.st_mode & 077) != 0)
      return;
    File output;
    output.fd = openat(directory.fd, "broker-file-action-private.log",
        O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    struct stat output_stat {};
    if (output.fd < 0 || fstat(output.fd, &output_stat) != 0 ||
        !S_ISREG(output_stat.st_mode) || output_stat.st_uid != geteuid() ||
        output_stat.st_nlink != 1 || (output_stat.st_mode & 077) != 0)
      return;
    if (flock(output.fd, LOCK_EX) != 0)
      return;
    if (fstat(output.fd, &output_stat) != 0 || output_stat.st_size < 0 ||
        output_stat.st_size > 65536)
      return;
    const auto detail = reply.error().detail();
    std::string line = "v=1 code=STALE_HANDLE source=";
    line.append(source);
    line.append(" operation_id=");
    line.append(diagnostic_hex(reply.operation_id()));
    line.append(" detail_hex=");
    line.append(diagnostic_hex(detail));
    line.push_back('\n');
    if (line.size() > 768 || output_stat.st_size + static_cast<off_t>(line.size()) > 65536)
      return;
    std::size_t written = 0;
    while (written < line.size()) {
      const auto count = write(output.fd, line.data() + written, line.size() - written);
      if (count < 0 && errno == EINTR)
        continue;
      if (count <= 0)
        return;
      written += static_cast<std::size_t>(count);
    }
    (void)fsync(output.fd);
  } catch (...) {
    // Test diagnostics never change the file-action result.
  }
}
#else
void record_file_action_diagnostic(const FileActionReply&, std::string_view) noexcept {}
#endif
const char* file_engine_stale_source(const std::string& detail) {
  if (detail == "file handle is stale") return "file_engine.handle_missing";
  if (detail == "file handle expired") return "file_engine.handle_expired";
  if (detail == "directory handle is stale") return "file_engine.directory_generation_mismatch";
  if (detail == "browse cursor is stale") return "file_engine.cursor_missing";
  if (detail == "directory changed during browse") return "file_engine.cursor_directory_changed";
  if (detail == "file changed during read") return "file_engine.file_generation_mismatch";
  if (detail == "trash object changed") return "file_engine.trash_identity_changed";
  return "file_engine.stale_reply_other";
}
} // namespace
struct ExecutionStore::Impl {
    std::filesystem::path state;
    File state_directory;
    const bool fixture_faults;
    bool exit_after_worktree = false;
    File lock;
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    std::unique_ptr<CopyJournal> copy_journal;
    std::map<std::string, std::unique_ptr<ApplicationProcess>> jobs;
    struct CopyTask {
        std::string id,requester;
        workspace::v1::WorkspaceCopyAuthorizationBinding binding;
        std::vector<std::string> applications;
        std::unique_ptr<CopyTreeJob> worker;
        bool lease_started=false,lease_recorded=false,released=false;
        int snapshot_index=0,stage_index=0;
        std::uint64_t staged_bytes=0;std::uint32_t staged_entries=0;
        bool worker_finalizing=false;
    };
    std::unique_ptr<CopyTask> copy_task;
    std::map<std::string,std::string> copy_barriers;
    std::set<std::string> launching_workspaces;
    bool advancing_copy=false;
    bool scanned_copy_recovery=false;
    WorkspaceAuthority* authority = nullptr;
    ExecutionStore* owner = nullptr;
    NativeLaunch* native = nullptr;
    WorkspaceResources* resources = nullptr;
    StorageClient* storage = nullptr;
    std::function<void()> observer;
    std::unique_ptr<FileOperationJournal> file_journal;
    std::optional<PinnedFileOperationJournal> cleanup_pin;
    std::string file_engine_incarnation;
    struct FileEngineEntry {
        int root_fd = -1;
        int staging_parent_fd = -1;
        std::string staging_root_name;
        dev_t device = 0; ino_t inode = 0;
        std::string workspace, worktree, path;
        std::unique_ptr<FileAuthority> authority;
        std::unique_ptr<FileEffectExecutor> effects;
        std::unique_ptr<FileEngine> engine;
        ~FileEngineEntry() {
            if(root_fd>=0) close(root_fd);
            if(staging_parent_fd>=0) close(staging_parent_fd);
        }
    };
    std::map<std::string,std::unique_ptr<FileEngineEntry>> file_engines;
    bool file_action_dispatch_active = false;
    bool copy_authority_reconcile_pending = false;
    std::exception_ptr authority_reconciliation_failure;
    std::string copy_expiry_cursor;
    bool git_parent_recovery_complete = false;
    bool git_effect_recovery_pending = false;
    FileEngineEntry& file_engine(const wire::WorkspaceReply& workspace,
                                 const wire::WorktreeReply& registered) {
        const auto id=registered.id();
        if(invalid_file_worktrees.contains(id))
            throw FileRootStale("store.cached_root_previously_invalidated");
        const auto managed_path=managed(registered.path(),"worktrees");
        const auto path=managed_path.string();
        const auto path_component=managed_path.filename().string();
        const auto found=file_engines.find(id);
        struct stat current{};
        if(found!=file_engines.end()) {
            auto& cached=*found->second;
            if(cached.workspace!=workspace.id() || cached.worktree!=id || cached.path!=path ||
               fstat(cached.root_fd,&current)!=0 ||
               static_cast<std::uint64_t>(current.st_dev)!=static_cast<std::uint64_t>(cached.device) ||
               static_cast<std::uint64_t>(current.st_ino)!=static_cast<std::uint64_t>(cached.inode)) {
                invalid_file_worktrees.insert(id);
                throw FileRootStale("store.cached_root_descriptor_changed");
            }
            struct stat named{};
            if(lstat(path.c_str(),&named)!=0 || !S_ISDIR(named.st_mode) || S_ISLNK(named.st_mode) ||
               named.st_dev!=cached.device || named.st_ino!=cached.inode) {
                invalid_file_worktrees.insert(id);
                throw FileRootStale("store.cached_root_path_changed");
            }
            return cached;
        }
        if(file_engines.size()>=64)
            throw WorldError(wire::RESOURCE_EXHAUSTED,"Workspace file-engine cache reached its 64-root bound");
        File root_parent; root_parent.fd=open((state/"worktrees").c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        if(root_parent.fd<0)throw FileRootStale("store.worktrees_parent_open_failed");
        open_how how{}; how.flags=O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW;
        how.resolve=RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS|RESOLVE_NO_XDEV;
        File root; root.fd=static_cast<int>(syscall(SYS_openat2,root_parent.fd,path_component.c_str(),&how,sizeof(how)));
        if(root.fd<0 || fstat(root.fd,&current)!=0 || !S_ISDIR(current.st_mode))
            throw FileRootStale("store.registered_root_open_failed");
        if(storage) {
            workspace::v1::StorageOwnerRequest inspection;
            *inspection.mutable_inspect()->mutable_object()=storage_object(workspace::v1::StorageObject::PROJECT,path);
            storage->call(std::move(inspection));
        }
        if(!file_journal->bind_root(id,static_cast<std::uint64_t>(current.st_dev),static_cast<std::uint64_t>(current.st_ino),observer))
            throw FileRootStale("store.journal_root_identity_changed");
        auto entry=std::make_unique<FileEngineEntry>();
        entry->root_fd=root.fd; root.fd=-1;
        entry->device=current.st_dev; entry->inode=current.st_ino;
        entry->workspace=workspace.id(); entry->worktree=id; entry->path=path;
        // Keep staging inside the registered PROJECT subvolume. Btrfs atomic
        // rename cannot publish across the parent worktrees subvolume boundary.
        // ApplicationProcess mounts a read-only tmpfs over this exact path.
        if(path_component.size()!=32 || path_component.find_first_not_of("0123456789abcdef")!=std::string::npos)
            throw FileRootStale("store.invalid_managed_path_for_copy_staging");
        const std::string legacy_name=".spatial-copy-staging-"+path_component;
        struct stat legacy{};
        if(fstatat(root_parent.fd,legacy_name.c_str(),&legacy,AT_SYMLINK_NOFOLLOW)==0 || errno!=ENOENT)
            throw WorldError(wire::UNAVAILABLE,"Legacy sibling copy staging requires explicit recovery");
        entry->staging_root_name=".spatial-copy-staging";
        entry->staging_parent_fd=fcntl(entry->root_fd,F_DUPFD_CLOEXEC,3);
        if(entry->staging_parent_fd<0)
            throw FileRootStale("store.copy_staging_parent_open_failed");
        if(mkdirat(entry->staging_parent_fd,entry->staging_root_name.c_str(),0700)!=0 && errno!=EEXIST)
            throw FileRootStale("store.copy_staging_root_create_failed");
        struct stat staging_named{};
        if(fstatat(entry->staging_parent_fd,entry->staging_root_name.c_str(),&staging_named,AT_SYMLINK_NOFOLLOW)!=0 ||
           !S_ISDIR(staging_named.st_mode) || S_ISLNK(staging_named.st_mode) ||
           staging_named.st_uid!=geteuid() || (staging_named.st_mode&0777)!=0700 ||
           staging_named.st_dev!=current.st_dev || fsync(entry->staging_parent_fd)!=0)
            throw FileRootStale("store.copy_staging_root_identity_invalid");
        entry->authority=std::make_unique<StoreFileAuthority>(*owner);
        entry->effects=std::make_unique<StoreFileEffectExecutor>(*owner,*file_journal,observer);
        entry->engine=std::make_unique<FileEngine>(entry->root_fd,workspace.id(),std::vector<std::string>{id},
            *entry->authority,[] { return std::chrono::steady_clock::now(); },entry->effects.get(),resources,
            entry->staging_parent_fd,entry->staging_root_name,file_journal.get(),observer,
            cleanup_pin?&*cleanup_pin:nullptr);
        return *file_engines.emplace(id,std::move(entry)).first->second;
    }
    void ensure_cleanup_pin() {
        if(cleanup_pin) return;
        const int state_fd=owner->duplicate_state_directory_fd();
        if(state_fd<0) throw WorldError(wire::UNAVAILABLE,"Cannot pin canonical execution state directory");
        try { cleanup_pin.emplace(FileOperationJournal::pin_existing(state_fd)); }
        catch(...) { close(state_fd); throw; }
        close(state_fd);
    }
    std::optional<workspace::v1::FileCopyScopeCleanupOutcome> validate_cleanup_disposition(
        const FileOperationJournal::CopyJobRecord& record) {
        if(!file_journal->scope_cleanup_outcome(record.workspace_id(),record.job_id(),observer)) return {};
        ensure_cleanup_pin();
        auto parents=file_journal->retained_git_worker_parents(observer);
        const auto active=file_journal->find_git_worker_parent(observer);
        if(active.state!=GitWorkerParentRecord::State::absent) parents.push_back(active);
        const auto parent=std::find_if(parents.begin(),parents.end(),[&](const auto& candidate) {
            return candidate.state==GitWorkerParentRecord::State::bound &&
                candidate.root_device==record.broker_root_device() &&
                candidate.root_inode==record.broker_root_inode() &&
                candidate.boot_id==record.broker_root_boot_id() &&
                candidate.identity_bytes==record.process_scope_parent().SerializeAsString();
        });
        return file_journal->validated_scope_cleanup_outcome(record,
            parent==parents.end()?nullptr:&*parent,*cleanup_pin,observer);
    }
    void recover_git_worker_parent_before_dispatch() {
        if(!resources || git_parent_recovery_complete) return;
        const auto active=file_journal->find_git_worker_parent(observer);
        auto parents=file_journal->retained_git_worker_parents(observer);
        if(active.state!=GitWorkerParentRecord::State::absent) parents.push_back(active);
        const auto records=file_journal->recovery_copy_jobs_all(observer);
        ensure_cleanup_pin();
        const auto current_root=resources->broker_root_path();
        const auto current_identity=resources->broker_root_identity();
        std::ifstream boot_file("/proc/sys/kernel/random/boot_id");std::string boot;std::getline(boot_file,boot);
        auto matches=[](const auto& parent,const auto& record) {
            return parent.state==GitWorkerParentRecord::State::bound &&
                parent.root_device==record.broker_root_device() && parent.root_inode==record.broker_root_inode() &&
                parent.boot_id==record.broker_root_boot_id() &&
                parent.identity_bytes==record.process_scope_parent().SerializeAsString();
        };
        // Validate all generation paths before constructing any engine. Engines can
        // recover several generations in one workspace.
        std::set<std::string> unsafe_parents;
        for(const auto& parent:parents) {
            const std::filesystem::path path(parent.root_path);
            struct stat info{};
            const bool exists=lstat(path.c_str(),&info)==0;
            const int path_errno=exists?0:errno;
            const bool unreadable=!exists && path_errno!=ENOENT;
            const char* unsafe_reason=nullptr;
            if((!path.is_absolute() && (unsafe_reason="stored-path-not-absolute",true)) ||
               (path.lexically_normal()!=path && (unsafe_reason="stored-path-not-normalized",true)) ||
               (path.parent_path()!=current_root.parent_path() && (unsafe_reason="stored-parent-mismatch",true)) ||
               (unreadable && (unsafe_reason="stored-path-lstat-error",true)) ||
               (parent.boot_id==boot && exists &&
                 ((static_cast<std::uint64_t>(info.st_dev)!=parent.root_device &&
                   (unsafe_reason="stored-device-mismatch",true)) ||
                  (static_cast<std::uint64_t>(info.st_ino)!=parent.root_inode &&
                   (unsafe_reason="stored-inode-mismatch",true))))) {
                bool affected=false;
                for(const auto& record:records) if(matches(parent,record)) {
                    if(record.state()!=workspace::v1::FILE_COPY_JOB_JOURNAL_UNCERTAIN)
                        throw WorldError(wire::UNAVAILABLE,"Active copy parent identity cannot be verified");
                    invalid_file_worktrees.insert(record.worktree_id());affected=true;
                }
                if(!affected) {
                    try {
                        auto escaped=[](std::string_view value) {
                            static constexpr char hex[]="0123456789abcdef";
                            std::string result;
                            result.reserve(value.size());
                            for(const unsigned char byte:value) {
                                if(byte>=0x20 && byte<=0x7e && byte!='\\' && byte!='"') result.push_back(static_cast<char>(byte));
                                else { result.append("\\x");result.push_back(hex[byte>>4]);result.push_back(hex[byte&0x0f]); }
                            }
                            return result;
                        };
                        const auto actual_parent=path.parent_path();
                        std::cerr<<"workspaced: retained Git worker parent rejected reason="
                            <<(unsafe_reason?unsafe_reason:"unknown")
                            <<" stored_path=\""<<escaped(parent.root_path)<<"\""
                            <<" expected_parent=\""<<escaped(current_root.parent_path().string())<<"\""
                            <<" actual_parent=\""<<escaped(actual_parent.string())<<"\""
                            <<" absolute="<<path.is_absolute()
                            <<" normalized="<<(path.lexically_normal()==path)
                            <<" exists="<<exists
                            <<" path_errno="<<path_errno
                            <<" stored_boot_id=\""<<escaped(parent.boot_id)<<"\""
                            <<" current_boot_id=\""<<escaped(boot)<<"\""
                            <<" stored_device="<<parent.root_device
                            <<" stored_inode="<<parent.root_inode
                            <<" current_device="<<current_identity.first
                            <<" current_inode="<<current_identity.second;
                        if(exists) std::cerr<<" lstat_device="<<static_cast<std::uint64_t>(info.st_dev)
                            <<" lstat_inode="<<static_cast<std::uint64_t>(info.st_ino);
                        std::cerr<<'\n';
                    } catch(...) {
                        // Diagnostics must not replace the existing fail-closed result.
                    }
                    throw WorldError(wire::UNAVAILABLE,"Git worker parent identity cannot be verified");
                }
                unsafe_parents.insert(parent.identity_bytes);
            }
        }
        for(const auto& record:records) {
            if(invalid_file_worktrees.contains(record.worktree_id())) continue;
            const auto parent=std::find_if(parents.begin(),parents.end(),[&](const auto& p){return matches(p,record);});
            try {
                if(!cleanup_pin) throw std::runtime_error("Current cleanup journal binding is unavailable");
                const auto disposition=file_journal->validated_scope_cleanup_outcome(record,
                    parent==parents.end()?nullptr:&*parent,*cleanup_pin,observer);
                if(disposition && disposition->kind()!=workspace::v1::FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED) {
                    invalid_file_worktrees.insert(record.worktree_id());continue;
                }
                const bool reconciled_reservation=disposition &&
                    disposition->kind()==workspace::v1::FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED;
                if(parent==parents.end() && !reconciled_reservation) {
                    if(record.state()!=workspace::v1::FILE_COPY_JOB_JOURNAL_UNCERTAIN)
                        throw WorldError(wire::UNAVAILABLE,"Copy job has no durable Git parent generation");
                    invalid_file_worktrees.insert(record.worktree_id());continue;
                }
                const auto workspace=owner->workspace_get(record.workspace_id());
                if(workspace.worktree()!=record.worktree_id())
                    throw std::runtime_error("Copy workspace binding changed");
                Query q(db.get(),"SELECT path,state FROM worktrees WHERE id=?");q.text(1,record.worktree_id());
                if(!q.step() || q.text(1)!="ready") throw std::runtime_error("Copy worktree unavailable");
                wire::WorktreeReply verified;verified.set_id(record.worktree_id());verified.set_path(q.text(0));
                (void)file_engine(workspace,verified);
            } catch(...) {
                const auto durable=file_journal->find_copy_job(record.workspace_id(),record.job_id(),observer);
                if(!durable || durable->state()!=workspace::v1::FILE_COPY_JOB_JOURNAL_UNCERTAIN) throw;
                invalid_file_worktrees.insert(record.worktree_id());
            }
        }
        // Even quarantined files must not leave an exact live worker running.
        // Cleanup failure remains uncertainty; it never clears the saved evidence.
        for(const auto& record:records) {
            if(!invalid_file_worktrees.contains(record.worktree_id()) || record.broker_root_boot_id()!=boot) continue;
            const auto parent=std::find_if(parents.begin(),parents.end(),[&](const auto& p){return matches(p,record);});
            if(parent==parents.end() || unsafe_parents.contains(parent->identity_bytes)) continue;
            try {
                const auto path=std::filesystem::path(parent->root_path)/"spatial-git-workers";
                if(record.has_process_scope())
                    (void)supervisor::ProcessScope::stop_recovered(path,
                        record.process_scope_parent().device(),record.process_scope_parent().inode(),record.process_scope());
                else
                    supervisor::ProcessScope::remove_reserved_empty(path,record.process_scope_parent(),record.reserved_process_scope_name());
            } catch(...) {}
        }
        if(active.state!=GitWorkerParentRecord::State::absent) {
            const auto reconciled=file_journal->recovery_copy_jobs_all(observer);
            const bool uncertain=std::any_of(reconciled.begin(),reconciled.end(),[&](const auto& record) {
                return matches(active,record) && record.state()==workspace::v1::FILE_COPY_JOB_JOURNAL_UNCERTAIN;
            });
            if(uncertain) {
                file_journal->retain_git_worker_parent(active,observer);
            } else {
                struct stat prior{};
                const bool prior_exists=lstat(active.root_path.c_str(),&prior)==0;
                if(!prior_exists && errno!=ENOENT)
                    throw WorldError(wire::UNAVAILABLE,"Cannot inspect Git worker root before retirement");
                if(active.boot_id==boot && prior_exists) {
                    if(active.state==GitWorkerParentRecord::State::reserved)
                        supervisor::ProcessScope::remove_reserved_git_parent(active.root_path,active.root_device,
                            active.root_inode,active.boot_id,"spatial-git-workers");
                    else {
                        supervisor::v1::ProcessScopeIdentity identity;
                        if(!identity.ParseFromString(active.identity_bytes))
                            throw WorldError(wire::UNAVAILABLE,"Git parent identity is malformed");
                        supervisor::ProcessScope::remove_recovered_git_parent(active.root_path,
                            active.root_device,active.root_inode,identity);
                    }
                }
                file_journal->clear_git_worker_parent(active,observer);
            }
        } else if(parents.empty()) {
            supervisor::ProcessScope::remove_unjournaled_empty_git_parent(current_root,current_identity.first,current_identity.second);
        }
        git_parent_recovery_complete=true;
    }
    std::set<std::string> invalid_file_worktrees;
    void validate_state_mapping(const workspace::v1::ConfiguredStateMapping& mapping) {
        copy_collections::mapping(mapping);
        std::map<std::string,std::pair<std::string,std::string>> expected;
        {Query q(db.get(),"SELECT key,state_id,contract FROM configured_application_states WHERE workspace=?");
         q.text(1,mapping.workspace_id());while(q.step()==SQLITE_ROW)expected.emplace(q.text(0),std::make_pair(q.text(1),q.text(2)));}
        for(const auto& state:mapping.entries()) {
            const auto found=expected.find(state.key());
            require(found!=expected.end() && found->second.first==state.state_object().id() &&
                    found->second.second==state.contract().SerializeAsString(),
                    "Owner state mapping differs from actual configured application storage");
            expected.erase(found);
        }
        require(expected.empty(),"Owner state mapping omitted an execution-bound configured state");
    }
    void filesystem_effect(std::function<void()> effect) {
        if (!observer) { effect(); return; }
        // Only filesystem I/O runs here. SQLite and authority remain on the broker thread.
        // The future joins before handles or paths can leave their owning operation.
        auto pending = std::async(std::launch::async, std::move(effect));
        while (pending.wait_for(std::chrono::milliseconds(10)) != std::future_status::ready) observer();
        pending.get(); observer();
    }
    void sync_path(const std::filesystem::path& path, bool directory) {
        filesystem_effect([path, directory] { spatial::sync_path(path, directory); });
    }
    void sync_tree(const std::filesystem::path& path) {
        filesystem_effect([path] { spatial::sync_tree(path); });
    }
    GitResult run_git(const std::filesystem::path& directory, const std::vector<std::string>& arguments,
                      std::vector<GitMount> mounts = {}, std::chrono::seconds timeout = std::chrono::seconds(60)) {
        if (!resources) throw WorldError(wire::UNAVAILABLE, "Administrative Git requires a configured worker resource boundary");
        if (mounts.empty()) mounts.push_back({directory, true});
        return git_command(*resources, directory, arguments, mounts, timeout, observer);
    }
    std::string checked(const std::filesystem::path& directory, const std::vector<std::string>& arguments,
                        std::vector<GitMount> mounts = {}, std::chrono::seconds timeout = std::chrono::seconds(60)) {
        const auto result = run_git(directory, arguments, std::move(mounts), timeout);
        if (result.status != 0) throw WorldError(wire::STORAGE_ERROR, "Git operation failed; no success acknowledged");
        return result.output;
    }
    explicit Impl(const std::filesystem::path& directory, unsigned migrate_from, bool enable_faults, StorageClient* owner,
                  WorkspaceResources* resource_owner)
        : fixture_faults(enable_faults), resources(resource_owner), storage(owner) {
        require(!migrate_from || !fixture_faults, "Migration cannot enable fixture faults");
        require(directory.is_absolute() && !std::filesystem::is_symlink(directory), "Execution state requires an absolute nonsymlink directory");
        if (migrate_from) require(std::filesystem::is_regular_file(directory / "execution.db"), "Migration requires an existing execution database");
        std::filesystem::create_directories(directory);
        state = std::filesystem::canonical(directory);
        lock.fd = open((state / "execution.lock").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (lock.fd < 0 || flock(lock.fd, LOCK_EX | LOCK_NB) != 0)
            throw WorldError(wire::IN_USE, "Execution state already has an authority or cannot be locked");
        state_directory.fd = open(state.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        struct stat state_info{};
        require(state_directory.fd >= 0 && fstat(state_directory.fd, &state_info) == 0 &&
            S_ISDIR(state_info.st_mode) && state_info.st_dev != 0 && state_info.st_ino != 0,
            "Cannot retain canonical execution state directory");
        for (const auto* name : {"execution.db", "execution.db-journal", "execution.db-wal", "execution.db-shm", "repositories", "worktrees", "effects", "retired", "applications"})
            require(!std::filesystem::is_symlink(state / name), "Execution metadata must not be a symlink");
        std::filesystem::create_directories(state / "repositories");
        std::filesystem::create_directories(state / "worktrees");
        std::filesystem::create_directories(state / "effects");
        file_journal = std::make_unique<FileOperationJournal>(state);
        file_engine_incarnation.resize(16);
        std::size_t incarnation_at=0;
        while(incarnation_at<file_engine_incarnation.size()) {
            const auto got=getrandom(file_engine_incarnation.data()+incarnation_at,
                file_engine_incarnation.size()-incarnation_at,0);
            if(got<0 && errno==EINTR) continue;
            if(got<=0) throw WorldError(wire::UNAVAILABLE,"Cannot initialize file-engine incarnation");
            incarnation_at+=static_cast<std::size_t>(got);
        }
        sqlite3* connection = nullptr;
        const auto result = sqlite3_open_v2((state / "execution.db").c_str(), &connection,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_NOMUTEX, nullptr);
        db.reset(connection); check(result); check(sqlite3_busy_timeout(db.get(), 1000));
        sql(db.get(), "PRAGMA foreign_keys=ON; PRAGMA synchronous=EXTRA; PRAGMA trusted_schema=OFF");
        { Query q(db.get(), "PRAGMA journal_mode=DELETE"); q.step();
          if (q.text(0) != "delete") throw WorldError(wire::STORAGE_ERROR, "Execution journal mode unavailable"); }
        int version;
        { Query q(db.get(), "PRAGMA user_version"); q.step(); version = sqlite3_column_int(q.value, 0); }
        if (version >= 1 && version <= 3 && !migrate_from)
            throw WorldError(wire::STORAGE_ERROR, "Execution schema requires workspaced --state-dir PATH --migrate-v1, --migrate-v2, or --migrate-v3 while the broker is stopped");
        if (version == 4 && !migrate_from)
            throw WorldError(wire::STORAGE_ERROR, "Execution schema requires workspaced --state-dir PATH --migrate-v4 while the broker is stopped");
        if (version == 5 && !migrate_from)
            throw WorldError(wire::STORAGE_ERROR, "Execution schema requires workspaced --state-dir PATH --migrate-v5 while the broker is stopped");
        if(version==6 && !migrate_from)
            throw WorldError(wire::STORAGE_ERROR,"Execution schema requires workspaced --state-dir PATH --migrate-v6 while the broker is stopped");
        if(version==7 && !migrate_from)
            throw WorldError(wire::STORAGE_ERROR,"Execution schema requires workspaced --state-dir PATH --migrate-v7 while the broker is stopped");
        if (version < 0 || version > 8 || (migrate_from && (version == 0 || (version != 8 && version != static_cast<int>(migrate_from)))))
            throw WorldError(wire::STORAGE_ERROR, "Unknown execution schema; explicit migration required");
        if (version == 0) {
            Query existing(db.get(), "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"); existing.step();
            if (sqlite3_column_int(existing.value, 0) != 0) throw WorldError(wire::STORAGE_ERROR, "Refusing unknown execution database");
            Transaction transaction(db.get());
            sql(db.get(), "CREATE TABLE repositories(id TEXT PRIMARY KEY,source TEXT NOT NULL,path TEXT UNIQUE NOT NULL,state TEXT NOT NULL);"
                "CREATE TABLE worktrees(id TEXT PRIMARY KEY,repo TEXT NOT NULL REFERENCES repositories(id),branch TEXT NOT NULL,path TEXT UNIQUE NOT NULL,head TEXT NOT NULL,state TEXT NOT NULL);"
                "CREATE TABLE operations(id TEXT PRIMARY KEY,payload BLOB NOT NULL,result BLOB NOT NULL,worktree TEXT NOT NULL REFERENCES worktrees(id),state TEXT NOT NULL);");
            sql(db.get(), removal_schema);
            sql(db.get(), workspace_schema);
            sql(db.get(), process_schema);
            CopyJournal::migrate_schema(db.get());
            transaction.commit();
        }
        { Query q(db.get(), "PRAGMA quick_check"); q.step(); if (q.text(0) != "ok") throw WorldError(wire::STORAGE_ERROR, "Execution registry integrity failure"); }
        { Query q(db.get(), "PRAGMA foreign_key_check"); if (q.step() != SQLITE_DONE) throw WorldError(wire::STORAGE_ERROR, "Execution registry reference failure"); }
        if (version == 5 && migrate_from == 5) {
            Transaction transaction(db.get());
            CopyJournal::migrate_v5(db.get());
            transaction.commit();
        }
        if (version > 0 && version < 5) {
            Transaction transaction(db.get());
            if (version == 1) {
                Query tables(db.get(), "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name");
                for (const auto* expected : {"operations", "repositories", "worktrees"})
                    if (tables.step() != SQLITE_ROW || tables.text(0) != expected) throw WorldError(wire::STORAGE_ERROR, "Unexpected v1 schema");
                if (tables.step() != SQLITE_DONE) throw WorldError(wire::STORAGE_ERROR, "Unexpected v1 schema");
                Query old_operations(db.get(), "SELECT id,payload,result,worktree,state FROM operations LIMIT 0");
                Query old_worktrees(db.get(), "SELECT id,repo,branch,path,head,state FROM worktrees LIMIT 0");
                Query old_repositories(db.get(), "SELECT id,source,path,state FROM repositories LIMIT 0");
                sql(db.get(), removal_schema); sql(db.get(), workspace_schema); sql(db.get(), process_schema);
            } else if (version == 2) {
                Query old_removals(db.get(), "SELECT id,payload,record,worktree,retired,state FROM removals LIMIT 0");
                sql(db.get(), workspace_schema); sql(db.get(), process_schema);
            } else if (version == 3) {
                Query old_workspaces(db.get(), "SELECT id,worktree,repo,policy,revision FROM workspaces LIMIT 0");
                Query old_members(db.get(), "SELECT workspace,actor,rights FROM workspace_members LIMIT 0");
                sql(db.get(), process_schema);
            }
            CopyJournal::migrate_schema(db.get());
            transaction.commit();
        }
        if(version<7) {
            Transaction migration(db.get());
            sql(db.get(),"CREATE TABLE workspace_copy_policies(workspace TEXT PRIMARY KEY REFERENCES workspaces(id),binding BLOB NOT NULL)");
            if(version==6)sql(db.get(),"UPDATE copy_operations SET phase=9,error=6 WHERE phase BETWEEN 1 AND 6");
            sql(db.get(),"PRAGMA user_version=7");migration.commit();
        }
        if(version<8) {
            Transaction migration(db.get());
            sql(db.get(),"CREATE TABLE configured_application_states(workspace TEXT NOT NULL REFERENCES workspaces(id),key TEXT NOT NULL,state_id TEXT UNIQUE NOT NULL,contract BLOB NOT NULL,PRIMARY KEY(workspace,key));"
                         "CREATE TABLE configured_process_states(process TEXT PRIMARY KEY REFERENCES processes(id),workspace TEXT NOT NULL,key TEXT NOT NULL,state_id TEXT NOT NULL,FOREIGN KEY(workspace,key) REFERENCES configured_application_states(workspace,key));PRAGMA user_version=8");
            if(version>0)sql(db.get(),"UPDATE copy_operations SET phase=9,error=6 WHERE phase BETWEEN 1 AND 6");
            migration.commit();
        }
        {Query shape(db.get(),"SELECT workspace,key,state_id,contract FROM configured_application_states LIMIT 0");}
        {Query shape(db.get(),"SELECT process,workspace,key,state_id FROM configured_process_states LIMIT 0");}
        {Query shape(db.get(),"SELECT workspace,binding FROM workspace_copy_policies LIMIT 0");}
        copy_journal = std::make_unique<CopyJournal>(db.get());
        std::filesystem::create_directories(state / "retired");
        std::filesystem::create_directories(state / "applications");
        if (migrate_from) { sync_path(state, true); return; }
        sql(db.get(), "UPDATE processes SET state='uncertain' WHERE state IN ('pending','running')");
        // A partial clone has no reliable completion witness. Preserve it for explicit repair.
        sql(db.get(), "UPDATE repositories SET state='uncertain' WHERE state='pending'");
        git_effect_recovery_pending = true;
        if (!resources) reconcile_pending_git_effects();
        else { sync_path(state, true); sync_path(state.parent_path(), true); }
    }
    std::filesystem::path allocate(const char* collection, const char* suffix = "") {
        Query q(db.get(), "SELECT lower(hex(randomblob(16)))"); q.step();
        const auto path = state / collection / (q.text(0) + suffix);
        require(!std::filesystem::exists(path), "Allocated Git path already exists"); return path;
    }
    std::filesystem::path managed(const std::string& value, const char* collection,
                                  bool symlink_means_stale = false) const {
        const std::filesystem::path path(value);
        if (path.parent_path() != state / collection)
            throw WorldError(wire::STORAGE_ERROR, "Stored Git path escaped its execution registry");
        if (std::filesystem::is_symlink(path)) {
            if (symlink_means_stale)
                throw FileRootStale("store.registered_root_symlink");
            throw WorldError(wire::STORAGE_ERROR, "Stored Git path escaped its execution registry");
        }
        return path;
    }
    wire::RepositoryReply repository(const std::string& id) {
        Query q(db.get(), "SELECT path,source,state FROM repositories WHERE id=?"); q.text(1, id);
        if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Repository not registered");
        if (q.text(2) != "ready") throw WorldError(wire::STORAGE_ERROR, "Repository import is uncertain; explicit repair required");
        const auto path = managed(q.text(0), "repositories");
        if (storage) {
            workspace::v1::StorageOwnerRequest inspection;
            *inspection.mutable_inspect()->mutable_object() = storage_object(workspace::v1::StorageObject::REPOSITORY, path);
            storage->call(std::move(inspection));
        }
        // Writable repository configuration is data, not broker execution authority.
        const std::set<std::string> allowed{"core.repositoryformatversion", "core.filemode", "core.bare",
            "core.logallrefupdates", "core.ignorecase", "core.precomposeunicode", "remote.origin.url",
            "remote.origin.fetch", "extensions.objectformat", "user.name", "user.email"};
        for (const auto& entry : tokens(checked(path, {"config", "--local", "--no-includes", "--null", "--list"}, {{path, false}}))) {
            const auto end = entry.find('\n');
            if (!allowed.contains(entry.substr(0, end)))
                throw WorldError(wire::FORBIDDEN, "Repository configuration requires authority unavailable to administrative Git");
        }
        if (line(checked(path, {"rev-parse", "--is-bare-repository"}, {{path, false}})) != "true")
            throw WorldError(wire::STORAGE_ERROR, "Registered backing store is not bare");
        wire::RepositoryReply out; out.set_id(id); out.set_path(path.string()); out.set_source(q.text(1)); return out;
    }
    wire::WorktreeReply result(const std::string& id) {
        Query q(db.get(), "SELECT result FROM operations WHERE id=?"); q.text(1, id);
        if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Git operation not found");
        wire::WorktreeReply out;
        if (!out.ParseFromString(q.text(0))) throw WorldError(wire::STORAGE_ERROR, "Invalid stored Git result");
        validate_message(out); managed(out.path(), "worktrees"); return out;
    }
    std::string link_text(const std::filesystem::path& path) {
        if (!std::filesystem::is_regular_file(std::filesystem::symlink_status(path)) || std::filesystem::file_size(path) > 16384)
            throw WorldError(wire::STORAGE_ERROR, "Unexpected Git administrative link");
        std::ifstream input(path, std::ios::binary);
        std::string bytes((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
        if (!input || bytes.find('\0') != std::string::npos) throw WorldError(wire::STORAGE_ERROR, "Cannot read Git administrative link");
        return line(bytes);
    }
    wire::WorktreeReply worktree(const std::string& id) {
        valid_text(id);
        Query q(db.get(), "SELECT repo,branch,path,head,state FROM worktrees WHERE id=?"); q.text(1, id);
        if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Worktree not registered");
        if (q.text(4) == "removed") throw WorldError(wire::NOT_FOUND, "Worktree was removed");
        if (q.text(4) != "ready") throw WorldError(wire::STORAGE_ERROR, "Worktree effect is pending or uncertain");
        wire::WorktreeReply out; out.set_id(id); out.set_repo(q.text(0)); out.set_branch(q.text(1)); out.set_path(q.text(2)); out.set_head(q.text(3));
        const auto directory = managed(out.path(), "worktrees");
        if (storage) {
            workspace::v1::StorageOwnerRequest inspection;
            *inspection.mutable_inspect()->mutable_object() = storage_object(workspace::v1::StorageObject::PROJECT, directory);
            storage->call(std::move(inspection));
        }
        const auto repo = repository(out.repo());
        const auto link = link_text(directory / ".git");
        if (!link.starts_with("gitdir: ")) throw WorldError(wire::STORAGE_ERROR, "Invalid worktree administrative link");
        std::filesystem::path admin(link.substr(8));
        if (admin.is_relative()) admin = directory / admin;
        admin = admin.lexically_normal();
        const auto expected_parent = std::filesystem::path(repo.path()) / "worktrees";
        if (admin.parent_path() != expected_parent || std::filesystem::canonical(admin) != admin ||
            link_text(admin / "gitdir") != (directory / ".git").string())
            throw WorldError(wire::STORAGE_ERROR, "Worktree administrative link changed authority");
        auto common = (admin / link_text(admin / "commondir")).lexically_normal();
        // Git writes ../.. here. Lexical normalization retains a trailing separator.
        if (common.filename().empty()) common = common.parent_path();
        if (common != repo.path()) throw WorldError(wire::STORAGE_ERROR, "Worktree common directory changed authority");
        const auto actual = line(checked(directory, {"rev-parse", "--path-format=absolute", "--git-common-dir"},
            {{directory, false}, {repo.path(), false}}));
        if (actual != repo.path()) throw WorldError(wire::STORAGE_ERROR, "Git resolved another execution authority");
        return out;
    }
    bool matches(const wire::WorktreeReply& record) {
        const auto repo = repository(record.repo());
        if (!std::filesystem::is_directory(record.path())) return false;
        bool path = false, branch = false, head = false;
        for (const auto& token : tokens(checked(repo.path(), {"worktree", "list", "--porcelain", "-z"}, {{repo.path(), false}}))) {
            if (token.empty()) {
                if (path && branch && head) {
                    const auto actual = run_git(record.path(), {"rev-parse", "--path-format=absolute", "--git-common-dir"},
                        {{record.path(), false}, {repo.path(), false}});
                    return actual.status == 0 && line(actual.output) == repo.path();
                }
                path = branch = head = false;
            } else if (token == "worktree " + record.path()) path = true;
            else if (token == "branch refs/heads/" + record.branch()) branch = true;
            else if (token == "HEAD " + record.head()) head = true;
        }
        return false;
    }
    std::filesystem::path receipt_path(const wire::WorktreeReply& record) const {
        return state / "effects" / (managed(record.path(), "worktrees").filename().string() + ".ready");
    }
    bool receipt_matches(const wire::WorktreeReply& record) const {
        File file; file.fd = open(receipt_path(record).c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (file.fd < 0) return false;
        const auto expected = record.SerializeAsString();
        std::string bytes(expected.size() + 1, '\0');
        std::size_t used = 0;
        while (used < bytes.size()) {
            const auto count = read(file.fd, bytes.data() + used, bytes.size() - used);
            if (count < 0 && errno == EINTR) continue;
            if (count < 0) return false;
            if (count == 0) break;
            used += static_cast<std::size_t>(count);
        }
        return used == expected.size() && bytes.substr(0, used) == expected;
    }
    void complete_effect(const wire::WorktreeReply& record) {
        // Git and SQLite are separate effects. Flush the real effect before its ready acknowledgment.
        sync_tree(record.path()); sync_tree(repository(record.repo()).path());
        sync_path(state / "worktrees", true); sync_path(state / "repositories", true);
        File file; file.fd = open(receipt_path(record).c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (file.fd < 0) throw WorldError(wire::STORAGE_ERROR, "Cannot create Git completion receipt");
        const auto bytes = record.SerializeAsString(); std::size_t used = 0;
        while (used < bytes.size()) {
            const auto count = write(file.fd, bytes.data() + used, bytes.size() - used);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) throw WorldError(wire::STORAGE_ERROR, "Cannot write Git completion receipt");
            used += static_cast<std::size_t>(count);
        }
        filesystem_effect([fd = file.fd] {
            if (fsync(fd) != 0) throw WorldError(wire::STORAGE_ERROR, "Cannot flush Git completion receipt");
        });
        sync_path(state / "effects", true);
    }
    void finish(const std::string& id, const wire::WorktreeReply& record) {
        Transaction transaction(db.get());
        Query worktree(db.get(), "UPDATE worktrees SET state='ready' WHERE id=?"); worktree.text(1, record.id()); worktree.step();
        Query operation(db.get(), "UPDATE operations SET state='ready' WHERE id=?"); operation.text(1, id); operation.step();
        transaction.commit();
    }
    void reconcile(const std::string& id) {
        const auto record = result(id);
        if (receipt_matches(record) && matches(record)) finish(id, record);
        else {
            Transaction transaction(db.get());
            Query operation(db.get(), "UPDATE operations SET state='uncertain' WHERE id=?"); operation.text(1, id); operation.step();
            Query worktree(db.get(), "UPDATE worktrees SET state='uncertain' WHERE id=?"); worktree.text(1, record.id()); worktree.step();
            transaction.commit();
        }
    }
    wire::GitStatusReply current_status(const std::filesystem::path& path, std::vector<GitMount> additional = {}) {
        std::vector<GitMount> mounts{{path, true}};
        mounts.insert(mounts.end(), additional.begin(), additional.end());
        return parse_git_status(checked(path, {"status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all", "--ignored=matching"}, std::move(mounts)));
    }
    wire::GitStatusReply removable(const std::filesystem::path& path, std::vector<GitMount> additional = {}) {
        const auto status = current_status(path, additional);
        if (!status.clean()) throw WorldError(wire::DIRTY_WORKTREE, "Worktree contains tracked, untracked, or ignored changes");
        std::vector<GitMount> mounts{{path, true}};
        mounts.insert(mounts.end(), additional.begin(), additional.end());
        for (const auto& entry : tokens(checked(path, {"ls-files", "-v", "-z"}, mounts)))
            if (!entry.starts_with("H ")) throw WorldError(wire::DIRTY_WORKTREE, "Index flags can hide worktree changes; clear them before removal");
        for (const auto& entry : tokens(checked(path, {"ls-files", "--stage", "-z"}, std::move(mounts))))
            if (entry.starts_with("160000 ")) throw WorldError(wire::IN_USE, "Submodule worktrees require explicit retirement");
        return status;
    }
    std::string retirement_ref(const std::filesystem::path& retired) {
        return "refs/spatial/retired/" + retired.filename().string();
    }
    void removal_state(const std::string& id, const std::string& worktree, bool complete) {
        Transaction transaction(db.get());
        Query op(db.get(), "UPDATE removals SET state=? WHERE id=?"); op.text(1, complete ? "ready" : "uncertain"); op.text(2, id); op.step();
        Query wt(db.get(), "UPDATE worktrees SET state=? WHERE id=?"); wt.text(1, complete ? "removed" : "uncertain"); wt.text(2, worktree); wt.step();
        transaction.commit();
    }
    void reconcile_removal(const std::string& id) {
        Query q(db.get(), "SELECT record,retired FROM removals WHERE id=?"); q.text(1, id); q.step();
        wire::WorktreeReply record;
        if (!record.ParseFromString(q.text(0))) throw WorldError(wire::STORAGE_ERROR, "Invalid removal record");
        validate_message(record);
        const auto retired = managed(q.text(1), "retired");
        bool complete = false;
        try {
            const auto repo = repository(record.repo());
            const auto entries = tokens(checked(repo.path(), {"worktree", "list", "--porcelain", "-z"}, {{repo.path(), false}}));
            const auto pin = record.head().empty() ? "" : line(checked(repo.path(), {"rev-parse", "--verify", retirement_ref(retired)}, {{repo.path(), false}}));
            complete = std::filesystem::is_directory(retired / "data/project") && std::filesystem::is_directory(retired / "git") &&
                std::filesystem::canonical(retired / "data/project") == retired / "data/project" &&
                std::filesystem::canonical(retired / "git") == retired / "git" &&
                pin == record.head() && matches_bytes(retired / "complete", record.SerializeAsString()) &&
                std::find(entries.begin(), entries.end(), "worktree " + (retired / "staging/project").string()) == entries.end();
        } catch (const std::exception&) { complete = false; }
        removal_state(id, record.id(), complete);
    }
    void reconcile_pending_git_effects() {
        if (!git_effect_recovery_pending) return;
        std::vector<std::string> pending;
        { Query q(db.get(), "SELECT id FROM operations WHERE state='pending'"); while (q.step() == SQLITE_ROW) pending.push_back(q.text(0)); }
        for (const auto& id : pending) reconcile(id);
        pending.clear();
        { Query q(db.get(), "SELECT id FROM removals WHERE state='pending'"); while (q.step() == SQLITE_ROW) pending.push_back(q.text(0)); }
        for (const auto& id : pending) reconcile_removal(id);
        sync_path(state, true); sync_path(state.parent_path(), true);
        git_effect_recovery_pending = false;
    }
};

ExecutionStore::ExecutionStore(const std::filesystem::path& state, unsigned migrate_from, bool fixture_faults,
                               StorageClient* storage, WorkspaceResources* resources)
    : impl_(std::make_unique<Impl>(state, migrate_from, fixture_faults, storage, resources)) { impl_->owner=this; }
ExecutionStore::~ExecutionStore() = default;
CopyJournal& ExecutionStore::copy_journal() { return *impl_->copy_journal; }
const CopyJournal& ExecutionStore::copy_journal() const { return *impl_->copy_journal; }
bool ExecutionStore::fixture_faults() const { return impl_->fixture_faults; }
int ExecutionStore::duplicate_state_directory_fd() const {
    if (impl_->state_directory.fd < 0) throw std::runtime_error("Execution state directory is not pinned");
    const int duplicate = fcntl(impl_->state_directory.fd, F_DUPFD_CLOEXEC, 3);
    if (duplicate < 0) throw std::runtime_error("Cannot duplicate canonical execution state directory");
    return duplicate;
}
wire::FaultArmReply ExecutionStore::arm_fault(const wire::FaultArmRequest& request) {
    if (!fixture_faults()) throw WorldError(wire::FORBIDDEN, "Fixture faults are disabled");
    validate_message(request);
    require(request.point() != wire::FAULT_POINT_UNSPECIFIED && request.action() == wire::FAULT_EXIT &&
            !request.has_storage_error(), "Invalid Git exit fault");
    if (request.point() != wire::FAULT_GIT_AFTER_WORKTREE_BEFORE_READY)
        throw WorldError(wire::UNIMPLEMENTED, "Requested broker fault boundary is not implemented");
    if (impl_->exit_after_worktree) throw WorldError(wire::BUSY, "A Git fault is already armed");
    impl_->exit_after_worktree = true;
    wire::FaultArmReply result; result.set_armed(true); return result;
}
wire::RepositoryReply ExecutionStore::import_repository(const wire::RepositoryImportRequest& request) {
    valid_text(request.id()); valid_text(request.path(), 16384);
    require(std::filesystem::path(request.path()).is_absolute() && std::filesystem::is_directory(request.path()), "Import requires an existing absolute local repository");
    const auto source = std::filesystem::canonical(request.path());
    { Query q(impl_->db.get(), "SELECT source FROM repositories WHERE id=?"); q.text(1, request.id());
      if (q.step() == SQLITE_ROW) {
          if (q.text(0) != source.string()) throw WorldError(wire::ALREADY_EXISTS, "Repository identity already has another source");
          return impl_->repository(request.id());
      } }
    const auto source_git = impl_->run_git(source, {"rev-parse", "--absolute-git-dir"}, {{source, false}});
    require(source_git.status == 0, "Import source is not a Git repository");
    const auto alternates = std::filesystem::path(line(source_git.output)) / "objects/info/alternates";
    require(!std::filesystem::exists(alternates), "Import requires an independent source object store");
    const auto target = impl_->allocate("repositories", ".git");
    {
        Transaction transaction(impl_->db.get());
        Query q(impl_->db.get(), "INSERT INTO repositories VALUES(?,?,?,'pending')");
        q.text(1, request.id()); q.text(2, source.string()); q.text(3, target.string()); q.step(); transaction.commit();
    }
    if (impl_->storage) {
        workspace::v1::StorageOwnerRequest allocation;
        *allocation.mutable_repository_prepare()->mutable_object() = storage_object(workspace::v1::StorageObject::REPOSITORY, target);
        impl_->storage->call(std::move(allocation));
    }
    std::filesystem::create_directories(target);
    impl_->checked(target, {"clone", "--bare", "--local", "--no-hardlinks", "--template=", "--", source.string(), target.string()},
        {{source, false}, {target, true}}, std::chrono::minutes(5));
    if (line(impl_->checked(target, {"rev-parse", "--is-bare-repository"}, {{target, false}})) != "true")
        throw WorldError(wire::STORAGE_ERROR, "Import did not create a bare repository");
    impl_->sync_tree(target); impl_->sync_path(target.parent_path(), true);
    {
        Transaction transaction(impl_->db.get());
        Query q(impl_->db.get(), "UPDATE repositories SET state='ready' WHERE id=?"); q.text(1, request.id()); q.step(); transaction.commit();
    }
    return impl_->repository(request.id());
}
wire::WorktreeReply ExecutionStore::create(const wire::WorktreeCreateRequest& request) {
    valid_text(request.repo()); valid_text(request.id()); valid_text(request.operation_id());
    for(const auto& [workspace,operation]:impl_->copy_barriers) {
        (void)operation;Query source(impl_->db.get(),"SELECT repo FROM workspaces WHERE id=?");source.text(1,workspace);
        if(source.step()==SQLITE_ROW && source.text(0)==request.repo())throw WorldError(wire::BUSY,"Workspace quiescence blocks Git mutation");
    }
    valid_text(request.branch(), 1024); valid_text(request.start(), 1024);
    const auto payload = request.SerializeAsString();
    { Query q(impl_->db.get(), "SELECT id FROM removals WHERE id=?"); q.text(1, request.operation_id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::OPERATION_CONFLICT, "Operation ID belongs to worktree removal"); }
    {
        Query q(impl_->db.get(), "SELECT payload,state FROM operations WHERE id=?"); q.text(1, request.operation_id());
        if (q.step() == SQLITE_ROW) {
            if (q.text(0) != payload) throw WorldError(wire::OPERATION_CONFLICT, "Operation ID has another payload");
            if (q.text(1) != "ready") throw WorldError(wire::STORAGE_ERROR, "Git effect is pending or uncertain; explicit recovery required");
            return impl_->result(request.operation_id());
        }
    }
    const auto repo = impl_->repository(request.repo());
    { Query q(impl_->db.get(), "SELECT id FROM workspaces WHERE repo=?"); q.text(1, request.repo());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::IN_USE, "Repository execution authority belongs to a workspace"); }
    require(request.branch().front() != '-' && impl_->run_git(repo.path(), {"check-ref-format", "refs/heads/" + request.branch()}, {{repo.path(), false}}).status == 0,
            "Invalid branch name");
    const auto list = tokens(impl_->checked(repo.path(), {"worktree", "list", "--porcelain", "-z"}, {{repo.path(), false}}));
    if (std::find(list.begin(), list.end(), "branch refs/heads/" + request.branch()) != list.end())
        throw WorldError(wire::BRANCH_IN_USE, "Branch already has a linked worktree");
    if (impl_->run_git(repo.path(), {"show-ref", "--verify", "--quiet", "refs/heads/" + request.branch()}, {{repo.path(), false}}).status == 0)
        throw WorldError(wire::ALREADY_EXISTS, "Requested new branch already exists");
    { Query q(impl_->db.get(), "SELECT id FROM worktrees WHERE id=?"); q.text(1, request.id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "Worktree identity already exists"); }
    const auto start = impl_->run_git(repo.path(), {"rev-parse", "--verify", "--end-of-options", request.start() + "^{commit}"}, {{repo.path(), false}});
    require(start.status == 0, "Start does not resolve to a commit");
    const auto head = line(start.output);
    require((head.size() == 40 || head.size() == 64) && head.find_first_not_of("0123456789abcdef") == std::string::npos, "Invalid resolved commit");
    wire::WorktreeReply out; out.set_id(request.id()); out.set_repo(request.repo()); out.set_branch(request.branch());
    out.set_path(impl_->allocate("worktrees").string()); out.set_head(head);
    {
        Transaction transaction(impl_->db.get());
        Query w(impl_->db.get(), "INSERT INTO worktrees VALUES(?,?,?,?,?,'pending')");
        w.text(1, out.id()); w.text(2, out.repo()); w.text(3, out.branch()); w.text(4, out.path()); w.text(5, out.head()); w.step();
        Query op(impl_->db.get(), "INSERT INTO operations VALUES(?,?,?,?,'pending')");
        op.text(1, request.operation_id()); op.blob(2, payload); op.blob(3, out.SerializeAsString()); op.text(4, out.id()); op.step(); transaction.commit();
    }
    if (impl_->storage) {
        workspace::v1::StorageOwnerRequest allocation;
        auto* prepare = allocation.mutable_project_prepare();
        *prepare->mutable_object() = storage_object(workspace::v1::StorageObject::PROJECT, out.path());
        *prepare->mutable_repository() = storage_object(workspace::v1::StorageObject::REPOSITORY, repo.path());
        impl_->storage->call(std::move(allocation));
    }
    std::filesystem::create_directories(out.path());
    impl_->checked(repo.path(), {"worktree", "add", "-b", request.branch(), "--", out.path(), out.head()},
        {{repo.path(), true}, {out.path(), true}});
    if (!impl_->matches(out)) throw WorldError(wire::STORAGE_ERROR, "Git worktree effect does not match its intent");
    impl_->complete_effect(out);
    // The external effect and its receipt are durable. The registry is still pending.
    if (impl_->exit_after_worktree) _exit(88);
    impl_->finish(request.operation_id(), out); return out;
}
wire::GitOperationReply ExecutionStore::operation(const std::string& id) {
    valid_text(id); Query q(impl_->db.get(), "SELECT state FROM operations WHERE id=? UNION ALL SELECT state FROM removals WHERE id=?"); q.text(1, id); q.text(2, id);
    if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Git operation not found");
    wire::GitOperationReply out; out.set_state(q.text(0)); return out;
}
wire::GitStatusReply ExecutionStore::status(const std::string& id) {
    const auto worktree = impl_->worktree(id);
    const auto repo = impl_->repository(worktree.repo());
    return impl_->current_status(worktree.path(), {{repo.path(), false}});
}
wire::WorktreeRemoveReply ExecutionStore::remove(const wire::WorktreeRemoveRequest& request) {
    valid_text(request.id()); valid_text(request.operation_id());
    for(const auto& [workspace,operation]:impl_->copy_barriers) {
        (void)operation;Query source(impl_->db.get(),"SELECT worktree FROM workspaces WHERE id=?");source.text(1,workspace);
        if(source.step()==SQLITE_ROW && source.text(0)==request.id())throw WorldError(wire::BUSY,"Workspace quiescence blocks Git mutation");
    }
    const auto payload = request.SerializeAsString();
    wire::WorktreeRemoveReply out; out.set_removed(true);
    { Query q(impl_->db.get(), "SELECT id FROM operations WHERE id=?"); q.text(1, request.operation_id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::OPERATION_CONFLICT, "Operation ID belongs to worktree creation"); }
    { Query q(impl_->db.get(), "SELECT payload,state FROM removals WHERE id=?"); q.text(1, request.operation_id());
      if (q.step() == SQLITE_ROW) {
          if (q.text(0) != payload) throw WorldError(wire::OPERATION_CONFLICT, "Operation ID has another payload");
          if (q.text(1) != "ready") throw WorldError(wire::STORAGE_ERROR, "Removal is pending or uncertain; inspect retained data");
          return out;
      } }
    { Query q(impl_->db.get(), "SELECT id FROM workspaces WHERE worktree=?"); q.text(1, request.id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::IN_USE, "Worktree belongs to a workspace"); }
    auto record = impl_->worktree(request.id());
    const auto repo = impl_->repository(record.repo());
    const auto admin = std::filesystem::path(line(impl_->checked(record.path(), {"rev-parse", "--absolute-git-dir"},
        {{record.path(), false}, {repo.path(), false}})));
    const auto admin_root = std::filesystem::canonical(std::filesystem::path(repo.path()) / "worktrees");
    if (std::filesystem::canonical(admin).parent_path() != admin_root)
        throw WorldError(wire::STORAGE_ERROR, "Git worktree metadata is outside its managed repository");
    if (std::filesystem::exists(admin / "locked")) throw WorldError(wire::IN_USE, "Worktree is locked");
    IndexLock index_lock(admin);
    const auto status = impl_->removable(record.path(), {{repo.path(), false}}); record.set_head(status.head()); record.set_branch(status.branch());
    const auto retired = impl_->allocate("retired");
    {
        Transaction transaction(impl_->db.get());
        Query op(impl_->db.get(), "INSERT INTO removals VALUES(?,?,?,?,?,'pending')");
        op.text(1, request.operation_id()); op.blob(2, payload); op.blob(3, record.SerializeAsString()); op.text(4, record.id()); op.text(5, retired.string()); op.step();
        Query wt(impl_->db.get(), "UPDATE worktrees SET state='removing' WHERE id=?"); wt.text(1, record.id()); wt.step(); transaction.commit();
    }
    if (impl_->storage) {
        workspace::v1::StorageOwnerRequest allocation;
        auto* prepare = allocation.mutable_retirement_prepare();
        *prepare->mutable_object() = storage_object(workspace::v1::StorageObject::RETIRED, retired);
        *prepare->mutable_repository() = storage_object(workspace::v1::StorageObject::REPOSITORY, repo.path());
        *prepare->mutable_project() = storage_object(workspace::v1::StorageObject::PROJECT, record.path());
        impl_->storage->call(std::move(allocation));
    }
    // Retain all project data. Git unregisters only an inaccessible private path.
    std::filesystem::create_directories(retired / "staging");
    std::filesystem::create_symlink(retired / "never-created", retired / "data");
    impl_->sync_path(retired / "staging", true); impl_->sync_path(retired, true); impl_->sync_path(impl_->state / "retired", true);
    const auto worktrees_root = std::filesystem::canonical(impl_->state / "worktrees");
    const auto move_stage = worktrees_root / (".retirement-" + retired.filename().string());
    if (std::filesystem::exists(move_stage))
        throw WorldError(wire::STORAGE_ERROR, "Retirement staging path already exists; inspect retained data");
    std::filesystem::create_directory(move_stage);
    std::vector<std::filesystem::path> hidden_worktrees;
    for (const auto& entry : std::filesystem::directory_iterator(worktrees_root)) {
        if (entry.path() == record.path() || entry.path() == move_stage) continue;
        struct stat entry_info{};
        if (lstat(entry.path().c_str(), &entry_info) != 0 || !S_ISDIR(entry_info.st_mode))
            throw WorldError(wire::STORAGE_ERROR, "Unexpected entry in managed worktree root; inspect state");
        hidden_worktrees.push_back(entry.path());
    }
    const auto move_target = move_stage / "project";
    const auto registered = retired / "staging/project";
    impl_->checked(repo.path(), {"worktree", "move", "--", record.path(), move_target.string()},
        {{repo.path(), true}, GitMount{worktrees_root, true, hidden_worktrees}});
    std::error_code move_error;
    std::filesystem::rename(move_target, registered, move_error);
    if (move_error) {
        const auto message = "Cannot move retired worktree into retained staging: " + move_error.message();
        throw WorldError(wire::STORAGE_ERROR, message.c_str());
    }
    if (rmdir(move_stage.c_str()) != 0)
        throw WorldError(wire::STORAGE_ERROR, "Cannot remove empty retirement staging directory");
    impl_->sync_path(impl_->state / "worktrees", true); impl_->sync_path(retired / "staging", true); impl_->sync_path(retired, true);
    const auto observed = impl_->removable(registered, {{repo.path(), false}});
    if (observed.head() != record.head() || observed.branch() != record.branch())
        throw WorldError(wire::STORAGE_ERROR, "Git changed during retirement; retained worktree requires inspection");
    if (!record.head().empty()) impl_->checked(repo.path(), {"update-ref", impl_->retirement_ref(retired), record.head(), std::string(record.head().size(), '0')});
    std::filesystem::copy(admin, retired / "git", std::filesystem::copy_options::recursive | std::filesystem::copy_options::copy_symlinks);
    impl_->sync_tree(retired / "git"); impl_->sync_tree(registered);
    if (syscall(SYS_renameat2, AT_FDCWD, (retired / "staging").c_str(), AT_FDCWD, (retired / "data").c_str(), RENAME_EXCHANGE) != 0)
        throw WorldError(wire::STORAGE_ERROR, "Cannot reserve retired worktree path");
    impl_->sync_path(retired, true);
    const auto retained_marker = retired / "data/project/.git";
    struct stat marker_info{};
    if (lstat(retained_marker.c_str(), &marker_info) != 0 || !S_ISREG(marker_info.st_mode) ||
        unlink(retained_marker.c_str()) != 0)
        throw WorldError(wire::STORAGE_ERROR, "Cannot detach retained project from Git metadata");
    std::error_code remove_error;
    const auto removed_metadata = std::filesystem::remove_all(admin, remove_error);
    if (remove_error || !removed_metadata)
        throw WorldError(wire::STORAGE_ERROR, "Cannot unregister retired worktree metadata");
    impl_->sync_tree(retired); impl_->sync_tree(repo.path()); impl_->sync_path(impl_->state / "retired", true);
    File receipt; receipt.fd = open((retired / "complete").c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (receipt.fd < 0) throw WorldError(wire::STORAGE_ERROR, "Cannot create removal receipt");
    const auto bytes = record.SerializeAsString(); std::size_t used = 0;
    while (used < bytes.size()) {
        const auto count = write(receipt.fd, bytes.data() + used, bytes.size() - used);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw WorldError(wire::STORAGE_ERROR, "Cannot write removal receipt");
        used += static_cast<std::size_t>(count);
    }
    impl_->filesystem_effect([fd = receipt.fd] {
        if (fsync(fd) != 0) throw WorldError(wire::STORAGE_ERROR, "Cannot flush removal receipt");
    });
    impl_->sync_path(retired, true);
    impl_->removal_state(request.operation_id(), record.id(), true); return out;
}
wire::WorkspaceReply ExecutionStore::workspace_get(const std::string& id) {
    valid_text(id);
    Query q(impl_->db.get(), "SELECT worktree,policy,revision FROM workspaces WHERE id=?"); q.text(1, id);
    if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Workspace not registered");
    const auto policy = sqlite3_column_int64(q.value, 1), revision = sqlite3_column_int64(q.value, 2);
    if (policy < 0 || (policy & ~30) || revision < 1) throw WorldError(wire::STORAGE_ERROR, "Invalid workspace policy or revision");
    wire::WorkspaceReply out; out.set_id(id); out.set_worktree(q.text(0)); out.set_revision(revision);
    for (int right = wire::WORKSPACE_SELECT; right <= wire::PROCESS_LAUNCH; ++right)
        if (policy & (1u << right)) out.add_policy(static_cast<wire::WorkspaceRight>(right));
    return out;
}
wire::WorkspaceReply ExecutionStore::workspace_create(const wire::WorkspaceCreateRequest& request) {
    valid_text(request.id()); valid_text(request.worktree());
    if (request.id() == "workspace-registry") throw WorldError(wire::INVALID_ARGUMENT, "Reserved workspace identity cannot own execution state");
    const auto policy = rights_mask(request.policy());
    { Query q(impl_->db.get(), "SELECT id FROM workspaces WHERE id=?"); q.text(1, request.id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "Workspace identity already exists"); }
    const auto worktree = impl_->worktree(request.worktree());
    { Query q(impl_->db.get(), "SELECT id FROM workspaces WHERE worktree=? OR repo=?"); q.text(1, worktree.id()); q.text(2, worktree.repo());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::IN_USE, "Workspace execution state or Git administration already has an owner"); }
    if (impl_->storage) {
        const auto repository = impl_->repository(worktree.repo());
        workspace::v1::StorageOwnerRequest allocation;
        auto* bind = allocation.mutable_workspace_bind(); bind->set_workspace(request.id());
        *bind->mutable_repository() = storage_object(workspace::v1::StorageObject::REPOSITORY, repository.path());
        *bind->mutable_project() = storage_object(workspace::v1::StorageObject::PROJECT, worktree.path());
        impl_->storage->call(std::move(allocation));
    }
    Transaction transaction(impl_->db.get());
    Query q(impl_->db.get(), "INSERT INTO workspaces VALUES(?,?,?,?,1)"); q.text(1, request.id()); q.text(2, worktree.id()); q.text(3, worktree.repo());
    check(sqlite3_bind_int(q.value, 4, static_cast<int>(policy))); q.step(); transaction.commit();
    if (impl_->authority) impl_->authority->policy_changed();
    return workspace_get(request.id());
}
wire::WorkspaceAck ExecutionStore::workspace_member(const wire::WorkspaceMemberRequest& request) {
    if (impl_->authority && impl_->authority->bound(request.id()))
        throw WorldError(wire::FORBIDDEN, "Persistent workspace authority cannot use static membership edits");
    valid_text(request.id()); valid_text(request.actor());
    if (request.id() == "workspace-registry") throw WorldError(wire::INVALID_ARGUMENT, "Reserved registry has no execution membership");
    const auto rights = rights_mask(request.rights());
    Transaction transaction(impl_->db.get()); const auto workspace = workspace_get(request.id());
    unsigned previous = 0;
    { Query q(impl_->db.get(), "SELECT rights FROM workspace_members WHERE workspace=? AND actor=?"); q.text(1, request.id()); q.text(2, request.actor());
      if (q.step() == SQLITE_ROW) previous = static_cast<unsigned>(sqlite3_column_int(q.value, 0)); }
    if (previous != rights) {
        if (workspace.revision() >= INT64_MAX) throw WorldError(wire::RESOURCE_EXHAUSTED, "Workspace revision exhausted");
        Query q(impl_->db.get(), "INSERT INTO workspace_members VALUES(?,?,?) ON CONFLICT(workspace,actor) DO UPDATE SET rights=excluded.rights");
        q.text(1, request.id()); q.text(2, request.actor()); check(sqlite3_bind_int(q.value, 3, static_cast<int>(rights))); q.step();
        Query revision(impl_->db.get(), "UPDATE workspaces SET revision=revision+1 WHERE id=?"); revision.text(1, request.id()); revision.step();
    }
    transaction.commit();
    if (previous != rights && impl_->authority) impl_->authority->policy_changed();
    // Selection and control are separate from the rights that sustain execution.
    const unsigned effective = rights & rights_mask(workspace.policy());
    std::vector<std::string> revoked;
    { Query q(impl_->db.get(), "SELECT id,rights FROM processes WHERE workspace=? AND actor=? AND state='running'");
      q.text(1, request.id()); q.text(2, request.actor());
      while (q.step() == SQLITE_ROW)
          if ((static_cast<unsigned>(sqlite3_column_int(q.value, 1)) & effective) != static_cast<unsigned>(sqlite3_column_int(q.value, 1)))
              revoked.push_back(q.text(0)); }
    for (const auto& id : revoked) {
        impl_->jobs.erase(id); // pidfd cancellation also tears down its PID namespace.
        Query q(impl_->db.get(), "UPDATE processes SET state='revoked' WHERE id=?"); q.text(1, id); q.step();
    }
    wire::WorkspaceAck out; out.set_accepted(true); return out;
}
workspace::v1::AccessReply ExecutionStore::access(const workspace::v1::AccessRequest& request, bool fresh) {
    valid_text(request.workspace()); valid_text(request.actor());
    if (request.workspace() == "workspace-registry") throw WorldError(wire::INVALID_ARGUMENT, "Reserved registry has no executable workspace access");
    const auto descriptor = workspace_get(request.workspace());
    unsigned rights = 0;
    if (impl_->authority && impl_->authority->bound(request.workspace())) {
        rights = impl_->authority->rights(request, fresh);
    } else {
        if (request.has_principal()) throw WorldError(wire::FORBIDDEN, "Persistent workspace authority is unavailable");
        Query q(impl_->db.get(), "SELECT rights FROM workspace_members WHERE workspace=? AND actor=?"); q.text(1, request.workspace()); q.text(2, request.actor());
        if (q.step() == SQLITE_ROW) {
            const auto stored = sqlite3_column_int64(q.value, 0);
            if (stored < 0 || (stored & ~30)) throw WorldError(wire::STORAGE_ERROR, "Invalid stored workspace membership");
            rights = static_cast<unsigned>(stored);
        }
    }
    rights &= rights_mask(descriptor.policy());
    workspace::v1::AccessReply out; out.set_revision(descriptor.revision());
    for (int right = wire::WORKSPACE_SELECT; right <= wire::PROCESS_LAUNCH; ++right)
        if (rights & (1u << right)) out.add_rights(static_cast<wire::WorkspaceRight>(right));
    return out;
}
void ExecutionStore::set_authority(WorkspaceAuthority* authority) { impl_->authority = authority; }
workspace::v1::ScopedAuthorityCheckReply ExecutionStore::scoped_authority_check(
    const workspace::v1::ScopedAuthorityCheckRequest& request, bool refresh) {
    if (!impl_->authority || !impl_->authority->bound(request.workspace()))
        throw WorldError(wire::UNIMPLEMENTED, "Persistent scoped workspace authority is not configured");
    auto decision = impl_->authority->scoped_check(request, refresh);
    if (request.workspace() != "workspace-registry") {
        const auto workspace = workspace_get(request.workspace());
        const auto policy = rights_mask(workspace.policy());
        std::optional<unsigned> required;
        using a = authority::v1::Right;
        switch (request.right()) {
        case a::WORKSPACE_ACCESS: required = wire::WORKSPACE_SELECT; break;
        case a::FILE_BROWSE: case a::FILE_READ: required = wire::FILES_READ; break;
        case a::FILE_CREATE: case a::FILE_EDIT: case a::FILE_RENAME: case a::FILE_MOVE:
        case a::FILE_COPY: case a::FILE_TRASH: case a::FILE_RESTORE: case a::FILE_PERMANENT_DELETE:
            required = wire::FILES_WRITE; break;
        case a::PROCESS_EXECUTE: required = wire::PROCESS_LAUNCH; break;
        default: break;
        }
        if (required && !(policy & (1u << *required))) decision.set_allowed(false);
    }
    return decision;
}
workspace::v1::FileActionReply ExecutionStore::file_action(const workspace::v1::FileActionRequest& request) {
    using namespace workspace::v1;
    const auto operation=request.operation_id();
    if(request.protocol_version()!=1 || operation.size()!=16 || request.ByteSizeLong()>1100000 ||
       request.workspace_id().empty() || request.workspace_id().size()>256 || !request.has_principal() ||
       request.principal().issuer_id().size()!=32 || request.principal().actor_id().size()!=32 ||
       request.authority_components_size()!=0 || file_right(request)==authority::v1::RIGHT_UNSPECIFIED)
        return file_action_error(operation,FileActionError::INVALID_ARGUMENT,"invalid private file action envelope or caller-supplied scope");
    auto uncertainty_fallback=file_action_error(operation,FileActionError::UNCERTAIN,"filesystem outcome requires recovery",true);
    auto uncertainty_reply=file_action_error(operation,FileActionError::UNCERTAIN,"filesystem outcome requires recovery",true);
    StoreFileEffectExecutor* active_executor=nullptr;
    try {
        const auto workspace=workspace_get(request.workspace_id());
        const auto worktree_id=workspace.worktree();
        if(worktree_id.empty() || worktree_id.size()>128)
            return file_action_error(operation,FileActionError::UNAVAILABLE,"workspace has no registered project binding");
        ScopedAuthorityCheckRequest admission;
        admission.set_workspace(workspace.id()); *admission.mutable_principal()=request.principal();
        admission.set_right(file_right(request)); admission.add_components(worktree_id);
        const auto allowed=scoped_authority_check(admission);
        if(!allowed.allowed()) {
            if(request.has_copy_job() && request.copy_job().command()!=FileCopyJob::START &&
               request.copy_job().job_id().size()==16) {
                const auto existing=impl_->file_engines.find(worktree_id);
                if(existing!=impl_->file_engines.end()) {
                    const auto principal_identity=request.principal().SerializeAsString();
                    existing->second->engine->stop_copy_job_for_authority_loss(
                        request.copy_job().job_id(),principal_identity);
                }
            }
            return file_action_error(operation,FileActionError::UNAUTHORIZED,"exact file right or workspace file policy denied the action");
        }
        if(impl_->invalid_file_worktrees.contains(worktree_id))
            return file_action_error(operation,FileActionError::UNCERTAIN,
                "copy recovery disposition quarantines this worktree; saved evidence retained",true);
        Query registered(impl_->db.get(),"SELECT path,state FROM worktrees WHERE id=?");
        registered.text(1,worktree_id);
        if(registered.step()!=SQLITE_ROW || registered.text(1)!="ready")
            return file_action_error(operation,FileActionError::UNAVAILABLE,"registered project is unavailable or not ready");
        const auto expected_path=impl_->managed(registered.text(0),"worktrees",true).string();
        struct stat project_path{};
        if(lstat(expected_path.c_str(),&project_path)!=0 || !S_ISDIR(project_path.st_mode) || S_ISLNK(project_path.st_mode)) {
            auto failure=file_action_error(operation,FileActionError::STALE_HANDLE,"registered project root is missing or unsafe",true);
            record_file_action_diagnostic(failure,"store.registered_root_missing_or_unsafe");
            return failure;
        }
        if(registered.text(0)!=expected_path)
            return file_action_error(operation,FileActionError::UNAVAILABLE,"registered project path differs from workspace binding");
        if(request.has_copy_job() && request.copy_job().command()==FileCopyJob::POLL) {
            const auto durable=impl_->file_journal->find_copy_job(workspace.id(),request.copy_job().job_id(),impl_->observer);
            if(durable && durable->worktree_id()==worktree_id) {
                if(durable->principal_issuer_id()!=request.principal().issuer_id() ||
                   durable->principal_actor_id()!=request.principal().actor_id())
                    return file_action_error(operation,FileActionError::UNAUTHORIZED,"cabinet copy job belongs to another principal");
                const auto disposition=impl_->validate_cleanup_disposition(*durable);
                if(disposition && disposition->kind()!=FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED) {
                    impl_->invalid_file_worktrees.insert(worktree_id);
                    return file_action_error(operation,FileActionError::UNCERTAIN,
                        "copy recovery disposition quarantines this worktree; saved evidence retained",true);
                }
                std::vector<std::string> source_components(
                    durable->source_path_components().begin(), durable->source_path_components().end());
                std::vector<std::string> destination_components(
                    durable->destination_directory_components().begin(),
                    durable->destination_directory_components().end());
                destination_components.push_back(durable->destination_basename());
                if(file_policy::is_registered_root_admin_path(source_components) ||
                   file_policy::is_registered_root_admin_path(destination_components))
                    return file_action_error(operation,FileActionError::UNCERTAIN,
                        "legacy copy touches registered root administration; saved evidence retained",true);
                const auto fresh=scoped_authority_check(admission);
                if(!fresh.allowed())
                    return file_action_error(operation,FileActionError::UNAUTHORIZED,"authority changed before durable copy receipt delivery");
                if(durable->has_terminal_receipt()) {
                    FileActionReply reply;reply.set_protocol_version(1);reply.set_operation_id(operation);
                    *reply.mutable_copy_job()=durable->terminal_receipt();
                    return reply;
                }
                if(impl_->invalid_file_worktrees.contains(worktree_id))
                    return file_action_error(operation,FileActionError::UNCERTAIN,"copy worker recovery is quarantined for this worktree",true);
            }
        }
        auto found=impl_->file_engines.find(worktree_id);
        wire::WorktreeReply verified;
        if(found==impl_->file_engines.end()) verified=impl_->worktree(worktree_id);
        else { verified.set_id(worktree_id); verified.set_path(expected_path); }
        auto& entry=impl_->file_engine(workspace,verified);
        auto trusted=request; trusted.clear_authority_components(); trusted.add_authority_components(worktree_id);
        const auto digest=file_policy::request_digest(trusted);
        std::string principal_identity=request.principal().issuer_id()+request.principal().actor_id();
        FileOperationBinding binding{workspace.id(),operation,std::move(principal_identity),
            worktree_id,static_cast<std::uint64_t>(entry.device),static_cast<std::uint64_t>(entry.inode),
            static_cast<int>(file_right(request)),digest,impl_->file_engine_incarnation};
        const auto prior=impl_->file_journal->find(binding,impl_->observer);
        const auto retry_admission=scoped_authority_check(admission);
        if(!retry_admission.allowed())
            return file_action_error(operation,FileActionError::UNAUTHORIZED,"authority was revoked before a cached file outcome could be returned");
        if(prior.state==FileOperationRecord::State::conflict)
            return file_action_error(operation,FileActionError::CONFLICT,"operation identity is already bound to a different trusted request");
        if(request.has_copy_job() && request.copy_job().command()==FileCopyJob::START) {
            const auto durable=impl_->file_journal->find_copy_job_by_operation(workspace.id(),operation,impl_->observer);
            if(durable) {
                if(durable->initiating_request_sha256()!=digest ||
                   durable->principal_issuer_id()!=request.principal().issuer_id() ||
                   durable->principal_actor_id()!=request.principal().actor_id() ||
                   durable->worktree_id()!=worktree_id)
                    return file_action_error(operation,FileActionError::CONFLICT,
                        "copy operation identity is already bound to different request context");
                const auto disposition=impl_->validate_cleanup_disposition(*durable);
                if(disposition && disposition->kind()!=FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED) {
                    impl_->invalid_file_worktrees.insert(worktree_id);
                    return file_action_error(operation,FileActionError::UNCERTAIN,
                        "copy recovery disposition quarantines this worktree; saved evidence retained",true);
                }
                FileActionReply replay;replay.set_protocol_version(1);replay.set_operation_id(operation);
                auto* receipt=replay.mutable_copy_job();receipt->set_job_id(durable->job_id());
                using J=FileCopyJobJournalState;using R=FileCopyJobReceipt;
                switch(durable->state()) {
                  case J::FILE_COPY_JOB_JOURNAL_QUEUED: receipt->set_state(R::QUEUED); break;
                  case J::FILE_COPY_JOB_JOURNAL_RUNNING:
                  case J::FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED: receipt->set_state(R::RUNNING); break;
                  default:
                    if(durable->has_terminal_receipt()) *receipt=durable->terminal_receipt();
                    else return file_action_error(operation,FileActionError::UNCERTAIN,
                        "copy operation recovery has no durable terminal receipt",true);
                }
                // Reconcile only an unfinished operation intent. A durable job
                // also resolves a retry whose operation outcome is already
                // complete or uncertain; never rewrite those retained outcomes.
                if(prior.state==FileOperationRecord::State::intent)
                    impl_->file_journal->finish(binding,FileOperationRecord::State::complete,replay,impl_->observer);
                return replay;
            }
        }
        if(prior.state==FileOperationRecord::State::intent || prior.state==FileOperationRecord::State::uncertain)
            return file_action_error(operation,FileActionError::UNCERTAIN,"operation has unresolved durable intent",true);
        if(prior.state==FileOperationRecord::State::complete) {
            if(prior.engine_incarnation==impl_->file_engine_incarnation) return prior.reply;
            if(prior.reply.has_error() || prior.reply.has_trash() || request.has_permanent_delete()) return prior.reply;
            auto failure=file_action_error(operation,FileActionError::STALE_HANDLE,"completed result contains process-local handles; inspect or browse to refresh",true);
            record_file_action_diagnostic(failure,"store.completed_result_handle_refresh_required");
            return failure;
        }
        auto* executor=dynamic_cast<StoreFileEffectExecutor*>(entry.effects.get());
        if(!executor) throw std::logic_error("file effect executor type mismatch");
        active_executor=executor;
        executor->set_operation(trusted,binding);
        impl_->file_action_dispatch_active = true;
        auto reply = [&] {
            struct ResetDispatch {
                Impl& state;
                ~ResetDispatch() { state.file_action_dispatch_active = false; }
            } reset{*impl_};
            return entry.engine->handle(trusted);
        }();
        if (impl_->copy_authority_reconcile_pending) {
            impl_->copy_authority_reconcile_pending = false;
            reconcile_authority();
        }
        if(reply.has_error() && reply.error().code()==FileActionError::STALE_HANDLE)
            record_file_action_diagnostic(reply,file_engine_stale_source(reply.error().detail()));
        if(executor->exhausted()) return file_action_error(operation,FileActionError::EXHAUSTED,"file-operation journal capacity is exhausted before filesystem effects");
        ScopedAuthorityCheckReply still_allowed;
        try { still_allowed=scoped_authority_check(admission); }
        catch(...) {
            if(executor->effect_started()) { try { impl_->file_journal->mark_uncertain(binding,uncertainty_reply,impl_->observer); } catch(...) {} return uncertainty_fallback; }
            return file_action_error(operation,FileActionError::UNAVAILABLE,"authority could not be refreshed after file I/O");
        }
        if(!still_allowed.allowed()) {
            if(executor->effect_started()) {
                try { impl_->file_journal->mark_uncertain(binding,uncertainty_reply,impl_->observer); } catch(...) {}
                return uncertainty_fallback;
            }
            return file_action_error(operation,FileActionError::UNAUTHORIZED,"authority was revoked before a filesystem effect");
        }
        if(executor->intent_started()) {
            if(executor->effect_started() && reply.has_error() && reply.error().code()!=FileActionError::UNCERTAIN) reply=std::move(uncertainty_reply);
            const auto uncertain=reply.has_error() && reply.error().code()==FileActionError::UNCERTAIN;
            const auto state=uncertain ? FileOperationRecord::State::uncertain : FileOperationRecord::State::complete;
            try { impl_->file_journal->finish(binding,state,reply,impl_->observer); }
            catch(...) { return uncertainty_fallback; }
            ScopedAuthorityCheckReply terminal_authority;
            try { terminal_authority=scoped_authority_check(admission); }
            catch(...) {
                if(executor->effect_started()) { try { impl_->file_journal->mark_uncertain(binding,uncertainty_reply,impl_->observer); } catch(...) {} return uncertainty_fallback; }
                return file_action_error(operation,FileActionError::UNAVAILABLE,"authority could not be refreshed after journal completion");
            }
            if(!terminal_authority.allowed()) {
                if(executor->effect_started()) { try { impl_->file_journal->mark_uncertain(binding,uncertainty_reply,impl_->observer); } catch(...) {} return uncertainty_fallback; }
                return file_action_error(operation,FileActionError::UNAUTHORIZED,"authority was revoked before file outcome delivery");
            }
        }
        return reply;
    } catch(const WorldError& e) {
        if(active_executor && active_executor->effect_started()) return uncertainty_fallback;
        const auto code=e.code==wire::FORBIDDEN || e.code==wire::UNAUTHORIZED ? FileActionError::UNAUTHORIZED :
            e.code==wire::NOT_FOUND ? FileActionError::NOT_FOUND :
            e.code==wire::RESOURCE_EXHAUSTED ? FileActionError::EXHAUSTED : FileActionError::UNAVAILABLE;
        return file_action_error(operation,code,"workspace file action admission or registry lookup failed");
    } catch(const FileRootStale& stale) {
        if(active_executor && active_executor->effect_started()) return uncertainty_fallback;
        auto failure=file_action_error(operation,FileActionError::STALE_HANDLE,"registered project root changed; existing file handles are stale",true);
        record_file_action_diagnostic(failure,stale.source);
        return failure;
    } catch(const std::length_error&) {
        if(active_executor && active_executor->effect_started()) return uncertainty_fallback;
        return file_action_error(operation,FileActionError::EXHAUSTED,"file-operation journal capacity is exhausted");
    } catch(const std::bad_alloc&) {
        if(active_executor && active_executor->effect_started()) return uncertainty_fallback;
        return file_action_error(operation,FileActionError::EXHAUSTED,"workspace file action resources exhausted");
    } catch(const std::exception&) {
        if(active_executor && active_executor->effect_started()) return uncertainty_fallback;
        return file_action_error(operation,FileActionError::UNAVAILABLE,"workspace file action failed closed");
    } catch(...) {
        if(active_executor && active_executor->effect_started()) return uncertainty_fallback;
        return file_action_error(operation,FileActionError::UNAVAILABLE,"workspace file action failed closed");
    }
}
namespace {
using CopyStatus = workspace::copy::v1::WorkspaceCopyStatus;
CopyStatus copy_status_for(const CopyJournal::Operation& operation, bool authorized) {
    CopyStatus status;
    status.set_operation_id(operation.operation_id);
    status.set_phase(operation.phase);
    if (operation.error != workspace::copy::v1::COPY_ERROR_UNSPECIFIED)
        status.set_error_code(operation.error);
    const bool outcome_phase = operation.phase == workspace::copy::v1::VALIDATED ||
        operation.phase == workspace::copy::v1::PUBLISHED || operation.phase == workspace::copy::v1::UNCERTAIN;
    if (authorized && outcome_phase && !operation.destination_workspace_id.empty()) {
        status.set_authorized_destination_workspace_id(operation.destination_workspace_id);
        if (operation.manifest_digest.size()==32)
            status.set_authorized_manifest_digest(operation.manifest_digest);
    }
    return status;
}
}
workspace::copy::v1::WorkspaceCopyStatus ExecutionStore::workspace_copy_begin(
        const workspace::copy::v1::WorkspaceCopyRequest& request,
        const authority::v1::ActorRef& principal) {
    validate_message(request);
    valid_text(request.source_workspace_id(),128);
    require(principal.issuer_id().size()==32 && principal.actor_id().size()==32,
            "Workspace copy requires a complete authenticated principal");
    const std::string requester=principal.issuer_id()+principal.actor_id();
    require(request.operation_id().size()==16 && request.protocol_version()==1 &&
            request.expected_source_authority_revision()>0 &&
            request.expected_source_policy_revision()>0 &&
            request.expected_destination_profile_revision()>0 &&
            !request.destination_policy_profile().empty() &&
            request.destination_policy_profile().size()<=128 &&
            request.destination_policy_profile().find_first_of("\r\n\t") == std::string::npos &&
            request.data_class_size()>=2 && request.data_class_size()<=5,
            "Invalid workspace copy request");
    const auto exact_request=request.SerializeAsString();
    bool retry=false;
    try {retry=impl_->copy_journal->exact_binding(request.operation_id(),requester,exact_request);}
    catch(const std::invalid_argument&) {throw WorldError(wire::OPERATION_CONFLICT,"Copy operation ID is already bound to another exact request or principal");}
    if (retry) {
        workspace::copy::v1::WorkspaceCopyStatusRequest status_request;
        status_request.set_protocol_version(1); status_request.set_operation_id(request.operation_id());
        return workspace_copy_status(status_request,principal);
    }
    std::set<int> classes;
    bool has_project=false,has_git=false;
    for (const auto data_class:request.data_class()) {
        require(data_class>=copy::PROJECT_TREE && data_class<=copy::ENVIRONMENT_CONFIG &&
                classes.insert(static_cast<int>(data_class)).second,
                "Unknown or duplicate workspace copy class");
        if(data_class==copy::PROJECT_TREE)has_project=true;
        if(data_class==copy::GIT_HISTORY)has_git=true;
    }
    require(has_project && has_git,"Workspace copy requires project and Git history");
    if (!impl_->authority || !impl_->storage || !impl_->resources)
        throw WorldError(wire::UNAVAILABLE,"Workspace copy authority, storage owner, or resource profile is unavailable");
    const auto source=workspace_get(request.source_workspace_id());
    if(source.revision()!=request.expected_source_policy_revision())
        throw WorldError(wire::REVISION_CONFLICT,"Source workspace policy revision changed");
    std::optional<std::string> incarnation;
    std::optional<std::uint64_t> epoch;
    auto check_right=[&](const std::string& workspace, auth::Right right,
                         const std::vector<std::string>& components, bool source_right) {
        workspace::v1::ScopedAuthorityCheckRequest check;
        check.set_workspace(workspace); *check.mutable_principal()=principal; check.set_right(right);
        for(const auto& component:components)check.add_components(component);
        const auto result=scoped_authority_check(check);
        if(!result.allowed())throw WorldError(wire::FORBIDDEN,"Workspace copy authority is denied");
        if(result.incarnation().size()!=16 || !result.epoch())
            throw WorldError(wire::UNAVAILABLE,"Workspace authority fence is malformed");
        if(!incarnation){incarnation=result.incarnation();epoch=result.epoch();}
        else if(*incarnation!=result.incarnation() || *epoch!=result.epoch())
            throw WorldError(wire::UNAVAILABLE,"Workspace authority changed during copy admission");
        if(source_right && result.revision()!=request.expected_source_authority_revision())
            throw WorldError(wire::REVISION_CONFLICT,"Source authority revision changed");
    };
    check_right("workspace-registry",auth::WORKSPACE_CREATE,{},false);
    check_right(request.source_workspace_id(),auth::WORKSPACE_COPY_PROJECT,{},true);
    check_right(request.source_workspace_id(),auth::WORKSPACE_COPY_GIT,{},true);
    auto right_for=[](copy::CopyDataClass data_class) {
        switch(data_class) {
        case copy::APPLICATION_DEFINITION:return auth::WORKSPACE_COPY_DEFINITION;
        case copy::APPLICATION_STATE:return auth::WORKSPACE_COPY_STATE;
        case copy::ENVIRONMENT_CONFIG:return auth::WORKSPACE_COPY_ENVIRONMENT;
        default:return auth::RIGHT_UNSPECIFIED;
        }
    };
    for(const auto data_class:request.data_class()) {
        const auto right=right_for(static_cast<copy::CopyDataClass>(data_class));
        if(right!=auth::RIGHT_UNSPECIFIED)
            check_right(request.source_workspace_id(),right,{},true);
    }
    check_right(request.source_workspace_id(),auth::FILE_BROWSE,{},true);
    check_right(request.source_workspace_id(),auth::FILE_READ,{},true);
    workspace::v1::StorageCopyCatalogQuery query;
    query.set_workspace_id(request.source_workspace_id());
    query.set_destination_profile(request.destination_policy_profile());
    query.set_expected_destination_profile_revision(request.expected_destination_profile_revision());
    query.set_expected_catalog_revision(0);
    for(const auto data_class:request.data_class())query.add_data_classes(static_cast<copy::CopyDataClass>(data_class));
    const auto catalog=impl_->storage->query_copy_catalog(query);
    if(catalog.has_state_mapping())impl_->validate_state_mapping(catalog.state_mapping());
    (void)rights_mask(catalog.destination_profile().execution_policy());
    const auto destination_resources=impl_->resources->resolve_copy_profile(catalog.destination_profile().execution_resource_profile(),catalog.destination_profile().application_profile());
    const auto project=std::find_if(catalog.entries().begin(),catalog.entries().end(),
        [](const auto& entry){return entry.data_class()==copy::PROJECT_TREE;});
    require(project!=catalog.entries().end(),"Owner copy catalog omitted the registered project object");
    check_right(request.source_workspace_id(),auth::FILE_BROWSE,{},true);
    check_right(request.source_workspace_id(),auth::FILE_READ,{},true);
    workspace::v1::WorkspaceCopyAuthorizationBinding binding;
    binding.set_protocol_version(1);
    binding.set_destination_resource_profile(destination_resources.SerializeAsString());
    *binding.mutable_owner_catalog()=catalog;
    binding.set_source_authority_incarnation(*incarnation);
    binding.set_source_authority_epoch(*epoch);
    binding.set_source_policy_revision(source.revision());
    binding.set_exact_request_digest(CopyJournal::digest(exact_request));
    std::ifstream boot_file("/proc/sys/kernel/random/boot_id");std::string boot;std::getline(boot_file,boot);
    require(boot.size()==36,"Copy admission cannot establish the boot identity");binding.set_boot_id(boot);
    const auto accepted=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    require(accepted>0,"Copy admission monotonic clock is invalid");binding.set_accepted_monotonic_ns(accepted);
    binding.set_destination_workspace_id(impl_->allocate("workspaces").filename().string());
    {
        Query q(impl_->db.get(),"SELECT r.path,t.path,t.branch,t.head FROM workspaces w JOIN repositories r ON r.id=w.repo JOIN worktrees t ON t.id=w.worktree WHERE w.id=? AND r.state='ready' AND t.state='ready'");
        q.text(1,request.source_workspace_id());require(q.step()==SQLITE_ROW,"Source copy registry is unavailable");
        binding.set_source_repository_path(q.text(0));binding.set_source_project_path(q.text(1));binding.set_source_branch(q.text(2));binding.set_source_head(q.text(3));
    }
    if(catalog.has_state_mapping()) {
        binding.set_source_state_mapping_revision(catalog.state_mapping().revision());
        for(const auto& state:catalog.state_mapping().entries()) {
            auto* plan=binding.add_state_companions();*plan->mutable_source()=state;
            plan->mutable_snapshot()->set_kind(workspace::v1::StorageObject::SNAPSHOT);
            plan->mutable_snapshot()->set_id(impl_->allocate("snapshots").filename().string());
            plan->mutable_staging()->set_kind(workspace::v1::StorageObject::COPY_STAGING);
            plan->mutable_staging()->set_id(impl_->allocate("staging").filename().string());
            plan->mutable_destination()->set_kind(workspace::v1::StorageObject::APPLICATION_STATE);
            plan->mutable_destination()->set_id(impl_->allocate("copy-identities").filename().string());
        }
    }
    for(const auto& entry:catalog.entries()) {
        auto* domain=binding.add_domains();domain->set_data_class(entry.data_class());
        domain->mutable_snapshot()->set_kind(workspace::v1::StorageObject::SNAPSHOT);
        domain->mutable_snapshot()->set_id(impl_->allocate("snapshots").filename().string());
        domain->mutable_staging()->set_kind(workspace::v1::StorageObject::COPY_STAGING);
        domain->mutable_staging()->set_id(impl_->allocate("staging").filename().string());
        domain->mutable_destination()->set_kind(entry.source_object().kind());
        domain->mutable_destination()->set_id(impl_->allocate("copy-identities").filename().string());
        if(entry.data_class()==copy::PROJECT_TREE &&
           (impl_->state/"worktrees"/entry.source_object().id()).string()!=binding.source_project_path())
            throw WorldError(wire::FORBIDDEN,"Catalog project differs from the actual workspace root");
        if(entry.data_class()==copy::GIT_HISTORY &&
           (impl_->state/"repositories"/(entry.source_object().id()+".git")).string()!=binding.source_repository_path())
            throw WorldError(wire::FORBIDDEN,"Catalog Git history differs from the actual workspace repository");
    }
    const auto final_source=workspace_get(request.source_workspace_id());
    if(final_source.revision()!=request.expected_source_policy_revision())
        throw WorldError(wire::REVISION_CONFLICT, "Source workspace policy changed during copy admission");
    const auto serialized_binding=binding.SerializeAsString();
    const auto operation=impl_->copy_journal->begin(request,requester,serialized_binding);
    return copy_status_for(operation,true);
}
workspace::copy::v1::WorkspaceCopyStatus ExecutionStore::workspace_copy_status(
        const workspace::copy::v1::WorkspaceCopyStatusRequest& request,
        const authority::v1::ActorRef& principal) {
    require(request.protocol_version()==1 && request.operation_id().size()==16 &&
            principal.issuer_id().size()==32 && principal.actor_id().size()==32,
            "Invalid workspace copy status identity");
    const std::string requester=principal.issuer_id()+principal.actor_id();
    const auto operation=impl_->copy_journal->get(request.operation_id(),requester);
    bool authorized=false;
    // Status observes the journal. It cannot borrow the owner's in-flight effect channel.
    // Actor rights, policy, configured state and the authorization fence remain fresh.
    try {revalidate_copy(operation,false);authorized=true;}catch(const WorldError&){}catch(const std::invalid_argument&){}
    return copy_status_for(operation,authorized);
}
void ExecutionStore::revalidate_copy(const CopyJournal::Operation& operation, bool query_owner) {
    const auto stored_request=impl_->copy_journal->request(operation.operation_id,operation.requester);
    require(operation.requester.size()==64,"Stored copy requester is malformed");
    authority::v1::ActorRef principal;principal.set_issuer_id(operation.requester.substr(0,32));principal.set_actor_id(operation.requester.substr(32));
        if(!impl_->authority || !impl_->storage || !impl_->resources)
            throw WorldError(wire::UNAVAILABLE,"Workspace copy authority is unavailable");
        const auto source=workspace_get(stored_request.source_workspace_id());
        if(source.revision()!=stored_request.expected_source_policy_revision())
            throw WorldError(wire::REVISION_CONFLICT,"Source policy changed");
        std::optional<std::string> incarnation;std::optional<std::uint64_t> epoch;
        auto check_right=[&](const std::string& workspace,authority::v1::Right right,
                             const std::vector<std::string>& components,bool source_right) {
            workspace::v1::ScopedAuthorityCheckRequest check;
            check.set_workspace(workspace);*check.mutable_principal()=principal;check.set_right(right);
            for(const auto& component:components)check.add_components(component);
            const auto result=scoped_authority_check(check);
            if(!result.allowed())throw WorldError(wire::FORBIDDEN,"Workspace copy status authority is denied");
            if(!incarnation){incarnation=result.incarnation();epoch=result.epoch();}
            else if(*incarnation!=result.incarnation() || *epoch!=result.epoch())
                throw WorldError(wire::UNAVAILABLE,"Workspace authority changed during status query");
            if(source_right && result.revision()!=stored_request.expected_source_authority_revision())
                throw WorldError(wire::REVISION_CONFLICT,"Source authority revision changed");
        };
        check_right("workspace-registry",authority::v1::WORKSPACE_CREATE,{},false);
        check_right(stored_request.source_workspace_id(),authority::v1::WORKSPACE_COPY_PROJECT,{},true);
        check_right(stored_request.source_workspace_id(),authority::v1::WORKSPACE_COPY_GIT,{},true);
        for(const auto data_class:stored_request.data_class()) {
            authority::v1::Right right=authority::v1::RIGHT_UNSPECIFIED;
            if(data_class==copy::APPLICATION_DEFINITION)right=authority::v1::WORKSPACE_COPY_DEFINITION;
            if(data_class==copy::APPLICATION_STATE)right=authority::v1::WORKSPACE_COPY_STATE;
            if(data_class==copy::ENVIRONMENT_CONFIG)right=authority::v1::WORKSPACE_COPY_ENVIRONMENT;
            if(right!=authority::v1::RIGHT_UNSPECIFIED)
                check_right(stored_request.source_workspace_id(),right,{},true);
        }
        workspace::v1::WorkspaceCopyAuthorizationBinding binding;
        if(!binding.ParseFromString(operation.authorization_binding) ||
           binding.SerializeAsString()!=operation.authorization_binding || binding.protocol_version()!=1 ||
           binding.exact_request_digest()!=operation.request_digest ||
           binding.source_policy_revision()!=stored_request.expected_source_policy_revision() ||
           binding.source_authority_incarnation()!=*incarnation ||
           binding.source_authority_epoch()!=*epoch || !binding.has_owner_catalog())
            throw WorldError(wire::UNAVAILABLE,"Workspace copy authorization binding is invalid");
        workspace::v1::StorageCopyCatalogQuery query;
        query.set_workspace_id(stored_request.source_workspace_id());
        query.set_destination_profile(stored_request.destination_policy_profile());
        query.set_expected_destination_profile_revision(stored_request.expected_destination_profile_revision());
        query.set_expected_catalog_revision(binding.owner_catalog().catalog_revision());
        query.set_expected_state_mapping_revision(binding.source_state_mapping_revision());
        require((binding.state_companions_size()==0 && !binding.owner_catalog().has_state_mapping() && binding.source_state_mapping_revision()==0) ||
                (binding.state_companions_size()>0 && binding.state_companions_size()<=32 && binding.owner_catalog().has_state_mapping() &&
                 binding.source_state_mapping_revision()==binding.owner_catalog().state_mapping().revision() &&
                 binding.state_companions_size()==binding.owner_catalog().state_mapping().entries_size()),"Pinned copy state mapping shape differs");
        for(int index=0;index<binding.state_companions_size();++index)
            require(binding.state_companions(index).source().SerializeAsString()==binding.owner_catalog().state_mapping().entries(index).SerializeAsString(),
                    "Pinned copy state companion differs from its owner mapping");
        for(const auto data_class:stored_request.data_class())query.add_data_classes(static_cast<copy::CopyDataClass>(data_class));
        // Owner configuration is fixed for this authenticated owner process. Effect
        // advancement still queries it before external effects. Observation validates
        // the pinned definition against current broker state without another exchange.
        impl_->storage->check_alive();
        const auto current=query_owner?impl_->storage->query_copy_catalog(query):binding.owner_catalog();
        if(current.has_state_mapping())impl_->validate_state_mapping(current.state_mapping());
        const auto current_resources=impl_->resources->resolve_copy_profile(current.destination_profile().execution_resource_profile(),current.destination_profile().application_profile());
        if(current.SerializeAsString()!=binding.owner_catalog().SerializeAsString() ||
           current_resources.SerializeAsString()!=binding.destination_resource_profile())
            throw WorldError(wire::UNAVAILABLE,"Pinned workspace copy catalog or destination profile changed");
        const auto project=std::find_if(current.entries().begin(),current.entries().end(),
            [](const auto& entry){return entry.data_class()==copy::PROJECT_TREE;});
        if(project==current.entries().end())
            throw WorldError(wire::UNAVAILABLE,"Pinned workspace project object is missing");
        check_right(stored_request.source_workspace_id(),authority::v1::FILE_BROWSE,{},true);
        check_right(stored_request.source_workspace_id(),authority::v1::FILE_READ,{},true);
        validate_message(binding);
        const auto fence=impl_->authority->fence_snapshot();
        if(fence.incarnation()!=binding.source_authority_incarnation() || fence.epoch()!=binding.source_authority_epoch() ||
           workspace_get(stored_request.source_workspace_id()).revision()!=binding.source_policy_revision())
            throw WorldError(wire::REVISION_CONFLICT,"Copy authority or policy changed during revalidation");
}
workspace::v1::AuthorizationFence ExecutionStore::authorization_fence(const workspace::v1::AuthorizationFenceRequest& request) {
    if (!impl_->authority) throw WorldError(wire::UNIMPLEMENTED, "Persistent workspace authority is not configured");
    if (!request.has_principal()) throw WorldError(wire::FORBIDDEN, "Authorization fence requires authenticated actor authority");
    return impl_->authority->authorization_fence(request.principal());
}
workspace::v1::ApplicationAccessReply ExecutionStore::application_access(const workspace::v1::ApplicationAccessRequest& request, bool refresh) {
    if (!request.has_principal()) throw WorldError(wire::FORBIDDEN, "Application access requires authenticated actor authority");
    const auto& principal = request.principal();
    require(principal.issuer_id().size() == 32 && principal.actor_id().size() == 32, "Invalid authenticated actor reference");
    require(request.applications_size() > 0 && request.applications_size() <= 64, "Application access requires one to 64 groups");
    std::set<std::string> unique;
    for (const auto& app : request.applications()) {
        valid_text(app.workspace()); valid_text(app.application());
        if (app.workspace() == "workspace-registry")
            throw WorldError(wire::INVALID_ARGUMENT, "Reserved registry has no application execution");
        require(app.server_id().size() == 32, "Invalid native server identity");
        require(unique.insert(app.SerializeAsString()).second, "Duplicate application group");
    }
    // Prepare each workspace once. The complete batch uses current issuer status
    // and component-scoped grants, not a previous application's cached decision.
    std::set<std::string> prepared;
    for (const auto& app : request.applications()) {
        if (refresh && impl_->authority && impl_->authority->bound(app.workspace()) && prepared.insert(app.workspace()).second)
            (void)impl_->authority->application_permissions(app, principal, true);
    }
    if (refresh) {
        reconcile_authority(); reap();
        if (impl_->native) impl_->native->check();
    }
    if (!impl_->authority) throw WorldError(wire::UNIMPLEMENTED, "Application authority fence is not configured");
    // Cached control checks run inside issuer waits. Do not start another issuer request.
    const auto stamp = refresh ? impl_->authority->authorization_fence(principal)
                               : impl_->authority->fence_snapshot();
    workspace::v1::ApplicationAccessReply reply; reply.set_incarnation(stamp.incarnation()); reply.set_epoch(stamp.epoch());
    for (const auto& app : request.applications()) {
        auto* result = reply.add_applications(); *result->mutable_application() = app;
        result->set_observe(false); result->set_control(false);
        if (!impl_->native || impl_->native->server_id() != app.server_id() || !impl_->jobs.contains(app.application()) ||
            !impl_->authority || !impl_->authority->bound(app.workspace())) continue;
        Query record(impl_->db.get(), "SELECT workspace,rights,state FROM processes WHERE id=?"); record.text(1, app.application());
        if (record.step() != SQLITE_ROW || record.text(0) != app.workspace() || record.text(2) != "running") continue;
        const auto stored = sqlite3_column_int64(record.value, 1);
        const unsigned required = (1u << wire::FILES_READ) | (1u << wire::PROCESS_LAUNCH);
        if (stored != required && stored != (required | (1u << wire::FILES_WRITE)))
            throw WorldError(wire::STORAGE_ERROR, "Stored process rights are invalid");
        // Status exchanges and process callbacks above can observe revocation.
        // Calculate every decision after that I/O, without reusing an earlier result.
        const auto permission = impl_->authority->application_permissions(app, principal, false);
        const unsigned effective = permission.rights & rights_mask(workspace_get(app.workspace()).policy());
        if (!(effective & (1u << wire::WORKSPACE_SELECT))) continue;
        result->set_observe(permission.observe);
        result->set_control(permission.observe && permission.control &&
            (effective & static_cast<unsigned>(stored)) == static_cast<unsigned>(stored));
    }
    const auto current = impl_->authority->fence_snapshot();
    if (current.incarnation() != stamp.incarnation() || current.epoch() != stamp.epoch())
        throw WorldError(wire::UNAVAILABLE, "Authorization changed during application access evaluation");
    return reply;
}
void ExecutionStore::reconcile_authority() try {
    if (!impl_->authority) return;
    std::exception_ptr reconciliation_failure;
    std::exception_ptr process_failure;
    const auto remember = [](std::exception_ptr& slot) {
        if (!slot) slot = std::current_exception();
    };
    struct Running { std::string id; workspace::v1::LaunchRequest request; unsigned rights; };
    std::vector<Running> records;
    try {
        Query q(impl_->db.get(), "SELECT id,payload,rights,workspace,actor FROM processes WHERE state='running'");
        for (;;) {
            int step = SQLITE_ERROR;
            try { step = q.step(); }
            catch (...) { remember(reconciliation_failure); break; }
            if (step != SQLITE_ROW) break;
            try {
                Running record; record.id = q.text(0);
                if (!record.request.ParseFromString(q.text(1)) || record.request.workspace() != q.text(3) || record.request.actor() != q.text(4))
                    throw WorldError(wire::STORAGE_ERROR, "Stored process authority is invalid");
                validate_message(record.request);
                const auto mask = sqlite3_column_int64(q.value, 2);
                if (mask < 0 || (mask & ~30)) throw WorldError(wire::STORAGE_ERROR, "Stored process rights are invalid");
                record.rights = static_cast<unsigned>(mask); records.push_back(std::move(record));
            } catch (...) { remember(reconciliation_failure); }
        }
    } catch (...) { remember(reconciliation_failure); }
    std::vector<std::string> revoked;
    for (const auto& record : records) {
        if (!record.request.has_principal()) continue;
        try {
            workspace::v1::AccessRequest request; request.set_workspace(record.request.workspace()); request.set_actor(record.request.actor());
            *request.mutable_principal() = record.request.principal();
            const auto effective = rights_mask(access(request, false).rights());
            if ((record.rights & effective) != record.rights) revoked.push_back(record.id);
        } catch (...) { remember(reconciliation_failure); }
    }
    if (revoked.empty()) {
        if (reconciliation_failure) std::rethrow_exception(reconciliation_failure);
        return;
    }

    std::vector<std::pair<std::string, ApplicationProcess*>> jobs;
    for (const auto& id : revoked) {
        const auto found = impl_->jobs.find(id);
        // Preserve the existing behavior for a durable running row without an
        // in-memory job. Startup recovery marks such rows uncertain before serving.
        if (found != impl_->jobs.end()) jobs.emplace_back(id, found->second.get());
    }

    // Use one deadline rooted at the first batch stop request. Request every
    // scope and pidfd signal before entering any per-application wait.
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    for (const auto& [id, process] : jobs) {
        (void)id;
        try { process->request_stop(deadline); }
        catch (...) { remember(process_failure); }
    }
    // A failed request for one application must not suppress drain attempts for
    // any other application. Each completion uses the same absolute deadline.
    for (const auto& [id, process] : jobs) {
        (void)id;
        try { process->complete_stop(deadline); }
        catch (...) { remember(process_failure); }
    }

    // Destruction performs owner-thread native credential revocation. Every
    // application scope has already received its stop request and drain attempt.
    for (const auto& [id, process] : jobs) {
        (void)process;
        impl_->jobs.erase(id);
    }
    if (impl_->native) {
        try { impl_->native->check(); }
        catch (...) { remember(process_failure); }
    }
    if (impl_->file_action_dispatch_active) {
        impl_->copy_authority_reconcile_pending = true;
    } else {
        // Application scopes are drained first, so slow copy cleanup cannot
        // consume their shared absolute termination deadline.
        for (const auto& [worktree, entry] : impl_->file_engines) {
            (void)worktree;
            if (!entry || !entry->engine) continue;
            const auto workspace = entry->workspace;
            const auto component = entry->worktree;
            try {
                entry->engine->reconcile_copy_job_authority([this, workspace, component](const std::string& encoded) {
                    auth::ActorRef principal;
                    if (!principal.ParseFromString(encoded)) return false;
                    workspace::v1::ScopedAuthorityCheckRequest check;
                    check.set_workspace(workspace);
                    *check.mutable_principal() = std::move(principal);
                    check.set_right(auth::FILE_COPY);
                    check.add_components(component);
                    return scoped_authority_check(check, false).allowed();
                });
            } catch (...) { remember(reconciliation_failure); }
        }
    }
    if (process_failure) std::rethrow_exception(process_failure);

    // Persist the complete application batch atomically. No applied-revision
    // acknowledgment can pass enforce() until this checked commit returns.
    {
        Transaction transaction(impl_->db.get());
        for (const auto& id : revoked) {
            Query q(impl_->db.get(), "UPDATE processes SET state='revoked' WHERE id=? AND state='running'");
            q.text(1, id); q.step();
            if (sqlite3_changes(impl_->db.get()) != 1)
                throw WorldError(wire::STORAGE_ERROR, "Revoked process state changed during authority reconciliation");
        }
        transaction.commit();
    }
    if (reconciliation_failure) std::rethrow_exception(reconciliation_failure);
} catch (...) {
    if (!impl_->authority_reconciliation_failure)
        impl_->authority_reconciliation_failure = std::current_exception();
    throw;
}

void ExecutionStore::check_authority_reconciliation() const {
    if (impl_->authority_reconciliation_failure)
        std::rethrow_exception(impl_->authority_reconciliation_failure);
}

wire::ProcessLaunchReply ExecutionStore::launch(const workspace::v1::LaunchRequest& request) {
    valid_text(request.workspace()); valid_text(request.actor());
    if(impl_->copy_barriers.contains(request.workspace()))
        throw WorldError(wire::BUSY,"Workspace copy quiescence blocks new application launches");
    const auto& command = request.command(); valid_text(command.operation_id());
    require(command.profile() == "development" || command.profile() == "read-only" ||
            !command.configured_application_key().empty(), "Unknown application profile");
    if(!command.configured_application_key().empty()) {
        require(impl_->resources && impl_->resources->has_profile(command.profile(),request.workspace()),
                "Configured application resource profile is unavailable");
    }
    require(command.argv_size() > 0 && command.argv_size() <= 256 && !command.argv(0).empty(), "Invalid application argv");
    std::size_t size = 0;
    for (const auto& arg : command.argv()) {
        require(arg.find('\0') == std::string::npos, "NUL in application argv"); size += arg.size() + 1;
    }
    require(size <= 32768, "Application argv exceeds limit");
    workspace::v1::AccessRequest query; query.set_workspace(request.workspace()); query.set_actor(request.actor());
    if (request.has_principal()) *query.mutable_principal() = request.principal();
    unsigned effective = rights_mask(access(query).rights());
    const unsigned required = (1u << wire::WORKSPACE_SELECT) | (1u << wire::FILES_READ) | (1u << wire::PROCESS_LAUNCH);
    if ((effective & required) != required) throw WorldError(wire::FORBIDDEN, "Application requires project read and execution authority");
    effective &= ~(1u << wire::WORKSPACE_SELECT);
    if (command.profile() == "read-only") effective &= ~(1u << wire::FILES_WRITE);
    // A fresh status reply can revoke an existing process before this retry lookup.
    reconcile_authority();
    const auto payload = request.SerializeAsString();
    { Query old(impl_->db.get(), "SELECT id,payload,state FROM processes WHERE actor=? AND operation=?");
      old.text(1, request.actor()); old.text(2, command.operation_id());
      if (old.step() == SQLITE_ROW) {
          if (old.text(1) != payload) throw WorldError(wire::OPERATION_CONFLICT, "Launch operation already binds different context or arguments");
          if (old.text(2) != "running" && old.text(2) != "exited") throw WorldError(wire::UNAVAILABLE, "Launch outcome is unavailable; side effects will not be replayed");
          wire::ProcessLaunchReply out; out.set_process_id(old.text(0)); out.set_workspace(request.workspace()); return out;
      } }
    reap();
    if (impl_->jobs.size() >= 32) throw WorldError(wire::RESOURCE_EXHAUSTED, "Application process budget exhausted");
    struct LaunchGuard {
        std::set<std::string>& active;std::string workspace;
        LaunchGuard(std::set<std::string>& values,const std::string& id):active(values),workspace(id) {
            if(!active.insert(workspace).second)throw WorldError(wire::BUSY,"Workspace launch setup is already in progress");
        }
        ~LaunchGuard(){active.erase(workspace);}
    } launch_guard(impl_->launching_workspaces,request.workspace());
    if(impl_->copy_barriers.contains(request.workspace()))throw WorldError(wire::BUSY,"Workspace entered copy quiescence before launch setup");
    std::optional<workspace::v1::StorageConfiguredApplicationReply> configured;
    std::filesystem::path persistent_state;
    if(!command.configured_application_key().empty()) {
        require(impl_->storage && copy_collections::key(command.configured_application_key()),"Configured applications require the authenticated storage owner");
        {Query active(impl_->db.get(),"SELECT p.state FROM configured_process_states b JOIN processes p ON p.id=b.process WHERE b.workspace=? AND b.key=? AND p.state NOT IN ('exited','revoked') LIMIT 1");
         active.text(1,request.workspace());active.text(2,command.configured_application_key());
         if(active.step()==SQLITE_ROW)throw WorldError(active.text(0)=="uncertain"?wire::UNAVAILABLE:wire::BUSY,"Configured state has a live or unresolved application instance");}
        workspace::v1::StorageConfiguredApplicationQuery query;query.set_workspace_id(request.workspace());query.set_key(command.configured_application_key());
        configured=impl_->storage->query_configured_application(query);
        const auto& definition=configured->definition();
        require(definition.profile()==command.profile() && command.argv_size()==definition.definition().arguments_size()+1 &&
                command.argv(0)==definition.definition().executable(),"Configured launch differs from its registered profile/executable/argv");
        for(int index=0;index<definition.definition().arguments_size();++index)
            require(command.argv(index+1)==definition.definition().arguments(index),"Configured launch argv differs from its registered definition");
        {Query known(impl_->db.get(),"SELECT state_id,contract FROM configured_application_states WHERE workspace=? AND key=?");
         known.text(1,request.workspace());known.text(2,command.configured_application_key());
         if(known.step()==SQLITE_ROW) {
             require(configured->has_state() && configured->state().object().id()==known.text(0) &&
                     configured->definition().state_contract().SerializeAsString()==known.text(1),
                     "Configured state owner mapping differs from the durable execution binding");
             persistent_state=impl_->state/"applications"/known.text(0);
         } else {
             require(!configured->has_state(),"Unbound configured state requires explicit reconciliation");
             persistent_state=impl_->allocate("applications");
         }}
    }
    const auto workspace = workspace_get(request.workspace());
    const auto worktree = impl_->worktree(workspace.worktree());
    const auto repository = impl_->repository(worktree.repo());
    if (!impl_->resources) throw WorldError(wire::UNAVAILABLE, "Configure workspace resource limits before execution");
    auto resources = impl_->resources->prepare(request, {worktree.path(), repository.path(), impl_->state / "applications"});
    const auto state = impl_->allocate("applications"); const auto id = state.filename().string();
    { Transaction transaction(impl_->db.get());
      Query q(impl_->db.get(), "INSERT INTO processes VALUES(?,?,?,?,?,?,'pending',NULL)");
      q.text(1, id); q.text(2, request.actor()); q.text(3, command.operation_id()); q.blob(4, payload); q.text(5, request.workspace());
      check(sqlite3_bind_int(q.value, 6, static_cast<int>(effective))); q.step();
      if(configured) {
          Query persistent(impl_->db.get(),"INSERT INTO configured_application_states VALUES(?,?,?,?) ON CONFLICT(workspace,key) DO NOTHING");
          persistent.text(1,request.workspace());persistent.text(2,command.configured_application_key());persistent.text(3,persistent_state.filename().string());
          persistent.blob(4,configured->definition().state_contract().SerializeAsString());persistent.step();
          Query binding(impl_->db.get(),"INSERT INTO configured_process_states VALUES(?,?,?,?)");binding.text(1,id);
          binding.text(2,request.workspace());binding.text(3,command.configured_application_key());binding.text(4,persistent_state.filename().string());binding.step();
      }
      transaction.commit(); }
    try {
        File persistent_pin;std::optional<PersistentApplicationStateMount> persistent_mount;
        std::function<void()> current = [] {};
        if (request.has_principal()) current = [this, query, effective] {
            impl_->authority->tick();
            const auto rights = rights_mask(access(query, false).rights());
            if ((effective & rights) != effective) throw WorldError(wire::FORBIDDEN, "Process execution authority was revoked");
        };
        if (impl_->storage) {
            workspace::v1::StorageOwnerRequest allocation;
            auto* prepare = allocation.mutable_application_prepare(); prepare->set_workspace(request.workspace());
            *prepare->mutable_object() = storage_object(workspace::v1::StorageObject::APPLICATION_STATE, state);
            prepare->set_profile(command.profile());
            if (request.has_principal()) *prepare->mutable_principal() = request.principal();
            else prepare->set_fixture_actor(request.actor());
            if(configured) {
                workspace::v1::StorageConfiguredApplicationPrepare batch;
                *batch.mutable_persistent()=*prepare;*batch.mutable_persistent()->mutable_object()=
                    storage_object(workspace::v1::StorageObject::APPLICATION_STATE,persistent_state);
                batch.mutable_persistent()->set_configured_application_key(command.configured_application_key());
                batch.mutable_persistent()->set_expected_catalog_revision(configured->catalog_revision());
                batch.mutable_persistent()->set_expected_state_mapping_revision(configured->state_mapping_revision());
                const auto& storage_policy=configured->storage_policy();
                auto actor_maximum=std::min(storage_policy.total_bytes(),storage_policy.actor_state_default_bytes());
                for(const auto& actor:storage_policy.actors()) {
                    const bool same=request.has_principal()?actor.has_principal() &&
                        actor.principal().SerializeAsString()==request.principal().SerializeAsString():
                        actor.has_fixture_actor() && actor.fixture_actor()==request.actor();
                    if(same)actor_maximum=std::min(storage_policy.total_bytes(),actor.state_bytes());
                }
                std::uint64_t state_maximum=0;
                for(const auto& profile:storage_policy.profiles())if(profile.name()==command.profile())
                    state_maximum=std::min(actor_maximum,profile.state_bytes());
                require(state_maximum>0,"Independently selected configured storage ceiling is absent");
                batch.mutable_persistent()->set_expected_state_maximum_bytes(state_maximum);
                batch.mutable_persistent()->set_expected_storage_policy_digest(CopyJournal::digest(storage_policy.SerializeAsString()));
                *batch.mutable_runtime_object()=prepare->object();current();
                const auto allocated=impl_->storage->prepare_configured_application(batch);current();
                persistent_pin.fd=impl_->storage->open_object(allocated.persistent());
                File runtime_pin;runtime_pin.fd=impl_->storage->open_object(allocated.runtime());
                workspace::v1::StorageConfiguredApplicationQuery check_mapping;check_mapping.set_workspace_id(request.workspace());
                check_mapping.set_key(command.configured_application_key());
                const auto checked=impl_->storage->query_configured_application(check_mapping);current();
                require(checked.catalog_revision()==configured->catalog_revision() &&
                        checked.definition().SerializeAsString()==configured->definition().SerializeAsString() &&
                        checked.storage_policy().SerializeAsString()==configured->storage_policy().SerializeAsString() &&
                        checked.state_mapping_revision()==allocated.state_mapping_revision() &&
                        checked.has_state() && checked.state().object().SerializeAsString()==allocated.persistent().object().SerializeAsString(),
                        "Configured mapping/profile changed after allocation");
                PersistentApplicationStateMount mount;mount.root_fd=persistent_pin.fd;
                for(const auto subdomain:configured->definition().state_contract().subdomains()) {
                    const char* name=subdomain==workspace::v1::PERSISTENT_DATA?"data":"config";
                    if(mkdirat(persistent_pin.fd,name,0700)!=0 && errno!=EEXIST)throw WorldError(wire::STORAGE_ERROR,"Cannot create declared persistent state directory");
                    File pinned;pinned.fd=openat(persistent_pin.fd,name,O_PATH|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
                    require(pinned.fd>=0,"Persistent subdomain is not a real directory");
                    if(subdomain==workspace::v1::PERSISTENT_DATA)mount.data=true;else mount.config=true;
                }
                require(fsync(persistent_pin.fd)==0,"Cannot persist configured state roots");persistent_mount=mount;
            } else {impl_->storage->call(std::move(allocation), current); current();}
            // Obtain a fresh issuer snapshot after storage work and before releasing execution.
            const auto updated = rights_mask(access(query).rights());
            if ((effective & updated) != effective) throw WorldError(wire::FORBIDDEN, "Process authority changed during storage preparation");
        } else if (!std::filesystem::create_directory(state)) throw std::runtime_error("Application state path already exists");
        for (const auto* name : {"home", "tmp", "cache", "config", "data"}) std::filesystem::create_directory(state / name);
        impl_->sync_path(state, true); impl_->sync_path(state.parent_path(), true);
        auto native = impl_->native ? impl_->native->issue(request.workspace(), id, current) : nullptr;
        auto process = std::make_unique<ApplicationProcess>(worktree.path(), repository.path(), state,
            (effective & (1u << wire::FILES_WRITE)) != 0, command, std::move(current), std::move(native), std::move(resources),persistent_mount);
        impl_->jobs.emplace(id, std::move(process));
        Query q(impl_->db.get(), "UPDATE processes SET state='running' WHERE id=?"); q.text(1, id); q.step();
    } catch (const WorldError& error) {
        impl_->jobs.erase(id);
        Query q(impl_->db.get(), "UPDATE processes SET state=? WHERE id=?");
        q.text(1, error.code == wire::FORBIDDEN ? "revoked" : "uncertain"); q.text(2, id); q.step(); throw;
    } catch (...) {
        impl_->jobs.erase(id);
        Query q(impl_->db.get(), "UPDATE processes SET state='uncertain' WHERE id=?"); q.text(1, id); q.step();
        throw;
    }
    wire::ProcessLaunchReply out; out.set_process_id(id); out.set_workspace(request.workspace()); return out;
}
wire::ProcessWaitReply ExecutionStore::wait(const wire::ProcessWaitRequest& request) {
    valid_text(request.id());
    require(request.has_timeout_seconds() && std::isfinite(request.timeout_seconds()) && request.timeout_seconds() >= 0 && request.timeout_seconds() <= 5,
            "Process wait requires a timeout from zero to five seconds");
    { Query q(impl_->db.get(), "SELECT state,exit_code FROM processes WHERE id=?"); q.text(1, request.id());
      if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Process record does not exist");
      if (q.text(0) == "exited" && sqlite3_column_type(q.value, 1) == SQLITE_INTEGER) {
          wire::ProcessWaitReply out; out.set_exit_code(sqlite3_column_int(q.value, 1)); return out;
      }
      if (q.text(0) != "running") throw WorldError(wire::UNAVAILABLE, "Process completion is uncertain or execution was revoked"); }
    const auto found = impl_->jobs.find(request.id());
    if (found == impl_->jobs.end()) throw WorldError(wire::UNAVAILABLE, "No live supervisor owns this process");
    std::optional<int> result;
    try { result = found->second->wait(request.timeout_seconds()); }
    catch (const WorldError& error) {
        impl_->jobs.erase(found);
        Query q(impl_->db.get(), "UPDATE processes SET state=? WHERE id=?");
        q.text(1, error.code == wire::FORBIDDEN ? "revoked" : "uncertain"); q.text(2, request.id()); q.step();
        throw WorldError(wire::UNAVAILABLE, "Process execution is revoked or completion is uncertain");
    }
    catch (const std::exception&) {
        // One damaged application supervisor cannot poison unrelated future launches.
        impl_->jobs.erase(found);
        Query q(impl_->db.get(), "UPDATE processes SET state='uncertain' WHERE id=?"); q.text(1, request.id()); q.step();
        throw WorldError(wire::UNAVAILABLE, "Application supervisor failed; completion remains uncertain");
    }
    if (!result) throw WorldError(wire::TIMEOUT, "Application has not completed");
    Query q(impl_->db.get(), "UPDATE processes SET state='exited',exit_code=? WHERE id=?");
    check(sqlite3_bind_int(q.value, 1, *result)); q.text(2, request.id()); q.step();
    impl_->jobs.erase(found);
    wire::ProcessWaitReply out; out.set_exit_code(*result); return out;
}
void ExecutionStore::set_native_launch(NativeLaunch* native) { impl_->native = native; }
void ExecutionStore::set_resources(WorkspaceResources* resources) {
    impl_->resources=resources;if(!resources)return;
    resources->bind_git_worker_journal(impl_->file_journal.get());
    Query missing(impl_->db.get(),"SELECT count(*) FROM copy_operations c JOIN workspaces w ON w.id=c.destination LEFT JOIN workspace_copy_policies p ON p.workspace=w.id WHERE c.phase=7 AND p.workspace IS NULL");
    require(missing.step()==SQLITE_ROW && sqlite3_column_int64(missing.value,0)==0,"Published copy is missing its durable resource policy");
    Query bindings(impl_->db.get(),"SELECT workspace,binding FROM workspace_copy_policies ORDER BY workspace");
    while(bindings.step()==SQLITE_ROW) {
        workspace::v1::WorkspaceCopyResourceProfile profile;
        require(profile.ParseFromString(bindings.text(1)),"Persisted copy resource policy is malformed");validate_message(profile);
        resources->register_copy_workspace(bindings.text(0),profile);
    }
    impl_->recover_git_worker_parent_before_dispatch();
    resources->finish_initialization();
    impl_->reconcile_pending_git_effects();
}
void ExecutionStore::set_observer(std::function<void()> observer) { impl_->observer = std::move(observer); }
void ExecutionStore::stop_all() {
    // Checked destruction cannot return after unconfirmed process termination.
    impl_->jobs.clear();
    Query q(impl_->db.get(), "UPDATE processes SET state='uncertain' WHERE state='running'"); q.step();
}
void ExecutionStore::reap() {
    // Completion is a process lifecycle event, not an actor's wait request.
    std::vector<std::string> running;
    for (const auto& [id, job] : impl_->jobs) { (void)job; running.push_back(id); }
    for (const auto& id : running) {
        wire::ProcessWaitRequest check; check.set_id(id); check.set_timeout_seconds(0);
        try { wait(check); } catch (const WorldError& error) {
            if (error.code != wire::TIMEOUT && error.code != wire::UNAVAILABLE) throw;
        }
    }
}
namespace {
std::string copy_effect_id(const std::string& operation,const std::string& kind,int index=0) {
    return CopyJournal::digest(operation+kind+std::to_string(index)).substr(0,16);
}
std::string copy_boot() {
    std::ifstream input("/proc/sys/kernel/random/boot_id");std::string value;std::getline(input,value);return value;
}
void copy_frame(std::string& bytes,const std::string& item) {
    const auto n=static_cast<std::uint32_t>(item.size());bytes.push_back(static_cast<char>(n>>24));bytes.push_back(static_cast<char>(n>>16));
    bytes.push_back(static_cast<char>(n>>8));bytes.push_back(static_cast<char>(n));bytes+=item;
}
workspace::v1::StorageCopyDestinationPrepare copy_allocation(const CopyJournal::Operation& operation,
        const workspace::v1::WorkspaceCopyAuthorizationBinding& binding) {
    workspace::v1::StorageCopyDestinationPrepare result;result.set_operation_id(operation.operation_id);
    result.set_request_digest(operation.request_digest);result.set_workspace_id(binding.destination_workspace_id());
    const auto& catalog=binding.owner_catalog();result.set_profile_name(catalog.destination_profile().name());
    result.set_profile_revision(catalog.destination_profile().revision());result.set_catalog_revision(catalog.catalog_revision());
    *result.mutable_domains()=binding.domains();*result.mutable_state_companions()=binding.state_companions();
    result.set_state_mapping_revision(binding.source_state_mapping_revision());
    require(binding.owner_catalog().entries_size()>0,"Copy allocation omitted its source workspace");
    result.set_source_workspace_id(binding.owner_catalog().entries(0).workspace_id());return result;
}
}
bool ExecutionStore::expire_one_workspace_copy() {
    if(impl_->file_action_dispatch_active)return false;
    impl_->file_action_dispatch_active=true;
    bool expired=false;
    try {
        if(!impl_->file_engines.empty()) {
            auto item=impl_->copy_expiry_cursor.empty()?impl_->file_engines.begin():
                impl_->file_engines.upper_bound(impl_->copy_expiry_cursor);
            if(item==impl_->file_engines.end())item=impl_->file_engines.begin();
            impl_->copy_expiry_cursor=item->first;
            if(item->second&&item->second->engine)expired=item->second->engine->expire_one_copy_job();
        }
    } catch(...) {
        impl_->file_action_dispatch_active=false;
        if(impl_->copy_authority_reconcile_pending) { impl_->copy_authority_reconcile_pending=false;reconcile_authority(); }
        throw;
    }
    impl_->file_action_dispatch_active=false;
    if(impl_->copy_authority_reconcile_pending) { impl_->copy_authority_reconcile_pending=false;reconcile_authority(); }
    return expired;
}
void ExecutionStore::advance_workspace_copies() {
    if(impl_->advancing_copy || !impl_->storage || !impl_->resources || !impl_->authority)return;
    struct Advancing {bool& active;explicit Advancing(bool& value):active(value){active=true;}~Advancing(){active=false;}} guard(impl_->advancing_copy);
    auto& journal=*impl_->copy_journal;
    if(!impl_->scanned_copy_recovery) {
        impl_->scanned_copy_recovery=true;
        for(const auto& old:journal.pending()) {
            if(old.phase==copy::REQUESTED && journal.effects(old.operation_id,old.requester).empty())continue;
            // Read exact registered receipts after restart. Never relaunch an unwitnessed worker.
            for(const auto& effect:journal.effects(old.operation_id,old.requester)) {
                if(!effect.uncertain)continue;
                try {
                    if(effect.kind=="snapshot") {
                        workspace::v1::StorageOwnerRequest original;require(original.ParseFromString(effect.intent) && original.has_snapshot_prepare(),"Malformed retained snapshot intent");
                        workspace::v1::StorageOwnerRequest inspect;*inspect.mutable_snapshot_inspect()->mutable_snapshot_object()=original.snapshot_prepare().snapshot_object();
                        const auto receipt=impl_->storage->call(std::move(inspect));
                        require(receipt.operation_digest()==original.snapshot_prepare().request_digest() && receipt.read_only() &&
                                receipt.source_object().SerializeAsString()==original.snapshot_prepare().source_object().SerializeAsString(),"Retained snapshot receipt differs");
                        journal.record_effect_receipt(old.operation_id,old.requester,effect.effect_id,old.request_digest,receipt.SerializeAsString());
                    } else if(effect.kind=="destination-allocation") {
                        workspace::v1::StorageCopyDestinationPrepare original;require(original.ParseFromString(effect.intent),"Malformed retained allocation");
                        workspace::v1::StorageCopyDestinationInspect inspect;inspect.set_operation_id(old.operation_id);inspect.set_request_digest(old.request_digest);
                        const auto receipt=impl_->storage->inspect_copy_destination(inspect);
                        require(receipt.allocation().SerializeAsString()==original.SerializeAsString() && receipt.state()!=workspace::v1::StorageCopyDestinationReceipt::UNCERTAIN,"Retained destination is uncertain");
                        journal.record_effect_receipt(old.operation_id,old.requester,effect.effect_id,old.request_digest,receipt.SerializeAsString());
                    } else if(effect.kind=="publication") {
                        workspace::v1::StorageCopyDestinationInspect inspect;inspect.set_operation_id(old.operation_id);inspect.set_request_digest(old.request_digest);
                        const auto receipt=impl_->storage->inspect_copy_destination(inspect);
                        if(receipt.state()==workspace::v1::StorageCopyDestinationReceipt::PUBLISHED) {
                            std::vector<workspace::v1::StorageObjectState> objects(receipt.objects().begin(),receipt.objects().end());
                            objects.insert(objects.end(),receipt.state_companions().begin(),receipt.state_companions().end());
                            journal.record_publication_receipts(old.operation_id,old.requester,effect.effect_id,objects);
                        }
                    }
                } catch(const std::exception&) { /* Keep the unresolved exact intent. */ }
            }
            journal.mark_uncertain(old.operation_id,old.requester,copy::COPY_UNCERTAIN);
        }
    }
    if(!impl_->copy_task) {
        const auto queued=journal.pending();if(queued.empty())return;
        auto task=std::make_unique<Impl::CopyTask>();task->id=queued.front().operation_id;task->requester=queued.front().requester;
        if(!task->binding.ParseFromString(queued.front().authorization_binding) || task->binding.protocol_version()!=1 ||
           task->binding.domains_size()<2 || task->binding.domains_size()>5 || task->binding.state_companions_size()>32 ||
           task->binding.destination_workspace_id().size()!=32) {
            journal.mark_uncertain(task->id,task->requester,copy::COPY_UNAVAILABLE);return;
        }
        impl_->copy_task=std::move(task);
    }
    auto& task=*impl_->copy_task;
    auto operation=journal.get(task.id,task.requester);
    const auto request=journal.request(task.id,task.requester);
    const auto& binding=task.binding;
    const auto domain_count=binding.domains_size()+binding.state_companions_size();
    require(binding.domains_size()>=2 && binding.domains_size()<=5 && binding.state_companions_size()<=32 && domain_count<=37,
            "Copy domain/companion bounds are invalid");
    const auto planned=[&](int index) {
        if(index<binding.domains_size())return binding.domains(index);
        const auto& state=binding.state_companions(index-binding.domains_size());
        workspace::v1::WorkspaceCopyPlannedDomain result;result.set_data_class(copy::APPLICATION_STATE);
        *result.mutable_snapshot()=state.snapshot();*result.mutable_staging()=state.staging();*result.mutable_destination()=state.destination();return result;
    };
    const auto catalog_entry=[&](int index)->const workspace::v1::WorkspaceCopyCatalogEntry& {
        if(index<binding.domains_size())return binding.owner_catalog().entries(index);
        for(const auto& entry:binding.owner_catalog().entries())if(entry.data_class()==copy::APPLICATION_STATE)return entry;
        throw WorldError(wire::STORAGE_ERROR,"Companion copy omitted the selected state descriptor");
    };
    const auto effect=[&](const std::string& kind,int index=0)->std::optional<CopyJournal::Effect> {
        const auto id=copy_effect_id(task.id,kind,index);
        for(const auto& found:journal.effects(task.id,task.requester))if(found.effect_id==id)return found;
        return {};
    };
    const auto intent=[&](const std::string& kind,const std::string& bytes,int index=0) {
        journal.record_effect_intent(task.id,task.requester,copy_effect_id(task.id,kind,index),kind,operation.request_digest,bytes);
    };
    const auto receipt=[&](const std::string& kind,const std::string& bytes,int index=0) {
        journal.record_effect_receipt(task.id,task.requester,copy_effect_id(task.id,kind,index),operation.request_digest,bytes);
    };
    const auto deadline=[&] {
        const auto now=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
        if(binding.boot_id()!=copy_boot() || binding.accepted_monotonic_ns()==0 || now<0 ||
           static_cast<std::uint64_t>(now)<binding.accepted_monotonic_ns() ||
           static_cast<std::uint64_t>(now)-binding.accepted_monotonic_ns()>=300ULL*1000*1000*1000)
            throw WorldError(wire::TIMEOUT,"Workspace copy absolute operation budget expired");
    };
    const auto revalidate=[&]{deadline();revalidate_copy(operation);deadline();};
    const auto release=[&] {
        if(!task.lease_started || task.released)return;
        workspace::v1::WorkspaceQuiescenceReceipt released;released.set_lease_id(copy_effect_id(task.id,"quiescence"));
        released.set_workspace_id(request.source_workspace_id());released.set_boot_id(binding.boot_id());released.set_released(true);
        intent("quiescence-release",released.SerializeAsString());
        bool thaw_failed=false;
        for(const auto& id:task.applications) {
            const auto found=impl_->jobs.find(id);
            if(found==impl_->jobs.end()) {thaw_failed=true;continue;}
            std::optional<supervisor::v1::PayloadQuiescenceReceipt> process;
            try {process=found->second->quiescence_receipt();}catch(...){}
            // Always attempt every owned thaw, even when a different process receipt is unavailable.
            try {found->second->release_quiescence();}catch(...) {thaw_failed=true;continue;}
            if(process){process->set_frozen(false);*released.add_applications()=*process;}
        }
        if(thaw_failed)throw WorldError(wire::UNAVAILABLE,"One or more owned payload thaws could not be proven");
        impl_->copy_barriers.erase(request.source_workspace_id());task.released=true;
        receipt("quiescence-release",released.SerializeAsString());
    };
    try {
        revalidate();
        if(operation.phase==copy::REQUESTED) {
            journal.transition(task.id,task.requester,copy::REQUESTED,copy::AUTHORIZED,CopyJournal::digest(operation.authorization_binding));return;
        }
        if(operation.phase==copy::AUTHORIZED) {
            if(!task.lease_started) {
                // A setup in progress may not have released its held payload yet.
                if(impl_->launching_workspaces.contains(request.source_workspace_id()))return;
                workspace::v1::WorkspaceQuiescenceReceipt lease;lease.set_lease_id(copy_effect_id(task.id,"quiescence"));
                lease.set_workspace_id(request.source_workspace_id());lease.set_boot_id(binding.boot_id());lease.set_launch_barrier(true);lease.set_filesystem_mutator_barrier(true);
                intent("quiescence",lease.SerializeAsString());
                impl_->copy_barriers.emplace(request.source_workspace_id(),task.id);task.lease_started=true;
                {Query q(impl_->db.get(),"SELECT id FROM processes WHERE workspace=? AND state='running' ORDER BY id");q.text(1,request.source_workspace_id());
                 while(q.step()==SQLITE_ROW)task.applications.push_back(q.text(0));}
                for(const auto& id:task.applications) {
                    const auto found=impl_->jobs.find(id);if(found==impl_->jobs.end())throw WorldError(wire::UNAVAILABLE,"Copy source process has no trusted supervisor");
                    found->second->begin_quiescence();
                }
                return;
            }
            if(!task.lease_recorded) {
                workspace::v1::WorkspaceQuiescenceReceipt lease;lease.set_lease_id(copy_effect_id(task.id,"quiescence"));
                lease.set_workspace_id(request.source_workspace_id());lease.set_boot_id(binding.boot_id());lease.set_launch_barrier(true);lease.set_filesystem_mutator_barrier(true);
                for(const auto& id:task.applications) {
                    const auto found=impl_->jobs.find(id);if(found==impl_->jobs.end())throw WorldError(wire::UNAVAILABLE,"Copy source process ownership changed");
                    if(!found->second->quiescent())return;
                    *lease.add_applications()=found->second->quiescence_receipt();
                }
                receipt("quiescence",lease.SerializeAsString());task.lease_recorded=true;revalidate();return;
            }
            if(task.snapshot_index<domain_count) {
                const auto index=task.snapshot_index;const auto domain=planned(index);const auto& entry=catalog_entry(index);
                workspace::v1::StorageOwnerRequest snapshot;
                auto* capture=snapshot.mutable_snapshot_prepare();
                *capture->mutable_source_object()=index<binding.domains_size()?entry.source_object():
                    binding.state_companions(index-binding.domains_size()).source().state_object();
                *capture->mutable_snapshot_object()=domain.snapshot();
                capture->set_operation_id(copy_effect_id(task.id,"snapshot",index));capture->set_request_digest(operation.request_digest);
                intent("snapshot",snapshot.SerializeAsString(),index);revalidate();
                const auto captured=impl_->storage->call(std::move(snapshot));
                receipt("snapshot",captured.SerializeAsString(),index);revalidate();++task.snapshot_index;return;
            }
            // Recheck the complete frozen process inventory after capturing every selected mutable domain.
            for(const auto& id:task.applications) {
                const auto found=impl_->jobs.find(id);if(found==impl_->jobs.end())throw WorldError(wire::UNAVAILABLE,"Quiescence process inventory was lost");
                (void)found->second->quiescence_receipt();
            }
            release();revalidate();
            journal.transition(task.id,task.requester,copy::AUTHORIZED,copy::SNAPSHOTS_READY,CopyJournal::digest(binding.SerializeAsString()));return;
        }
        const auto allocation=copy_allocation(operation,binding);
        if(operation.phase==copy::SNAPSHOTS_READY) {
            intent("destination-allocation",allocation.SerializeAsString());revalidate();
            const auto allocated=impl_->storage->prepare_copy_destination(allocation);
            if(binding.state_companions_size()>0) {
                std::uint64_t expected_maximum=0;const auto& profile=binding.owner_catalog().destination_profile();
                for(const auto& application:profile.storage_policy().profiles())
                    if(application.name()==profile.application_profile())expected_maximum=std::min(application.state_bytes(),profile.storage_policy().total_bytes());
                require(expected_maximum>0 && allocated.state_companions_size()==binding.state_companions_size(),"Independent companion quota policy is unavailable");
                for(const auto& state:allocated.state_companions())if(state.maximum_bytes()!=expected_maximum) {
                    impl_->storage->fence_unavailable();throw WorldError(wire::UNAVAILABLE,"Owner companion quota receipt differs from independently pinned destination policy");
                }
            }
            receipt("destination-allocation",allocated.SerializeAsString());revalidate();
            if(allocated.state()!=workspace::v1::StorageCopyDestinationReceipt::ALLOCATED)
                throw WorldError(wire::UNAVAILABLE,"Copy destination allocation is uncertain");
            journal.transition(task.id,task.requester,copy::SNAPSHOTS_READY,copy::DESTINATION_ALLOCATED,allocated.SerializeAsString());return;
        }
        if(operation.phase==copy::DESTINATION_ALLOCATED) {
            if(task.stage_index>=domain_count) {
                journal.transition(task.id,task.requester,copy::DESTINATION_ALLOCATED,copy::DATA_STAGED,CopyJournal::digest(allocation.SerializeAsString()));return;
            }
            const auto index=task.stage_index;const auto domain=planned(index);const auto& entry=catalog_entry(index);
            const auto snapshot_effect=effect("snapshot",index);require(snapshot_effect && !snapshot_effect->uncertain,"Copy snapshot has no exact receipt");
            workspace::v1::StorageObjectState snapshot;require(snapshot.ParseFromString(snapshot_effect->receipt),"Malformed snapshot receipt");
            const auto destination_effect=effect("destination-allocation");require(destination_effect && !destination_effect->uncertain,"Destination allocation receipt is absent");
            workspace::v1::StorageCopyDestinationReceipt destination;require(destination.ParseFromString(destination_effect->receipt),"Malformed destination receipt");
            workspace::v1::StorageCopyStageBegin stage;*stage.mutable_snapshot_object()=domain.snapshot();*stage.mutable_destination_object()=domain.staging();
            stage.set_operation_id(copy_effect_id(task.id,"stage",index));stage.set_request_digest(operation.request_digest);
            const auto& destination_state=index<binding.domains_size()?destination.objects(index):
                destination.state_companions(index-binding.domains_size());
            const auto& profile=binding.owner_catalog().destination_profile();
            require(task.staged_bytes<profile.maximum_total_bytes() && task.staged_entries<profile.maximum_entries(),"Copy aggregate staging budget exhausted");
            stage.set_maximum_bytes(std::min({entry.maximum_bytes(),destination_state.maximum_bytes(),profile.maximum_total_bytes()-task.staged_bytes}));
            stage.set_maximum_entries(std::min(entry.maximum_entries(),profile.maximum_entries()-task.staged_entries));
            if(!task.worker) {
                intent("stage-begin",stage.SerializeAsString(),index);revalidate();
                const auto begun=impl_->storage->begin_copy_stage(stage);validate_copy_stage_receipt(begun,stage,snapshot,destination_state,*impl_->storage);receipt("stage-begin",begun.SerializeAsString(),index);revalidate();
                require(begun.state()==workspace::v1::StorageCopyStageReceipt::PREPARED,"New copy stage did not return PREPARED");
                workspace::v1::StorageCopyStageStart claim;claim.set_operation_id(stage.operation_id());claim.set_request_digest(stage.request_digest());
                intent("stage-start",claim.SerializeAsString(),index);revalidate();
                const auto claimed=impl_->storage->start_copy_stage(claim);validate_copy_stage_receipt(claimed,stage,snapshot,destination_state,*impl_->storage);receipt("stage-start",claimed.SerializeAsString(),index);revalidate();
                require(claimed.claimed() && claimed.state()==workspace::v1::StorageCopyStageReceipt::COPYING,"New stage was not exclusively claimed");
                intent("worker-launch",stage.SerializeAsString(),index);intent("worker-output",stage.SerializeAsString(),index);revalidate();
                File source,dest;source.fd=impl_->storage->open_object(snapshot);dest.fd=impl_->storage->open_object(destination_state);
                workspace::copyworker::v1::StateExportRequest export_request;
                const bool companion=index>=binding.domains_size();
                if(companion) {
                    const auto& mapped=binding.state_companions(index-binding.domains_size()).source();
                    export_request.set_key(mapped.key());export_request.set_schema_id(mapped.contract().schema_id());
                    export_request.set_schema_version(mapped.contract().schema_version());
                    for(const auto subdomain:mapped.contract().subdomains())export_request.add_subdomains(subdomain);
                }
                task.worker=std::make_unique<CopyTreeJob>(*impl_->resources,source.fd,dest.fd,snapshot.subvolume_id(),
                    destination_state.subvolume_id(),stage.maximum_bytes(),stage.maximum_entries(),companion?&export_request:nullptr);
                receipt("worker-launch",task.worker->identity().SerializeAsString(),index);revalidate();return;
            }
            const auto copied=task.worker->poll();if(!copied)return;
            workspace::copyworker::v1::CopyReceipt copied_record;copied_record.set_actual_bytes(copied->bytes);copied_record.set_actual_entries(copied->entries);copied_record.set_content_digest(copied->digest);
            receipt("worker-output",copied_record.SerializeAsString(),index);task.worker.reset();revalidate();
            workspace::v1::StorageCopyStageSeal seal;*seal.mutable_snapshot_object()=domain.snapshot();*seal.mutable_destination_object()=domain.staging();
            seal.set_operation_id(stage.operation_id());seal.set_request_digest(stage.request_digest());seal.set_actual_bytes(copied->bytes);seal.set_actual_entries(copied->entries);seal.set_content_digest(copied->digest);
            intent("stage-seal",seal.SerializeAsString(),index);revalidate();
            const auto sealed=impl_->storage->seal_copy_stage(seal);
            validate_copy_stage_receipt(sealed,stage,snapshot,destination_state,*impl_->storage);
            if(sealed.state()!=workspace::v1::StorageCopyStageReceipt::SEALED || sealed.actual_bytes()!=copied->bytes ||
               sealed.actual_entries()!=copied->entries || sealed.content_digest()!=copied->digest) {
                impl_->storage->fence_unavailable();throw WorldError(wire::UNAVAILABLE,"Sealed stage receipt differs from the actual worker result");
            }
            receipt("stage-seal",sealed.SerializeAsString(),index);revalidate();
            task.staged_bytes+=copied->bytes;task.staged_entries+=copied->entries;++task.stage_index;return;
        }
        if(operation.phase==copy::DATA_STAGED) {
            if(!task.worker) {
                workspace::copyworker::v1::WorkerRequest finalization;finalization.set_protocol_version(2);
                finalization.set_maximum_bytes(binding.owner_catalog().destination_profile().maximum_total_bytes());
                finalization.set_maximum_entries(binding.owner_catalog().destination_profile().maximum_entries());
                auto* job=finalization.mutable_finalization();job->set_source_repository_path(binding.source_repository_path());job->set_derive_head_from_snapshot(true);
                job->set_destination_application_profile(binding.owner_catalog().destination_profile().application_profile());
                for(const auto& entry:binding.owner_catalog().entries()) {
                    if(entry.data_class()==copy::APPLICATION_STATE)job->set_state_collection_version(entry.schema_version());
                    if(entry.data_class()==copy::APPLICATION_DEFINITION)job->set_definition_collection_version(entry.schema_version());
                }
                for(int index=0;index<binding.state_companions_size();++index) {
                    const auto& state=binding.state_companions(index);
                    const auto sealed_effect=effect("stage-seal",binding.domains_size()+index);
                    require(sealed_effect && !sealed_effect->uncertain,"Companion finalization omitted a sealed export receipt");
                    workspace::v1::StorageCopyStageReceipt sealed;require(sealed.ParseFromString(sealed_effect->receipt),"Malformed companion sealed receipt");
                    auto* validation=job->add_state_companions();validation->set_key(state.source().key());
                    validation->set_source_storage_object(state.source().state_object().SerializeAsString());
                    validation->set_snapshot_storage_object(state.snapshot().SerializeAsString());
                    validation->set_destination_storage_object(state.destination().SerializeAsString());
                    validation->set_contract_digest(CopyJournal::digest(state.source().contract().SerializeAsString()));
                    validation->set_actual_bytes(sealed.actual_bytes());validation->set_actual_entries(sealed.actual_entries());
                    validation->set_content_digest(sealed.content_digest());
                }
                std::vector<int> descriptors;std::vector<std::unique_ptr<File>> retained;
                for(const auto& domain:binding.domains()) {
                    job->add_classes(domain.data_class());
                    if(domain.data_class()==copy::PROJECT_TREE) {
                        job->set_destination_project_path((impl_->state/"worktrees"/domain.destination().id()).string());job->set_destination_admin_id(domain.destination().id());
                    }
                    if(domain.data_class()==copy::GIT_HISTORY)job->set_destination_repository_path((impl_->state/"repositories"/(domain.destination().id()+".git")).string());
                    workspace::v1::StorageOwnerRequest inspect;*inspect.mutable_inspect()->mutable_object()=domain.staging();const auto current=impl_->storage->call(std::move(inspect));revalidate();
                    auto fd=std::make_unique<File>();fd->fd=impl_->storage->open_object(current);descriptors.push_back(fd->fd);retained.push_back(std::move(fd));
                }
                intent("finalize-launch",finalization.SerializeAsString());intent("destination-validation",finalization.SerializeAsString());revalidate();
                task.worker=std::make_unique<CopyTreeJob>(*impl_->resources,descriptors,finalization);task.worker_finalizing=true;
                receipt("finalize-launch",task.worker->identity().SerializeAsString());revalidate();return;
            }
            const auto validated=task.worker->poll();if(!validated)return;
            require(!validated->validation.empty(),"Finalization omitted its separate destination receipt");
            receipt("destination-validation",validated->validation);task.worker.reset();revalidate();
            std::vector<copy::CopyManifestEntry> entries;std::map<std::string,copy::SnapshotReceipt> snapshots;std::string manifest;
            for(int index=0;index<binding.domains_size();++index) {
                const auto& domain=binding.domains(index);const auto& entry=binding.owner_catalog().entries(index);
                const auto original=effect("snapshot",index),staged=effect("stage-seal",index);
                require(original && staged && !original->uncertain && !staged->uncertain,"Manifest lacks exact source or staging receipts");
                workspace::v1::StorageObjectState capture;workspace::v1::StorageCopyStageReceipt sealed;
                require(capture.ParseFromString(original->receipt) && sealed.ParseFromString(staged->receipt),"Malformed source/stage receipt");
                copy::CopyManifestEntry item;item.set_data_class(entry.data_class());item.set_object_id(copy_effect_id(task.id,"manifest",index));
                item.set_schema_id(entry.schema_id());item.set_schema_version(entry.schema_version());item.set_source_storage_object(entry.source_object().SerializeAsString());
                item.set_byte_count(sealed.actual_bytes());item.set_content_digest(sealed.content_digest());item.set_snapshot_receipt_id(copy_effect_id(task.id,"snapshot",index));
                item.set_destination_validation_digest(CopyJournal::digest(validated->validation));
                copy::SnapshotReceipt source;source.set_source_storage_object(item.source_storage_object());source.set_snapshot_storage_object(domain.snapshot().SerializeAsString());
                source.set_source_subvolume_id(capture.source_subvolume_id());source.set_snapshot_subvolume_id(capture.subvolume_id());source.set_content_digest(sealed.content_digest());
                source.set_readonly(true);source.set_receipt_id(item.snapshot_receipt_id());source.set_request_digest(operation.request_digest);
                snapshots.emplace(source.receipt_id(),source);
                if(domain.data_class()==copy::APPLICATION_STATE && entry.schema_version()==2) {
                    for(int state_index=0;state_index<binding.state_companions_size();++state_index) {
                        const auto planned_index=binding.domains_size()+state_index;const auto& state=binding.state_companions(state_index);
                        const auto captured_effect=effect("snapshot",planned_index),sealed_effect=effect("stage-seal",planned_index);
                        require(captured_effect && sealed_effect && !captured_effect->uncertain && !sealed_effect->uncertain,"Companion manifest omitted source/stage evidence");
                        workspace::v1::StorageObjectState captured;workspace::v1::StorageCopyStageReceipt exported;
                        require(captured.ParseFromString(captured_effect->receipt) && exported.ParseFromString(sealed_effect->receipt),"Malformed companion source/stage receipt");
                        auto* component=item.add_state_components();component->set_key(state.source().key());component->set_contract(state.source().contract().SerializeAsString());
                        component->set_source_storage_object(state.source().state_object().SerializeAsString());component->set_destination_storage_object(state.destination().SerializeAsString());
                        component->set_snapshot_receipt_id(copy_effect_id(task.id,"snapshot",planned_index));component->set_byte_count(exported.actual_bytes());
                        component->set_entry_count(exported.actual_entries());component->set_content_digest(exported.content_digest());
                        copy::SnapshotReceipt witness;witness.set_source_storage_object(component->source_storage_object());
                        witness.set_snapshot_storage_object(state.snapshot().SerializeAsString());witness.set_source_subvolume_id(captured.source_subvolume_id());
                        witness.set_snapshot_subvolume_id(captured.subvolume_id());witness.set_content_digest(component->content_digest());
                        witness.set_readonly(true);witness.set_receipt_id(component->snapshot_receipt_id());witness.set_request_digest(operation.request_digest);
                        snapshots.emplace(witness.receipt_id(),witness);
                    }
                }
                entries.push_back(item);copy_frame(manifest,item.SerializeAsString());
            }
            journal.record_validated_result(task.id,task.requester,binding.destination_workspace_id(),manifest,entries,snapshots,
                binding.owner_catalog().destination_profile().maximum_total_bytes(),static_cast<std::size_t>(binding.domains_size()));return;
        }
        if(operation.phase==copy::VALIDATED) {
            const auto validation=effect("destination-validation");require(validation && !validation->uncertain,"Publication lacks destination validation");
            workspace::copyworker::v1::FinalizeReceipt final;require(final.ParseFromString(validation->receipt),"Malformed destination validation");
            workspace::v1::StorageCopyDestinationPublish publish;*publish.mutable_allocation()=allocation;*publish.mutable_validation()=final;
            std::vector<CopyJournal::PublicationTarget> targets;
            const auto publication_digest=CopyJournal::digest(publish.SerializeAsString());
            for(const auto& domain:binding.domains())targets.push_back({domain.destination(),publication_digest});
            for(const auto& state:binding.state_companions())targets.push_back({state.destination(),publication_digest});
            const auto publication=copy_effect_id(task.id,"publication");journal.record_publication_intent(task.id,task.requester,publication,targets);revalidate();
            const auto published=impl_->storage->publish_copy_destination(publish);
            require(published.state()==workspace::v1::StorageCopyDestinationReceipt::PUBLISHED,"Owner publication receipt is uncertain");
            std::vector<workspace::v1::StorageObjectState> published_objects(published.objects().begin(),published.objects().end());
            published_objects.insert(published_objects.end(),published.state_companions().begin(),published.state_companions().end());
            const auto publication_receipt=journal.record_publication_receipts(task.id,task.requester,publication,published_objects);
            // Store the actual owner receipt before checking authority after the yielding effect.
            try {revalidate();}catch(...) {journal.mark_uncertain(task.id,task.requester,copy::COPY_UNCERTAIN);throw;}
            std::string repository,project;
            for(const auto& domain:binding.domains()) {
                if(domain.data_class()==copy::PROJECT_TREE)project=domain.destination().id();
                if(domain.data_class()==copy::GIT_HISTORY)repository=domain.destination().id();
            }
            wire::WorkspaceCreateRequest registry;registry.set_id(binding.destination_workspace_id());registry.set_worktree(project);
            *registry.mutable_policy()=binding.owner_catalog().destination_profile().execution_policy();
            workspace::v1::WorkspaceCopyRegistryPublication registry_intent;registry_intent.set_protocol_version(1);
            registry_intent.set_workspace_create(registry.SerializeAsString());registry_intent.mutable_repository()->set_kind(workspace::v1::StorageObject::REPOSITORY);
            registry_intent.mutable_repository()->set_id(repository);registry_intent.set_head(final.head());registry_intent.set_branch(final.branch());
            registry_intent.set_resource_profile(binding.destination_resource_profile());
            registry_intent.set_owner_state_mapping_revision(published.destination_state_mapping_revision());
            for(const auto& state:binding.state_companions()) {
                auto* configured=registry_intent.add_states();configured->set_key(state.source().key());*configured->mutable_contract()=state.source().contract();
                *configured->mutable_state_object()=state.destination();
            }
            intent("registry-publication",registry_intent.SerializeAsString());revalidate();
            {
                Transaction tx(impl_->db.get());
                Query repo(impl_->db.get(),"INSERT INTO repositories VALUES(?,'workspace-copy',?,'ready')");repo.text(1,repository);
                repo.text(2,(impl_->state/"repositories"/(repository+".git")).string());repo.step();
                Query tree(impl_->db.get(),"INSERT INTO worktrees VALUES(?,?,?,?,?,'ready')");tree.text(1,project);tree.text(2,repository);tree.text(3,final.branch());
                tree.text(4,(impl_->state/"worktrees"/project).string());tree.text(5,final.head());tree.step();
                Query workspace(impl_->db.get(),"INSERT INTO workspaces VALUES(?,?,?,?,1)");workspace.text(1,registry.id());workspace.text(2,project);workspace.text(3,repository);
                check(sqlite3_bind_int(workspace.value,4,static_cast<int>(rights_mask(registry.policy()))));workspace.step();
                Query policy(impl_->db.get(),"INSERT INTO workspace_copy_policies VALUES(?,?)");policy.text(1,registry.id());policy.blob(2,binding.destination_resource_profile());policy.step();
                for(const auto& state:binding.state_companions()) {
                    Query configured(impl_->db.get(),"INSERT INTO configured_application_states VALUES(?,?,?,?)");
                    configured.text(1,registry.id());configured.text(2,state.source().key());configured.text(3,state.destination().id());
                    configured.blob(4,state.source().contract().SerializeAsString());configured.step();
                }
                tx.commit();
            }
            if(registry_intent.states_size()>0) {
                workspace::v1::ConfiguredStateMapping mapping;mapping.set_workspace_id(registry.id());
                mapping.set_revision(registry_intent.owner_state_mapping_revision());*mapping.mutable_entries()=registry_intent.states();
                impl_->validate_state_mapping(mapping);
            }
            registry_intent.set_workspace_reply(workspace_get(registry.id()).SerializeAsString());
            receipt("registry-publication",registry_intent.SerializeAsString());
            workspace::v1::WorkspaceCopyResourceProfile destination_policy;require(destination_policy.ParseFromString(binding.destination_resource_profile()),"Pinned resource policy is malformed");
            impl_->resources->register_copy_workspace(registry.id(),destination_policy);
            journal.transition(task.id,task.requester,copy::VALIDATED,copy::PUBLISHED,publication_receipt);
            impl_->copy_task.reset();return;
        }
        impl_->copy_task.reset();
    } catch(const std::exception& error) {
        constexpr char digits[]="0123456789abcdef";std::string operation_hex;
        for(const unsigned char byte:task.id){operation_hex+=digits[byte>>4];operation_hex+=digits[byte&15];}
        const auto* reason=error.what();std::size_t length=0;
        while(length<512 && reason[length])++length;
        std::string detail(reason,length);
        for(auto& byte:detail)if(static_cast<unsigned char>(byte)<32 || static_cast<unsigned char>(byte)>126)byte='?';
        std::cerr<<"workspace copy failed operation="<<operation_hex<<" phase="<<operation.phase<<" reason="<<detail<<'\n';
        bool cleanup_uncertain=false;
        try {release();}catch(...) {cleanup_uncertain=true;}
        task.worker.reset();
        operation=journal.get(task.id,task.requester);
        if(operation.phase>=copy::REQUESTED && operation.phase<=copy::VALIDATED) {
            const auto pending=journal.effects(task.id,task.requester);
            const bool unresolved=std::any_of(pending.begin(),pending.end(),[](const auto& item){return item.uncertain;});
            const bool publication_effect=std::any_of(pending.begin(),pending.end(),[](const auto& item){return item.kind=="publication";});
            if(cleanup_uncertain || unresolved || publication_effect)journal.mark_uncertain(task.id,task.requester,copy::COPY_UNCERTAIN);
            else {
                const auto* failure=dynamic_cast<const WorldError*>(&error);
                journal.abort(task.id,task.requester,failure && failure->code==wire::FORBIDDEN?copy::COPY_FORBIDDEN:
                    failure && failure->code==wire::RESOURCE_EXHAUSTED?copy::COPY_EXHAUSTED:
                    failure && failure->code==wire::REVISION_CONFLICT?copy::COPY_CONFLICT:copy::COPY_UNAVAILABLE,operation.request_digest);
            }
        }
        // Never silently clear a barrier whose owned freeze could not be restored.
        if(!cleanup_uncertain)impl_->copy_barriers.erase(request.source_workspace_id());
        impl_->copy_task.reset();
    }
}

}
