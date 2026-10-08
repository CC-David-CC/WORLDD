#include "workspace/store.hpp"
#include "workspace/git_command.hpp"
#include "workspace/status.hpp"
#include "workspace/process.hpp"
#include "workspace/native_launch.hpp"
#include "workspace/authority.hpp"
#include "world/store.hpp"
#include <sqlite3.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <linux/fs.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <optional>
#include <set>
#include <fstream>
#include <map>
#include <cmath>

namespace spatial {
namespace {
void require(bool value, const char* message) { if (!value) throw std::invalid_argument(message); }
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
std::string checked(const std::filesystem::path& directory, const std::vector<std::string>& argv) {
    const auto result = git_command(directory, argv);
    if (result.status != 0) throw WorldError(wire::STORAGE_ERROR, "Git operation failed; no success acknowledged");
    return result.output;
}
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

struct ExecutionStore::Impl {
    std::filesystem::path state;
    const bool fixture_faults;
    bool exit_after_worktree = false;
    File lock;
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    std::map<std::string, std::unique_ptr<ApplicationProcess>> jobs;
    WorkspaceAuthority* authority = nullptr;
    NativeLaunch* native = nullptr;
    explicit Impl(const std::filesystem::path& directory, unsigned migrate_from, bool enable_faults) : fixture_faults(enable_faults) {
        require(!migrate_from || !fixture_faults, "Migration cannot enable fixture faults");
        require(directory.is_absolute() && !std::filesystem::is_symlink(directory), "Execution state requires an absolute nonsymlink directory");
        if (migrate_from) require(std::filesystem::is_regular_file(directory / "execution.db"), "Migration requires an existing execution database");
        std::filesystem::create_directories(directory);
        state = std::filesystem::canonical(directory);
        lock.fd = open((state / "execution.lock").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (lock.fd < 0 || flock(lock.fd, LOCK_EX | LOCK_NB) != 0)
            throw WorldError(wire::IN_USE, "Execution state already has an authority or cannot be locked");
        for (const auto* name : {"execution.db", "execution.db-journal", "execution.db-wal", "execution.db-shm", "repositories", "worktrees", "effects", "retired", "applications"})
            require(!std::filesystem::is_symlink(state / name), "Execution metadata must not be a symlink");
        std::filesystem::create_directories(state / "repositories");
        std::filesystem::create_directories(state / "worktrees");
        std::filesystem::create_directories(state / "effects");
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
        if (version < 0 || version > 4 || (migrate_from && (version == 0 || (version != 4 && version != static_cast<int>(migrate_from)))))
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
            transaction.commit();
        }
        { Query q(db.get(), "PRAGMA quick_check"); q.step(); if (q.text(0) != "ok") throw WorldError(wire::STORAGE_ERROR, "Execution registry integrity failure"); }
        { Query q(db.get(), "PRAGMA foreign_key_check"); if (q.step() != SQLITE_DONE) throw WorldError(wire::STORAGE_ERROR, "Execution registry reference failure"); }
        if (version == 1) {
            Transaction transaction(db.get());
            Query tables(db.get(), "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name");
            for (const auto* expected : {"operations", "repositories", "worktrees"})
                if (tables.step() != SQLITE_ROW || tables.text(0) != expected) throw WorldError(wire::STORAGE_ERROR, "Unexpected v1 schema");
            if (tables.step() != SQLITE_DONE) throw WorldError(wire::STORAGE_ERROR, "Unexpected v1 schema");
            Query old_operations(db.get(), "SELECT id,payload,result,worktree,state FROM operations LIMIT 0");
            Query old_worktrees(db.get(), "SELECT id,repo,branch,path,head,state FROM worktrees LIMIT 0");
            Query old_repositories(db.get(), "SELECT id,source,path,state FROM repositories LIMIT 0");
            sql(db.get(), removal_schema); sql(db.get(), workspace_schema); sql(db.get(), process_schema); transaction.commit();
        } else if (version == 2) {
            Transaction transaction(db.get());
            Query old_removals(db.get(), "SELECT id,payload,record,worktree,retired,state FROM removals LIMIT 0");
            sql(db.get(), workspace_schema); sql(db.get(), process_schema); transaction.commit();
        } else if (version == 3) {
            Transaction transaction(db.get());
            Query old_workspaces(db.get(), "SELECT id,worktree,repo,policy,revision FROM workspaces LIMIT 0");
            Query old_members(db.get(), "SELECT workspace,actor,rights FROM workspace_members LIMIT 0");
            sql(db.get(), process_schema); transaction.commit();
        }
        std::filesystem::create_directories(state / "retired");
        std::filesystem::create_directories(state / "applications");
        if (migrate_from) { sync_path(state, true); return; }
        sql(db.get(), "UPDATE processes SET state='uncertain' WHERE state IN ('pending','running')");
        // A partial clone has no reliable completion witness. Preserve it for explicit repair.
        sql(db.get(), "UPDATE repositories SET state='uncertain' WHERE state='pending'");
        std::vector<std::string> pending;
        { Query q(db.get(), "SELECT id FROM operations WHERE state='pending'"); while (q.step() == SQLITE_ROW) pending.push_back(q.text(0)); }
        for (const auto& id : pending) reconcile(id);
        pending.clear();
        { Query q(db.get(), "SELECT id FROM removals WHERE state='pending'"); while (q.step() == SQLITE_ROW) pending.push_back(q.text(0)); }
        for (const auto& id : pending) reconcile_removal(id);
        sync_path(state, true); sync_path(state.parent_path(), true);
    }
    std::filesystem::path allocate(const char* collection, const char* suffix = "") {
        Query q(db.get(), "SELECT lower(hex(randomblob(16)))"); q.step();
        const auto path = state / collection / (q.text(0) + suffix);
        require(!std::filesystem::exists(path), "Allocated Git path already exists"); return path;
    }
    std::filesystem::path managed(const std::string& value, const char* collection) const {
        const std::filesystem::path path(value);
        if (path.parent_path() != state / collection || std::filesystem::is_symlink(path))
            throw WorldError(wire::STORAGE_ERROR, "Stored Git path escaped its execution registry");
        return path;
    }
    wire::RepositoryReply repository(const std::string& id) {
        Query q(db.get(), "SELECT path,source,state FROM repositories WHERE id=?"); q.text(1, id);
        if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Repository not registered");
        if (q.text(2) != "ready") throw WorldError(wire::STORAGE_ERROR, "Repository import is uncertain; explicit repair required");
        const auto path = managed(q.text(0), "repositories");
        // Writable repository configuration is data, not broker execution authority.
        const std::set<std::string> allowed{"core.repositoryformatversion", "core.filemode", "core.bare",
            "core.logallrefupdates", "core.ignorecase", "core.precomposeunicode", "remote.origin.url",
            "remote.origin.fetch", "extensions.objectformat", "user.name", "user.email"};
        for (const auto& entry : tokens(checked(path, {"config", "--local", "--no-includes", "--null", "--list"}))) {
            const auto end = entry.find('\n');
            if (!allowed.contains(entry.substr(0, end)))
                throw WorldError(wire::FORBIDDEN, "Repository configuration requires authority unavailable to administrative Git");
        }
        if (line(checked(path, {"rev-parse", "--is-bare-repository"})) != "true")
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
        const auto actual = line(checked(directory, {"rev-parse", "--path-format=absolute", "--git-common-dir"}));
        if (actual != repo.path()) throw WorldError(wire::STORAGE_ERROR, "Git resolved another execution authority");
        return out;
    }
    bool matches(const wire::WorktreeReply& record) {
        const auto repo = repository(record.repo());
        if (!std::filesystem::is_directory(record.path())) return false;
        bool path = false, branch = false, head = false;
        for (const auto& token : tokens(checked(repo.path(), {"worktree", "list", "--porcelain", "-z"}))) {
            if (token.empty()) {
                if (path && branch && head) {
                    const auto actual = git_command(record.path(), {"rev-parse", "--path-format=absolute", "--git-common-dir"});
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
        if (fsync(file.fd) != 0) throw WorldError(wire::STORAGE_ERROR, "Cannot flush Git completion receipt");
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
    wire::GitStatusReply current_status(const std::filesystem::path& path) {
        return parse_git_status(checked(path, {"status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all", "--ignored=matching"}));
    }
    wire::GitStatusReply removable(const std::filesystem::path& path) {
        const auto status = current_status(path);
        if (!status.clean()) throw WorldError(wire::DIRTY_WORKTREE, "Worktree contains tracked, untracked, or ignored changes");
        for (const auto& entry : tokens(checked(path, {"ls-files", "-v", "-z"})))
            if (!entry.starts_with("H ")) throw WorldError(wire::DIRTY_WORKTREE, "Index flags can hide worktree changes; clear them before removal");
        for (const auto& entry : tokens(checked(path, {"ls-files", "--stage", "-z"})))
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
            const auto entries = tokens(checked(repo.path(), {"worktree", "list", "--porcelain", "-z"}));
            const auto pin = record.head().empty() ? "" : line(checked(repo.path(), {"rev-parse", "--verify", retirement_ref(retired)}));
            complete = std::filesystem::is_directory(retired / "data/project") && std::filesystem::is_directory(retired / "git") &&
                std::filesystem::canonical(retired / "data/project") == retired / "data/project" &&
                std::filesystem::canonical(retired / "git") == retired / "git" &&
                pin == record.head() && matches_bytes(retired / "complete", record.SerializeAsString()) &&
                std::find(entries.begin(), entries.end(), "worktree " + (retired / "staging/project").string()) == entries.end();
        } catch (const std::exception&) { complete = false; }
        removal_state(id, record.id(), complete);
    }
};

ExecutionStore::ExecutionStore(const std::filesystem::path& state, unsigned migrate_from, bool fixture_faults)
    : impl_(std::make_unique<Impl>(state, migrate_from, fixture_faults)) {}
ExecutionStore::~ExecutionStore() = default;
bool ExecutionStore::fixture_faults() const { return impl_->fixture_faults; }
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
    const auto source_git = git_command(source, {"rev-parse", "--absolute-git-dir"});
    require(source_git.status == 0, "Import source is not a Git repository");
    const auto alternates = std::filesystem::path(line(source_git.output)) / "objects/info/alternates";
    require(!std::filesystem::exists(alternates), "Import requires an independent source object store");
    const auto target = impl_->allocate("repositories", ".git");
    {
        Transaction transaction(impl_->db.get());
        Query q(impl_->db.get(), "INSERT INTO repositories VALUES(?,?,?,'pending')");
        q.text(1, request.id()); q.text(2, source.string()); q.text(3, target.string()); q.step(); transaction.commit();
    }
    checked(impl_->state, {"clone", "--bare", "--local", "--no-hardlinks", "--template=", "--", source.string(), target.string()});
    if (line(checked(target, {"rev-parse", "--is-bare-repository"})) != "true")
        throw WorldError(wire::STORAGE_ERROR, "Import did not create a bare repository");
    sync_tree(target); sync_path(target.parent_path(), true);
    {
        Transaction transaction(impl_->db.get());
        Query q(impl_->db.get(), "UPDATE repositories SET state='ready' WHERE id=?"); q.text(1, request.id()); q.step(); transaction.commit();
    }
    return impl_->repository(request.id());
}
wire::WorktreeReply ExecutionStore::create(const wire::WorktreeCreateRequest& request) {
    valid_text(request.repo()); valid_text(request.id()); valid_text(request.operation_id());
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
    require(request.branch().front() != '-' && git_command(repo.path(), {"check-ref-format", "refs/heads/" + request.branch()}).status == 0,
            "Invalid branch name");
    const auto list = tokens(checked(repo.path(), {"worktree", "list", "--porcelain", "-z"}));
    if (std::find(list.begin(), list.end(), "branch refs/heads/" + request.branch()) != list.end())
        throw WorldError(wire::BRANCH_IN_USE, "Branch already has a linked worktree");
    if (git_command(repo.path(), {"show-ref", "--verify", "--quiet", "refs/heads/" + request.branch()}).status == 0)
        throw WorldError(wire::ALREADY_EXISTS, "Requested new branch already exists");
    { Query q(impl_->db.get(), "SELECT id FROM worktrees WHERE id=?"); q.text(1, request.id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "Worktree identity already exists"); }
    const auto start = git_command(repo.path(), {"rev-parse", "--verify", "--end-of-options", request.start() + "^{commit}"});
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
    checked(repo.path(), {"worktree", "add", "-b", request.branch(), "--", out.path(), out.head()});
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
    return impl_->current_status(worktree.path());
}
wire::WorktreeRemoveReply ExecutionStore::remove(const wire::WorktreeRemoveRequest& request) {
    valid_text(request.id()); valid_text(request.operation_id());
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
    const auto admin = std::filesystem::path(line(checked(record.path(), {"rev-parse", "--absolute-git-dir"})));
    if (std::filesystem::exists(admin / "locked")) throw WorldError(wire::IN_USE, "Worktree is locked");
    IndexLock index_lock(admin);
    const auto status = impl_->removable(record.path()); record.set_head(status.head()); record.set_branch(status.branch());
    const auto retired = impl_->allocate("retired");
    {
        Transaction transaction(impl_->db.get());
        Query op(impl_->db.get(), "INSERT INTO removals VALUES(?,?,?,?,?,'pending')");
        op.text(1, request.operation_id()); op.blob(2, payload); op.blob(3, record.SerializeAsString()); op.text(4, record.id()); op.text(5, retired.string()); op.step();
        Query wt(impl_->db.get(), "UPDATE worktrees SET state='removing' WHERE id=?"); wt.text(1, record.id()); wt.step(); transaction.commit();
    }
    // Retain all project data. Git unregisters only an inaccessible private path.
    std::filesystem::create_directories(retired / "staging");
    std::filesystem::create_symlink(retired / "never-created", retired / "data");
    sync_path(retired / "staging", true); sync_path(retired, true); sync_path(impl_->state / "retired", true);
    const auto registered = retired / "staging/project";
    checked(repo.path(), {"worktree", "move", "--", record.path(), registered.string()});
    sync_path(impl_->state / "worktrees", true); sync_path(retired / "staging", true); sync_path(retired, true);
    const auto observed = impl_->removable(registered);
    if (observed.head() != record.head() || observed.branch() != record.branch())
        throw WorldError(wire::STORAGE_ERROR, "Git changed during retirement; retained worktree requires inspection");
    if (!record.head().empty()) checked(repo.path(), {"update-ref", impl_->retirement_ref(retired), record.head(), std::string(record.head().size(), '0')});
    std::filesystem::copy(admin, retired / "git", std::filesystem::copy_options::recursive | std::filesystem::copy_options::copy_symlinks);
    sync_tree(retired / "git"); sync_tree(registered);
    if (syscall(SYS_renameat2, AT_FDCWD, (retired / "staging").c_str(), AT_FDCWD, (retired / "data").c_str(), RENAME_EXCHANGE) != 0)
        throw WorldError(wire::STORAGE_ERROR, "Cannot reserve retired worktree path");
    sync_path(retired, true);
    checked(repo.path(), {"worktree", "remove", "--", registered.string()});
    sync_tree(retired); sync_tree(repo.path()); sync_path(impl_->state / "retired", true);
    File receipt; receipt.fd = open((retired / "complete").c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (receipt.fd < 0) throw WorldError(wire::STORAGE_ERROR, "Cannot create removal receipt");
    const auto bytes = record.SerializeAsString(); std::size_t used = 0;
    while (used < bytes.size()) {
        const auto count = write(receipt.fd, bytes.data() + used, bytes.size() - used);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw WorldError(wire::STORAGE_ERROR, "Cannot write removal receipt");
        used += static_cast<std::size_t>(count);
    }
    if (fsync(receipt.fd) != 0) throw WorldError(wire::STORAGE_ERROR, "Cannot flush removal receipt");
    sync_path(retired, true);
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
    valid_text(request.id()); valid_text(request.worktree()); const auto policy = rights_mask(request.policy());
    Transaction transaction(impl_->db.get());
    { Query q(impl_->db.get(), "SELECT id FROM workspaces WHERE id=?"); q.text(1, request.id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "Workspace identity already exists"); }
    const auto worktree = impl_->worktree(request.worktree());
    { Query q(impl_->db.get(), "SELECT id FROM workspaces WHERE worktree=? OR repo=?"); q.text(1, worktree.id()); q.text(2, worktree.repo());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::IN_USE, "Workspace execution state or Git administration already has an owner"); }
    Query q(impl_->db.get(), "INSERT INTO workspaces VALUES(?,?,?,?,1)"); q.text(1, request.id()); q.text(2, worktree.id()); q.text(3, worktree.repo());
    check(sqlite3_bind_int(q.value, 4, static_cast<int>(policy))); q.step(); transaction.commit();
    return workspace_get(request.id());
}
wire::WorkspaceAck ExecutionStore::workspace_member(const wire::WorkspaceMemberRequest& request) {
    if (impl_->authority && impl_->authority->bound(request.id()))
        throw WorldError(wire::FORBIDDEN, "Persistent workspace authority cannot use static membership edits");
    valid_text(request.id()); valid_text(request.actor()); const auto rights = rights_mask(request.rights());
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
    valid_text(request.workspace()); valid_text(request.actor()); const auto descriptor = workspace_get(request.workspace());
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
workspace::v1::ApplicationAccessReply ExecutionStore::application_access(const workspace::v1::ApplicationAccessRequest& request) {
    if (!request.has_principal()) throw WorldError(wire::FORBIDDEN, "Application access requires authenticated actor authority");
    const auto& principal = request.principal();
    require(principal.issuer_id().size() == 32 && principal.actor_id().size() == 32, "Invalid authenticated actor reference");
    require(request.applications_size() > 0 && request.applications_size() <= 64, "Application access requires one to 64 groups");
    std::set<std::string> unique;
    for (const auto& app : request.applications()) {
        valid_text(app.workspace()); valid_text(app.application());
        require(app.server_id().size() == 32, "Invalid native server identity");
        require(unique.insert(app.SerializeAsString()).second, "Duplicate application group");
    }
    // Prepare each workspace once. The complete batch uses current issuer status
    // and component-scoped grants, not a previous application's cached decision.
    std::set<std::string> prepared;
    for (const auto& app : request.applications()) {
        if (impl_->authority && impl_->authority->bound(app.workspace()) && prepared.insert(app.workspace()).second)
            (void)impl_->authority->application_permissions(app, principal, true);
    }
    reconcile_authority(); reap();
    if (impl_->native) impl_->native->check();
    workspace::v1::ApplicationAccessReply reply;
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
    return reply;
}
void ExecutionStore::reconcile_authority() {
    if (!impl_->authority) return;
    struct Running { std::string id; workspace::v1::LaunchRequest request; unsigned rights; };
    std::vector<Running> records;
    {
        Query q(impl_->db.get(), "SELECT id,payload,rights,workspace,actor FROM processes WHERE state='running'");
        while (q.step() == SQLITE_ROW) {
            Running record; record.id = q.text(0);
            if (!record.request.ParseFromString(q.text(1)) || record.request.workspace() != q.text(3) || record.request.actor() != q.text(4))
                throw WorldError(wire::STORAGE_ERROR, "Stored process authority is invalid");
            validate_message(record.request);
            const auto mask = sqlite3_column_int64(q.value, 2);
            if (mask < 0 || (mask & ~30)) throw WorldError(wire::STORAGE_ERROR, "Stored process rights are invalid");
            record.rights = static_cast<unsigned>(mask); records.push_back(std::move(record));
        }
    }
    for (const auto& record : records) {
        if (!record.request.has_principal()) continue;
        workspace::v1::AccessRequest request; request.set_workspace(record.request.workspace()); request.set_actor(record.request.actor());
        *request.mutable_principal() = record.request.principal();
        const auto effective = rights_mask(access(request, false).rights());
        if ((record.rights & effective) == record.rights) continue;
        impl_->jobs.erase(record.id);
        Query q(impl_->db.get(), "UPDATE processes SET state='revoked' WHERE id=?"); q.text(1, record.id); q.step();
    }
}
wire::ProcessLaunchReply ExecutionStore::launch(const workspace::v1::LaunchRequest& request) {
    valid_text(request.workspace()); valid_text(request.actor());
    const auto& command = request.command(); valid_text(command.operation_id());
    require(command.profile() == "development" || command.profile() == "read-only", "Unknown application profile");
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
    const auto workspace = workspace_get(request.workspace());
    const auto worktree = impl_->worktree(workspace.worktree());
    const auto repository = impl_->repository(worktree.repo());
    const auto state = impl_->allocate("applications"); const auto id = state.filename().string();
    { Transaction transaction(impl_->db.get());
      Query q(impl_->db.get(), "INSERT INTO processes VALUES(?,?,?,?,?,?,'pending',NULL)");
      q.text(1, id); q.text(2, request.actor()); q.text(3, command.operation_id()); q.blob(4, payload); q.text(5, request.workspace());
      check(sqlite3_bind_int(q.value, 6, static_cast<int>(effective))); q.step(); transaction.commit(); }
    try {
        if (!std::filesystem::create_directory(state)) throw std::runtime_error("Application state path already exists");
        for (const auto* name : {"home", "tmp", "cache", "config", "data"}) std::filesystem::create_directory(state / name);
        sync_path(state, true); sync_path(state.parent_path(), true);
        std::function<void()> current = [] {};
        if (request.has_principal()) current = [this, query, effective] {
            impl_->authority->tick();
            const auto rights = rights_mask(access(query, false).rights());
            if ((effective & rights) != effective) throw WorldError(wire::FORBIDDEN, "Process execution authority was revoked");
        };
        auto native = impl_->native ? impl_->native->issue(request.workspace(), id, current) : nullptr;
        auto process = std::make_unique<ApplicationProcess>(worktree.path(), repository.path(), state,
            (effective & (1u << wire::FILES_WRITE)) != 0, command, std::move(current), std::move(native));
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
}
