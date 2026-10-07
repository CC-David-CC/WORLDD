#include "workspace/git.hpp"
#include "workspace/git_command.hpp"
#include "world/store.hpp"
#include <sqlite3.h>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <optional>
#include <set>

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
}

struct WorkspaceGit::Impl {
    std::filesystem::path state;
    File lock;
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    explicit Impl(const std::filesystem::path& directory) {
        require(directory.is_absolute() && !std::filesystem::is_symlink(directory), "Execution state requires an absolute nonsymlink directory");
        std::filesystem::create_directories(directory);
        state = std::filesystem::canonical(directory);
        lock.fd = open((state / "execution.lock").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (lock.fd < 0 || flock(lock.fd, LOCK_EX | LOCK_NB) != 0)
            throw WorldError(wire::IN_USE, "Execution state already has an authority or cannot be locked");
        for (const auto* name : {"execution.db", "execution.db-journal", "execution.db-wal", "execution.db-shm", "repositories", "worktrees", "effects"})
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
        if (version != 0 && version != 1) throw WorldError(wire::STORAGE_ERROR, "Unknown execution schema; explicit migration required");
        if (version == 0) {
            Query existing(db.get(), "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"); existing.step();
            if (sqlite3_column_int(existing.value, 0) != 0) throw WorldError(wire::STORAGE_ERROR, "Refusing unknown execution database");
            Transaction transaction(db.get());
            sql(db.get(), "CREATE TABLE repositories(id TEXT PRIMARY KEY,source TEXT NOT NULL,path TEXT UNIQUE NOT NULL,state TEXT NOT NULL);"
                "CREATE TABLE worktrees(id TEXT PRIMARY KEY,repo TEXT NOT NULL REFERENCES repositories(id),branch TEXT NOT NULL,path TEXT UNIQUE NOT NULL,head TEXT NOT NULL,state TEXT NOT NULL);"
                "CREATE TABLE operations(id TEXT PRIMARY KEY,payload BLOB NOT NULL,result BLOB NOT NULL,worktree TEXT NOT NULL REFERENCES worktrees(id),state TEXT NOT NULL);"
                "PRAGMA user_version=1");
            transaction.commit();
        }
        { Query q(db.get(), "PRAGMA quick_check"); q.step(); if (q.text(0) != "ok") throw WorldError(wire::STORAGE_ERROR, "Execution registry integrity failure"); }
        { Query q(db.get(), "PRAGMA foreign_key_check"); if (q.step() != SQLITE_DONE) throw WorldError(wire::STORAGE_ERROR, "Execution registry reference failure"); }
        // A partial clone has no reliable completion witness. Preserve it for explicit repair.
        sql(db.get(), "UPDATE repositories SET state='uncertain' WHERE state='pending'");
        std::vector<std::string> pending;
        { Query q(db.get(), "SELECT id FROM operations WHERE state='pending'"); while (q.step() == SQLITE_ROW) pending.push_back(q.text(0)); }
        for (const auto& id : pending) reconcile(id);
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
};

WorkspaceGit::WorkspaceGit(const std::filesystem::path& state) : impl_(std::make_unique<Impl>(state)) {}
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
    valid_text(id); Query q(impl_->db.get(), "SELECT state FROM operations WHERE id=?"); q.text(1, id);
    if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Git operation not found");
    wire::GitOperationReply out; out.set_state(q.text(0)); return out;
}
}
