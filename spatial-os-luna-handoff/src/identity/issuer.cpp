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
    require(operation.has_policy_set(), "Issuer operation is required");
    const auto& change = operation.policy_set();
    require(change.has_policy() && change.has_expected_revision() && change.expected_revision() > 0 &&
            change.expected_revision() <= INT64_MAX, "Policy and positive expected revision are required");
    validate_policy(change.policy());
}
} // namespace

struct Issuer::Impl {
    struct Pending { v1::IssuerOperation operation; v1::SignRequest content; Clock::time_point expires; };
    File directory, lock;
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    std::optional<KeyPair> key;
    v1::IssuerInfo identity;
    std::string owner_public, incarnation = random_id();
    std::chrono::milliseconds lifetime;
    std::map<std::string, Pending> pending;

    Impl(const std::filesystem::path& state, const std::optional<std::string>& owner, std::chrono::milliseconds ttl)
        : lifetime(ttl) {
        require(state.is_absolute(), "Issuer state directory must be absolute");
        require(ttl.count() >= 50 && ttl.count() <= 60000, "Challenge lifetime must be between 50 and 60000 milliseconds");
        require(!owner || owner->size() == 32, "Initial owner requires an Ed25519 public key");
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
        if (!owner) {
            Query version(db.get(), "PRAGMA user_version"); version.step();
            Query application(db.get(), "PRAGMA application_id"); application.step();
            storage(version.integer(0) == 1 && application.integer(0) == application_id,
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
            insert.blob(4, policy.SerializeAsString()); insert.step(); tx.commit();
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
};

Issuer::Issuer(const std::filesystem::path& state, const std::optional<std::string>& owner, std::chrono::milliseconds ttl)
    : impl_(std::make_unique<Impl>(state, owner, ttl)) {}
Issuer::~Issuer() = default;
v1::IssuerInfo Issuer::info() {
    auto result = impl_->identity; result.set_policy_revision(impl_->policy().revision()); return result;
}
v1::ChallengeReply Issuer::challenge(const v1::IssuerOperation& operation) {
    validate_operation(operation); impl_->expire();
    if (impl_->pending.size() >= 256) throw WorldError(wire::BUSY, "Issuer challenge capacity reached");
    const auto nonce = random_id();
    if (impl_->pending.contains(nonce)) throw std::runtime_error("Issuer entropy collision");
    v1::IssuerChallenge payload; payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_incarnation(impl_->incarnation); payload.set_nonce(nonce); payload.set_owner_public_key(impl_->owner_public);
    *payload.mutable_operation() = operation; payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count()));
    payload.set_audience("identityd/admin/v1");
    v1::ChallengeReply result; result.set_nonce(nonce); result.mutable_signing_request()->set_purpose(v1::ISSUER_ADMINISTRATION);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    impl_->pending.emplace(nonce, Impl::Pending{operation, result.signing_request(), Clock::now() + impl_->lifetime});
    return result;
}
v1::PolicyState Issuer::execute(const v1::IssuerExecute& request) {
    validate_message(request);
    require(request.nonce().size() == 32 && request.has_proof(), "Issuer nonce and proof are required");
    impl_->expire();
    const auto found = impl_->pending.find(request.nonce());
    if (found == impl_->pending.end()) throw WorldError(wire::UNAUTHORIZED, "Issuer challenge is unavailable");
    const auto pending = std::move(found->second); impl_->pending.erase(found);
    v1::VerifyRequest verification; *verification.mutable_content() = pending.content;
    *verification.mutable_proof() = request.proof(); verification.set_expected_public_key(impl_->owner_public);
    if (!verify(verification) || Clock::now() >= pending.expires)
        throw WorldError(wire::UNAUTHORIZED, "Issuer owner proof is invalid or expired");
    // Execute only the server-stored operation authenticated by this consumed challenge.
    if (pending.operation.has_policy_read()) return impl_->policy();
    return impl_->set_policy(pending.operation.policy_set());
}
} // namespace spatial::identity
