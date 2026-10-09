#include "authority/store.hpp"
#include "world/store.hpp"
#include <openssl/rand.h>
#include <sqlite3.h>
#include <algorithm>
#include <cerrno>
#include <fcntl.h>
#include <map>
#include <set>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

namespace spatial::authority {
namespace {
namespace pb = v1;
void require(bool value, const char* message) { if (!value) throw std::invalid_argument(message); }
void stored(bool value) { if (!value) throw WorldError(wire::STORAGE_ERROR, "Authority storage is invalid or unavailable"); }
void permit(bool value) { if (!value) throw WorldError(wire::FORBIDDEN, "Grant authority is insufficient"); }
void sql_check(int value) { stored(value == SQLITE_OK || value == SQLITE_ROW || value == SQLITE_DONE); }
void sql(sqlite3* db, const char* command) { sql_check(sqlite3_exec(db, command, nullptr, nullptr, nullptr)); }
struct File { int fd = -1; ~File() { if (fd >= 0) close(fd); } };
struct Query {
    sqlite3_stmt* value = nullptr;
    Query(sqlite3* db, const char* command) { sql_check(sqlite3_prepare_v2(db, command, -1, &value, nullptr)); }
    ~Query() { sqlite3_finalize(value); }
    void blob(int index, const std::string& bytes) {
        sql_check(sqlite3_bind_blob(value, index, bytes.data(), static_cast<int>(bytes.size()), SQLITE_TRANSIENT));
    }
    int step() { const int status = sqlite3_step(value); sql_check(status); return status; }
    std::string blob(int column) {
        const auto* bytes = static_cast<const char*>(sqlite3_column_blob(value, column));
        return bytes ? std::string(bytes, sqlite3_column_bytes(value, column)) : "";
    }
    sqlite3_int64 integer(int column) { return sqlite3_column_int64(value, column); }
};
struct Transaction {
    sqlite3* db; bool done = false;
    explicit Transaction(sqlite3* db) : db(db) { sql(db, "BEGIN IMMEDIATE"); }
    ~Transaction() { if (!done) sqlite3_exec(db, "ROLLBACK", nullptr, nullptr, nullptr); }
    void commit() { sql(db, "COMMIT"); done = true; }
};
void private_file(int directory, const char* name, bool required) {
    struct stat state{};
    if (fstatat(directory, name, &state, AT_SYMLINK_NOFOLLOW)) {
        if (!required && errno == ENOENT) return;
        stored(false);
    }
    if (!S_ISREG(state.st_mode) || state.st_uid != geteuid() || state.st_nlink != 1 || (state.st_mode & 0077))
        throw WorldError(wire::FORBIDDEN, "Authority files must be owned private regular files with one link");
}
void actor(const pb::ActorRef& value) {
    validate_message(value);
    require(value.issuer_id().size() == 32 && value.actor_id().size() == 32, "Actor requires issuer and actor identities");
}
bool same(const pb::ActorRef& a, const pb::ActorRef& b) {
    return a.issuer_id() == b.issuer_id() && a.actor_id() == b.actor_id();
}
void domain(const pb::Domain& value) {
    validate_message(value);
    require(value.kind() == pb::WORLD || value.kind() == pb::WORKSPACE, "Authority domain kind is required");
    require(!value.id().empty() && value.id().size() <= 256 && value.id().find('\0') == std::string::npos,
            "Authority domain identity must contain one to 256 nonzero bytes");
}
bool same(const pb::Domain& a, const pb::Domain& b) { return a.kind() == b.kind() && a.id() == b.id(); }
void scope(const pb::Scope& value) {
    validate_message(value); require(value.has_domain(), "Scope domain is required"); domain(value.domain());
    require(value.components_size() <= 32, "Scope exceeds 32 components");
    for (const auto& part : value.components()) require(!part.empty() && part.size() <= 256, "Invalid opaque scope component");
}
bool contains(const pb::Scope& parent, const pb::Scope& child) {
    return same(parent.domain(), child.domain()) && parent.components_size() <= child.components_size() &&
        std::equal(parent.components().begin(), parent.components().end(), child.components().begin());
}
bool valid_right(pb::DomainKind kind, int right) {
    if (right == pb::POLICY_ADMINISTER) return true;
    return kind == pb::WORLD ? right >= pb::WORLD_ENTER && right <= pb::WORLD_MANIPULATE :
        right >= pb::WORKSPACE_ACCESS && right <= pb::PROCESS_EXECUTE;
}
bool includes(const google::protobuf::RepeatedField<int>& rights, int right) {
    return std::find(rights.begin(), rights.end(), right) != rights.end();
}
bool subset(const google::protobuf::RepeatedField<int>& child, const google::protobuf::RepeatedField<int>& parent) {
    return std::all_of(child.begin(), child.end(), [&](int right) { return includes(parent, right); });
}
void terms(const pb::GrantTerms& value) {
    validate_message(value); require(value.has_subject() && value.has_scope(), "Grant subject and scope are required");
    actor(value.subject()); validate_permissions(value);
}
} // namespace

void validate_permissions(const v1::GrantTerms& value) {
    validate_message(value); require(value.has_scope(), "Grant scope is required");
    scope(value.scope());
    require(value.delegation_depth() <= 32, "Delegation depth exceeds 32");
    for (const auto* rights : {&value.rights(), &value.delegable_rights()}) {
        std::set<int> seen;
        for (const int right : *rights)
            require(valid_right(value.scope().domain().kind(), right) && seen.insert(right).second, "Invalid or duplicate grant right");
    }
    require(subset(value.delegable_rights(), value.rights()), "Delegable rights must be held rights");
    require(value.delegation_depth() || value.delegable_rights().empty(), "Zero depth cannot delegate rights");
}
namespace {
bool narrows(const pb::GrantTerms& child, const pb::GrantTerms& parent) {
    return contains(parent.scope(), child.scope()) && child.delegation_depth() < parent.delegation_depth() &&
        subset(child.rights(), parent.delegable_rights()) && subset(child.delegable_rights(), parent.delegable_rights());
}
void bootstrap_selector(const pb::BootstrapSelector& value) {
    validate_message(value);
    require(value.issuer_id().size() == 32 && value.profile_revision() > 0 && value.profile_revision() <= INT64_MAX,
            "Bootstrap issuer and positive profile revision are required");
    for (const auto* id : {&value.profile_id(), &value.template_id()})
        require(!id->empty() && id->size() <= 128 && id->find('\0') == std::string::npos,
                "Bootstrap identifiers require one to 128 nonzero bytes");
}
bool same(const pb::BootstrapSelector& a, const pb::BootstrapSelector& b) {
    return a.issuer_id() == b.issuer_id() && a.profile_id() == b.profile_id() &&
        a.profile_revision() == b.profile_revision() && a.template_id() == b.template_id();
}
void bootstrap_terms(const pb::GrantTerms& value) {
    require(!value.has_subject(), "Bootstrap template must not assign a recipient");
    validate_permissions(value);
}
void create_bootstrap_schema(sqlite3* db) {
    sql(db, "CREATE TABLE bootstrap_binding(id BLOB PRIMARY KEY CHECK(length(id)=32),"
            "issuer BLOB NOT NULL CHECK(length(issuer)=32),profile BLOB NOT NULL,"
            "profile_revision INTEGER NOT NULL CHECK(profile_revision>0),template BLOB NOT NULL,"
            "record BLOB NOT NULL,UNIQUE(issuer,profile,profile_revision,template));"
            "CREATE TABLE bootstrap_receipt(issuer BLOB NOT NULL CHECK(length(issuer)=32),"
            "actor BLOB NOT NULL CHECK(length(actor)=32),profile BLOB NOT NULL,template BLOB NOT NULL,"
            "binding_id BLOB NOT NULL REFERENCES bootstrap_binding(id),"
            "grant_id BLOB NOT NULL UNIQUE REFERENCES grant_record(id),"
            "PRIMARY KEY(issuer,actor,profile,template)); PRAGMA user_version=2");
}
std::string random_id() {
    std::string bytes(32, '\0');
    if (RAND_bytes(reinterpret_cast<unsigned char*>(bytes.data()), bytes.size()) != 1)
        throw std::runtime_error("Cannot obtain authority entropy");
    return bytes;
}
}

struct Store::Impl {
    File directory, lock;
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    pb::Domain owner;
    Impl(const std::filesystem::path& path, const pb::Domain& selected, bool initialize, bool migrate) : owner(selected) {
        domain(owner); require(path.is_absolute(), "Authority directory must be absolute");
        require(!initialize || !migrate, "Authority initialization and migration are separate operations");
        directory.fd = open(path.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        struct stat status{};
        if (directory.fd < 0 || fstat(directory.fd, &status) || status.st_uid != geteuid() || (status.st_mode & 0077))
            throw WorldError(wire::FORBIDDEN, "Authority directory must exist, be owned, and be private");
        lock.fd = openat(directory.fd, "authority.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0600);
        private_file(directory.fd, "authority.lock", true);
        if (lock.fd < 0 || flock(lock.fd, LOCK_EX | LOCK_NB))
            throw WorldError(wire::IN_USE, "Authority store is already in use or cannot be locked");
        const auto anchored = std::filesystem::path("/proc/self/fd") / std::to_string(directory.fd);
        if (initialize) {
            for (const auto& entry : std::filesystem::directory_iterator(anchored))
                if (entry.path().filename() != "authority.lock")
                    throw WorldError(wire::ALREADY_EXISTS, "Authority initialization requires an empty private directory");
            File created; created.fd = openat(directory.fd, "authority.db", O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
            stored(created.fd >= 0);
        }
        private_file(directory.fd, "authority.db", true);
        for (const auto* name : {"authority.db-journal", "authority.db-wal", "authority.db-shm"}) private_file(directory.fd, name, false);
        // SQLite NOFOLLOW rejects the intentional /proc descriptor symlink too.
        // Use the kernel-resolved directory name while retaining NOFOLLOW.
        const auto database_path = std::filesystem::read_symlink(anchored) / "authority.db";
        struct stat named{};
        stored(stat(database_path.parent_path().c_str(), &named) == 0 && named.st_dev == status.st_dev && named.st_ino == status.st_ino);
        sqlite3* connection = nullptr;
        const int opened = sqlite3_open_v2(database_path.c_str(), &connection,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_NOMUTEX | SQLITE_OPEN_NOFOLLOW, nullptr);
        db.reset(connection); sql_check(opened); sql_check(sqlite3_busy_timeout(db.get(), 2000));
        stored(stat(database_path.parent_path().c_str(), &named) == 0 && named.st_dev == status.st_dev && named.st_ino == status.st_ino);
        sqlite3_int64 schema = 2;
        if (!initialize) {
            Query version(db.get(), "PRAGMA user_version"); version.step();
            Query application(db.get(), "PRAGMA application_id"); application.step();
            schema = version.integer(0);
            if (!((schema == 2 || (schema == 1 && migrate)) && application.integer(0) == 0x53504752))
                throw WorldError(wire::STORAGE_ERROR, "Unknown authority schema; explicit migration is required");
        }
        sql(db.get(), "PRAGMA trusted_schema=OFF; PRAGMA foreign_keys=ON; PRAGMA synchronous=EXTRA");
        { Query mode(db.get(), "PRAGMA journal_mode=DELETE"); mode.step(); stored(mode.blob(0) == "delete"); }
        if (initialize) {
            Transaction tx(db.get());
            sql(db.get(), "CREATE TABLE authority(singleton INTEGER PRIMARY KEY CHECK(singleton=1),domain BLOB NOT NULL,"
                          "revision INTEGER NOT NULL CHECK(revision>=0));"
                          "CREATE TABLE grant_record(id BLOB PRIMARY KEY CHECK(length(id)=32),issuer BLOB NOT NULL,"
                          "actor BLOB NOT NULL,record BLOB NOT NULL);"
                          "CREATE INDEX grant_subject ON grant_record(issuer,actor);"
                          "PRAGMA application_id=1397770066; PRAGMA user_version=1");
            Query insert(db.get(), "INSERT INTO authority VALUES(1,?,0)"); insert.blob(1, owner.SerializeAsString()); insert.step();
            create_bootstrap_schema(db.get());
            tx.commit(); stored(fsync(directory.fd) == 0);
        }
        { Query integrity(db.get(), "PRAGMA quick_check"); stored(integrity.step() == SQLITE_ROW && integrity.blob(0) == "ok"); }
        Query row(db.get(), "SELECT domain FROM authority WHERE singleton=1"); stored(row.step() == SQLITE_ROW);
        pb::Domain actual; stored(actual.ParseFromString(row.blob(0)));
        validate_message(actual); permit(same(actual, owner)); revision();
        if (schema == 1) {
            Transaction tx(db.get()); create_bootstrap_schema(db.get()); tx.commit();
        }
    }
    std::uint64_t revision() {
        Query row(db.get(), "SELECT revision FROM authority WHERE singleton=1"); stored(row.step() == SQLITE_ROW && row.integer(0) >= 0);
        return static_cast<std::uint64_t>(row.integer(0));
    }
    void expected(std::uint64_t value) {
        require(value <= INT64_MAX, "Authority revision exceeds storage range");
        if (revision() != value) throw WorldError(wire::REVISION_CONFLICT, "Authority revision changed");
    }
    std::uint64_t advance() {
        const auto value = revision();
        if (value == INT64_MAX) throw WorldError(wire::RESOURCE_EXHAUSTED, "Authority revision is exhausted");
        sql(db.get(), "UPDATE authority SET revision=revision+1 WHERE singleton=1"); return value+1;
    }
    pb::GrantRecord get(const std::string& id) {
        require(id.size() == 32, "Grant identity must contain 32 bytes");
        Query row(db.get(), "SELECT issuer,actor,record FROM grant_record WHERE id=?"); row.blob(1, id);
        if (row.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Grant does not exist");
        const auto bytes = row.blob(2); pb::GrantRecord record;
        stored(bytes.size() <= 16384 && record.ParseFromString(bytes));
        try {
            validate_message(record); terms(record.terms());
            stored(record.protocol_version() == 1 && record.id() == id && same(record.terms().scope().domain(), owner) &&
                record.terms().subject().issuer_id() == row.blob(0) && record.terms().subject().actor_id() == row.blob(1));
            if (record.parent_id().empty()) stored(!record.has_issuer());
            else { actor(record.issuer()); stored(record.parent_id().size() == 32); }
        } catch (const std::invalid_argument&) { stored(false); }
        const auto head = revision();
        stored(record.created_revision() > 0 && record.created_revision() <= head &&
            (!record.revoked_revision() || (record.revoked_revision() > record.created_revision() && record.revoked_revision() <= head)));
        return record;
    }
    bool active(pb::GrantRecord record, const IdentityCheck& identity) {
        require(static_cast<bool>(identity), "Current identity checker is required");
        std::set<std::string> seen;
        bool valid = true;
        for (;;) {
            stored(seen.size() <= 32 && seen.insert(record.id()).second);
            // Validate the whole ancestry even if one ancestor is revoked.
            if (record.revoked_revision() || !identity(record.terms().subject())) valid = false;
            if (record.parent_id().empty()) return valid;
            pb::GrantRecord parent;
            try { parent = get(record.parent_id()); }
            catch (const WorldError& error) { if (error.code == wire::NOT_FOUND) stored(false); throw; }
            stored(parent.created_revision() < record.created_revision() && same(record.issuer(), parent.terms().subject()) &&
                narrows(record.terms(), parent.terms()));
            record = std::move(parent);
        }
    }
    pb::Decision check(const pb::ActorRef& caller, const pb::Scope& target, pb::Right right, const IdentityCheck& identity) {
        actor(caller); scope(target); require(valid_right(target.domain().kind(), right), "Invalid requested right");
        require(static_cast<bool>(identity), "Current identity checker is required");
        pb::Decision result; result.set_revision(revision());
        if (!same(target.domain(), owner) || !identity(caller)) return result;
        Query rows(db.get(), "SELECT id FROM grant_record WHERE issuer=? AND actor=? ORDER BY id");
        rows.blob(1, caller.issuer_id()); rows.blob(2, caller.actor_id());
        while (rows.step() == SQLITE_ROW) {
            const auto record = get(rows.blob(0));
            if (contains(record.terms().scope(), target) && includes(record.terms().rights(), right) && active(record, identity)) {
                result.set_allowed(true); result.set_grant_id(record.id()); break;
            }
        }
        return result;
    }
    pb::GrantRecord insert(const pb::GrantTerms& value, const std::string& parent = {}, const pb::ActorRef* caller = nullptr) {
        Query count(db.get(), "SELECT count(*) FROM grant_record"); count.step();
        if (count.integer(0) >= 4096) throw WorldError(wire::RESOURCE_EXHAUSTED, "Authority grant limit reached");
        pb::GrantRecord record; record.set_protocol_version(1); record.set_id(random_id()); record.set_parent_id(parent);
        if (caller) *record.mutable_issuer() = *caller;
        *record.mutable_terms() = value; record.set_created_revision(advance());
        Query write(db.get(), "INSERT INTO grant_record VALUES(?,?,?,?)");
        write.blob(1, record.id()); write.blob(2, value.subject().issuer_id()); write.blob(3, value.subject().actor_id());
        write.blob(4, record.SerializeAsString()); write.step(); return record;
    }

    pb::BootstrapBinding binding(const std::string& id) {
        require(id.size() == 32, "Bootstrap binding identity requires 32 bytes");
        Query row(db.get(), "SELECT issuer,profile,profile_revision,template,record FROM bootstrap_binding WHERE id=?");
        row.blob(1, id);
        if (row.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Bootstrap binding does not exist");
        const auto bytes = row.blob(4); pb::BootstrapBinding result;
        stored(bytes.size() <= 16384 && result.ParseFromString(bytes));
        try {
            validate_message(result); bootstrap_selector(result.selector()); bootstrap_terms(result.terms()); actor(result.authorizer());
        } catch (const std::invalid_argument&) { stored(false); }
        stored(result.id() == id && result.parent_id().size() == 32 && same(result.terms().scope().domain(), owner) &&
            result.selector().issuer_id() == row.blob(0) && result.selector().profile_id() == row.blob(1) &&
            row.integer(2) > 0 && result.selector().profile_revision() == static_cast<std::uint64_t>(row.integer(2)) &&
            result.selector().template_id() == row.blob(3));
        const auto head = revision();
        stored(result.created_revision() > 0 && result.created_revision() <= head &&
            (!result.revoked_revision() || (result.revoked_revision() > result.created_revision() && result.revoked_revision() <= head)));
        pb::GrantRecord parent;
        try { parent = get(result.parent_id()); }
        catch (const WorldError& error) { if (error.code == wire::NOT_FOUND) stored(false); throw; }
        stored(parent.created_revision() < result.created_revision() && same(parent.terms().subject(), result.authorizer()) &&
               narrows(result.terms(), parent.terms()));
        return result;
    }
};
Store::Store(const std::filesystem::path& directory, const pb::Domain& domain, bool initialize, bool migrate)
    : impl_(std::make_unique<Impl>(directory, domain, initialize, migrate)) {}
Store::~Store() = default;
std::vector<pb::GrantRecord> Store::install_roots(const std::vector<pb::GrantTerms>& values, std::uint64_t revision) {
    require(!values.empty() && values.size() <= 64, "Provision between one and 64 root grants");
    for (const auto& value : values) { terms(value); permit(same(value.scope().domain(), impl_->owner)); }
    Transaction tx(impl_->db.get()); impl_->expected(revision);
    std::vector<pb::GrantRecord> result;
    for (const auto& value : values) result.push_back(impl_->insert(value));
    tx.commit(); return result;
}
std::vector<pb::ActorRef> Store::subjects(const pb::ActorRef& caller) {
    actor(caller); Transaction tx(impl_->db.get());
    std::map<std::pair<std::string, std::string>, pb::ActorRef> found;
    const auto collect = [&](const pb::ActorRef& value) {
        found.emplace(std::make_pair(value.issuer_id(), value.actor_id()), value); return true;
    };
    collect(caller);
    Query rows(impl_->db.get(), "SELECT id FROM grant_record WHERE issuer=? AND actor=? ORDER BY id");
    rows.blob(1, caller.issuer_id()); rows.blob(2, caller.actor_id());
    while (rows.step() == SQLITE_ROW) impl_->active(impl_->get(rows.blob(0)), collect);
    std::vector<pb::ActorRef> result;
    for (const auto& [key, value] : found) { (void)key; result.push_back(value); }
    tx.commit(); return result;
}
pb::GrantPage Store::list(const pb::ActorRef& caller, const std::string& after, std::uint32_t limit) {
    actor(caller); require(after.empty() || after.size() == 32, "Invalid grant cursor");
    require(limit <= 16, "Grant page limit exceeds 16"); if (!limit) limit = 16;
    Transaction tx(impl_->db.get()); pb::GrantPage result; result.set_revision(impl_->revision());
    Query rows(impl_->db.get(), "SELECT id FROM grant_record WHERE issuer=? AND actor=? AND id>? ORDER BY id LIMIT 17");
    rows.blob(1, caller.issuer_id()); rows.blob(2, caller.actor_id()); rows.blob(3, after);
    while (rows.step() == SQLITE_ROW) {
        if (result.grants_size() == static_cast<int>(limit)) {
            result.set_next_after(result.grants(result.grants_size()-1).id()); break;
        }
        *result.add_grants() = impl_->get(rows.blob(0));
    }
    tx.commit(); return result;
}
pb::GrantRecord Store::install_root(const pb::GrantTerms& value, std::uint64_t revision) {
    terms(value); permit(same(value.scope().domain(), impl_->owner));
    Transaction tx(impl_->db.get()); impl_->expected(revision);
    const auto result = impl_->insert(value); tx.commit(); return result;
}
pb::GrantRecord Store::delegate(const pb::ActorRef& caller, const std::string& parent_id, const pb::GrantTerms& value,
                               std::uint64_t revision, const IdentityCheck& identity) {
    actor(caller); terms(value); require(static_cast<bool>(identity), "Current identity checker is required");
    Transaction tx(impl_->db.get()); impl_->expected(revision);
    const auto parent = impl_->get(parent_id);
    permit(same(caller, parent.terms().subject()) && same(value.scope().domain(), impl_->owner) &&
        narrows(value, parent.terms()) && impl_->active(parent, identity) && identity(value.subject()));
    const auto result = impl_->insert(value, parent_id, &caller); tx.commit(); return result;
}
pb::GrantRecord Store::revoke(const pb::ActorRef& caller, const std::string& id, std::uint64_t revision, const IdentityCheck& identity) {
    actor(caller); require(static_cast<bool>(identity), "Current identity checker is required");
    Transaction tx(impl_->db.get()); impl_->expected(revision); auto record = impl_->get(id);
    permit(identity(caller) && (same(caller, record.terms().subject()) || (record.has_issuer() && same(caller, record.issuer())) ||
        impl_->check(caller, record.terms().scope(), pb::POLICY_ADMINISTER, identity).allowed()));
    if (!record.revoked_revision()) {
        record.set_revoked_revision(impl_->advance());
        Query write(impl_->db.get(), "UPDATE grant_record SET record=? WHERE id=?");
        write.blob(1, record.SerializeAsString()); write.blob(2, id); write.step();
    }
    tx.commit(); return record;
}
pb::Decision Store::check(const pb::ActorRef& caller, const pb::Scope& scope, pb::Right right, const IdentityCheck& identity) {
    Transaction tx(impl_->db.get()); const auto result = impl_->check(caller, scope, right, identity); tx.commit(); return result;
}
pb::GrantRecord Store::get(const std::string& id) {
    Transaction tx(impl_->db.get()); const auto result = impl_->get(id); tx.commit(); return result;
}

pb::BootstrapBinding Store::bind_bootstrap(const pb::ActorRef& caller, const pb::BootstrapSelector& selector,
    const std::string& parent_id, const pb::GrantTerms& value, std::uint64_t revision, const IdentityCheck& identity) {
    actor(caller); bootstrap_selector(selector); bootstrap_terms(value);
    require(static_cast<bool>(identity), "Current identity checker is required");
    Transaction tx(impl_->db.get()); impl_->expected(revision);
    const auto parent = impl_->get(parent_id);
    permit(same(caller, parent.terms().subject()) && same(value.scope().domain(), impl_->owner) &&
           narrows(value, parent.terms()) && impl_->active(parent, identity));
    Query existing(impl_->db.get(), "SELECT id FROM bootstrap_binding WHERE issuer=? AND profile=? AND profile_revision=? AND template=?");
    existing.blob(1, selector.issuer_id()); existing.blob(2, selector.profile_id());
    sql_check(sqlite3_bind_int64(existing.value, 3, static_cast<sqlite3_int64>(selector.profile_revision())));
    existing.blob(4, selector.template_id());
    if (existing.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "Bootstrap template already has a binding");
    Query count(impl_->db.get(), "SELECT count(*) FROM bootstrap_binding"); count.step();
    if (count.integer(0) >= 1024) throw WorldError(wire::RESOURCE_EXHAUSTED, "Bootstrap binding limit reached");
    pb::BootstrapBinding result; result.set_id(random_id()); *result.mutable_selector() = selector;
    result.set_parent_id(parent_id); *result.mutable_authorizer() = caller; *result.mutable_terms() = value;
    result.set_created_revision(impl_->advance());
    Query write(impl_->db.get(), "INSERT INTO bootstrap_binding VALUES(?,?,?,?,?,?)");
    write.blob(1, result.id()); write.blob(2, selector.issuer_id()); write.blob(3, selector.profile_id());
    sql_check(sqlite3_bind_int64(write.value, 4, static_cast<sqlite3_int64>(selector.profile_revision())));
    write.blob(5, selector.template_id()); write.blob(6, result.SerializeAsString()); write.step();
    tx.commit(); return result;
}

pb::BootstrapBinding Store::bootstrap_binding(const pb::BootstrapSelector& selector) {
    bootstrap_selector(selector); Transaction tx(impl_->db.get());
    Query row(impl_->db.get(), "SELECT id FROM bootstrap_binding WHERE issuer=? AND profile=? AND profile_revision=? AND template=?");
    row.blob(1, selector.issuer_id()); row.blob(2, selector.profile_id());
    sql_check(sqlite3_bind_int64(row.value, 3, static_cast<sqlite3_int64>(selector.profile_revision())));
    row.blob(4, selector.template_id());
    if (row.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Bootstrap template has no accepted binding");
    const auto result = impl_->binding(row.blob(0)); tx.commit(); return result;
}

std::vector<pb::ActorRef> Store::bootstrap_subjects(const std::vector<std::string>& bindings, const pb::ActorRef& recipient) {
    actor(recipient); require(bindings.size() <= 32, "At most 32 bootstrap bindings may be prepared");
    Transaction tx(impl_->db.get());
    std::map<std::pair<std::string, std::string>, pb::ActorRef> found;
    const auto collect = [&](const pb::ActorRef& value) {
        found.emplace(std::make_pair(value.issuer_id(), value.actor_id()), value); return true;
    };
    collect(recipient);
    for (const auto& id : bindings) impl_->active(impl_->get(impl_->binding(id).parent_id()), collect);
    std::vector<pb::ActorRef> result;
    for (const auto& [key, value] : found) { (void)key; result.push_back(value); }
    tx.commit(); return result;
}

pb::GrantRecord Store::materialize_bootstrap(const std::string& binding_id, const pb::ActorRef& recipient,
    const pb::BootstrapSelector& selector, const pb::GrantTerms& checked_template, const IdentityCheck& identity) {
    actor(recipient); bootstrap_selector(selector); bootstrap_terms(checked_template);
    require(static_cast<bool>(identity), "Current identity checker is required");
    Transaction tx(impl_->db.get()); const auto binding = impl_->binding(binding_id);
    permit(same(binding.selector(), selector) && binding.terms().SerializeAsString() == checked_template.SerializeAsString() &&
           recipient.issuer_id() == selector.issuer_id() && identity(recipient));
    auto terms = checked_template; *terms.mutable_subject() = recipient;
    Query receipt(impl_->db.get(), "SELECT binding_id,grant_id FROM bootstrap_receipt WHERE issuer=? AND actor=? AND profile=? AND template=?");
    receipt.blob(1, recipient.issuer_id()); receipt.blob(2, recipient.actor_id());
    receipt.blob(3, selector.profile_id()); receipt.blob(4, selector.template_id());
    if (receipt.step() == SQLITE_ROW) {
        stored(receipt.blob(0).size() == 32 && receipt.blob(1).size() == 32);
        // A different revision or parent cannot replace the first durable grant.
        if (receipt.blob(0) != binding_id) throw WorldError(wire::REVISION_CONFLICT, "Bootstrap grant was already materialized under another binding");
        pb::GrantRecord result;
        try { result = impl_->get(receipt.blob(1)); }
        catch (const WorldError& error) { if (error.code == wire::NOT_FOUND) stored(false); throw; }
        stored(result.parent_id() == binding.parent_id() && same(result.issuer(), binding.authorizer()) &&
               result.terms().SerializeAsString() == terms.SerializeAsString() && result.created_revision() > binding.created_revision());
        // Return the existing record, including its revocation. This does not authorize an action.
        tx.commit(); return result;
    }
    permit(!binding.revoked_revision() && impl_->active(impl_->get(binding.parent_id()), identity));
    const auto result = impl_->insert(terms, binding.parent_id(), &binding.authorizer());
    Query write(impl_->db.get(), "INSERT INTO bootstrap_receipt VALUES(?,?,?,?,?,?)");
    write.blob(1, recipient.issuer_id()); write.blob(2, recipient.actor_id()); write.blob(3, selector.profile_id());
    write.blob(4, selector.template_id()); write.blob(5, binding_id); write.blob(6, result.id()); write.step();
    tx.commit(); return result;
}

pb::BootstrapBinding Store::revoke_bootstrap(const pb::ActorRef& caller, const std::string& id,
    std::uint64_t revision, const IdentityCheck& identity) {
    actor(caller); require(static_cast<bool>(identity), "Current identity checker is required");
    Transaction tx(impl_->db.get()); impl_->expected(revision); auto result = impl_->binding(id);
    permit(identity(caller) && (same(caller, result.authorizer()) ||
        impl_->check(caller, result.terms().scope(), pb::POLICY_ADMINISTER, identity).allowed()));
    if (!result.revoked_revision()) {
        result.set_revoked_revision(impl_->advance());
        Query write(impl_->db.get(), "UPDATE bootstrap_binding SET record=? WHERE id=?");
        write.blob(1, result.SerializeAsString()); write.blob(2, id); write.step();
        stored(sqlite3_changes(impl_->db.get()) == 1);
    }
    tx.commit(); return result;
}
} // namespace spatial::authority
