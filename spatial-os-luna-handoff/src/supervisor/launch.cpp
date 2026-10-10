#include "supervisor/launch.hpp"
#include "supervisor/native_scope.hpp"
#include "supervisor/process_scope.hpp"
#include "core/protocol.hpp"
#include "identity/participant_runtime.hpp"
#include "identity/participant_stream.hpp"
#include <deque>
#include <utility>
#include "identity/participants.hpp"
#include "identity/issuer_client.hpp"
#include "identity/keys.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <cerrno>
#include <fcntl.h>
#include <iostream>
#include <sqlite3.h>
#include <stdexcept>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace spatial::supervisor {
namespace {
struct File {
    int fd = -1;
    ~File() { if (fd >= 0) close(fd); }
};
void require(bool okay, const char* message) {
    if (!okay) throw std::runtime_error(message);
}
void sql(sqlite3* db, const char* text) {
    require(sqlite3_exec(db, text, nullptr, nullptr, nullptr) == SQLITE_OK, "Launch journal transaction failed");
}
struct Query {
    sqlite3_stmt* statement = nullptr;
    Query(sqlite3* db, const char* text) {
        require(sqlite3_prepare_v2(db, text, -1, &statement, nullptr) == SQLITE_OK, "Launch journal query failed");
    }
    ~Query() { sqlite3_finalize(statement); }
    int step() {
        const int code = sqlite3_step(statement);
        require(code == SQLITE_ROW || code == SQLITE_DONE, "Launch journal operation failed");
        return code;
    }
    std::string bytes(int column) const {
        const auto* value = static_cast<const char*>(sqlite3_column_blob(statement, column));
        const int size = sqlite3_column_bytes(statement, column);
        return size ? std::string(value, static_cast<std::size_t>(size)) : std::string();
    }
};
bool private_file(int directory, const char* name, bool required) {
    struct stat info{};
    if (fstatat(directory, name, &info, AT_SYMLINK_NOFOLLOW) < 0) {
        require(!required && errno == ENOENT, "Cannot inspect launch journal file");
        return false;
    }
    require(S_ISREG(info.st_mode) && info.st_uid == geteuid() && !(info.st_mode & 0077) && info.st_nlink == 1,
            "Launch journal files must be private owned regular files");
    return true;
}
v1::NativeScopeConfig configuration(int fd) {
    constexpr int seals = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
    struct stat info{};
    const int actual = fcntl(fd, F_GET_SEALS);
    require(actual >= 0 && (actual & seals) == seals && fstat(fd, &info) == 0 && info.st_size > 0 && info.st_size <= 65536,
            "Expected sealed native configuration");
    std::string bytes(static_cast<std::size_t>(info.st_size), '\0');
    require(pread(fd, bytes.data(), bytes.size(), 0) == static_cast<ssize_t>(bytes.size()), "Cannot read native configuration");
    v1::NativeScopeConfig value;
    require(value.ParseFromString(bytes), "Malformed native configuration");
    validate_message(value);
    require(value.protocol_version() == 1 && value.has_display_number() && value.display_number() <= 65535 &&
        value.supervisor_cookie().size() == 32 && value.server_id().size() == 32 && value.has_server_pid() &&
        value.server_pid() > 0 && value.has_server_uid() && value.launch_id().size() == 32 &&
        (value.role() == security::v1::BROKER || value.role() == security::v1::COMPOSITOR), "Invalid native configuration");
    return value;
}
void validate_record(const v1::AuthorityLaunchRecord& value) {
    validate_message(value);
    const auto& binding = value.binding();
    const auto& process = value.process();
    const auto& native = value.native();
    require(value.schema_version() == (binding.supervised() ? 2u : 1u) && value.revision() > 0 && binding.issuer_id().size() == 32 &&
        binding.participant_id().size() == 32 && binding.registered_revision() > 0 && binding.service_incarnation().size() == 32 &&
        process.launch_id().size() == 32 && process.boot_id().size() == 36 && process.device() && process.inode() &&
        !process.scope_name().empty() && native.launch_id() == process.launch_id() && native.server_id().size() == 32 &&
        (native.role() == security::v1::BROKER || native.role() == security::v1::COMPOSITOR) &&
        value.stage() >= v1::PREPARING && value.stage() <= v1::OWNED_SCOPES_STOPPED,
        "Invalid durable launch record");
    if (value.has_authorization()) {
        const auto& approval = value.authorization();
        const auto& request = approval.request();
        const auto& service = request.service();
        identity::validate_launch_begin(request, binding.issuer_id());
        require(binding.supervised() && approval.authorization_id().size() == 32 &&
            approval.supervisor_public_key().size() == 32 && approval.generation() == 0 &&
            service.registration().participant().id() == binding.participant_id() &&
            service.registration().registered_revision() == binding.registered_revision() &&
            service.service_incarnation() == binding.service_incarnation() &&
            request.launch_id() == process.launch_id(), "Invalid durable participant approval");
    }
    if (value.has_fence()) {
        const auto& fence = value.fence();
        const auto& approval = value.authorization();
        const auto& request = approval.request();
        require(value.has_authorization() && binding.supervised() && value.stage() >= v1::START_ATTEMPTED &&
            request.previous_generation() < INT64_MAX && fence.issuer_id() == binding.issuer_id() &&
            fence.registration().SerializeAsString() == request.service().registration().SerializeAsString() &&
            fence.generation() == request.previous_generation()+1 &&
            fence.service_incarnation() == binding.service_incarnation() &&
            fence.authorization_id() == approval.authorization_id() && fence.launch_id() == process.launch_id() &&
            fence.supervision_revision() == request.supervision_revision() &&
            fence.supervisor_public_key() == approval.supervisor_public_key() &&
            fence.fence_id().size() == 32 && fence.security_revision() <= INT64_MAX,
            "Invalid durable authority fence");
    }
    require(!binding.supervised() || value.stage() != v1::START_ATTEMPTED || value.has_authorization(),
        "Supervised execution requires durable approval");
    require(native.credential_id().size() == 32 ||
        (native.credential_id().empty() && (value.stage() == v1::PREPARING || value.stage() == v1::CLEANUP_PENDING)),
        "Missing durable native identity");
    require(!value.has_process_stopped() || (value.stage() >= v1::CLEANUP_PENDING &&
        value.process_stopped().identity().SerializeAsString() == process.SerializeAsString()), "Invalid process cleanup record");
    require(!value.has_native_stopped() || (value.stage() >= v1::CLEANUP_PENDING && native.credential_id().size() == 32 &&
        value.native_stopped().identity().SerializeAsString() == native.SerializeAsString()), "Invalid native cleanup record");
    require(value.stage() != v1::OWNED_SCOPES_STOPPED || (value.has_process_stopped() && value.has_native_stopped()),
        "Incomplete owned scope cleanup");
}
identity::v1::AuthorityFenceBegin owned_generation(const v1::AuthorityLaunchRecord& record) {
    const auto& binding = record.binding();
    const auto& approval = record.authorization();
    const auto& authorized = approval.request();
    require(binding.supervised() && record.has_authorization() && authorized.previous_generation() < INT64_MAX,
        "Owned generation requires a bounded supervised authorization");
    identity::v1::AuthorityFenceBegin result;
    result.set_participant_id(binding.participant_id());
    result.set_registered_revision(binding.registered_revision());
    result.set_generation(authorized.previous_generation()+1);
    result.set_service_incarnation(binding.service_incarnation());
    result.set_launch_id(record.process().launch_id());
    result.set_supervision_revision(authorized.supervision_revision());
    result.set_authorization_id(approval.authorization_id());
    return result;
}
}

struct AuthorityLaunch::Impl {
    File directory, lock;
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    v1::AuthorityLaunchRecord record;
    std::unique_ptr<ProcessScope> process;
    std::unique_ptr<NativeScope> native;
    std::unique_ptr<identity::ParticipantRuntime> participant;
    std::optional<identity::v1::AuthorityAuthenticationResult> authenticated;
    std::optional<v1::AuthorityLaunchAuthorize> authorization_config;
    bool attached = false, stopping = false, started = false;
    bool relay_prepared = false, relay_terminal = false, relay_finished = false, relay_failed = false;
    bool relay_released = false;
    std::uint32_t relay_target = 0;
    File relay_child;
    std::unique_ptr<identity::ParticipantStream> relay;
    std::deque<identity::v1::ParticipantUpdate> bootstrap;
    std::optional<std::uint64_t> relay_request;
    void prepared_participant() const {
        require(record.binding().supervised() && record.stage() == v1::PREPARED && process && native && !stopping,
            "No live prepared supervised ownership");
    }
    explicit Impl(const std::filesystem::path& path) {
        require(path.is_absolute(), "Launch directory must be absolute");
        directory.fd = open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        struct stat info{};
        require(directory.fd >= 0 && fstat(directory.fd, &info) == 0 && info.st_uid == geteuid() && !(info.st_mode & 0077),
                "Launch directory must exist and be private and owned");
        lock.fd = openat(directory.fd, "launch.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600);
        private_file(directory.fd, "launch.lock", true);
        require(lock.fd >= 0 && flock(lock.fd, LOCK_EX | LOCK_NB) == 0, "Launch directory is already owned");
        const bool existing = private_file(directory.fd, "launch.db", false);
        if (!existing) {
            for (const auto& item : std::filesystem::directory_iterator(path))
                require(item.path().filename() == "launch.lock", "New launch journal requires an empty directory");
            File created;
            created.fd = openat(directory.fd, "launch.db", O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
            require(created.fd >= 0, "Cannot create launch journal");
        }
        for (const auto* name : {"launch.db-journal", "launch.db-wal", "launch.db-shm"}) private_file(directory.fd, name, false);
        sqlite3* opened = nullptr;
        const int result = sqlite3_open_v2((path/"launch.db").c_str(), &opened,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_NOMUTEX | SQLITE_OPEN_NOFOLLOW, nullptr);
        db.reset(opened);
        require(result == SQLITE_OK, "Cannot open launch journal");
        require(sqlite3_busy_timeout(db.get(), 2000) == SQLITE_OK, "Cannot set launch journal deadline");
        if (existing) {
            Query version(db.get(), "PRAGMA user_version"), application(db.get(), "PRAGMA application_id");
            require(version.step() == SQLITE_ROW && sqlite3_column_int(version.statement, 0) == 1 &&
                application.step() == SQLITE_ROW && sqlite3_column_int(application.statement, 0) == 1397771340,
                "Unknown launch schema; preserve journal for explicit migration");
        }
        sql(db.get(), "PRAGMA trusted_schema=OFF; PRAGMA synchronous=EXTRA");
        { Query mode(db.get(), "PRAGMA journal_mode=DELETE");
          require(mode.step() == SQLITE_ROW && mode.bytes(0) == "delete", "Required launch journal mode unavailable"); }
        if (!existing) {
            sql(db.get(), "BEGIN IMMEDIATE; CREATE TABLE launch(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                "record BLOB NOT NULL); PRAGMA user_version=1; PRAGMA application_id=1397771340; COMMIT");
            require(fsync(directory.fd) == 0, "Launch journal directory durability uncertain");
        }
        { Query check(db.get(), "PRAGMA quick_check");
          require(check.step() == SQLITE_ROW && check.bytes(0) == "ok", "Launch journal integrity failure"); }
        Query row(db.get(), "SELECT record FROM launch WHERE singleton=1");
        if (row.step() == SQLITE_ROW) {
            const auto bytes = row.bytes(0);
            require(bytes.size() <= 65536 && record.ParseFromString(bytes), "Malformed durable launch record");
            validate_record(record);
        }
    }
    void persist(v1::AuthorityLaunchRecord next) {
        require(record.revision() < UINT64_MAX, "Launch journal revision exhausted");
        next.set_revision(record.revision()+1);
        validate_record(next);
        const auto bytes = next.SerializeAsString();
        sql(db.get(), "BEGIN IMMEDIATE");
        try {
            Query put(db.get(), "INSERT INTO launch VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET record=excluded.record");
            require(sqlite3_bind_blob(put.statement, 1, bytes.data(), static_cast<int>(bytes.size()), SQLITE_TRANSIENT) == SQLITE_OK,
                    "Cannot bind launch record");
            require(put.step() == SQLITE_DONE, "Cannot persist launch record");
            sql(db.get(), "COMMIT");
        } catch (...) { sqlite3_exec(db.get(), "ROLLBACK", nullptr, nullptr, nullptr); throw; }
        record = std::move(next);
    }
};

AuthorityLaunch::AuthorityLaunch(const std::filesystem::path& directory) : impl_(std::make_unique<Impl>(directory)) {}
v1::AuthorityLaunchRecord AuthorityLaunch::inspect() const { return impl_->record; }
v1::AuthorityLaunchView AuthorityLaunch::prepare(const std::filesystem::path& root,
    const v1::AuthorityLaunchBinding& binding, int fd) {
    require(!impl_->record.revision() && !impl_->process, "Launch already exists; recovery cannot authorize replacement");
    validate_message(binding);
    require(binding.issuer_id().size() == 32 && binding.participant_id().size() == 32 &&
        binding.registered_revision() > 0 && binding.service_incarnation().size() == 32, "Invalid launch binding");
    auto config = configuration(fd);
    impl_->process = ProcessScope::prepare(root);
    v1::AuthorityLaunchRecord initial;
    initial.set_schema_version(binding.supervised() ? 2 : 1); *initial.mutable_binding() = binding;
    *initial.mutable_process() = impl_->process->state().identity();
    auto* native = initial.mutable_native();
    native->set_launch_id(initial.process().launch_id()); native->set_server_id(config.server_id()); native->set_role(config.role());
    initial.set_stage(v1::PREPARING);
    impl_->persist(initial); // Durable intent precedes the native external effect.
    config.set_launch_id(initial.process().launch_id());
    const auto bytes = config.SerializeAsString();
    File sealed;
    sealed.fd = memfd_create("authority-native-launch", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    require(sealed.fd >= 0 && write(sealed.fd, bytes.data(), bytes.size()) == static_cast<ssize_t>(bytes.size()) &&
        fcntl(sealed.fd, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) == 0,
        "Cannot prepare sealed native launch");
    impl_->native = std::make_unique<NativeScope>(sealed.fd);
    auto ready = impl_->native->ready();
    auto next = impl_->record; *next.mutable_native() = ready.identity(); next.set_stage(v1::PREPARED);
    impl_->persist(next);
    v1::AuthorityLaunchView result; *result.mutable_record() = impl_->record; *result.mutable_ready() = ready;
    return result;
}
v1::AuthorityLaunchRecord AuthorityLaunch::start(const v1::AuthorityProcessLaunch& request,
    const std::array<int, 3>& streams, const std::vector<int>& inherited) {
    require(impl_->record.stage() == v1::PREPARED && impl_->process && impl_->native, "No live prepared launch ownership");
    require(!impl_->stopping, "Launch cleanup has started");
    if (impl_->record.binding().supervised())
        require(impl_->record.has_authorization() && impl_->participant && !impl_->participant->failed(),
            "Supervised launch requires a live approved participant connection");
    auto actual = request; auto descriptors = inherited;
    if (impl_->relay_prepared) {
        require(impl_->relay && !impl_->relay_failed && impl_->relay_child.fd >= 3 && inherited.size() < 16 &&
            static_cast<std::size_t>(request.inherited_fds_size()) == inherited.size(), "Invalid private channel launch descriptors");
        for (const auto target : request.inherited_fds())
            require(target != impl_->relay_target, "Inherited descriptor collides with participant channel");
        actual.add_inherited_fds(impl_->relay_target); descriptors.push_back(impl_->relay_child.fd);
    } else impl_->bootstrap.clear();
    auto next = impl_->record; next.set_stage(v1::START_ATTEMPTED);
    impl_->persist(next); // A crash after this point must never replay execution.
    try { impl_->process->start(actual, streams, descriptors); }
    catch (...) {
        if (impl_->relay_prepared) {
            impl_->relay_failed = true; impl_->relay.reset(); impl_->participant.reset();
            close(std::exchange(impl_->relay_child.fd, -1));
        }
        throw;
    }
    if (impl_->relay_child.fd >= 0) close(std::exchange(impl_->relay_child.fd, -1));
    impl_->started = true;
    return impl_->record;
}
v1::AuthorityLaunchRecord AuthorityLaunch::stop() {
    if (impl_->record.stage() == v1::OWNED_SCOPES_STOPPED) return impl_->record;
    require(impl_->record.revision() && impl_->process && impl_->native, "Live ownership is unavailable; cleanup remains unresolved");
    impl_->stopping = true;
    impl_->relay.reset(); impl_->bootstrap.clear();
    if (impl_->relay_child.fd >= 0) close(std::exchange(impl_->relay_child.fd, -1));
    impl_->participant.reset(); // Closing a connection does not retire its issuer generation.
    if (impl_->record.stage() != v1::CLEANUP_PENDING) {
        auto next = impl_->record;
        // Native issuance can succeed before its prepared-record commit fails.
        // Recover identity only from this retained live ownership object.
        if (next.native().credential_id().empty()) *next.mutable_native() = impl_->native->ready().identity();
        next.set_stage(v1::CLEANUP_PENDING); impl_->persist(next);
    }
    if (!impl_->record.has_process_stopped()) {
        auto next = impl_->record; *next.mutable_process_stopped() = impl_->process->stop(); impl_->persist(next);
    }
    if (!impl_->record.has_native_stopped()) {
        auto next = impl_->record; *next.mutable_native_stopped() = impl_->native->stop(); impl_->persist(next);
    }
    auto next = impl_->record; next.set_stage(v1::OWNED_SCOPES_STOPPED); impl_->persist(next);
    return impl_->record;
}
void AuthorityLaunch::attach(const v1::AuthorityParticipantAttach& request) {
    impl_->prepared_participant(); validate_message(request);
    require(!impl_->attached && !impl_->record.has_authorization(), "Participant attachment cannot be repeated");
    require(std::filesystem::path(request.runtime_config()).is_absolute(), "Participant configuration must be absolute");
    identity::v1::AuthorityParticipantRuntimeConfig config;
    require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(request.runtime_config()), &config),
        "Invalid participant configuration");
    validate_message(config);
    const auto& binding = impl_->record.binding();
    require(config.protocol_version() == 1 && config.participant_id() == binding.participant_id() &&
        config.registered_revision() == binding.registered_revision() && config.has_service_incarnation() &&
        config.service_incarnation() == binding.service_incarnation(), "Participant configuration differs from owned launch");
    identity::v1::IssuerTlsClientConfig issuer;
    require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(config.issuer_client_config()), &issuer),
        "Invalid participant issuer configuration");
    validate_message(issuer);
    require(issuer.issuer_id() == binding.issuer_id(), "Participant issuer differs from owned launch");
    impl_->attached = true; // A failed attempt must not substitute another connection.
    impl_->participant = std::make_unique<identity::ParticipantRuntime>(request.runtime_config(),
        std::vector<authority::v1::Domain>(request.domains().begin(), request.domains().end()));
}
std::optional<identity::v1::ParticipantUpdate> AuthorityLaunch::poll_participant() {
    require(!impl_->relay_prepared, "Private relay owns participant consumption");
    require(impl_->participant && !impl_->stopping, "No retained participant connection");
    auto update = impl_->participant->poll();
    if (update && update->has_authenticated()) {
        const auto& service = update->authenticated(); const auto& binding = impl_->record.binding();
        if (impl_->authenticated || service.issuer_id() != binding.issuer_id() ||
            service.registration().participant().id() != binding.participant_id() ||
            service.registration().registered_revision() != binding.registered_revision() ||
            service.service_incarnation() != binding.service_incarnation()) {
            impl_->participant.reset(); throw std::runtime_error("Retained participant identity differs from owned launch");
        }
        impl_->authenticated = service;
    }
    if (update && !impl_->started && (update->has_authenticated() || update->has_revocations())) {
        if (impl_->bootstrap.size() >= 256) {
            impl_->participant.reset(); impl_->bootstrap.clear();
            throw std::runtime_error("Participant bootstrap consumer fell behind");
        }
        impl_->bootstrap.push_back(*update);
    }
    return update;
}
v1::AuthorityLaunchRecord AuthorityLaunch::authorize(const v1::AuthorityLaunchAuthorize& request) {
    impl_->prepared_participant(); validate_message(request);
    require(impl_->participant && !impl_->participant->failed() && impl_->authenticated &&
        !impl_->record.has_authorization(), "No unapproved retained participant connection");
    require(std::filesystem::path(request.issuer_client_config()).is_absolute() &&
        std::filesystem::path(request.private_key_path()).is_absolute() && request.supervision_revision() > 0 &&
        request.supervision_revision() <= INT64_MAX && request.has_previous_generation() &&
        request.previous_generation() <= INT64_MAX, "Invalid supervisor approval configuration");
    auto key = identity::KeyPair::load(request.private_key_path());
    identity::IssuerClient client(request.issuer_client_config());
    identity::v1::IssuerRequest begin; begin.set_protocol_version(1);
    auto* approval = begin.mutable_authority_launch_begin();
    *approval->mutable_service() = *impl_->authenticated;
    approval->set_launch_id(impl_->record.process().launch_id());
    approval->set_supervision_revision(request.supervision_revision());
    approval->set_previous_generation(request.previous_generation());
    auto result = client.call(std::move(begin));
    require(result.has_challenge(), "Issuer rejected supervisor approval challenge");
    identity::v1::IssuerRequest complete; complete.set_protocol_version(1);
    auto* proof = complete.mutable_authority_launch_complete();
    proof->set_nonce(result.challenge().nonce());
    *proof->mutable_proof() = key.sign(result.challenge().signing_request());
    result = client.call(std::move(complete));
    require(result.has_authority_launch_authorization() && result.authority_launch_authorization().generation() == 0,
        "Issuer did not approve the retained participant connection");
    require(!impl_->participant->failed(), "Participant failed during supervisor approval");
    auto next = impl_->record; *next.mutable_authorization() = result.authority_launch_authorization();
    impl_->persist(next); // The remote approval alone never releases child execution.
    impl_->authorization_config = request; // Private paths stay in this live supervisor only.
    return impl_->record;
}
void AuthorityLaunch::fence_after_failure() {
    // A reopened journal has no retained ownership or signing configuration.
    if (!impl_->record.binding().supervised() || !impl_->record.has_authorization() ||
        impl_->record.stage() < v1::START_ATTEMPTED || impl_->relay_released ||
        !impl_->process || !impl_->native || !impl_->authorization_config) return;
    // The issuer, not this local eligibility check, proves exact activation.
    fence();
}
workspace::v1::ControlOwner AuthorityLaunch::control_owner() const {
    require(impl_->record.binding().supervised() && impl_->record.has_authorization() &&
        impl_->process && impl_->native && impl_->participant && !impl_->participant->failed() &&
        impl_->authorization_config && !impl_->stopping && !impl_->relay_failed && !impl_->relay_released &&
        (impl_->record.stage() == v1::PREPARED ||
            (impl_->record.stage() == v1::START_ATTEMPTED && impl_->started)),
        "Control owner requires retained live authorized launch ownership");
    require(impl_->record.native().role() == security::v1::COMPOSITOR,
        "Control owner requires an owned compositor scope");
    if (impl_->started) {
        const auto state = impl_->process->state();
        require(state.populated() && !state.direct_child_exited(), "Control owner child is no longer running");
    }
    workspace::v1::ControlOwner owner;
    owner.set_issuer_id(impl_->record.binding().issuer_id());
    *owner.mutable_launch() = owned_generation(impl_->record);
    *owner.mutable_native() = impl_->record.native();
    return owner;
}
int AuthorityLaunch::duplicate_broker_pidfd(const v1::AuthorityLaunchRecord& expected) const {
    validate_message(expected);
    require(impl_->record.binding().supervised() && impl_->record.has_authorization() &&
        impl_->record.stage() == v1::START_ATTEMPTED && impl_->started &&
        impl_->record.native().role() == security::v1::BROKER &&
        impl_->process && impl_->native && impl_->participant && !impl_->participant->failed() &&
        impl_->authorization_config && !impl_->stopping && !impl_->relay_failed && !impl_->relay_released &&
        !impl_->record.has_fence(), "Broker handle requires retained live authorized launch ownership");
    require(expected.SerializeAsString() == impl_->record.SerializeAsString(),
        "Broker handle query does not match the retained launch");
    return impl_->process->duplicate_child_pidfd();
}
v1::AuthorityLaunchRecord AuthorityLaunch::fence() {
    require(impl_->record.binding().supervised() && impl_->record.has_authorization() &&
        impl_->record.stage() >= v1::START_ATTEMPTED && impl_->process && impl_->native &&
        impl_->authorization_config, "Fencing requires retained authorized launch ownership");
    if (impl_->record.has_fence()) return impl_->record;
    impl_->stopping = true;
    impl_->relay.reset(); impl_->bootstrap.clear();
    if (impl_->relay_child.fd >= 0) close(std::exchange(impl_->relay_child.fd, -1));
    impl_->participant.reset();
    const auto& approval = impl_->record.authorization();
    auto key = identity::KeyPair::load(impl_->authorization_config->private_key_path());
    require(key.public_key() == approval.supervisor_public_key(), "Fence signing key differs from owned authorization");
    identity::IssuerClient client(impl_->authorization_config->issuer_client_config());
    identity::v1::IssuerRequest begin; begin.set_protocol_version(1);
    *begin.mutable_authority_fence_begin() = owned_generation(impl_->record);
    auto result = client.call(std::move(begin));
    require(result.has_challenge(), "Issuer rejected owned authority fence challenge");
    identity::v1::IssuerRequest complete; complete.set_protocol_version(1);
    auto* proof = complete.mutable_authority_fence_complete();
    proof->set_nonce(result.challenge().nonce());
    *proof->mutable_proof() = key.sign(result.challenge().signing_request());
    result = client.call(std::move(complete));
    require(result.has_authority_fence(), "Issuer did not fence the owned authority launch");
    auto next = impl_->record; *next.mutable_fence() = result.authority_fence();
    impl_->persist(next); // Full binding validation precedes the durable local write.
    return impl_->record;
}
void AuthorityLaunch::submit_participant(identity::v1::ParticipantCommand command) {
    require(!impl_->relay_prepared, "Private relay owns participant commands");
    require(impl_->record.stage() == v1::START_ATTEMPTED && impl_->record.has_authorization() &&
        impl_->participant && !impl_->participant->failed() && !impl_->stopping && impl_->started,
        "Participant commands require a started owned supervised launch");
    const auto process = impl_->process->state();
    require(process.populated() && !process.direct_child_exited(), "Authority child is no longer running");
    impl_->participant->submit(std::move(command));
}
identity::v1::AuthorityParticipantRuntimeConfig AuthorityLaunch::prepare_channel(std::uint32_t target_fd) {
    impl_->prepared_participant();
    require(!impl_->relay_prepared && impl_->record.has_authorization() && impl_->participant &&
        !impl_->participant->failed() && impl_->authenticated && target_fd >= 3 && target_fd <= 255,
        "Private channel requires a live approved participant and valid target descriptor");
    require(impl_->bootstrap.size() >= 2 && impl_->bootstrap.front().has_authenticated() &&
        impl_->bootstrap[1].has_revocations() && impl_->bootstrap[1].revocations().initial(),
        "Private channel requires the retained authentication and initial feed");
    int pair[2]; require(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) == 0,
        "Cannot create private participant channel");
    File parent, child; parent.fd = pair[0]; child.fd = pair[1];
    auto stream = std::make_unique<identity::ParticipantStream>(std::exchange(parent.fd, -1), getpid());
    identity::v1::AuthorityParticipantRuntimeConfig config;
    const auto& binding = impl_->record.binding();
    config.set_protocol_version(1); config.set_participant_id(binding.participant_id());
    config.set_registered_revision(binding.registered_revision()); config.set_service_incarnation(binding.service_incarnation());
    config.set_supervisor_issuer_id(binding.issuer_id()); config.set_supervisor_channel_fd(target_fd);
    impl_->relay = std::move(stream); impl_->relay_child.fd = std::exchange(child.fd, -1);
    impl_->relay_target = target_fd; impl_->relay_prepared = true;
    return config;
}
void AuthorityLaunch::pump_channel() {
    require(impl_->relay_prepared && !impl_->relay_failed && !impl_->stopping, "No usable private participant relay");
    if (impl_->relay_finished) return;
    require(impl_->started && impl_->record.stage() == v1::START_ATTEMPTED,
        "Private participant relay requires successful child execution");
    try {
        require(impl_->relay && impl_->participant && impl_->process, "Private relay lost live ownership");
        const auto process = impl_->process->state();
        require(process.populated() && !process.direct_child_exited(), "Authority child exited before relay completion");
        while (!impl_->bootstrap.empty()) {
            impl_->relay->send(impl_->bootstrap.front().SerializeAsString()); impl_->bootstrap.pop_front();
        }
        for (int count = 0; count < 16 && !impl_->relay_terminal; ++count) {
            auto update = impl_->participant->poll(); if (!update) break;
            require(!update->has_authenticated(), "Private relay received repeated authentication");
            if (!update->has_revocations()) {
                require((impl_->relay_request && update->request_id() == *impl_->relay_request) ||
                    (update->has_error() && update->request_id() == 0), "Private relay received an uncorrelated result");
                impl_->relay_request.reset();
            }
            impl_->relay->send(update->SerializeAsString());
            if (update->has_released()) impl_->relay_released = true;
            if (update->has_released() || (update->has_error() && impl_->participant->failed())) impl_->relay_terminal = true;
        }
        impl_->relay->flush();
        if (impl_->relay_terminal) {
            if (!impl_->relay->pending_output()) {
                impl_->relay_finished = true; impl_->relay.reset(); impl_->participant.reset();
            }
            return;
        }
        if (auto bytes = impl_->relay->receive()) {
            require(!impl_->relay_request, "Private relay already has a consequential command");
            identity::v1::ParticipantCommand command;
            require(command.ParseFromString(*bytes), "Malformed private participant command"); validate_message(command);
            require(command.operation_case() != identity::v1::ParticipantCommand::OPERATION_NOT_SET,
                "Private participant command requires an operation");
            impl_->participant->submit_forwarded(command); impl_->relay_request = command.request_id();
        }
    } catch (...) {
        impl_->relay_failed = true; impl_->relay.reset(); impl_->participant.reset(); impl_->bootstrap.clear();
        throw;
    }
}
bool AuthorityLaunch::child_completed() const {
    require(impl_->started && impl_->process && !impl_->stopping, "No running owned child");
    if (!impl_->process->state().direct_child_exited()) return false;
    require(impl_->relay_finished && impl_->relay_released && !impl_->relay_failed,
        "Authority child exited without completed release");
    require(impl_->process->exited_successfully(), "Authority child failed after release");
    return true;
}
AuthorityLaunch::~AuthorityLaunch() {
    // Destructors still stop owned effects. Only a successful explicit stop persists receipts.
    // Preserve the record on partial preparation or any journal/cleanup failure.
    impl_->relay.reset();
    if (impl_->relay_child.fd >= 0) close(std::exchange(impl_->relay_child.fd, -1));
    impl_->participant.reset();
    impl_->process.reset();
    impl_->native.reset();
}
}
