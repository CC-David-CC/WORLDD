#include "identity/issuer.hpp"
#include "identity/keys.hpp"
#include "world/store.hpp"
#include <openssl/rand.h>
#include <sqlite3.h>
#include <cerrno>
#include <fcntl.h>
#include <limits>
#include <map>
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
void validate_operation(const v1::IssuerOperation& operation) {
    validate_message(operation);
    if (operation.has_policy_read()) return;
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
} // namespace

struct Issuer::Impl {
    struct Pending {
        v1::IssuerOperation operation;
        v1::EnrollmentIntent enrollment;
        v1::SignRequest content;
        std::string expected_key;
        Clock::time_point expires;
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
        sqlite3_int64 schema = 2;
        if (!owner) {
            Query version(db.get(), "PRAGMA user_version"); version.step();
            Query application(db.get(), "PRAGMA application_id"); application.step();
            schema = version.integer(0);
            storage((schema == 2 || (schema == 1 && migrate)) && application.integer(0) == application_id,
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
            insert.blob(4, policy.SerializeAsString()); insert.step(); create_enrollment_schema(db.get()); tx.commit();
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
        if (schema == 1) {
            Transaction migration(db.get()); create_enrollment_schema(db.get()); migration.commit();
        }
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
    void issue(const v1::EnrollmentRecord& record, const v1::PolicyState& policy, const std::string& target) {
        // This immutable profile grants no capabilities. Unknown profiles never imply grants.
        if (policy.policy().bootstrap_profile() != "empty-v1")
            throw WorldError(wire::NOT_FOUND, "Configured bootstrap profile is unavailable");
        auto actor = target;
        if (actor.empty()) {
            actor = random_id();
            Query insert(db.get(), "INSERT INTO actor(id,kind,label) VALUES(?,?,?)");
            insert.blob(1, actor); insert.integer(2, record.intent().kind()); insert.blob(3, record.intent().label()); insert.step();
        } else {
            Query query(db.get(), "SELECT kind FROM actor WHERE id=?"); query.blob(1, actor);
            if (query.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Target actor does not exist");
            require(query.integer(0) == record.intent().kind(), "Credential actor kind cannot change the target identity");
        }
        v1::ActorCredential claims; claims.set_protocol_version(1); claims.set_issuer_id(identity.issuer_id());
        claims.set_actor_id(actor); claims.set_credential_id(random_id()); claims.set_public_key(record.intent().public_key());
        claims.set_kind(record.intent().kind()); claims.set_enrollment_id(record.enrollment_id());
        claims.set_bootstrap_profile("empty-v1"); claims.set_bootstrap_profile_revision(1);
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
        if (current.policy().mode() == v1::APPROVE_ALWAYS) issue(enrollment(id), current, {});
        const auto result = enrollment(id); transaction.commit(); return result;
    }
};

Issuer::Issuer(const std::filesystem::path& state, const std::optional<std::string>& owner, std::chrono::milliseconds ttl, bool migrate)
    : impl_(std::make_unique<Impl>(state, owner, ttl, migrate)) {}
Issuer::~Issuer() = default;
v1::IssuerInfo Issuer::info() {
    auto result = impl_->identity; result.set_policy_revision(impl_->policy().revision()); return result;
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
    impl_->pending.emplace(nonce, Impl::Pending{operation, {}, result.signing_request(), impl_->owner_public, Clock::now() + impl_->lifetime});
    return result;
}
v1::AdminResult Issuer::execute(const v1::IssuerExecute& request) {
    validate_message(request);
    require(request.nonce().size() == 32 && request.has_proof(), "Issuer nonce and proof are required");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::ISSUER_ADMINISTRATION);
    // Execute only the server-stored operation authenticated by this consumed challenge.
    v1::AdminResult result;
    if (pending.operation.has_policy_read()) *result.mutable_policy() = impl_->policy();
    else if (pending.operation.has_policy_set()) *result.mutable_policy() = impl_->set_policy(pending.operation.policy_set());
    else if (pending.operation.has_enrollment_read())
        *result.mutable_enrollment() = impl_->enrollment(pending.operation.enrollment_read().enrollment_id());
    else *result.mutable_enrollment() = impl_->decide(pending.operation.enrollment_decision());
    return result;
}
v1::ChallengeReply Issuer::enrollment_begin(const v1::EnrollmentIntent& intent) {
    validate_intent(intent);
    require(intent.public_key() != impl_->owner_public && intent.public_key() != impl_->identity.public_key(),
            "Administrative keys cannot serve as actor credentials");
    const auto nonce = impl_->nonce();
    v1::EnrollmentChallenge payload; payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_incarnation(impl_->incarnation); payload.set_nonce(nonce); *payload.mutable_intent() = intent;
    payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count())); payload.set_audience("identityd/enroll/v1");
    v1::ChallengeReply result; result.set_nonce(nonce); result.mutable_signing_request()->set_purpose(v1::ENROLLMENT_PROOF);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    impl_->pending.emplace(nonce, Impl::Pending{{}, intent, result.signing_request(), intent.public_key(), Clock::now() + impl_->lifetime});
    return result;
}
v1::EnrollmentRecord Issuer::enrollment_complete(const v1::EnrollmentComplete& request) {
    validate_message(request);
    require(request.nonce().size() == 32 && request.has_proof(), "Enrollment nonce and proof are required");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::ENROLLMENT_PROOF);
    return impl_->submit(pending.enrollment);
}
} // namespace spatial::identity
