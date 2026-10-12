#include "identity/issuer.hpp"
#include "identity/bootstrap.hpp"
#include "identity/participants.hpp"
#include "identity/keys.hpp"
#include "world/store.hpp"
#include <openssl/rand.h>
#include <sqlite3.h>
#include <algorithm>
#include <cerrno>
#include <fcntl.h>
#include <limits>
#include <map>
#include <set>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

namespace spatial::identity {
namespace {
using Clock = std::chrono::steady_clock;
constexpr int application_id = 0x53504944;
void require(bool condition, const char* message) { if (!condition) throw std::invalid_argument(message); }
void storage(bool condition, const char* message) { if (!condition) throw WorldError(wire::STORAGE_ERROR, message); }
std::string random_id() {
    std::string result(32, '\0');
    if (RAND_bytes(reinterpret_cast<unsigned char*>(result.data()), result.size()) != 1)
        throw std::runtime_error("Cannot obtain issuer entropy");
    return result;
}
struct File { int fd = -1; ~File() { if (fd >= 0) close(fd); } };
void check(int value) {
    if (value != SQLITE_OK && value != SQLITE_ROW && value != SQLITE_DONE)
        throw WorldError(wire::STORAGE_ERROR, "Issuer database operation failed");
}
void sql(sqlite3* db, const char* text) { check(sqlite3_exec(db, text, nullptr, nullptr, nullptr)); }
struct Query {
    sqlite3_stmt* statement = nullptr;
    Query(sqlite3* db, const char* text) { check(sqlite3_prepare_v2(db, text, -1, &statement, nullptr)); }
    ~Query() { sqlite3_finalize(statement); }
    void blob(int index, const std::string& bytes) {
        check(sqlite3_bind_blob(statement, index, bytes.data(), static_cast<int>(bytes.size()), SQLITE_TRANSIENT));
    }
    void integer(int index, std::uint64_t value) {
        require(value <= INT64_MAX, "Issuer revision exceeds storage range");
        check(sqlite3_bind_int64(statement, index, static_cast<sqlite3_int64>(value)));
    }
    int step() { const int result = sqlite3_step(statement); check(result); return result; }
    std::string blob(int column) {
        const auto* bytes = static_cast<const char*>(sqlite3_column_blob(statement, column));
        return bytes ? std::string(bytes, sqlite3_column_bytes(statement, column)) : "";
    }
    sqlite3_int64 integer(int column) { return sqlite3_column_int64(statement, column); }
};
struct Transaction {
    sqlite3* db; bool done = false;
    explicit Transaction(sqlite3* db) : db(db) { sql(db, "BEGIN IMMEDIATE"); }
    ~Transaction() { if (!done) sqlite3_exec(db, "ROLLBACK", nullptr, nullptr, nullptr); }
    void commit() { sql(db, "COMMIT"); done = true; }
};
void private_file(int directory, const char* name, bool required) {
    struct stat status{};
    if (fstatat(directory, name, &status, AT_SYMLINK_NOFOLLOW)) {
        if (errno == ENOENT && !required) return;
        throw WorldError(wire::STORAGE_ERROR, "Required issuer file is missing or inaccessible");
    }
    if (!S_ISREG(status.st_mode) || status.st_uid != geteuid() || status.st_nlink != 1 || (status.st_mode & 0077))
        throw WorldError(wire::FORBIDDEN, "Issuer files must be owned private regular files with one link");
}
void validate_policy(const v1::EnrollmentPolicy& policy) {
    validate_message(policy);
    require(policy.mode() == v1::MANUAL || policy.mode() == v1::APPROVE_ALWAYS, "Enrollment mode is required");
    require(!policy.bootstrap_profile().empty() && policy.bootstrap_profile().size() <= 128 &&
            policy.bootstrap_profile().find('\0') == std::string::npos, "Invalid bootstrap profile reference");
}
void validate_reference(const v1::IdentityReference& target) {
    validate_message(target);
    require((target.kind() == v1::CREDENTIAL_TARGET || target.kind() == v1::ACTOR_TARGET) && target.id().size() == 32,
            "A typed credential or actor identity is required");
}
void validate_operation(const v1::IssuerOperation& operation) {
    validate_message(operation);
    if (operation.has_policy_read()) return;
    if (operation.has_supervisor_read()) {
        require(operation.supervisor_read().participant_id().size() == 32, "Supervisor participant identity is required"); return;
    }
    if (operation.has_supervisor_bind()) {
        const auto& bind = operation.supervisor_bind();
        require(bind.participant_id().size() == 32 && bind.public_key().size() == 32 &&
                bind.registered_revision() > 0 && bind.registered_revision() <= INT64_MAX &&
                bind.has_expected_revision() && bind.expected_revision() <= INT64_MAX,
                "Supervisor identity, registration, key, and expected revision are required"); return;
    }
    if (operation.has_barrier_read()) {
        const auto& read = operation.barrier_read();
        require(read.revision() > 0 && read.revision() <= INT64_MAX && read.limit() <= 16 &&
                (read.after_participant_id().empty() || read.after_participant_id().size() == 32), "Invalid barrier query");
        return;
    }
    if (operation.has_registry_read()) { validate_registry_read(operation.registry_read()); return; }
    if (operation.has_participant_register()) {
        const auto& request = operation.participant_register();
        require(request.has_participant() && request.has_expected_registry_revision() &&
                request.expected_registry_revision() <= INT64_MAX, "Participant and registry revision are required");
        validate_participant(request.participant()); return;
    }
    if (operation.has_profile_read()) { validate_bootstrap_read(operation.profile_read(), false); return; }
    if (operation.has_profile_put()) {
        const auto& request = operation.profile_put();
        require(request.has_profile() && request.has_expected_revision() && request.expected_revision() <= INT64_MAX,
                "Bootstrap profile and expected revision are required");
        validate_bootstrap_profile(request.profile()); return;
    }
    if (operation.has_identity_read()) { validate_reference(operation.identity_read()); return; }
    if (operation.has_identity_revoke()) {
        const auto& request = operation.identity_revoke();
        require(request.has_target() && request.has_expected_security_revision() && request.expected_security_revision() <= INT64_MAX,
                "Revocation target and expected security revision are required");
        validate_reference(request.target());
        require(!request.reason().empty() && request.reason().size() <= 256 && request.reason().find('\0') == std::string::npos,
                "Revocation reason must contain between 1 and 256 bytes without zero bytes"); return;
    }
    if (operation.has_enrollment_read()) {
        require(operation.enrollment_read().enrollment_id().size() == 32, "Enrollment identity is required"); return;
    }
    if (operation.has_enrollment_decision()) {
        const auto& decision = operation.enrollment_decision();
        require(decision.enrollment_id().size() == 32 && decision.has_expected_policy_revision() &&
                decision.expected_policy_revision() > 0 && decision.expected_policy_revision() <= INT64_MAX,
                "Enrollment identity and positive policy revision are required");
        require(decision.choice() == v1::APPROVE_NEW || decision.choice() == v1::APPROVE_EXISTING ||
                decision.choice() == v1::REJECT, "Enrollment decision is required");
        require(decision.choice() == v1::APPROVE_EXISTING ? decision.target_actor_id().size() == 32 :
                decision.target_actor_id().empty(), "Only explicit credential attachment accepts a target actor"); return;
    }
    require(operation.has_policy_set(), "Issuer operation is required");
    const auto& change = operation.policy_set();
    require(change.has_policy() && change.has_expected_revision() && change.expected_revision() > 0 &&
            change.expected_revision() <= INT64_MAX, "Policy and positive expected revision are required");
    validate_policy(change.policy());
}
void validate_intent(const v1::EnrollmentIntent& intent) {
    validate_message(intent);
    require(intent.public_key().size() == 32, "Enrollment requires an Ed25519 public key");
    require(intent.kind() == v1::HUMAN || intent.kind() == v1::AGENT, "Actor kind is required");
    require(!intent.label().empty() && intent.label().size() <= 256 && intent.label().find('\0') == std::string::npos,
            "Actor label must contain between 1 and 256 bytes without zero bytes");
}
void create_enrollment_schema(sqlite3* db) {
    sql(db, "CREATE TABLE actor(id BLOB PRIMARY KEY CHECK(length(id)=32),kind INTEGER NOT NULL CHECK(kind IN(1,2)),"
            "label BLOB NOT NULL);"
            "CREATE TABLE enrollment(id BLOB PRIMARY KEY CHECK(length(id)=32),public_key BLOB NOT NULL UNIQUE CHECK(length(public_key)=32),"
            "intent BLOB NOT NULL,status INTEGER NOT NULL CHECK(status IN(1,2,3)));"
            "CREATE TABLE credential(id BLOB PRIMARY KEY CHECK(length(id)=32),actor_id BLOB NOT NULL REFERENCES actor(id),"
            "public_key BLOB NOT NULL UNIQUE CHECK(length(public_key)=32),enrollment_id BLOB NOT NULL UNIQUE REFERENCES enrollment(id),"
            "signed_credential BLOB NOT NULL); PRAGMA user_version=2");
}
void create_security_schema(sqlite3* db) {
    sql(db, "CREATE TABLE security_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>=0));"
            "INSERT INTO security_state VALUES(1,0);"
            "CREATE TABLE revocation(kind INTEGER NOT NULL CHECK(kind IN(1,2)),target_id BLOB NOT NULL CHECK(length(target_id)=32),"
            "revision INTEGER NOT NULL UNIQUE CHECK(revision>0),reason BLOB NOT NULL,PRIMARY KEY(kind,target_id));"
            "PRAGMA user_version=3");
}
void create_bootstrap_schema(sqlite3* db) {
    sql(db, "CREATE TABLE bootstrap_profile(id BLOB NOT NULL,revision INTEGER NOT NULL CHECK(revision>0),"
            "profile BLOB NOT NULL,PRIMARY KEY(id,revision)); PRAGMA user_version=4");
}
void create_participant_schema(sqlite3* db) {
    sql(db, "CREATE TABLE authority_registry(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
            "revision INTEGER NOT NULL CHECK(revision>=0)); INSERT INTO authority_registry VALUES(1,0);"
            "CREATE TABLE authority_participant(id BLOB PRIMARY KEY CHECK(length(id)=32),"
            "public_key BLOB NOT NULL UNIQUE CHECK(length(public_key)=32),"
            "registered_revision INTEGER NOT NULL UNIQUE CHECK(registered_revision>0),definition BLOB NOT NULL);"
            "CREATE TABLE authority_participant_domain(kind INTEGER NOT NULL CHECK(kind IN(1,2)),"
            "domain_id BLOB NOT NULL CHECK(length(domain_id)>0 AND length(domain_id)<=256),"
            "participant_id BLOB NOT NULL REFERENCES authority_participant(id),PRIMARY KEY(kind,domain_id));"
            "PRAGMA user_version=5");
}
void create_barrier_schema(sqlite3* db) {
    sql(db, "CREATE TABLE authority_activation("
            "participant_id BLOB NOT NULL REFERENCES authority_participant(id),"
            "generation INTEGER NOT NULL CHECK(generation>0),"
            "service_incarnation BLOB NOT NULL CHECK(length(service_incarnation)=32),"
            "issuer_incarnation BLOB NOT NULL CHECK(length(issuer_incarnation)=32),"
            "connection_binding BLOB NOT NULL CHECK(length(connection_binding)=32),"
            "registration BLOB NOT NULL,initial_revision INTEGER NOT NULL CHECK(initial_revision>=0),"
            "applied_revision INTEGER NOT NULL CHECK(applied_revision>=initial_revision),"
            "active INTEGER NOT NULL CHECK(active IN(0,1)),PRIMARY KEY(participant_id,generation),"
            "UNIQUE(participant_id,service_incarnation));"
            "CREATE UNIQUE INDEX authority_active_participant ON authority_activation(participant_id) WHERE active=1;"
            "CREATE TABLE authority_barrier(revision INTEGER PRIMARY KEY REFERENCES revocation(revision),"
            "participant_count INTEGER NOT NULL CHECK(participant_count>=0));"
            "CREATE TABLE authority_obligation(revision INTEGER NOT NULL REFERENCES authority_barrier(revision),"
            "participant_id BLOB NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(revision,participant_id),"
            "FOREIGN KEY(participant_id,generation) REFERENCES authority_activation(participant_id,generation));"
            "PRAGMA user_version=6");
}
void create_supervisor_schema(sqlite3* db) {
    sql(db, "CREATE TABLE authority_supervisor("
            "participant_id BLOB PRIMARY KEY REFERENCES authority_participant(id) CHECK(length(participant_id)=32),"
            "registration BLOB NOT NULL,public_key BLOB NOT NULL CHECK(length(public_key)=32),"
            "revision INTEGER NOT NULL CHECK(revision>0)); PRAGMA user_version=7");
}
void create_launch_schema(sqlite3* db) {
    sql(db, "CREATE TABLE authority_launch_authorization("
            "authorization_id BLOB PRIMARY KEY CHECK(length(authorization_id)=32),"
            "participant_id BLOB NOT NULL REFERENCES authority_supervisor(participant_id),"
            "service_incarnation BLOB NOT NULL CHECK(length(service_incarnation)=32),"
            "launch_id BLOB NOT NULL UNIQUE CHECK(length(launch_id)=32),request BLOB NOT NULL,"
            "supervisor_public_key BLOB NOT NULL CHECK(length(supervisor_public_key)=32),"
            "generation INTEGER NOT NULL CHECK(generation>=0),UNIQUE(participant_id,service_incarnation)); PRAGMA user_version=8");
}
void create_fence_schema(sqlite3* db) {
    sql(db, "CREATE TABLE authority_fence("
            "participant_id BLOB NOT NULL CHECK(length(participant_id)=32),"
            "generation INTEGER NOT NULL CHECK(generation>0),"
            "fence_id BLOB NOT NULL UNIQUE CHECK(length(fence_id)=32),"
            "security_revision INTEGER NOT NULL CHECK(security_revision>=0),"
            "supervisor_public_key BLOB NOT NULL CHECK(length(supervisor_public_key)=32),"
            "request BLOB NOT NULL,state BLOB NOT NULL,PRIMARY KEY(participant_id,generation),"
            "FOREIGN KEY(participant_id,generation) REFERENCES authority_activation(participant_id,generation));"
            "PRAGMA user_version=9");
}
void create_retirement_schema(sqlite3* db) {
    sql(db, "CREATE TABLE authority_retirement("
            "participant_id BLOB NOT NULL CHECK(length(participant_id)=32),generation INTEGER NOT NULL CHECK(generation>0),"
            "retirement_id BLOB NOT NULL UNIQUE CHECK(length(retirement_id)=32),"
            "security_revision INTEGER NOT NULL CHECK(security_revision>=0),"
            "applied_revision INTEGER NOT NULL CHECK(applied_revision>=0 AND applied_revision<=security_revision),"
            "supervisor_public_key BLOB NOT NULL CHECK(length(supervisor_public_key)=32),request BLOB NOT NULL,state BLOB NOT NULL,"
            "PRIMARY KEY(participant_id,generation),"
            "FOREIGN KEY(participant_id,generation) REFERENCES authority_fence(participant_id,generation)); PRAGMA user_version=10");
}
} // namespace

struct Issuer::Impl {
    struct Pending {
        v1::IssuerOperation operation;
        v1::EnrollmentIntent enrollment;
        v1::SignRequest content;
        std::string expected_key;
        Clock::time_point expires;
        v1::AuthenticationBegin authentication;
        v1::IdentityStatus authenticated_identity;
        v1::AuthorityAuthenticationBegin authority{};
        std::string connection_binding{};
        v1::AuthorityLaunchBegin launch{};
        v1::AuthorityFenceBegin fence{};
        v1::AuthorityRetireBegin retirement{};
    };
    File directory, lock;
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    std::optional<KeyPair> key;
    v1::IssuerInfo identity;
    std::string owner_public, incarnation = random_id();
    std::chrono::milliseconds lifetime;
    std::map<std::string, Pending> pending;

    Impl(const std::filesystem::path& state, const std::optional<std::string>& owner, std::chrono::milliseconds ttl, bool migrate)
        : lifetime(ttl) {
        require(state.is_absolute(), "Issuer state directory must be absolute");
        require(ttl.count() >= 50 && ttl.count() <= 60000, "Challenge lifetime must be between 50 and 60000 milliseconds");
        require(!owner || owner->size() == 32, "Initial owner requires an Ed25519 public key");
        require(!owner || !migrate, "Initialization and migration are separate operations");
        directory.fd = open(state.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        struct stat status{};
        if (directory.fd < 0 || fstat(directory.fd, &status) || status.st_uid != geteuid() || (status.st_mode & 0077))
            throw WorldError(wire::FORBIDDEN, "Issuer directory must exist and be private and owned by this user");
        lock.fd = openat(directory.fd, "identity.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600);
        private_file(directory.fd, "identity.lock", true);
        if (lock.fd < 0 || flock(lock.fd, LOCK_EX | LOCK_NB))
            throw WorldError(wire::IN_USE, "Issuer directory already has an authority or cannot be locked");
        if (owner) {
            for (const auto& entry : std::filesystem::directory_iterator(state))
                if (entry.path().filename() != "identity.lock")
                    throw WorldError(wire::ALREADY_EXISTS, "Issuer initialization requires an empty private directory");
            key.emplace(KeyPair::generate()); key->save_new(state / "issuer.pem");
            File created;
            created.fd = openat(directory.fd, "identity.db", O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
            storage(created.fd >= 0, "Cannot create issuer database; preserve partial initialization");
        } else key.emplace(KeyPair::load(state / "issuer.pem"));
        private_file(directory.fd, "identity.db", true);
        for (const auto* name : {"identity.db-journal", "identity.db-wal", "identity.db-shm"})
            private_file(directory.fd, name, false);
        sqlite3* connection = nullptr;
        const int opened = sqlite3_open_v2((state / "identity.db").c_str(), &connection,
                                          SQLITE_OPEN_READWRITE | SQLITE_OPEN_NOMUTEX | SQLITE_OPEN_NOFOLLOW, nullptr);
        db.reset(connection); check(opened); check(sqlite3_busy_timeout(db.get(), 2000));
        sqlite3_int64 schema = 10;
        if (!owner) {
            Query version(db.get(), "PRAGMA user_version"); version.step();
            Query application(db.get(), "PRAGMA application_id"); application.step();
            schema = version.integer(0);
            storage((schema == 10 || ((schema >= 1 && schema <= 9) && migrate)) && application.integer(0) == application_id,
                    "Unknown issuer schema; explicit migration is required");
        }
        sql(db.get(), "PRAGMA trusted_schema=OFF; PRAGMA foreign_keys=ON; PRAGMA synchronous=EXTRA");
        { Query mode(db.get(), "PRAGMA journal_mode=DELETE"); mode.step();
          storage(mode.blob(0) == "delete", "Required issuer journal mode is unavailable"); }
        if (owner) {
            Transaction tx(db.get());
            sql(db.get(), "CREATE TABLE issuer(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                          "issuer_id BLOB NOT NULL CHECK(length(issuer_id)=32),public_key BLOB NOT NULL CHECK(length(public_key)=32),"
                          "owner_public_key BLOB NOT NULL CHECK(length(owner_public_key)=32),"
                          "revision INTEGER NOT NULL CHECK(revision>0),policy BLOB NOT NULL);"
                          "PRAGMA application_id=1397770564; PRAGMA user_version=1");
            v1::EnrollmentPolicy policy; policy.set_mode(v1::MANUAL); policy.set_bootstrap_profile("empty-v1");
            Query insert(db.get(), "INSERT INTO issuer VALUES(1,?,?,?,1,?)");
            insert.blob(1, random_id()); insert.blob(2, key->public_key()); insert.blob(3, *owner);
            insert.blob(4, policy.SerializeAsString()); insert.step();
            create_enrollment_schema(db.get()); create_security_schema(db.get()); create_bootstrap_schema(db.get());
            create_participant_schema(db.get()); create_barrier_schema(db.get()); create_supervisor_schema(db.get()); create_launch_schema(db.get()); create_fence_schema(db.get()); create_retirement_schema(db.get()); tx.commit();
            storage(fsync(directory.fd) == 0, "Issuer was initialized but directory durability is uncertain; preserve its files");
        }
        { Query integrity(db.get(), "PRAGMA quick_check");
          storage(integrity.step() == SQLITE_ROW && integrity.blob(0) == "ok", "Issuer database integrity check failed"); }
        Query row(db.get(), "SELECT issuer_id,public_key,owner_public_key FROM issuer WHERE singleton=1");
        storage(row.step() == SQLITE_ROW, "Issuer metadata is missing");
        identity.set_issuer_id(row.blob(0)); identity.set_public_key(row.blob(1)); owner_public = row.blob(2);
        storage(identity.issuer_id().size() == 32 && identity.public_key() == key->public_key() && owner_public.size() == 32,
                "Issuer key does not match its durable identity");
        policy();
        if (schema < 10) {
            Transaction migration(db.get());
            if (schema == 1) create_enrollment_schema(db.get());
            if (schema < 3) create_security_schema(db.get());
            if (schema < 4) create_bootstrap_schema(db.get());
            if (schema < 5) create_participant_schema(db.get());
            if (schema < 6) create_barrier_schema(db.get());
            if (schema < 7) create_supervisor_schema(db.get());
            if (schema < 8) create_launch_schema(db.get());
            if (schema < 9) create_fence_schema(db.get());
            create_retirement_schema(db.get()); migration.commit();
        }
    }

    std::uint64_t registry_revision() {
        Query row(db.get(), "SELECT revision FROM authority_registry WHERE singleton=1");
        storage(row.step() == SQLITE_ROW && row.integer(0) >= 0, "Invalid authority registry revision");
        return static_cast<std::uint64_t>(row.integer(0));
    }
    const v1::AuthorityAuthenticationResult& service_context(const IssuerConnection& connection) {
        if (!connection.authority || connection.binding.size() != 32)
            throw WorldError(wire::FORBIDDEN, "Authenticated service connection is required");
        const auto& context = *connection.authority;
        if (context.issuer_id() != identity.issuer_id() || context.issuer_incarnation() != incarnation ||
                context.connection_binding() != connection.binding || context.service_incarnation().size() != 32)
            throw WorldError(wire::UNAUTHORIZED, "Stale service connection context");
        const auto current = participant_record(context.registration().participant().id(), registry_revision());
        storage(current.SerializeAsString() == context.registration().SerializeAsString(), "Service registration changed");
        return context;
    }
    v1::AuthorityActivationState activation(const std::string& participant, std::uint64_t generation = 0) {
        Query row(db.get(), "SELECT generation,service_incarnation,issuer_incarnation,connection_binding,"
                            "registration,initial_revision,applied_revision,active FROM authority_activation "
                            "WHERE participant_id=? AND (?=0 OR generation=?) ORDER BY generation DESC LIMIT 1");
        row.blob(1, participant); row.integer(2, generation); row.integer(3, generation);
        if (row.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Service generation does not exist");
        const auto head = security_revision();
        storage(row.integer(0) > 0 && row.blob(1).size() == 32 && row.blob(2).size() == 32 && row.blob(3).size() == 32 &&
                row.integer(5) >= 0 && row.integer(6) >= row.integer(5) && static_cast<std::uint64_t>(row.integer(6)) <= head &&
                (row.integer(7) == 0 || row.integer(7) == 1), "Invalid durable service generation");
        v1::AuthorityActivationState result; result.set_issuer_id(identity.issuer_id());
        result.set_generation(row.integer(0)); result.set_service_incarnation(row.blob(1));
        result.set_issuer_incarnation(row.blob(2)); result.set_connection_binding(row.blob(3));
        storage(result.mutable_registration()->ParseFromString(row.blob(4)), "Invalid activation registration");
        const auto registered = participant_record(participant, registry_revision());
        storage(result.registration().SerializeAsString() == registered.SerializeAsString(), "Activation registration changed");
        result.set_applied_revision(row.integer(6)); result.set_head_revision(head); result.set_active(row.integer(7) != 0);
        return result;
    }
    v1::AuthorityActivationState bound_activation(const v1::AuthorityAuthenticationResult& context, std::uint64_t generation) {
        auto state = activation(context.registration().participant().id(), generation);
        if (state.service_incarnation() != context.service_incarnation() || state.issuer_incarnation() != context.issuer_incarnation() ||
                state.connection_binding() != context.connection_binding())
            throw WorldError(wire::UNAUTHORIZED, "Generation belongs to another service connection");
        return state;
    }
    v1::AuthorityBarrierStatus barrier(const v1::AuthorityBarrierRead& request) {
        Transaction transaction(db.get());
        Query row(db.get(), "SELECT b.participant_count,r.kind,r.target_id FROM authority_barrier b "
                            "JOIN revocation r ON r.revision=b.revision WHERE b.revision=?");
        row.integer(1, request.revision());
        if (row.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "No recorded cancellation barrier for this revision");
        storage(row.integer(0) >= 0 && row.blob(2).size() == 32 &&
                (row.integer(1) == v1::ACTOR_TARGET || row.integer(1) == v1::CREDENTIAL_TARGET), "Invalid durable barrier");
        v1::AuthorityBarrierStatus result; result.set_issuer_id(identity.issuer_id()); result.set_revision(request.revision());
        result.set_participant_count(row.integer(0)); result.mutable_target()->set_kind(static_cast<v1::IdentityTargetKind>(row.integer(1)));
        result.mutable_target()->set_id(row.blob(2));
        Query count(db.get(), "SELECT COUNT(*),COALESCE(SUM(a.applied_revision<o.revision AND t.generation IS NULL),0) "
                              "FROM authority_obligation o JOIN authority_activation a "
                              "ON a.participant_id=o.participant_id AND a.generation=o.generation "
                              "LEFT JOIN authority_retirement t ON t.participant_id=o.participant_id AND t.generation=o.generation WHERE o.revision=?");
        count.integer(1, request.revision());
        storage(count.step() == SQLITE_ROW && count.integer(0) == row.integer(0) && count.integer(1) >= 0 &&
                count.integer(1) <= count.integer(0), "Inconsistent barrier obligation count");
        result.set_pending_count(count.integer(1)); result.set_complete(count.integer(1) == 0);
        const auto limit = request.limit() ? request.limit() : 16;
        Query page(db.get(), "SELECT participant_id,generation FROM authority_obligation WHERE revision=? AND participant_id>? "
                             "ORDER BY participant_id LIMIT ?");
        page.integer(1, request.revision()); page.blob(2, request.after_participant_id()); page.integer(3, limit+1);
        while (page.step() == SQLITE_ROW) {
            if (static_cast<unsigned>(result.obligations_size()) == limit) {
                result.set_next_after(result.obligations(result.obligations_size()-1).registration().participant().id()); break;
            }
            storage(page.blob(0).size() == 32 && page.integer(1) > 0, "Invalid durable obligation identity");
            const auto state = activation(page.blob(0), page.integer(1));
            auto* obligation = result.add_obligations(); *obligation->mutable_registration() = state.registration();
            obligation->set_generation(state.generation()); obligation->set_service_incarnation(state.service_incarnation());
            obligation->set_applied_revision(state.applied_revision());
            const auto retired = retirement(page.blob(0), page.integer(1));
            if (retired) {
                storage(!state.active() && retired->registration().SerializeAsString() == state.registration().SerializeAsString() &&
                    retired->request().owner().service_incarnation() == state.service_incarnation() &&
                    retired->applied_revision() == state.applied_revision() && retired->security_revision() >= request.revision(),
                    "Retirement does not match barrier generation");
                *obligation->mutable_retirement() = *retired;
            }
            obligation->set_complete(state.applied_revision() >= request.revision() || retired.has_value());
        }
        transaction.commit(); return result;
    }
    bool fenced(const std::string& participant, std::uint64_t generation) {
        Query row(db.get(), "SELECT 1 FROM authority_fence WHERE participant_id=? AND generation=?");
        row.blob(1, participant); row.integer(2, generation);
        return row.step() == SQLITE_ROW;
    }
    v1::AuthoritySupervisorBinding fence_context(const v1::AuthorityFenceBegin& request, bool require_active = true) {
        validate_fence_begin(request);
        const auto& participant = request.participant_id();
        if (!supervised(participant)) throw WorldError(wire::FORBIDDEN, "Fencing requires a registered supervisor");
        const auto binding = supervision_record(participant);
        if (binding.registration().registered_revision() != request.registered_revision() ||
                binding.revision() != request.supervision_revision())
            throw WorldError(wire::REVISION_CONFLICT, "Fence registration or supervisor revision changed");
        const auto active = activation(participant, request.generation());
        if ((require_active && !active.active()) || active.service_incarnation() != request.service_incarnation() ||
                active.registration().SerializeAsString() != binding.registration().SerializeAsString())
            throw WorldError(wire::UNAUTHORIZED, "Fence does not match the active registered generation");
        const auto authorized = launch_authorization(participant, request.service_incarnation());
        if (!authorized || authorized->generation() != request.generation() ||
                authorized->authorization_id() != request.authorization_id() ||
                authorized->request().launch_id() != request.launch_id() ||
                authorized->request().supervision_revision() != request.supervision_revision() ||
                authorized->supervisor_public_key() != binding.public_key() ||
                authorized->request().service().registration().SerializeAsString() != active.registration().SerializeAsString() ||
                authorized->request().service().issuer_incarnation() != active.issuer_incarnation() ||
                authorized->request().service().connection_binding() != active.connection_binding())
            throw WorldError(wire::UNAUTHORIZED, "Fence does not match consumed launch authorization");
        return binding;
    }
    v1::AuthorityFenceState fence(const v1::AuthorityFenceBegin& request, const std::string& key) {
        const auto binding = fence_context(request);
        if (binding.public_key() != key) throw WorldError(wire::UNAUTHORIZED, "Fence supervisor key changed during proof");
        Query prior(db.get(), "SELECT fence_id,security_revision,supervisor_public_key,request,state "
                             "FROM authority_fence WHERE participant_id=? AND generation=?");
        prior.blob(1, request.participant_id()); prior.integer(2, request.generation());
        if (prior.step() == SQLITE_ROW) {
            v1::AuthorityFenceState result;
            storage(result.ParseFromString(prior.blob(4)), "Invalid durable fence state");
            try { validate_message(result); }
            catch (const std::invalid_argument&) { throw WorldError(wire::STORAGE_ERROR, "Invalid durable fence fields"); }
            storage(prior.blob(0).size() == 32 && prior.integer(1) >= 0 &&
                    static_cast<std::uint64_t>(prior.integer(1)) <= security_revision() && prior.blob(2) == key &&
                    prior.blob(3) == request.SerializeAsString() && result.issuer_id() == identity.issuer_id() &&
                    result.registration().SerializeAsString() == binding.registration().SerializeAsString() &&
                    result.generation() == request.generation() && result.service_incarnation() == request.service_incarnation() &&
                    result.authorization_id() == request.authorization_id() && result.launch_id() == request.launch_id() &&
                    result.supervision_revision() == request.supervision_revision() && result.fence_id() == prior.blob(0) &&
                    result.security_revision() == static_cast<std::uint64_t>(prior.integer(1)) && result.supervisor_public_key() == key,
                    "Inconsistent durable fence identity");
            return result;
        }
        v1::AuthorityFenceState result; result.set_issuer_id(identity.issuer_id());
        *result.mutable_registration() = binding.registration(); result.set_generation(request.generation());
        result.set_service_incarnation(request.service_incarnation()); result.set_authorization_id(request.authorization_id());
        result.set_launch_id(request.launch_id()); result.set_supervision_revision(request.supervision_revision());
        result.set_fence_id(random_id()); result.set_security_revision(security_revision()); result.set_supervisor_public_key(key);
        Query insert(db.get(), "INSERT INTO authority_fence VALUES(?,?,?,?,?,?,?)");
        insert.blob(1, request.participant_id()); insert.integer(2, request.generation()); insert.blob(3, result.fence_id());
        insert.integer(4, result.security_revision()); insert.blob(5, key); insert.blob(6, request.SerializeAsString());
        insert.blob(7, result.SerializeAsString()); insert.step();
        return result;
    }
    std::optional<v1::AuthorityRetirementState> retirement(const std::string& participant, std::uint64_t generation) {
        Query row(db.get(), "SELECT retirement_id,security_revision,applied_revision,supervisor_public_key,request,state "
                            "FROM authority_retirement WHERE participant_id=? AND generation=?");
        row.blob(1, participant); row.integer(2, generation);
        if (row.step() != SQLITE_ROW) return std::nullopt;
        v1::AuthorityRetirementState value;
        storage(value.ParseFromString(row.blob(5)), "Invalid durable retirement record");
        try { validate_retirement_state(value, identity.issuer_id()); }
        catch (const std::invalid_argument&) { throw WorldError(wire::STORAGE_ERROR, "Invalid durable retirement fields"); }
        storage(row.integer(1) >= 0 && row.integer(2) >= 0 && value.request().owner().participant_id() == participant &&
            value.request().owner().generation() == generation && value.retirement_id() == row.blob(0) &&
            value.security_revision() == static_cast<std::uint64_t>(row.integer(1)) && value.security_revision() <= security_revision() &&
            value.applied_revision() == static_cast<std::uint64_t>(row.integer(2)) && value.supervisor_public_key() == row.blob(3) &&
            value.request().SerializeAsString() == row.blob(4), "Inconsistent durable retirement identity");
        Query fence_row(db.get(), "SELECT fence_id,request,supervisor_public_key,security_revision FROM authority_fence "
                                  "WHERE participant_id=? AND generation=?");
        fence_row.blob(1, participant); fence_row.integer(2, generation);
        storage(fence_row.step() == SQLITE_ROW && fence_row.blob(0) == value.request().fence_id() &&
            fence_row.blob(1) == value.request().owner().SerializeAsString() && fence_row.blob(2) == value.supervisor_public_key() &&
            fence_row.integer(3) >= 0 && static_cast<std::uint64_t>(fence_row.integer(3)) <= value.security_revision(),
            "Retirement differs from durable fence");
        return value;
    }
    v1::AuthoritySupervisorBinding retirement_context(const v1::AuthorityRetireBegin& request) {
        validate_retire_begin(request);
        const auto& owner = request.owner();
        const auto binding = fence_context(owner, false);
        Query row(db.get(), "SELECT fence_id,security_revision,supervisor_public_key,request,state FROM authority_fence "
                            "WHERE participant_id=? AND generation=?");
        row.blob(1, owner.participant_id()); row.integer(2, owner.generation());
        if (row.step() != SQLITE_ROW) throw WorldError(wire::FORBIDDEN, "Retirement requires a committed fence");
        if (row.blob(0) != request.fence_id()) throw WorldError(wire::UNAUTHORIZED, "Retirement fence identity changed");
        v1::AuthorityFenceState expected;
        expected.set_issuer_id(identity.issuer_id()); *expected.mutable_registration() = binding.registration();
        expected.set_generation(owner.generation()); expected.set_service_incarnation(owner.service_incarnation());
        expected.set_authorization_id(owner.authorization_id()); expected.set_launch_id(owner.launch_id());
        expected.set_supervision_revision(owner.supervision_revision()); expected.set_fence_id(request.fence_id());
        expected.set_security_revision(row.integer(1)); expected.set_supervisor_public_key(binding.public_key());
        storage(row.integer(1) >= 0 && static_cast<std::uint64_t>(row.integer(1)) <= security_revision() &&
            row.blob(2) == binding.public_key() && row.blob(3) == owner.SerializeAsString() &&
            row.blob(4) == expected.SerializeAsString(), "Inconsistent retirement fence context");
        const auto prior = retirement(owner.participant_id(), owner.generation());
        const auto active = activation(owner.participant_id(), owner.generation());
        if (prior) {
            if (prior->request().SerializeAsString() != request.SerializeAsString())
                throw WorldError(wire::REVISION_CONFLICT, "Retirement retry changed its cleanup receipt");
            storage(!active.active() && prior->registration().SerializeAsString() == binding.registration().SerializeAsString() &&
                prior->supervisor_public_key() == binding.public_key() && prior->applied_revision() == active.applied_revision(),
                "Retirement activation history changed");
        } else if (!active.active()) throw WorldError(wire::UNAUTHORIZED, "Generation is inactive without checked retirement");
        return binding;
    }
    v1::AuthorityRetirementState retire(const v1::AuthorityRetireBegin& request, const std::string& key) {
        const auto binding = retirement_context(request);
        if (binding.public_key() != key) throw WorldError(wire::UNAUTHORIZED, "Retirement supervisor changed during proof");
        const auto& owner = request.owner();
        if (auto prior = retirement(owner.participant_id(), owner.generation())) return *prior;
        const auto active = activation(owner.participant_id(), owner.generation());
        v1::AuthorityRetirementState value;
        value.set_issuer_id(identity.issuer_id()); *value.mutable_request() = request;
        *value.mutable_registration() = binding.registration(); value.set_supervisor_public_key(key);
        value.set_retirement_id(random_id()); value.set_security_revision(security_revision());
        value.set_applied_revision(active.applied_revision());
        validate_retirement_state(value, identity.issuer_id());
        Query insert(db.get(), "INSERT INTO authority_retirement VALUES(?,?,?,?,?,?,?,?)");
        insert.blob(1, owner.participant_id()); insert.integer(2, owner.generation()); insert.blob(3, value.retirement_id());
        insert.integer(4, value.security_revision()); insert.integer(5, value.applied_revision()); insert.blob(6, key);
        insert.blob(7, request.SerializeAsString()); insert.blob(8, value.SerializeAsString()); insert.step();
        Query update(db.get(), "UPDATE authority_activation SET active=0 WHERE participant_id=? AND generation=? AND active=1");
        update.blob(1, owner.participant_id()); update.integer(2, owner.generation()); update.step();
        storage(sqlite3_changes(db.get()) == 1, "Retirement lost its active generation");
        return value;
    }
    v1::AuthoritySupervisorBinding launch_context(const v1::AuthorityLaunchBegin& request) {
        validate_launch_begin(request, identity.issuer_id());
        const auto& service = request.service();
        if (service.issuer_incarnation() != incarnation || service.security_revision() > security_revision())
            throw WorldError(wire::UNAUTHORIZED, "Launch service context is stale or ahead of issuer history");
        const auto& participant = service.registration().participant().id();
        if (!supervised(participant)) throw WorldError(wire::FORBIDDEN, "Participant has no registered supervisor");
        const auto binding = supervision_record(participant);
        if (binding.registration().SerializeAsString() != service.registration().SerializeAsString())
            throw WorldError(wire::UNAUTHORIZED, "Launch service registration does not match supervisor binding");
        if (binding.revision() != request.supervision_revision())
            throw WorldError(wire::REVISION_CONFLICT, "Launch supervisor revision changed");
        return binding;
    }
    std::optional<v1::AuthorityLaunchAuthorization> launch_authorization(const std::string& participant, const std::string& service) {
        Query row(db.get(), "SELECT authorization_id,launch_id,request,supervisor_public_key,generation "
                            "FROM authority_launch_authorization WHERE participant_id=? AND service_incarnation=?");
        row.blob(1, participant); row.blob(2, service);
        if (row.step() != SQLITE_ROW) return {};
        v1::AuthorityLaunchAuthorization result;
        storage(row.blob(0).size() == 32 && row.blob(1).size() == 32 && row.blob(3).size() == 32 && row.integer(4) >= 0 &&
                result.mutable_request()->ParseFromString(row.blob(2)), "Invalid durable launch authorization");
        try { validate_launch_begin(result.request(), identity.issuer_id()); }
        catch (const std::invalid_argument&) { throw WorldError(wire::STORAGE_ERROR, "Invalid durable launch request"); }
        const auto& request = result.request();
        storage(request.service().registration().participant().id() == participant && request.service().service_incarnation() == service &&
                request.launch_id() == row.blob(1) && (row.integer(4) == 0 || (request.previous_generation() < INT64_MAX &&
                static_cast<std::uint64_t>(row.integer(4)) == request.previous_generation()+1)), "Contradictory durable launch context");
        result.set_authorization_id(row.blob(0)); result.set_supervisor_public_key(row.blob(3)); result.set_generation(row.integer(4));
        return result;
    }
    v1::AuthorityLaunchAuthorization authorize_launch(const v1::AuthorityLaunchBegin& request, const std::string& public_key) {
        const auto binding = launch_context(request);
        if (binding.public_key() != public_key) throw WorldError(wire::UNAUTHORIZED, "Launch supervisor key changed during proof");
        const auto& participant = request.service().registration().participant().id();
        const auto existing = launch_authorization(participant, request.service().service_incarnation());
        if (existing) {
            if (existing->request().SerializeAsString() != request.SerializeAsString() || existing->supervisor_public_key() != public_key)
                throw WorldError(wire::ALREADY_EXISTS, "Service incarnation already has another launch authorization");
            return *existing;
        }
        Query active(db.get(), "SELECT 1 FROM authority_activation WHERE participant_id=? AND active=1"); active.blob(1, participant);
        if (active.step() == SQLITE_ROW) throw WorldError(wire::BUSY, "New launch requires prior generation cleanup");
        Query prior(db.get(), "SELECT COALESCE(MAX(generation),0) FROM authority_activation WHERE participant_id=?"); prior.blob(1, participant);
        storage(prior.step() == SQLITE_ROW && prior.integer(0) >= 0, "Invalid previous generation");
        if (static_cast<std::uint64_t>(prior.integer(0)) != request.previous_generation())
            throw WorldError(wire::REVISION_CONFLICT, "Launch previous generation changed");
        Query used(db.get(), "SELECT 1 FROM authority_activation WHERE participant_id=? AND service_incarnation=?");
        used.blob(1, participant); used.blob(2, request.service().service_incarnation());
        if (used.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "Activated service incarnation cannot receive a new authorization");
        Query launch(db.get(), "SELECT 1 FROM authority_launch_authorization WHERE launch_id=?"); launch.blob(1, request.launch_id());
        if (launch.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "Launch identity already has an authorization");
        Query insert(db.get(), "INSERT INTO authority_launch_authorization VALUES(?,?,?,?,?,?,0)");
        insert.blob(1, random_id()); insert.blob(2, participant); insert.blob(3, request.service().service_incarnation());
        insert.blob(4, request.launch_id()); insert.blob(5, request.SerializeAsString()); insert.blob(6, public_key); insert.step();
        const auto result = launch_authorization(participant, request.service().service_incarnation());
        storage(result.has_value(), "Committed launch authorization is missing"); return *result;
    }
    bool supervisor_key(const std::string& public_key) {
        Query row(db.get(), "SELECT 1 FROM authority_supervisor WHERE public_key=?"); row.blob(1, public_key);
        return row.step() == SQLITE_ROW;
    }
    bool supervised(const std::string& participant) {
        Query row(db.get(), "SELECT 1 FROM authority_supervisor WHERE participant_id=?"); row.blob(1, participant);
        return row.step() == SQLITE_ROW;
    }
    v1::AuthoritySupervisorBinding supervision_record(const std::string& participant) {
        Query row(db.get(), "SELECT registration,public_key,revision FROM authority_supervisor WHERE participant_id=?");
        row.blob(1, participant);
        if (row.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Supervisor binding does not exist");
        v1::AuthoritySupervisorBinding result;
        storage(result.mutable_registration()->ParseFromString(row.blob(0)) && row.blob(1).size() == 32 && row.integer(2) > 0,
                "Invalid durable supervisor binding");
        const auto registration = participant_record(participant, registry_revision());
        storage(result.registration().SerializeAsString() == registration.SerializeAsString(), "Supervisor registration changed");
        result.set_public_key(row.blob(1)); result.set_revision(row.integer(2));
        storage(result.public_key() != owner_public && result.public_key() != identity.public_key() &&
                !participant_key(result.public_key()), "Supervisor key has another authority role");
        Query actor(db.get(), "SELECT 1 FROM enrollment WHERE public_key=?"); actor.blob(1, result.public_key());
        storage(actor.step() == SQLITE_DONE, "Supervisor key is enrolled as an actor");
        return result;
    }
    v1::AuthoritySupervisorBinding read_supervisor(const v1::AuthoritySupervisorRead& request) {
        Transaction transaction(db.get()); auto result = supervision_record(request.participant_id());
        transaction.commit(); return result;
    }
    v1::AuthoritySupervisorBinding bind_supervisor(const v1::AuthoritySupervisorBind& request) {
        Transaction transaction(db.get());
        Query exists(db.get(), "SELECT 1 FROM authority_participant WHERE id=?"); exists.blob(1, request.participant_id());
        if (exists.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Authority participant does not exist");
        const auto registration = participant_record(request.participant_id(), registry_revision());
        if (registration.registered_revision() != request.registered_revision())
            throw WorldError(wire::UNAUTHORIZED, "Supervisor binding names another registration");
        Query active(db.get(), "SELECT 1 FROM authority_activation WHERE participant_id=? AND active=1");
        active.blob(1, request.participant_id());
        if (active.step() == SQLITE_ROW) throw WorldError(wire::BUSY, "Supervisor binding cannot adopt an active generation");
        const bool existing = supervised(request.participant_id());
        const auto revision = existing ? supervision_record(request.participant_id()).revision() : 0;
        if (revision != request.expected_revision()) throw WorldError(wire::REVISION_CONFLICT, "Supervisor binding revision changed");
        const auto& public_key = request.public_key();
        if (public_key == owner_public || public_key == identity.public_key() || participant_key(public_key))
            throw WorldError(wire::FORBIDDEN, "Supervisor key must be separate from issuer and service keys");
        Query actor(db.get(), "SELECT 1 FROM enrollment WHERE public_key=?"); actor.blob(1, public_key);
        if (actor.step() == SQLITE_ROW) throw WorldError(wire::FORBIDDEN, "Actor enrollment keys cannot supervise services");
        storage(revision < INT64_MAX, "Supervisor binding revision limit reached");
        Query write(db.get(), existing ?
            "UPDATE authority_supervisor SET registration=?,public_key=?,revision=? WHERE participant_id=?" :
            "INSERT INTO authority_supervisor(registration,public_key,revision,participant_id) VALUES(?,?,?,?)");
        write.blob(1, registration.SerializeAsString()); write.blob(2, public_key); write.integer(3, revision+1);
        write.blob(4, request.participant_id()); write.step();
        storage(sqlite3_changes(db.get()) == 1, "Supervisor binding mutation was not recorded");
        auto result = supervision_record(request.participant_id()); transaction.commit(); return result;
    }
    bool participant_key(const std::string& public_key) {
        Query row(db.get(), "SELECT 1 FROM authority_participant WHERE public_key=?"); row.blob(1, public_key);
        return row.step() == SQLITE_ROW;
    }
    v1::AuthorityParticipantRecord participant_record(const std::string& id, std::uint64_t head) {
        Query row(db.get(), "SELECT public_key,registered_revision,definition FROM authority_participant WHERE id=?");
        row.blob(1, id);
        storage(row.step() == SQLITE_ROW, "Required authority participant is missing");
        v1::AuthorityParticipantRecord record; record.set_issuer_id(identity.issuer_id());
        storage(row.integer(1) > 0 && static_cast<std::uint64_t>(row.integer(1)) <= head,
                "Invalid authority participant revision");
        record.set_registered_revision(static_cast<std::uint64_t>(row.integer(1)));
        storage(record.mutable_participant()->ParseFromString(row.blob(2)), "Corrupt authority participant");
        try { validate_participant_record(record, identity.issuer_id()); }
        catch (const std::invalid_argument&) { throw WorldError(wire::STORAGE_ERROR, "Invalid durable authority participant"); }
        storage(record.participant().id() == id && record.participant().public_key() == row.blob(0) &&
                row.blob(0) != owner_public && row.blob(0) != identity.public_key(), "Contradictory authority participant identity");
        Query actor(db.get(), "SELECT 1 FROM enrollment WHERE public_key=?"); actor.blob(1, row.blob(0));
        storage(actor.step() == SQLITE_DONE, "Service key is also registered for actor enrollment");
        Query domains(db.get(), "SELECT kind,domain_id FROM authority_participant_domain WHERE participant_id=? ORDER BY kind,domain_id");
        domains.blob(1, id); int index = 0;
        while (domains.step() == SQLITE_ROW) {
            storage(index < record.participant().domains_size(), "Excess authority participant domain");
            const auto& expected = record.participant().domains(index++);
            storage(domains.integer(0) == expected.kind() && domains.blob(1) == expected.id(),
                    "Contradictory authority participant domain");
        }
        storage(index == record.participant().domains_size(), "Missing authority participant domain");
        return record;
    }
    v1::AuthorityRegistryPage registry(const v1::AuthorityRegistryRead& request) {
        Transaction transaction(db.get());
        const auto head = registry_revision(); const auto limit = request.limit() ? request.limit() : 16;
        v1::AuthorityRegistryPage page; page.set_issuer_id(identity.issuer_id()); page.set_registry_revision(head);
        Query rows(db.get(), "SELECT id FROM authority_participant WHERE id>? ORDER BY id LIMIT ?");
        rows.blob(1, request.after_id()); rows.integer(2, limit+1);
        while (rows.step() == SQLITE_ROW) {
            if (static_cast<unsigned>(page.participants_size()) == limit) {
                page.set_next_after(page.participants(page.participants_size()-1).participant().id()); break;
            }
            *page.add_participants() = participant_record(rows.blob(0), head);
        }
        try { validate_registry_page(page, identity.issuer_id()); }
        catch (const std::invalid_argument&) { throw WorldError(wire::STORAGE_ERROR, "Invalid durable authority registry"); }
        transaction.commit(); return page;
    }
    v1::AuthorityParticipantRecord register_participant(const v1::AuthorityParticipantRegister& request) {
        Transaction transaction(db.get()); const auto head = registry_revision();
        if (head != request.expected_registry_revision()) throw WorldError(wire::REVISION_CONFLICT, "Authority registry revision changed");
        auto participant = request.participant();
        if (supervisor_key(participant.public_key())) throw WorldError(wire::FORBIDDEN, "Supervisor keys cannot register as services");
        std::sort(participant.mutable_domains()->begin(), participant.mutable_domains()->end(), [](const auto& a, const auto& b) {
            return participant_detail::domain_key(a) < participant_detail::domain_key(b);
        });
        if (participant.public_key() == owner_public || participant.public_key() == identity.public_key())
            throw WorldError(wire::FORBIDDEN, "Issuer keys cannot serve as authority participant keys");
        Query actor(db.get(), "SELECT 1 FROM enrollment WHERE public_key=?"); actor.blob(1, participant.public_key());
        if (actor.step() == SQLITE_ROW) throw WorldError(wire::FORBIDDEN, "Actor enrollment keys cannot serve as authority participant keys");
        Query existing(db.get(), "SELECT 1 FROM authority_participant WHERE id=? OR public_key=?");
        existing.blob(1, participant.id()); existing.blob(2, participant.public_key());
        if (existing.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "Authority participant identity or key is already registered");
        for (const auto& domain : participant.domains()) {
            Query claimed(db.get(), "SELECT 1 FROM authority_participant_domain WHERE kind=? AND domain_id=?");
            claimed.integer(1, domain.kind()); claimed.blob(2, domain.id());
            if (claimed.step() == SQLITE_ROW) throw WorldError(wire::IN_USE, "Authority domain already has a registered participant");
        }
        storage(head < INT64_MAX, "Authority registry revision limit reached");
        Query insert(db.get(), "INSERT INTO authority_participant(id,public_key,registered_revision,definition) VALUES(?,?,?,?)");
        insert.blob(1, participant.id()); insert.blob(2, participant.public_key()); insert.integer(3, head+1);
        insert.blob(4, participant.SerializeAsString()); insert.step();
        for (const auto& domain : participant.domains()) {
            Query claim(db.get(), "INSERT INTO authority_participant_domain(kind,domain_id,participant_id) VALUES(?,?,?)");
            claim.integer(1, domain.kind()); claim.blob(2, domain.id()); claim.blob(3, participant.id()); claim.step();
        }
        Query update(db.get(), "UPDATE authority_registry SET revision=? WHERE singleton=1 AND revision=?");
        update.integer(1, head+1); update.integer(2, head); update.step();
        storage(sqlite3_changes(db.get()) == 1, "Authority registry lost its revision");
        const auto result = participant_record(participant.id(), head+1); transaction.commit(); return result;
    }
    v1::BootstrapProfileState profile(const v1::BootstrapProfileRead& request) {
        v1::BootstrapProfileState result; result.set_issuer_id(identity.issuer_id());
        if (request.id() == "empty-v1") {
            if (request.has_revision() && request.revision() != 1)
                throw WorldError(wire::NOT_FOUND, "Bootstrap profile revision is unavailable");
            result.mutable_profile()->set_id("empty-v1"); result.set_revision(1); return result;
        }
        Query row(db.get(), request.has_revision() ?
            "SELECT revision,profile FROM bootstrap_profile WHERE id=? AND revision=?" :
            "SELECT revision,profile FROM bootstrap_profile WHERE id=? ORDER BY revision DESC LIMIT 1");
        row.blob(1, request.id());
        if (request.has_revision()) row.integer(2, request.revision());
        if (row.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Bootstrap profile revision is unavailable");
        const auto bytes = row.blob(1);
        storage(row.integer(0) > 0 && bytes.size() <= 32768 && result.mutable_profile()->ParseFromString(bytes),
                "Bootstrap profile storage is invalid");
        try { validate_bootstrap_profile(result.profile()); }
        catch (const std::invalid_argument&) { throw WorldError(wire::STORAGE_ERROR, "Bootstrap profile is corrupt"); }
        storage(result.profile().id() == request.id(), "Bootstrap profile identity is corrupt");
        result.set_revision(static_cast<std::uint64_t>(row.integer(0))); return result;
    }

    v1::BootstrapProfileState put_profile(const v1::BootstrapProfilePut& request) {
        if (request.profile().id() == "empty-v1") throw WorldError(wire::FORBIDDEN, "The empty bootstrap profile is immutable");
        Transaction transaction(db.get());
        std::uint64_t revision = 0;
        v1::BootstrapProfileRead read; read.set_id(request.profile().id());
        try { revision = profile(read).revision(); }
        catch (const WorldError& error) { if (error.code != wire::NOT_FOUND) throw; }
        if (revision != request.expected_revision()) throw WorldError(wire::REVISION_CONFLICT, "Bootstrap profile revision changed");
        storage(revision < INT64_MAX, "Bootstrap profile revision limit reached");
        Query insert(db.get(), "INSERT INTO bootstrap_profile(id,revision,profile) VALUES(?,?,?)");
        insert.blob(1, request.profile().id()); insert.integer(2, revision+1);
        insert.blob(3, request.profile().SerializeAsString()); insert.step();
        const auto current = policy();
        if (current.policy().bootstrap_profile() == request.profile().id()) {
            storage(current.revision() < INT64_MAX, "Issuer policy revision limit reached");
            sql(db.get(), "UPDATE issuer SET revision=revision+1 WHERE singleton=1");
            storage(sqlite3_changes(db.get()) == 1, "Issuer policy update did not change one record");
        }
        v1::BootstrapProfileState result; *result.mutable_profile() = request.profile();
        result.set_revision(revision+1); result.set_issuer_id(identity.issuer_id());
        transaction.commit(); return result;
    }

    v1::PolicyState policy() {
        Query query(db.get(), "SELECT revision,policy FROM issuer WHERE singleton=1");
        storage(query.step() == SQLITE_ROW && query.integer(0) > 0, "Issuer policy is missing or invalid");
        v1::PolicyState result; result.set_revision(static_cast<std::uint64_t>(query.integer(0)));
        storage(result.mutable_policy()->ParseFromString(query.blob(1)), "Issuer policy protobuf is corrupt");
        try { validate_policy(result.policy()); }
        catch (const std::invalid_argument&) { throw WorldError(wire::STORAGE_ERROR, "Issuer policy is invalid"); }
        return result;
    }

    v1::PolicyState set_policy(const v1::PolicySet& request) {
        Transaction transaction(db.get());
        auto current = policy();
        if (current.revision() != request.expected_revision())
            throw WorldError(wire::REVISION_CONFLICT, "Issuer policy revision changed");
        storage(current.revision() < INT64_MAX, "Issuer revision limit reached");
        Query update(db.get(), "UPDATE issuer SET policy=?,revision=revision+1 WHERE singleton=1 AND revision=?");
        update.blob(1, request.policy().SerializeAsString()); update.integer(2, current.revision()); update.step();
        storage(sqlite3_changes(db.get()) == 1, "Issuer policy update did not change one record");
        transaction.commit();
        *current.mutable_policy() = request.policy(); current.set_revision(current.revision() + 1);
        return current;
    }
    void expire() {
        const auto now = Clock::now();
        std::erase_if(pending, [&](const auto& item) { return now >= item.second.expires; });
    }
    std::string nonce() {
        expire();
        if (pending.size() >= 256) throw WorldError(wire::BUSY, "Issuer challenge capacity reached");
        auto value = random_id();
        if (pending.contains(value)) throw std::runtime_error("Issuer entropy collision");
        return value;
    }
    Pending consume(const std::string& nonce, const v1::KeyProof& proof, v1::SignaturePurpose purpose) {
        expire();
        const auto found = pending.find(nonce);
        if (found == pending.end()) throw WorldError(wire::UNAUTHORIZED, "Issuer challenge is unavailable");
        auto value = std::move(found->second); pending.erase(found);
        if (value.content.purpose() != purpose) throw WorldError(wire::UNAUTHORIZED, "Wrong issuer challenge purpose");
        v1::VerifyRequest verification; *verification.mutable_content() = value.content;
        *verification.mutable_proof() = proof; verification.set_expected_public_key(value.expected_key);
        if (!verify(verification) || Clock::now() >= value.expires)
            throw WorldError(wire::UNAUTHORIZED, "Issuer proof is invalid or expired");
        return value;
    }
    bool revoked(v1::IdentityTargetKind kind, const std::string& id) {
        Query query(db.get(), "SELECT 1 FROM revocation WHERE kind=? AND target_id=?");
        query.integer(1, kind); query.blob(2, id); return query.step() == SQLITE_ROW;
    }
    std::uint64_t security_revision() {
        Query query(db.get(), "SELECT revision FROM security_state WHERE singleton=1");
        storage(query.step() == SQLITE_ROW && query.integer(0) >= 0, "Invalid security revision");
        return static_cast<std::uint64_t>(query.integer(0));
    }
    v1::IdentityStatus identity_status(const v1::IdentityReference& target) {
        v1::IdentityStatus result; result.set_issuer_id(identity.issuer_id());
        if (target.kind() == v1::ACTOR_TARGET) {
            Query query(db.get(), "SELECT kind FROM actor WHERE id=?"); query.blob(1, target.id());
            if (query.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Actor identity does not exist");
            result.set_actor_id(target.id()); result.set_kind(static_cast<v1::ActorKind>(query.integer(0)));
        } else {
            Query query(db.get(), "SELECT c.actor_id,a.kind FROM credential c JOIN actor a ON a.id=c.actor_id WHERE c.id=?");
            query.blob(1, target.id());
            if (query.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Credential identity does not exist");
            result.set_credential_id(target.id()); result.set_actor_id(query.blob(0));
            result.set_kind(static_cast<v1::ActorKind>(query.integer(1)));
            result.set_credential_revoked(revoked(v1::CREDENTIAL_TARGET, target.id()));
        }
        storage(result.actor_id().size() == 32 && (result.kind() == v1::HUMAN || result.kind() == v1::AGENT), "Invalid durable actor identity");
        result.set_actor_revoked(revoked(v1::ACTOR_TARGET, result.actor_id()));
        result.set_active(!result.actor_revoked() && !result.credential_revoked());
        result.set_security_revision(security_revision()); return result;
    }
    v1::IdentityStatus check_credential(const v1::SignedCredential& credential) {
        validate_message(credential);
        require(credential.has_content() && credential.has_proof(), "Credential content and proof are required");
        if (credential.content().purpose() != v1::ISSUER_CREDENTIAL)
            throw WorldError(wire::UNAUTHORIZED, "Wrong credential signature purpose");
        v1::VerifyRequest verification; *verification.mutable_content() = credential.content();
        *verification.mutable_proof() = credential.proof(); verification.set_expected_public_key(identity.public_key());
        if (!verify(verification)) throw WorldError(wire::UNAUTHORIZED, "Issuer credential signature is invalid");
        v1::ActorCredential claims;
        require(claims.ParseFromString(credential.content().payload()), "Malformed credential claims");
        validate_message(claims);
        if (claims.protocol_version() != 1 || claims.issuer_id() != identity.issuer_id() ||
            claims.audience() != "spatial/actor-credential/v1" || claims.actor_id().size() != 32 ||
            claims.credential_id().size() != 32 || claims.public_key().size() != 32 || claims.enrollment_id().size() != 32)
            throw WorldError(wire::UNAUTHORIZED, "Invalid issuer credential claims");
        Query query(db.get(), "SELECT c.actor_id,c.public_key,c.enrollment_id,c.signed_credential,a.kind "
                              "FROM credential c JOIN actor a ON a.id=c.actor_id WHERE c.id=?");
        query.blob(1, claims.credential_id());
        if (query.step() != SQLITE_ROW || query.blob(0) != claims.actor_id() || query.blob(1) != claims.public_key() ||
            query.blob(2) != claims.enrollment_id() || query.blob(3) != credential.SerializeAsString() || query.integer(4) != claims.kind())
            throw WorldError(wire::UNAUTHORIZED, "Credential is not registered with this issuer");
        v1::IdentityReference target; target.set_kind(v1::CREDENTIAL_TARGET); target.set_id(claims.credential_id());
        return identity_status(target);
    }
    v1::IdentityStatus revoke(const v1::IdentityRevoke& request) {
        Transaction transaction(db.get());
        const auto before = identity_status(request.target());
        if (before.security_revision() != request.expected_security_revision())
            throw WorldError(wire::REVISION_CONFLICT, "Issuer security revision changed");
        if (!revoked(request.target().kind(), request.target().id())) {
            storage(before.security_revision() < INT64_MAX, "Issuer security revision limit reached");
            Query revision(db.get(), "UPDATE security_state SET revision=revision+1 WHERE singleton=1 AND revision=?");
            revision.integer(1, before.security_revision()); revision.step();
            storage(sqlite3_changes(db.get()) == 1, "Revocation lost its security revision");
            Query insert(db.get(), "INSERT INTO revocation(kind,target_id,revision,reason) VALUES(?,?,?,?)");
            insert.integer(1, request.target().kind()); insert.blob(2, request.target().id());
            insert.integer(3, before.security_revision()+1); insert.blob(4, request.reason()); insert.step();
            Query barrier(db.get(), "INSERT INTO authority_barrier(revision,participant_count) "
                                    "SELECT ?,COUNT(*) FROM authority_activation WHERE active=1");
            barrier.integer(1, before.security_revision()+1); barrier.step();
            Query obligations(db.get(), "INSERT INTO authority_obligation(revision,participant_id,generation) "
                                        "SELECT ?,participant_id,generation FROM authority_activation WHERE active=1");
            obligations.integer(1, before.security_revision()+1); obligations.step();
        }
        const auto result = identity_status(request.target()); transaction.commit();
        // Only committed revocation invalidates pending authentication. Independent credentials survive.
        std::erase_if(pending, [&](const auto& item) {
            const auto& subject = item.second.authenticated_identity;
            return request.target().kind() == v1::ACTOR_TARGET ? subject.actor_id() == request.target().id() :
                   subject.credential_id() == request.target().id();
        });
        return result;
    }
    v1::EnrollmentRecord enrollment(const std::string& id) {
        Query query(db.get(), "SELECT e.id,e.intent,e.status,c.signed_credential FROM enrollment e "
                              "LEFT JOIN credential c ON c.enrollment_id=e.id WHERE e.id=?");
        query.blob(1, id);
        if (query.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Enrollment request does not exist");
        v1::EnrollmentRecord result; result.set_enrollment_id(query.blob(0));
        storage(result.mutable_intent()->ParseFromString(query.blob(1)), "Corrupt enrollment intent");
        try { validate_intent(result.intent()); }
        catch (const std::invalid_argument&) { throw WorldError(wire::STORAGE_ERROR, "Invalid durable enrollment intent"); }
        const auto status = query.integer(2);
        storage(status >= v1::PENDING && status <= v1::REJECTED, "Invalid enrollment status");
        result.set_status(static_cast<v1::EnrollmentStatus>(status));
        if (status == v1::APPROVED) {
            storage(!query.blob(3).empty() && result.mutable_credential()->ParseFromString(query.blob(3)), "Missing issued credential");
        } else storage(query.blob(3).empty(), "Unapproved enrollment has a credential");
        return result;
    }
    void issue(const v1::EnrollmentRecord& record, const v1::PolicyState& policy, const std::string& target,
               bool automatic = false) {
        // Resolve inside the enrollment transaction. Templates do not install domain grants.
        v1::BootstrapProfileRead read; read.set_id(policy.policy().bootstrap_profile());
        const auto selected = profile(read);
        if (automatic && bootstrap_has_administration(selected.profile()))
            throw WorldError(wire::FORBIDDEN, "Automatic enrollment cannot issue administration templates");
        auto actor = target;
        if (actor.empty()) {
            actor = random_id();
            Query insert(db.get(), "INSERT INTO actor(id,kind,label) VALUES(?,?,?)");
            insert.blob(1, actor); insert.integer(2, record.intent().kind()); insert.blob(3, record.intent().label()); insert.step();
        } else {
            Query query(db.get(), "SELECT kind FROM actor WHERE id=?"); query.blob(1, actor);
            if (query.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Target actor does not exist");
            if (revoked(v1::ACTOR_TARGET, actor)) throw WorldError(wire::UNAUTHORIZED, "Target actor is revoked");
            require(query.integer(0) == record.intent().kind(), "Credential actor kind cannot change the target identity");
        }
        v1::ActorCredential claims; claims.set_protocol_version(1); claims.set_issuer_id(identity.issuer_id());
        claims.set_actor_id(actor); claims.set_credential_id(random_id()); claims.set_public_key(record.intent().public_key());
        claims.set_kind(record.intent().kind()); claims.set_enrollment_id(record.enrollment_id());
        claims.set_bootstrap_profile(selected.profile().id()); claims.set_bootstrap_profile_revision(selected.revision());
        claims.set_policy_revision(policy.revision()); claims.set_audience("spatial/actor-credential/v1");
        v1::SignedCredential signed_credential;
        signed_credential.mutable_content()->set_purpose(v1::ISSUER_CREDENTIAL);
        signed_credential.mutable_content()->set_payload(claims.SerializeAsString());
        *signed_credential.mutable_proof() = key->sign(signed_credential.content());
        Query insert(db.get(), "INSERT INTO credential(id,actor_id,public_key,enrollment_id,signed_credential) VALUES(?,?,?,?,?)");
        insert.blob(1, claims.credential_id()); insert.blob(2, actor); insert.blob(3, record.intent().public_key());
        insert.blob(4, record.enrollment_id()); insert.blob(5, signed_credential.SerializeAsString()); insert.step();
        Query update(db.get(), "UPDATE enrollment SET status=2 WHERE id=? AND status=1");
        update.blob(1, record.enrollment_id()); update.step();
        storage(sqlite3_changes(db.get()) == 1, "Enrollment issuance lost its pending request");
    }
    v1::EnrollmentRecord decide(const v1::EnrollmentDecision& decision) {
        Transaction transaction(db.get());
        const auto record = enrollment(decision.enrollment_id());
        if (record.status() != v1::PENDING) throw WorldError(wire::REVISION_CONFLICT, "Enrollment decision is already final");
        const auto current = policy();
        if (current.revision() != decision.expected_policy_revision())
            throw WorldError(wire::REVISION_CONFLICT, "Issuer policy changed before enrollment decision");
        if (decision.choice() == v1::REJECT) {
            Query update(db.get(), "UPDATE enrollment SET status=3 WHERE id=? AND status=1");
            update.blob(1, record.enrollment_id()); update.step();
            storage(sqlite3_changes(db.get()) == 1, "Enrollment rejection lost its pending request");
        } else issue(record, current, decision.target_actor_id());
        const auto result = enrollment(record.enrollment_id()); transaction.commit(); return result;
    }
    v1::EnrollmentRecord submit(const v1::EnrollmentIntent& intent) {
        Transaction transaction(db.get());
        if (supervisor_key(intent.public_key())) throw WorldError(wire::FORBIDDEN, "Supervisor keys cannot enroll as actors");
        if (participant_key(intent.public_key())) throw WorldError(wire::FORBIDDEN, "Authority participant keys cannot enroll as actors");
        std::string existing;
        { Query query(db.get(), "SELECT id FROM enrollment WHERE public_key=?"); query.blob(1, intent.public_key());
          if (query.step() == SQLITE_ROW) existing = query.blob(0); }
        if (!existing.empty()) {
            const auto record = enrollment(existing);
            if (record.intent().SerializeAsString() != intent.SerializeAsString())
                throw WorldError(wire::ALREADY_EXISTS, "This key already has a different enrollment intent");
            transaction.commit(); return record;
        }
        const auto current = policy();
        if (current.policy().mode() == v1::MANUAL) {
            Query count(db.get(), "SELECT count(*) FROM enrollment WHERE status=1"); count.step();
            if (count.integer(0) >= 1024) throw WorldError(wire::BUSY, "Pending enrollment capacity reached");
        }
        const auto id = random_id();
        Query insert(db.get(), "INSERT INTO enrollment(id,public_key,intent,status) VALUES(?,?,?,1)");
        insert.blob(1, id); insert.blob(2, intent.public_key()); insert.blob(3, intent.SerializeAsString()); insert.step();
        if (current.policy().mode() == v1::APPROVE_ALWAYS) issue(enrollment(id), current, {}, true);
        const auto result = enrollment(id); transaction.commit(); return result;
    }
};

Issuer::Issuer(const std::filesystem::path& state, const std::optional<std::string>& owner, std::chrono::milliseconds ttl, bool migrate)
    : impl_(std::make_unique<Impl>(state, owner, ttl, migrate)) {}
Issuer::~Issuer() = default;
std::uint64_t Issuer::security_revision() { return impl_->security_revision(); }
v1::RevocationBatch Issuer::revocations(const v1::RevocationRead& request) {
    validate_message(request);
    require(request.wait_ms() <= 1000 && request.after_revision() <= INT64_MAX,
            "Revocation cursor or wait is out of range");
    Transaction transaction(impl_->db.get());
    const auto head = impl_->security_revision();
    v1::RevocationBatch result;
    result.set_issuer_id(impl_->identity.issuer_id()); result.set_head_revision(head);
    const auto from = request.has_after_revision() ? request.after_revision() : head;
    require(from <= head, "Revocation cursor is ahead of issuer history");
    result.set_initial(!request.has_after_revision()); result.set_from_revision(from);
    auto through = from;
    if (request.has_after_revision()) {
        Query rows(impl_->db.get(), "SELECT kind,target_id,revision FROM revocation WHERE revision>? ORDER BY revision LIMIT 128");
        rows.integer(1, from);
        while (rows.step() == SQLITE_ROW) {
            const auto kind = rows.integer(0); const auto id = rows.blob(1); const auto revision = rows.integer(2);
            storage((kind == v1::ACTOR_TARGET || kind == v1::CREDENTIAL_TARGET) && id.size() == 32 &&
                    revision > 0 && static_cast<std::uint64_t>(revision) == through+1 &&
                    static_cast<std::uint64_t>(revision) <= head, "Invalid durable revocation history");
            auto* event = result.add_events(); event->set_revision(++through);
            event->mutable_target()->set_kind(static_cast<v1::IdentityTargetKind>(kind));
            event->mutable_target()->set_id(id);
        }
        storage(through == from+std::min<std::uint64_t>(head-from, 128), "Missing durable revocation history");
    }
    result.set_through_revision(through); transaction.commit(); return result;
}
v1::IssuerInfo Issuer::info() {
    auto result = impl_->identity; result.set_policy_revision(impl_->policy().revision()); return result;
}
v1::BootstrapProfileState Issuer::profile_read(const v1::BootstrapProfileRead& request) {
    validate_bootstrap_read(request, true); return impl_->profile(request);
}
v1::ChallengeReply Issuer::challenge(const v1::IssuerOperation& operation) {
    validate_operation(operation);
    const auto nonce = impl_->nonce();
    v1::IssuerChallenge payload; payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_incarnation(impl_->incarnation); payload.set_nonce(nonce); payload.set_owner_public_key(impl_->owner_public);
    *payload.mutable_operation() = operation; payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count()));
    payload.set_audience("identityd/admin/v1");
    v1::ChallengeReply result; result.set_nonce(nonce); result.mutable_signing_request()->set_purpose(v1::ISSUER_ADMINISTRATION);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    impl_->pending.emplace(nonce, Impl::Pending{operation, {}, result.signing_request(), impl_->owner_public, Clock::now() + impl_->lifetime, {}, {}});
    return result;
}
v1::AdminResult Issuer::execute(const v1::IssuerExecute& request) {
    validate_message(request);
    require(request.nonce().size() == 32 && request.has_proof(), "Issuer nonce and proof are required");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::ISSUER_ADMINISTRATION);
    // Execute only the server-stored operation authenticated by this consumed challenge.
    v1::AdminResult result;
    if (pending.operation.has_policy_read()) *result.mutable_policy() = impl_->policy();
    else if (pending.operation.has_supervisor_bind()) *result.mutable_supervision() = impl_->bind_supervisor(pending.operation.supervisor_bind());
    else if (pending.operation.has_supervisor_read()) *result.mutable_supervision() = impl_->read_supervisor(pending.operation.supervisor_read());
    else if (pending.operation.has_barrier_read()) *result.mutable_barrier() = impl_->barrier(pending.operation.barrier_read());
    else if (pending.operation.has_registry_read()) *result.mutable_registry() = impl_->registry(pending.operation.registry_read());
    else if (pending.operation.has_participant_register()) *result.mutable_participant() = impl_->register_participant(pending.operation.participant_register());
    else if (pending.operation.has_policy_set()) *result.mutable_policy() = impl_->set_policy(pending.operation.policy_set());
    else if (pending.operation.has_profile_read()) *result.mutable_profile() = impl_->profile(pending.operation.profile_read());
    else if (pending.operation.has_profile_put()) *result.mutable_profile() = impl_->put_profile(pending.operation.profile_put());
    else if (pending.operation.has_enrollment_read())
        *result.mutable_enrollment() = impl_->enrollment(pending.operation.enrollment_read().enrollment_id());
    else if (pending.operation.has_enrollment_decision()) *result.mutable_enrollment() = impl_->decide(pending.operation.enrollment_decision());
    else if (pending.operation.has_identity_read()) *result.mutable_identity_status() = impl_->identity_status(pending.operation.identity_read());
    else *result.mutable_identity_status() = impl_->revoke(pending.operation.identity_revoke());
    return result;
}
v1::ChallengeReply Issuer::enrollment_begin(const v1::EnrollmentIntent& intent) {
    validate_intent(intent);
    if (impl_->supervisor_key(intent.public_key())) throw WorldError(wire::FORBIDDEN, "Supervisor keys cannot enroll as actors");
    if (impl_->participant_key(intent.public_key())) throw WorldError(wire::FORBIDDEN, "Authority participant keys cannot enroll as actors");
    require(intent.public_key() != impl_->owner_public && intent.public_key() != impl_->identity.public_key(),
            "Administrative keys cannot serve as actor credentials");
    const auto nonce = impl_->nonce();
    v1::EnrollmentChallenge payload; payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_incarnation(impl_->incarnation); payload.set_nonce(nonce); *payload.mutable_intent() = intent;
    payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count())); payload.set_audience("identityd/enroll/v1");
    v1::ChallengeReply result; result.set_nonce(nonce); result.mutable_signing_request()->set_purpose(v1::ENROLLMENT_PROOF);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    impl_->pending.emplace(nonce, Impl::Pending{{}, intent, result.signing_request(), intent.public_key(), Clock::now() + impl_->lifetime, {}, {}});
    return result;
}
v1::EnrollmentRecord Issuer::enrollment_complete(const v1::EnrollmentComplete& request) {
    validate_message(request);
    require(request.nonce().size() == 32 && request.has_proof(), "Enrollment nonce and proof are required");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::ENROLLMENT_PROOF);
    return impl_->submit(pending.enrollment);
}
v1::IdentityStatus Issuer::credential_check(const v1::SignedCredential& credential) {
    return impl_->check_credential(credential);
}
v1::ActorStatusBatch Issuer::actor_status(const v1::ActorStatusRead& request) {
    validate_message(request);
    require(request.actor_ids_size() > 0 && request.actor_ids_size() <= 128, "Actor status requires one to 128 identities");
    std::set<std::string> distinct;
    for (const auto& id : request.actor_ids())
        require(id.size() == 32 && distinct.insert(id).second, "Actor status identities must be distinct and contain 32 bytes");
    Transaction transaction(impl_->db.get());
    v1::ActorStatusBatch result; result.set_issuer_id(impl_->identity.issuer_id());
    result.set_security_revision(impl_->security_revision());
    for (const auto& id : request.actor_ids()) {
        auto* status = result.add_actors(); status->set_actor_id(id);
        Query actor(impl_->db.get(), "SELECT kind FROM actor WHERE id=?"); actor.blob(1, id);
        if (actor.step() != SQLITE_ROW) continue;
        storage(actor.integer(0) == v1::HUMAN || actor.integer(0) == v1::AGENT, "Invalid stored actor kind");
        status->set_known(true); status->set_revoked(impl_->revoked(v1::ACTOR_TARGET, id));
        status->set_active(!status->revoked());
    }
    transaction.commit(); return result;
}
v1::ChallengeReply Issuer::authentication_begin(const v1::AuthenticationBegin& request) {
    validate_message(request);
    require(request.has_credential() && !request.audience().empty() && request.audience().size() <= 128 &&
            request.audience().find('\0') == std::string::npos && request.channel_binding().size() == 32,
            "Authentication requires a credential, intended audience, and 32-byte channel binding");
    const auto identity = impl_->check_credential(request.credential());
    if (!identity.active()) throw WorldError(wire::UNAUTHORIZED, "Actor or credential is revoked");
    v1::ActorCredential claims; storage(claims.ParseFromString(request.credential().content().payload()), "Invalid checked credential");
    const auto nonce = impl_->nonce();
    v1::AuthenticationChallenge payload; payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_incarnation(impl_->incarnation); payload.set_nonce(nonce); *payload.mutable_request() = request;
    payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count()));
    v1::ChallengeReply result; result.set_nonce(nonce); result.mutable_signing_request()->set_purpose(v1::ACTOR_AUTHENTICATION);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    impl_->pending.emplace(nonce, Impl::Pending{{}, {}, result.signing_request(), claims.public_key(), Clock::now() + impl_->lifetime, request, identity});
    return result;
}
v1::AuthenticationResult Issuer::authentication_complete(const v1::AuthenticationComplete& request) {
    validate_message(request);
    require(request.nonce().size() == 32 && request.has_proof(), "Authentication nonce and proof are required");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::ACTOR_AUTHENTICATION);
    const auto current = impl_->check_credential(pending.authentication.credential());
    if (!current.active()) throw WorldError(wire::UNAUTHORIZED, "Actor or credential was revoked before authentication");
    v1::AuthenticationResult result; *result.mutable_identity() = current;
    result.set_audience(pending.authentication.audience()); result.set_channel_binding(pending.authentication.channel_binding());
    return result;
}
v1::AuthorityActivationState Issuer::authority_state(IssuerConnection& connection) {
    Transaction transaction(impl_->db.get());
    const auto& context = impl_->service_context(connection);
    auto result = impl_->activation(context.registration().participant().id());
    transaction.commit(); return result;
}
v1::ChallengeReply Issuer::authority_launch_begin(const v1::AuthorityLaunchBegin& request, IssuerConnection& connection) {
    if (connection.binding.size() != 32) throw WorldError(wire::FORBIDDEN, "Launch proof requires a TLS connection identity");
    Transaction transaction(impl_->db.get()); const auto binding = impl_->launch_context(request); transaction.commit();
    const auto nonce = impl_->nonce();
    v1::AuthorityLaunchChallenge payload; payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_issuer_incarnation(impl_->incarnation); payload.set_nonce(nonce); payload.set_connection_binding(connection.binding);
    *payload.mutable_request() = request; payload.set_supervisor_public_key(binding.public_key());
    payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count())); payload.set_audience("identityd/authority-launch/v1");
    v1::ChallengeReply result; result.set_nonce(nonce); result.mutable_signing_request()->set_purpose(v1::AUTHORITY_LAUNCH_AUTHORIZATION);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    Impl::Pending pending; pending.content = result.signing_request(); pending.expected_key = binding.public_key();
    pending.expires = Clock::now()+impl_->lifetime; pending.connection_binding = connection.binding; pending.launch = request;
    impl_->pending.emplace(nonce, std::move(pending)); return result;
}
v1::AuthorityLaunchAuthorization Issuer::authority_launch_complete(const v1::AuthorityLaunchComplete& request, IssuerConnection& connection) {
    validate_message(request); require(request.nonce().size() == 32 && request.has_proof(), "Launch nonce and proof are required");
    if (connection.binding.size() != 32) throw WorldError(wire::FORBIDDEN, "Launch proof requires a TLS connection identity");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::AUTHORITY_LAUNCH_AUTHORIZATION);
    if (pending.connection_binding != connection.binding) throw WorldError(wire::UNAUTHORIZED, "Launch proof belongs to another connection");
    Transaction transaction(impl_->db.get()); const auto result = impl_->authorize_launch(pending.launch, pending.expected_key);
    transaction.commit(); return result;
}
v1::ChallengeReply Issuer::authority_fence_begin(const v1::AuthorityFenceBegin& request, IssuerConnection& connection) {
    if (connection.binding.size() != 32) throw WorldError(wire::FORBIDDEN, "Fence proof requires a TLS connection identity");
    Transaction transaction(impl_->db.get()); const auto binding = impl_->fence_context(request); transaction.commit();
    const auto nonce = impl_->nonce();
    v1::AuthorityFenceChallenge payload; payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_issuer_incarnation(impl_->incarnation); payload.set_nonce(nonce); payload.set_connection_binding(connection.binding);
    *payload.mutable_request() = request; payload.set_supervisor_public_key(binding.public_key());
    payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count())); payload.set_audience("identityd/authority-fence/v1");
    v1::ChallengeReply result; result.set_nonce(nonce); result.mutable_signing_request()->set_purpose(v1::AUTHORITY_FENCING);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    Impl::Pending pending; pending.content = result.signing_request(); pending.expected_key = binding.public_key();
    pending.expires = Clock::now()+impl_->lifetime; pending.connection_binding = connection.binding; pending.fence = request;
    impl_->pending.emplace(nonce, std::move(pending)); return result;
}
v1::AuthorityFenceState Issuer::authority_fence_complete(const v1::AuthorityFenceComplete& request, IssuerConnection& connection) {
    validate_message(request); require(request.nonce().size() == 32 && request.has_proof(), "Fence nonce and proof are required");
    if (connection.binding.size() != 32) throw WorldError(wire::FORBIDDEN, "Fence proof requires a TLS connection identity");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::AUTHORITY_FENCING);
    if (pending.connection_binding != connection.binding) throw WorldError(wire::UNAUTHORIZED, "Fence proof belongs to another connection");
    Transaction transaction(impl_->db.get()); const auto result = impl_->fence(pending.fence, pending.expected_key);
    transaction.commit(); return result;
}
v1::ChallengeReply Issuer::authority_retire_begin(const v1::AuthorityRetireBegin& request, IssuerConnection& connection) {
    if (connection.binding.size() != 32) throw WorldError(wire::FORBIDDEN, "Retirement proof requires a TLS connection identity");
    Transaction transaction(impl_->db.get()); const auto binding = impl_->retirement_context(request); transaction.commit();
    const auto nonce = impl_->nonce();
    v1::AuthorityRetireChallenge payload; payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_issuer_incarnation(impl_->incarnation); payload.set_nonce(nonce); payload.set_connection_binding(connection.binding);
    *payload.mutable_request() = request; payload.set_supervisor_public_key(binding.public_key());
    payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count())); payload.set_audience("identityd/authority-retirement/v1");
    v1::ChallengeReply result; result.set_nonce(nonce); result.mutable_signing_request()->set_purpose(v1::AUTHORITY_RETIREMENT);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    Impl::Pending pending; pending.content = result.signing_request(); pending.expected_key = binding.public_key();
    pending.expires = Clock::now()+impl_->lifetime; pending.connection_binding = connection.binding; pending.retirement = request;
    impl_->pending.emplace(nonce, std::move(pending)); return result;
}
v1::AuthorityRetirementState Issuer::authority_retire_complete(const v1::AuthorityRetireComplete& request, IssuerConnection& connection) {
    validate_message(request); require(request.nonce().size() == 32 && request.has_proof(), "Retirement nonce and proof are required");
    if (connection.binding.size() != 32) throw WorldError(wire::FORBIDDEN, "Retirement proof requires a TLS connection identity");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::AUTHORITY_RETIREMENT);
    if (pending.connection_binding != connection.binding) throw WorldError(wire::UNAUTHORIZED, "Retirement proof belongs to another connection");
    Transaction transaction(impl_->db.get()); const auto result = impl_->retire(pending.retirement, pending.expected_key);
    transaction.commit(); return result;
}
v1::AuthorityActivationState Issuer::authority_activate(const v1::AuthorityActivate& request, IssuerConnection& connection) {
    validate_message(request);
    require(request.has_synchronized_revision() && request.synchronized_revision() <= INT64_MAX, "Synchronization revision is required");
    Transaction transaction(impl_->db.get()); const auto& context = impl_->service_context(connection);
    const auto& participant = context.registration().participant().id();
    std::optional<v1::AuthorityLaunchAuthorization> authorization;
    if (impl_->supervised(participant)) {
        authorization = impl_->launch_authorization(participant, context.service_incarnation());
        const auto binding = impl_->supervision_record(participant);
        if (!authorization || authorization->request().service().SerializeAsString() != context.SerializeAsString() ||
                authorization->request().supervision_revision() != binding.revision() || authorization->supervisor_public_key() != binding.public_key())
            throw WorldError(wire::FORBIDDEN, "Matching supervisor launch authorization is required");
        if (authorization->generation()) throw WorldError(wire::ALREADY_EXISTS, "Launch authorization was already consumed");
    }
    Query active(impl_->db.get(), "SELECT 1 FROM authority_activation WHERE participant_id=? AND active=1");
    active.blob(1, participant);
    if (active.step() == SQLITE_ROW) throw WorldError(wire::BUSY, "Service already has an active generation; cleanup is required");
    const auto head = impl_->security_revision();
    if (request.synchronized_revision() != head) throw WorldError(wire::REVISION_CONFLICT, "Service synchronization is stale");
    Query reused(impl_->db.get(), "SELECT 1 FROM authority_activation WHERE participant_id=? AND service_incarnation=?");
    reused.blob(1, participant); reused.blob(2, context.service_incarnation());
    if (reused.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "A service process incarnation cannot be reused");
    Query prior(impl_->db.get(), "SELECT COALESCE(MAX(generation),0) FROM authority_activation WHERE participant_id=?");
    prior.blob(1, participant);
    storage(prior.step() == SQLITE_ROW && prior.integer(0) >= 0 && prior.integer(0) < INT64_MAX, "Service generation limit reached");
    if (authorization && authorization->request().previous_generation() != static_cast<std::uint64_t>(prior.integer(0)))
        throw WorldError(wire::REVISION_CONFLICT, "Launch authorization was superseded by another generation");
    const auto generation = prior.integer(0)+1;
    Query insert(impl_->db.get(), "INSERT INTO authority_activation VALUES(?,?,?,?,?,?,?,?,1)");
    insert.blob(1, participant); insert.integer(2, generation); insert.blob(3, context.service_incarnation());
    insert.blob(4, context.issuer_incarnation()); insert.blob(5, context.connection_binding());
    insert.blob(6, context.registration().SerializeAsString()); insert.integer(7, head); insert.integer(8, head); insert.step();
    if (authorization) {
        Query consume(impl_->db.get(), "UPDATE authority_launch_authorization SET generation=? WHERE authorization_id=? AND generation=0");
        consume.integer(1, generation); consume.blob(2, authorization->authorization_id()); consume.step();
        storage(sqlite3_changes(impl_->db.get()) == 1, "Launch authorization was not consumed");
    }
    auto result = impl_->activation(participant, generation); transaction.commit(); return result;
}
v1::AuthorityActivationState Issuer::authority_applied(const v1::AuthorityApplied& request, IssuerConnection& connection) {
    validate_message(request);
    require(request.generation() > 0 && request.generation() <= INT64_MAX && request.has_from_revision() && request.has_through_revision() &&
            request.from_revision() < INT64_MAX && request.through_revision() == request.from_revision()+1,
            "Application requires one contiguous revision and a valid generation");
    Transaction transaction(impl_->db.get()); const auto& context = impl_->service_context(connection);
    const auto state = impl_->bound_activation(context, request.generation());
    if (!state.active() || impl_->fenced(context.registration().participant().id(), request.generation()))
        throw WorldError(wire::UNAUTHORIZED, "Service generation is inactive or fenced");
    if (request.through_revision() > state.head_revision()) throw WorldError(wire::REVISION_CONFLICT, "Cannot apply a future revision");
    Query original(impl_->db.get(), "SELECT initial_revision FROM authority_activation WHERE participant_id=? AND generation=?");
    original.blob(1, context.registration().participant().id()); original.integer(2, request.generation());
    storage(original.step() == SQLITE_ROW, "Service generation disappeared");
    if (state.applied_revision() == request.through_revision() && request.through_revision() > static_cast<std::uint64_t>(original.integer(0))) {
        transaction.commit(); return state;
    }
    if (state.applied_revision() != request.from_revision()) throw WorldError(wire::REVISION_CONFLICT, "Application revision has a gap or rollback");
    Query event(impl_->db.get(), "SELECT 1 FROM revocation WHERE revision=?"); event.integer(1, request.through_revision());
    storage(event.step() == SQLITE_ROW, "Required revocation event is missing");
    Query update(impl_->db.get(), "UPDATE authority_activation SET applied_revision=? WHERE participant_id=? AND generation=? AND applied_revision=? AND active=1");
    update.integer(1, request.through_revision()); update.blob(2, context.registration().participant().id());
    update.integer(3, request.generation()); update.integer(4, request.from_revision()); update.step();
    storage(sqlite3_changes(impl_->db.get()) == 1, "Service application lost its revision");
    auto result = impl_->activation(context.registration().participant().id(), request.generation());
    transaction.commit(); return result;
}
v1::AuthorityActivationState Issuer::authority_release(const v1::AuthorityRelease& request, IssuerConnection& connection) {
    validate_message(request);
    require(request.generation() > 0 && request.generation() <= INT64_MAX && request.has_applied_revision() && request.applied_revision() <= INT64_MAX,
            "Release requires generation and applied revision");
    Transaction transaction(impl_->db.get()); const auto& context = impl_->service_context(connection);
    auto state = impl_->bound_activation(context, request.generation());
    if (impl_->fenced(context.registration().participant().id(), request.generation()))
        throw WorldError(wire::UNAUTHORIZED, "Service generation is fenced");
    if (state.applied_revision() != request.applied_revision()) throw WorldError(wire::REVISION_CONFLICT, "Release applied revision changed");
    if (!state.active()) { transaction.commit(); return state; }
    if (state.applied_revision() != state.head_revision()) throw WorldError(wire::BUSY, "Release has unapplied cancellation effects");
    Query pending(impl_->db.get(), "SELECT 1 FROM authority_obligation WHERE participant_id=? AND generation=? AND revision>? LIMIT 1");
    pending.blob(1, context.registration().participant().id()); pending.integer(2, request.generation()); pending.integer(3, state.applied_revision());
    storage(pending.step() == SQLITE_DONE, "Release has unresolved obligations");
    Query update(impl_->db.get(), "UPDATE authority_activation SET active=0 WHERE participant_id=? AND generation=? AND active=1");
    update.blob(1, context.registration().participant().id()); update.integer(2, request.generation()); update.step();
    storage(sqlite3_changes(impl_->db.get()) == 1, "Release lost its service generation");
    state.set_active(false); transaction.commit(); return state;
}
v1::ChallengeReply Issuer::authority_authentication_begin(const v1::AuthorityAuthenticationBegin& request,
                                                        IssuerConnection& connection) {
    validate_message(request);
    require(request.participant_id().size() == 32 && request.registered_revision() > 0 &&
            request.registered_revision() <= INT64_MAX && request.service_incarnation().size() == 32,
            "Service proof requires participant identity, registration revision, and process incarnation");
    if (connection.binding.size() != 32)
        throw WorldError(wire::FORBIDDEN, "Service proof requires a server connection identity");
    if (connection.authority) throw WorldError(wire::BUSY, "This connection already has a service identity");
    Transaction transaction(impl_->db.get());
    Query registered(impl_->db.get(), "SELECT registered_revision FROM authority_participant WHERE id=?");
    registered.blob(1, request.participant_id());
    if (registered.step() != SQLITE_ROW || registered.integer(0) <= 0 ||
            static_cast<std::uint64_t>(registered.integer(0)) != request.registered_revision())
        throw WorldError(wire::UNAUTHORIZED, "Service registration is unavailable");
    const auto record = impl_->participant_record(request.participant_id(), impl_->registry_revision());
    transaction.commit();
    const auto nonce = impl_->nonce();
    v1::AuthorityAuthenticationChallenge payload;
    payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_issuer_incarnation(impl_->incarnation); payload.set_nonce(nonce);
    *payload.mutable_request() = request; payload.set_connection_binding(connection.binding);
    payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count()));
    payload.set_audience("identityd/authority-participation/v1");
    v1::ChallengeReply result; result.set_nonce(nonce);
    result.mutable_signing_request()->set_purpose(v1::AUTHORITY_PARTICIPATION);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    impl_->pending.emplace(nonce, Impl::Pending{{}, {}, result.signing_request(), record.participant().public_key(),
        Clock::now() + impl_->lifetime, {}, {}, request, connection.binding});
    return result;
}
v1::AuthorityAuthenticationResult Issuer::authority_authentication_complete(const v1::AuthorityAuthenticationComplete& request,
                                                                          IssuerConnection& connection) {
    validate_message(request);
    require(request.nonce().size() == 32 && request.has_proof(), "Service nonce and proof are required");
    if (connection.binding.size() != 32)
        throw WorldError(wire::FORBIDDEN, "Service proof requires a server connection identity");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::AUTHORITY_PARTICIPATION);
    if (pending.connection_binding != connection.binding)
        throw WorldError(wire::UNAUTHORIZED, "Service proof belongs to another connection");
    if (connection.authority) throw WorldError(wire::BUSY, "This connection already has a service identity");
    Transaction transaction(impl_->db.get());
    const auto record = impl_->participant_record(pending.authority.participant_id(), impl_->registry_revision());
    storage(record.registered_revision() == pending.authority.registered_revision() &&
            record.participant().public_key() == pending.expected_key, "Service registration changed during proof");
    v1::AuthorityAuthenticationResult result;
    result.set_issuer_id(impl_->identity.issuer_id()); result.set_issuer_incarnation(impl_->incarnation);
    *result.mutable_registration() = record;
    result.set_service_incarnation(pending.authority.service_incarnation());
    result.set_connection_binding(connection.binding); result.set_security_revision(impl_->security_revision());
    transaction.commit();
    // This connection-local proof is not execution activation or cancellation completion.
    connection.authority = result;
    return result;
}
} // namespace spatial::identity
