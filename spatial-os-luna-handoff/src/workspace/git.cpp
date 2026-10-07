#include "workspace/git.hpp"
#include "workspace/git_command.hpp"
#include "workspace/status.hpp"
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
}

struct WorkspaceGit::Impl {
    std::filesystem::path state;
    File lock;
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    explicit Impl(const std::filesystem::path& directory, bool migrate_v1) {
        require(directory.is_absolute() && !std::filesystem::is_symlink(directory), "Execution state requires an absolute nonsymlink directory");
        if (migrate_v1) require(std::filesystem::is_regular_file(directory / "execution.db"), "Migration requires an existing execution database");
        std::filesystem::create_directories(directory);
        state = std::filesystem::canonical(directory);
        lock.fd = open((state / "execution.lock").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (lock.fd < 0 || flock(lock.fd, LOCK_EX | LOCK_NB) != 0)
            throw WorldError(wire::IN_USE, "Execution state already has an authority or cannot be locked");
        for (const auto* name : {"execution.db", "execution.db-journal", "execution.db-wal", "execution.db-shm", "repositories", "worktrees", "effects", "retired"})
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
        if (version == 1 && !migrate_v1)
            throw WorldError(wire::STORAGE_ERROR, "Execution schema v1 requires workspaced --state-dir PATH --migrate-v1 while the broker is stopped");
        if (version < 0 || version > 2 || (migrate_v1 && version == 0))
            throw WorldError(wire::STORAGE_ERROR, "Unknown execution schema; explicit migration required");
        if (version == 0) {
            Query existing(db.get(), "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"); existing.step();
            if (sqlite3_column_int(existing.value, 0) != 0) throw WorldError(wire::STORAGE_ERROR, "Refusing unknown execution database");
            Transaction transaction(db.get());
            sql(db.get(), "CREATE TABLE repositories(id TEXT PRIMARY KEY,source TEXT NOT NULL,path TEXT UNIQUE NOT NULL,state TEXT NOT NULL);"
                "CREATE TABLE worktrees(id TEXT PRIMARY KEY,repo TEXT NOT NULL REFERENCES repositories(id),branch TEXT NOT NULL,path TEXT UNIQUE NOT NULL,head TEXT NOT NULL,state TEXT NOT NULL);"
                "CREATE TABLE operations(id TEXT PRIMARY KEY,payload BLOB NOT NULL,result BLOB NOT NULL,worktree TEXT NOT NULL REFERENCES worktrees(id),state TEXT NOT NULL);");
            sql(db.get(), removal_schema);
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
            sql(db.get(), removal_schema); transaction.commit();
        }
        std::filesystem::create_directories(state / "retired");
        if (migrate_v1) { sync_path(state, true); return; }
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

WorkspaceGit::WorkspaceGit(const std::filesystem::path& state, bool migrate_v1) : impl_(std::make_unique<Impl>(state, migrate_v1)) {}
WorkspaceGit::~WorkspaceGit() = default;
wire::RepositoryReply WorkspaceGit::import_repository(const wire::RepositoryImportRequest& request) {
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
wire::WorktreeReply WorkspaceGit::create(const wire::WorktreeCreateRequest& request) {
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
    impl_->finish(request.operation_id(), out); return out;
}
wire::GitOperationReply WorkspaceGit::operation(const std::string& id) {
    valid_text(id); Query q(impl_->db.get(), "SELECT state FROM operations WHERE id=? UNION ALL SELECT state FROM removals WHERE id=?"); q.text(1, id); q.text(2, id);
    if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Git operation not found");
    wire::GitOperationReply out; out.set_state(q.text(0)); return out;
}
wire::GitStatusReply WorkspaceGit::status(const std::string& id) {
    const auto worktree = impl_->worktree(id);
    return impl_->current_status(worktree.path());
}
wire::WorktreeRemoveReply WorkspaceGit::remove(const wire::WorktreeRemoveRequest& request) {
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
}
