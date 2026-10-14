#include "workspace/storage_owner.hpp"
#include "workspace/storage_devices.hpp"
#include "core/protocol.hpp"
#include "identity/tls_support.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstring>
#include <endian.h>
#include <fcntl.h>
#include <filesystem>
#include <linux/btrfs.h>
#include <linux/btrfs_tree.h>
#include <linux/magic.h>
#include <limits>
#include <string_view>
#include <optional>
#include <set>
#include <sqlite3.h>
#include <stdexcept>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <unistd.h>

namespace spatial {
namespace {
namespace pb = workspace::v1;
using Fd = identity::tls_detail::Fd;
constexpr std::uint64_t pool = (3ULL << BTRFS_QGROUP_LEVEL_SHIFT) | 1;
struct Error : std::runtime_error {
    pb::StorageOwnerError::Code code;
    Error(pb::StorageOwnerError::Code value, std::string message) : std::runtime_error(message), code(value) {}
};
void require(bool value, const char* message) { if (!value) throw Error(pb::StorageOwnerError::INVALID_ARGUMENT, message); }
void check(bool value, const char* message) {
    if (!value) throw Error(errno == EDQUOT || errno == ENOSPC ? pb::StorageOwnerError::EXHAUSTED : pb::StorageOwnerError::UNAVAILABLE, std::string(message) + ": " + std::strerror(errno));
}
bool identifier(const std::string& value) { return !value.empty() && value.size() <= 128 && value.find('\0') == std::string::npos; }
bool safe_identifier(const std::string& value) {
    return identifier(value) && std::none_of(value.begin(), value.end(), [](unsigned char c) { return c < 0x20 || c == 0x7f; });
}
void bytes(std::uint64_t value) { require(value >= 4*1024*1024 && value <= (1ULL << 50) && value % 4096 == 0, "Storage limit must be a bounded positive block multiple"); }
std::string actor_key(const authority::v1::ActorRef& principal) {
    require(principal.issuer_id().size() == 32 && principal.actor_id().size() == 32, "Expected exact storage actor identity");
    return "p" + principal.issuer_id() + principal.actor_id();
}
std::string subject(const pb::ActorStorageLimit& value) {
    if (value.has_principal()) return actor_key(value.principal());
    require(value.has_fixture_actor() && identifier(value.fixture_actor()), "Expected explicit storage actor selector");
    return "f" + value.fixture_actor();
}
std::string subject(const pb::StorageApplicationPrepare& value) {
    if (value.has_principal()) return actor_key(value.principal());
    require(value.has_fixture_actor() && identifier(value.fixture_actor()), "Expected explicit storage actor selector");
    return "f" + value.fixture_actor();
}
void policy(const pb::WorkspaceStoragePolicy& value, bool defaults) {
    require(defaults ? value.workspace().empty() : identifier(value.workspace()), "Invalid storage policy identity");
    require(value.has_total_bytes() && value.has_actor_state_default_bytes(), "Storage budgets require explicit presence");
    bytes(value.total_bytes()); bytes(value.actor_state_default_bytes());
    require(value.actors_size() <= 128 && value.profiles_size() > 0 && value.profiles_size() <= 32, "Storage policy exceeds size bounds");
    std::set<std::string> actors, profiles;
    for (const auto& actor : value.actors()) {
        require(actor.has_state_bytes() && actors.insert(subject(actor)).second, "Duplicate or incomplete storage actor"); bytes(actor.state_bytes());
    }
    for (const auto& profile : value.profiles()) {
        require(identifier(profile.name()) && profile.has_state_bytes() && profiles.insert(profile.name()).second, "Duplicate or incomplete storage profile"); bytes(profile.state_bytes());
    }
}
std::string collection(pb::StorageObject::Kind kind) {
    switch (kind) {
        case pb::StorageObject::REPOSITORY: return "repositories";
        case pb::StorageObject::PROJECT: return "worktrees";
        case pb::StorageObject::APPLICATION_STATE: return "applications";
        case pb::StorageObject::APPLICATION_DEFINITION: return "definitions";
        case pb::StorageObject::ENVIRONMENT_CONFIG: return "environments";
        case pb::StorageObject::RETIRED: return "retired";
        case pb::StorageObject::SNAPSHOT: return "snapshots";
        case pb::StorageObject::COPY_STAGING: return "staging";
        default: throw Error(pb::StorageOwnerError::INVALID_ARGUMENT, "Unknown storage collection");
    }
}
std::string name(const pb::StorageObject& object) {
    require(object.id().size() == 32 && object.id().find_first_not_of("0123456789abcdef") == std::string::npos, "Expected opaque broker-generated path component");
    (void)collection(object.kind());
    return object.id() + (object.kind() == pb::StorageObject::REPOSITORY ? ".git" : "");
}
void database_check(int result, const char* message) {
    if (result == SQLITE_OK || result == SQLITE_ROW || result == SQLITE_DONE) return;
    const auto code = result & 0xff;
    throw Error(code == SQLITE_FULL ? pb::StorageOwnerError::EXHAUSTED :
                code == SQLITE_CONSTRAINT ? pb::StorageOwnerError::CONFLICT : pb::StorageOwnerError::UNAVAILABLE, message);
}
// Keep SQLite's real Unix I/O and locking. Preserve the pinned-directory path
// instead of canonicalizing its deliberate /proc/self/fd directory link.
struct CatalogVfs : sqlite3_vfs {
    std::string label, pinned;
    explicit CatalogVfs(int directory) {
        auto* parent = sqlite3_vfs_find(nullptr);
        if (!parent) throw Error(pb::StorageOwnerError::UNAVAILABLE, "SQLite Unix VFS is unavailable");
        static_cast<sqlite3_vfs&>(*this) = *parent;
        label = "spatial-storage-catalog-" + std::to_string(directory);
        pinned = "/proc/self/fd/" + std::to_string(directory) + "/catalog.db";
        zName = label.c_str(); pNext = nullptr;
        xFullPathname = [](sqlite3_vfs* vfs, const char* input, int count, char* output) {
            const auto& owner = *static_cast<CatalogVfs*>(vfs);
            if (!input || owner.pinned != input || count <= 0 || owner.pinned.size() >= static_cast<std::size_t>(count))
                return SQLITE_CANTOPEN;
            std::memcpy(output, owner.pinned.c_str(), owner.pinned.size()+1);
            return SQLITE_OK;
        };
        database_check(sqlite3_vfs_register(this, 0), "Cannot register private catalog VFS");
    }
    ~CatalogVfs() { sqlite3_vfs_unregister(this); }
};
struct Database {
    sqlite3* value = nullptr;
    ~Database() { if (value) sqlite3_close(value); }
    operator sqlite3*() const { return value; }
};
void sql(sqlite3* db, const char* text) { database_check(sqlite3_exec(db, text, nullptr, nullptr, nullptr), "Storage catalog operation failed"); }
struct Query {
    sqlite3_stmt* statement = nullptr;
    Query(sqlite3* db, const char* text) { database_check(sqlite3_prepare_v2(db, text, -1, &statement, nullptr), "Cannot prepare storage catalog query"); }
    ~Query() { if (statement) sqlite3_finalize(statement); }
    void number(int index, std::uint64_t value) { require(value <= INT64_MAX, "Storage catalog integer overflow"); database_check(sqlite3_bind_int64(statement, index, static_cast<sqlite3_int64>(value)), "Cannot bind storage integer"); }
    void text(int index, const std::string& value) { database_check(sqlite3_bind_text(statement, index, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT), "Cannot bind storage text"); }
    void blob(int index, const std::string& value) { database_check(sqlite3_bind_blob(statement, index, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT), "Cannot bind storage context"); }
    bool row() { const auto result = sqlite3_step(statement); database_check(result, "Storage catalog query failed"); return result == SQLITE_ROW; }
    std::uint64_t number(int column) const { const auto value = sqlite3_column_int64(statement, column); require(value >= 0, "Negative storage catalog value"); return static_cast<std::uint64_t>(value); }
    std::string text(int column) const { const auto* data = sqlite3_column_text(statement, column); return data ? std::string(reinterpret_cast<const char*>(data), static_cast<std::size_t>(sqlite3_column_bytes(statement, column))) : std::string{}; }
    std::string blob(int column) const { const auto* data = sqlite3_column_blob(statement, column); return data ? std::string(static_cast<const char*>(data), static_cast<std::size_t>(sqlite3_column_bytes(statement, column))) : std::string{}; }
};
struct Transaction {
    sqlite3* db; bool committed = false;
    explicit Transaction(sqlite3* value) : db(value) { sql(db, "BEGIN IMMEDIATE"); }
    ~Transaction() { if (!committed) sqlite3_exec(db, "ROLLBACK", nullptr, nullptr, nullptr); }
    void commit() { sql(db, "COMMIT"); committed = true; }
};
template<class T> std::optional<T> item(int root, std::uint64_t object, unsigned type, std::uint64_t offset) {
    btrfs_ioctl_search_args search{};
    auto& key = search.key; key.tree_id = BTRFS_QUOTA_TREE_OBJECTID;
    key.min_objectid = key.max_objectid = object; key.min_offset = key.max_offset = offset;
    key.min_type = key.max_type = type; key.max_transid = UINT64_MAX; key.nr_items = 1;
    check(ioctl(root, BTRFS_IOC_TREE_SEARCH, &search) == 0, "Cannot inspect kernel quota accounting");
    if (!key.nr_items) return std::nullopt;
    btrfs_ioctl_search_header header{}; std::memcpy(&header, search.buf, sizeof(header));
    require(key.nr_items == 1 && header.objectid == object && header.type == type && header.offset == offset &&
            header.len >= sizeof(T) && header.len <= sizeof(search.buf)-sizeof(header), "Unexpected kernel quota item");
    T result{}; std::memcpy(&result, search.buf+sizeof(header), sizeof(T)); return result;
}
bool relation(int root, std::uint64_t child, std::uint64_t parent) {
    btrfs_ioctl_search_args search{};
    auto& key = search.key; key.tree_id = BTRFS_QUOTA_TREE_OBJECTID;
    key.min_objectid = key.max_objectid = child; key.min_offset = key.max_offset = parent;
    key.min_type = key.max_type = BTRFS_QGROUP_RELATION_KEY; key.max_transid = UINT64_MAX; key.nr_items = 1;
    check(ioctl(root, BTRFS_IOC_TREE_SEARCH, &search) == 0, "Cannot inspect quota membership");
    if (!key.nr_items) return false;
    btrfs_ioctl_search_header header{}; std::memcpy(&header, search.buf, sizeof(header));
    require(key.nr_items == 1 && header.objectid == child && header.type == BTRFS_QGROUP_RELATION_KEY &&
            header.offset == parent && header.len == 0, "Unexpected kernel quota relation");
    return true;
}
void checked_limit(int root, std::uint64_t group, std::uint64_t maximum) {
    const auto info = item<btrfs_qgroup_info_item>(root, 0, BTRFS_QGROUP_INFO_KEY, group);
    const auto bound = item<btrfs_qgroup_limit_item>(root, 0, BTRFS_QGROUP_LIMIT_KEY, group);
    if (!info || !bound || le64toh(bound->flags) != (maximum ? BTRFS_QGROUP_LIMIT_MAX_RFER : 0) ||
        le64toh(bound->max_rfer) != maximum)
        throw Error(pb::StorageOwnerError::UNCERTAIN, "Storage quota limit requires checked recovery");
}
void checked_membership(int root, std::uint64_t child, std::uint64_t parent) {
    if (!relation(root, child, parent) || !relation(root, parent, child))
        throw Error(pb::StorageOwnerError::UNCERTAIN, "Storage quota membership requires checked recovery");
}
bool consistent(int root) {
    const auto status = item<btrfs_qgroup_status_item>(root, 0, BTRFS_QGROUP_STATUS_KEY, 0);
    return status && le64toh(status->version) == BTRFS_QGROUP_STATUS_VERSION &&
        le64toh(status->flags) == BTRFS_QGROUP_STATUS_FLAG_ON;
}
void healthy(int root) { if (!consistent(root)) throw Error(pb::StorageOwnerError::UNAVAILABLE, "Quota accounting is not active and consistent"); }
void sync(int root) { check(ioctl(root, BTRFS_IOC_SYNC) == 0, "Cannot durably synchronize storage metadata"); healthy(root); }
void create_group(int root, std::uint64_t group) {
    btrfs_ioctl_qgroup_create_args arguments{}; arguments.create = 1; arguments.qgroupid = group;
    check(ioctl(root, BTRFS_IOC_QGROUP_CREATE, &arguments) == 0 || errno == EEXIST, "Cannot create storage quota group");
}
void limit(int root, std::uint64_t group, std::uint64_t maximum) {
    btrfs_ioctl_qgroup_limit_args arguments{}; arguments.qgroupid = group;
    arguments.lim.flags = BTRFS_QGROUP_LIMIT_MAX_RFER; arguments.lim.max_rfer = maximum;
    check(ioctl(root, BTRFS_IOC_QGROUP_LIMIT, &arguments) == 0, "Cannot install storage quota limit");
}
void assign(int root, std::uint64_t child, std::uint64_t parent) {
    btrfs_ioctl_qgroup_assign_args arguments{}; arguments.assign = 1; arguments.src = child; arguments.dst = parent;
    check(ioctl(root, BTRFS_IOC_QGROUP_ASSIGN, &arguments) == 0 || errno == EEXIST, "Cannot assign quota membership without a rescan");
    healthy(root);
}
std::uint64_t subvolume(int directory) {
    btrfs_ioctl_get_subvol_info_args arguments{};
    check(ioctl(directory, BTRFS_IOC_GET_SUBVOL_INFO, &arguments) >= 0, "Cannot verify storage subvolume identity"); return arguments.treeid;
}
struct Object { std::uint64_t group, subvolume, maximum; std::string payload, state; };
struct SnapshotRecord {
    std::uint64_t domain = 0, source_subvolume = 0, snapshot_subvolume = 0;
    int source_kind = 0;
    std::string source_id, source_uuid, operation_id, request_digest, state, retire_operation_id, snapshot_uuid;
};
// Count bound only: this divisor is not reserved space or reported snapshot usage.
constexpr std::uint64_t snapshot_min_charge = 4 * 1024 * 1024;
constexpr std::uint64_t snapshot_owner_limit = 256;
}
void validate_copy_configuration(const pb::StorageOwnerConfig& config) {
    const auto& catalog = config.copy_catalog();
    require(config.copy_destination_profiles_size() <= 64, "Too many workspace copy destination profiles");
    if (!config.has_copy_catalog()) {
        require(config.copy_destination_profiles_size() == 0, "Copy profiles require a versioned copy catalog");
        return;
    }
    require(catalog.protocol_version() == 1 && catalog.revision() > 0 &&
            catalog.entries_size() > 0 && catalog.entries_size() <= 4096,
            "Invalid or empty workspace copy catalog");
    using Data = workspace::copy::v1::CopyDataClass;
    std::set<std::pair<std::string,int>> entries;
    for (const auto& entry : catalog.entries()) {
        require(identifier(entry.workspace_id()) &&
                entry.has_source_object() && identifier(entry.schema_id()) &&
                safe_identifier(entry.schema_id()) &&
                entry.schema_version() > 0 && entry.maximum_bytes() >= 4*1024*1024 &&
                entry.maximum_bytes() <= (4ULL << 30) && entry.maximum_bytes() % 4096 == 0 &&
                entry.maximum_entries() > 0 && entry.maximum_entries() <= 200000 &&
                entry.consistency() == pb::CRASH_CONSISTENT_AFTER_QUIESCENCE,
                "Invalid workspace copy catalog entry or unsupported consistency contract");
        pb::StorageObject::Kind expected = pb::StorageObject::UNSPECIFIED;
        const char* schema = nullptr;
        switch (entry.data_class()) {
        case Data::PROJECT_TREE: expected = pb::StorageObject::PROJECT; schema = "spatial.workspace.project-tree"; break;
        case Data::GIT_HISTORY: expected = pb::StorageObject::REPOSITORY; schema = "spatial.workspace.git-history"; break;
        case Data::APPLICATION_DEFINITION: expected = pb::StorageObject::APPLICATION_DEFINITION; schema = "spatial.workspace.application-definition"; break;
        case Data::APPLICATION_STATE: expected = pb::StorageObject::APPLICATION_STATE; schema = "spatial.workspace.application-state"; break;
        case Data::ENVIRONMENT_CONFIG: expected = pb::StorageObject::ENVIRONMENT_CONFIG; schema = "spatial.workspace.environment-config"; break;
        default: throw Error(pb::StorageOwnerError::INVALID_ARGUMENT, "Unknown workspace copy class");
        }
        require(entry.schema_id() == schema && entry.schema_version() == 1 &&
                entry.source_object().kind() == expected &&
                entry.source_object().id().size() == 32 &&
                entry.source_object().id().find_first_not_of("0123456789abcdef") == std::string::npos &&
                entries.emplace(entry.workspace_id(), static_cast<int>(entry.data_class())).second,
                "Workspace copy class must map to a unique exact registered object");
    }
    std::set<std::string> profiles;
    for (const auto& profile : config.copy_destination_profiles()) {
        require(safe_identifier(profile.name()) && profile.revision() > 0 &&
                profile.maximum_total_bytes() >= 4*1024*1024 &&
                profile.maximum_total_bytes() <= (4ULL << 30) &&
                profile.maximum_total_bytes() <= config.volume_bytes() &&
                profile.maximum_total_bytes() % 4096 == 0 &&
                profile.maximum_entries() > 0 && profile.maximum_entries() <= 200000 &&
                profile.has_storage_policy() && profile.storage_policy().workspace().empty() &&
                profile.storage_policy().actors_size() == 0 &&
                profile.storage_policy().total_bytes() <= profile.maximum_total_bytes() &&
                safe_identifier(profile.execution_resource_profile()) &&
                safe_identifier(profile.application_profile()) &&
                profiles.insert(profile.name()).second,
                "Invalid or duplicate workspace copy destination profile");
        policy(profile.storage_policy(), true);
        std::set<int> classes;
        for (const auto data_class : profile.allowed_classes()) {
            require(data_class >= Data::PROJECT_TREE && data_class <= Data::ENVIRONMENT_CONFIG &&
                    classes.insert(static_cast<int>(data_class)).second,
                    "Invalid or duplicate destination copy class");
        }
        require(classes.contains(Data::PROJECT_TREE) && classes.contains(Data::GIT_HISTORY),
                "Destination profile must allow the mandatory project and Git classes");
        bool app_profile = false;
        for (const auto& app : profile.storage_policy().profiles())
            if (app.name() == profile.application_profile()) app_profile = true;
        require(app_profile, "Destination profile references an unknown application storage profile");
    }
}
void validate_storage_policy(const workspace::v1::StorageOwnerConfig& config) {
    validate_message(config);
    require(config.protocol_version() == 1 && config.filesystem_id().size() == BTRFS_FSID_SIZE &&
            std::filesystem::path(config.volume_root()).is_absolute() && config.volume_root().find('\0') == std::string::npos,
            "Invalid fixed storage volume identity");
    require(config.has_broker_uid() && config.broker_uid() > 0 && config.broker_uid() != UINT32_MAX &&
            config.has_broker_pid() && config.broker_pid() > 1 && config.broker_pid() <= INT32_MAX,
            "Storage owner requires an exact unprivileged broker process");
    require(config.has_volume_bytes() && config.has_control_plane_bytes() && config.has_filesystem_reserve_bytes(), "Storage volume requires explicit capacity reservations");
    bytes(config.volume_bytes()); bytes(config.control_plane_bytes()); bytes(config.filesystem_reserve_bytes());
    require(config.control_plane_bytes() < config.volume_bytes() && config.filesystem_reserve_bytes() < config.volume_bytes()-config.control_plane_bytes(), "Storage reservations exceed volume capacity");
    require(config.has_default_policy() && config.workspaces_size() <= 64, "Expected bounded default storage policy");
    policy(config.default_policy(), true); validate_copy_configuration(config); std::set<std::string> workspaces;
    for (const auto& selected : config.workspaces()) { policy(selected, false); require(workspaces.insert(selected.workspace()).second, "Duplicate workspace storage policy"); }
}
struct StorageOwner::Impl {
    pb::StorageOwnerConfig config;
    Fd root, metadata, lock;
    std::unique_ptr<CatalogVfs> catalog_vfs;
    Database db;
    std::uint64_t capacity = 0;
    explicit Impl(pb::StorageOwnerConfig value, bool require_kernel_io) : config(std::move(value)) {
        require(getuid() == 0 && geteuid() == 0, "Storage owner requires operator privilege"); validate_storage_policy(config);
        root.reset(open(config.volume_root().c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)); check(root.value >= 0, "Cannot pin storage volume");
        struct statfs filesystem{}; check(fstatfs(root.value, &filesystem) == 0, "Cannot inspect storage filesystem");
        require(filesystem.f_type == BTRFS_SUPER_MAGIC && subvolume(root.value) == BTRFS_FS_TREE_OBJECTID, "Storage owner requires the dedicated Btrfs top-level volume");
        btrfs_ioctl_fs_info_args info{}; check(ioctl(root.value, BTRFS_IOC_FS_INFO, &info) == 0, "Cannot inspect storage volume identity");
        require(info.num_devices == 1 && std::string(reinterpret_cast<char*>(info.fsid), BTRFS_FSID_SIZE) == config.filesystem_id(), "Storage volume identity differs from operator configuration");
        btrfs_ioctl_dev_info_args device{}; device.devid = info.max_id;
        check(ioctl(root.value, BTRFS_IOC_DEV_INFO, &device) == 0, "Cannot inspect storage device capacity");
        require(device.total_bytes == config.volume_bytes(), "Storage device capacity differs from its reservation");
        if (require_kernel_io) validate_current_storage_io(root.value);
        const bool fresh = mkdirat(root.value, ".storage-owner", 0700) == 0;
        if (!fresh) check(errno == EEXIST, "Cannot create private storage catalog");
        metadata.reset(openat(root.value, ".storage-owner", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
        struct stat status{}; check(metadata.value >= 0 && fstat(metadata.value, &status) == 0, "Cannot pin private storage catalog");
        require(status.st_uid == 0 && (status.st_mode & 0077) == 0, "Storage catalog must remain operator-owned and private");
        lock.reset(openat(metadata.value, "owner.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600));
        struct stat lock_status{};
        check(lock.value >= 0 && fstat(lock.value, &lock_status) == 0, "Cannot inspect private owner lock");
        require(S_ISREG(lock_status.st_mode) && lock_status.st_uid == 0 && lock_status.st_nlink == 1 &&
                (lock_status.st_mode & 0077) == 0, "Storage owner lock must remain private and singly linked");
        check(lock.value >= 0 && flock(lock.value, LOCK_EX | LOCK_NB) == 0, "Storage volume already has an owner");
        if (fresh) {
            const auto pinned_path = "/proc/self/fd/" + std::to_string(root.value);
            for (const auto& entry : std::filesystem::directory_iterator(pinned_path))
                require(entry.path().filename() == ".storage-owner", "Fresh storage volume contains unrecognized data");
            btrfs_ioctl_quota_ctl_args enable{}; enable.cmd = BTRFS_QUOTA_CTL_ENABLE;
            check(ioctl(root.value, BTRFS_IOC_QUOTA_CTL, &enable) == 0, "Cannot enable volume quota accounting");
            check(ioctl(root.value, BTRFS_IOC_QUOTA_RESCAN_WAIT) == 0, "Cannot await initial quota accounting");
        }
        healthy(root.value); capacity = config.volume_bytes()-config.control_plane_bytes()-config.filesystem_reserve_bytes();
        require(config.default_policy().total_bytes() <= capacity, "Default workspace budget exceeds storage pool");
        for (const auto& selected : config.workspaces())
            require(selected.total_bytes() <= capacity, "Workspace budget exceeds storage pool");
        Fd catalog_file(openat(metadata.value, "catalog.db", O_RDWR | (fresh ? O_CREAT | O_EXCL : 0) | O_NOFOLLOW | O_CLOEXEC, 0600));
        check(catalog_file.value >= 0, "Cannot pin storage catalog file");
        catalog_vfs = std::make_unique<CatalogVfs>(metadata.value);
        const auto& catalog = catalog_vfs->pinned;
        database_check(sqlite3_open_v2(catalog.c_str(), &db.value,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX | SQLITE_OPEN_NOFOLLOW, catalog_vfs->zName), "Cannot open storage catalog");
        struct stat catalog_status{};
        check(fstatat(metadata.value, "catalog.db", &catalog_status, AT_SYMLINK_NOFOLLOW) == 0, "Cannot inspect storage catalog file");
        require(S_ISREG(catalog_status.st_mode) && catalog_status.st_uid == 0 && catalog_status.st_nlink == 1 &&
                (catalog_status.st_mode & 0077) == 0, "Storage catalog file must remain private and singly linked");
        sql(db, "PRAGMA synchronous=EXTRA;PRAGMA journal_mode=DELETE;PRAGMA foreign_keys=ON;PRAGMA trusted_schema=OFF;");
        if (fresh) {
            sql(db, "CREATE TABLE metadata(next INTEGER NOT NULL,policy BLOB NOT NULL);CREATE TABLE domains(id INTEGER PRIMARY KEY,repo TEXT UNIQUE NOT NULL,maximum INTEGER NOT NULL,workspace TEXT UNIQUE);CREATE TABLE objects(kind INTEGER NOT NULL,id TEXT NOT NULL,domain INTEGER NOT NULL,subvolume INTEGER NOT NULL,maximum INTEGER NOT NULL,payload BLOB NOT NULL,state TEXT NOT NULL,PRIMARY KEY(kind,id));CREATE TABLE actors(workspace TEXT NOT NULL,subject BLOB NOT NULL,id INTEGER UNIQUE NOT NULL,maximum INTEGER NOT NULL,PRIMARY KEY(workspace,subject));CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,domain INTEGER NOT NULL,source_kind INTEGER NOT NULL,source_id TEXT NOT NULL,source_subvolume INTEGER NOT NULL,source_uuid BLOB NOT NULL,operation_id BLOB NOT NULL UNIQUE,request_digest BLOB NOT NULL,snapshot_subvolume INTEGER NOT NULL DEFAULT 0,snapshot_uuid BLOB NOT NULL DEFAULT X'',state TEXT NOT NULL,retire_operation_id BLOB UNIQUE );CREATE TABLE copy_stages(operation_id BLOB PRIMARY KEY CHECK(length(operation_id)=16),request_digest BLOB NOT NULL CHECK(length(request_digest)=32),snapshot_id TEXT NOT NULL,destination_kind INTEGER NOT NULL,destination_id TEXT NOT NULL,source_subvolume INTEGER NOT NULL,destination_subvolume INTEGER NOT NULL,maximum_bytes INTEGER NOT NULL,maximum_entries INTEGER NOT NULL,state INTEGER NOT NULL,actual_bytes INTEGER NOT NULL DEFAULT 0,actual_entries INTEGER NOT NULL DEFAULT 0,content_digest BLOB NOT NULL DEFAULT X'');PRAGMA user_version=4;");
            auto stable = config; stable.clear_broker_pid();
            Query insert(db, "INSERT INTO metadata VALUES(100,?)"); insert.blob(1, stable.SerializeAsString()); insert.row();
            create_group(root.value, pool); limit(root.value, pool, capacity); limit(root.value, BTRFS_FS_TREE_OBJECTID, config.control_plane_bytes());
        } else {
            Query version(db, "PRAGMA user_version"); require(version.row(), "Storage catalog has no schema version");
            const auto catalog_version = version.number(0);
            require(catalog_version >= 1 && catalog_version <= 4, "Storage catalog requires an explicit supported migration");
            auto stable = config; stable.clear_broker_pid(); Query previous(db, "SELECT policy FROM metadata");
            require(previous.row() && previous.blob(0) == stable.SerializeAsString(), "Storage policy changes require a stopped explicit migration");
            if (catalog_version == 1 || catalog_version == 2) {
                Transaction migration(db);
                if (catalog_version == 1)
                    sql(db, "CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,domain INTEGER NOT NULL,source_kind INTEGER NOT NULL,source_id TEXT NOT NULL,source_subvolume INTEGER NOT NULL,source_uuid BLOB NOT NULL,operation_id BLOB NOT NULL UNIQUE,request_digest BLOB NOT NULL,snapshot_subvolume INTEGER NOT NULL DEFAULT 0,snapshot_uuid BLOB NOT NULL DEFAULT X'',state TEXT NOT NULL,retire_operation_id BLOB UNIQUE);");
                sql(db, "CREATE TABLE copy_stages(operation_id BLOB PRIMARY KEY CHECK(length(operation_id)=16),request_digest BLOB NOT NULL CHECK(length(request_digest)=32),snapshot_id TEXT NOT NULL,destination_kind INTEGER NOT NULL,destination_id TEXT NOT NULL,source_subvolume INTEGER NOT NULL,destination_subvolume INTEGER NOT NULL,maximum_bytes INTEGER NOT NULL,maximum_entries INTEGER NOT NULL,state INTEGER NOT NULL,actual_bytes INTEGER NOT NULL DEFAULT 0,actual_entries INTEGER NOT NULL DEFAULT 0,content_digest BLOB NOT NULL DEFAULT X'');PRAGMA user_version=4;");
                migration.commit();
                if(catalog_version<3) sql(db, "UPDATE copy_stages SET state=4 WHERE state=2;");
                else sql(db, "UPDATE copy_stages SET state=CASE state WHEN 1 THEN 4 WHEN 2 THEN 3 WHEN 3 THEN 4 ELSE state END; PRAGMA user_version=4;");
            } else if (catalog_version == 3) {
                Transaction migration(db);
                sql(db, "UPDATE copy_stages SET state=CASE state WHEN 1 THEN 4 WHEN 2 THEN 3 WHEN 3 THEN 4 ELSE state END; PRAGMA user_version=4;");
                migration.commit();
            }
        }
        if (!fresh) sql(db, "UPDATE copy_stages SET state=4 WHERE state=2;");
        for (const auto* directory : {"repositories", "worktrees", "applications", "definitions", "environments", "retired", "snapshots", "staging", "effects"}) {
            if (mkdirat(root.value, directory, 0700) != 0) check(errno == EEXIST, "Cannot create storage collection");
            Fd pinned(openat(root.value, directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
            check(pinned.value >= 0 && fchown(pinned.value, config.broker_uid(), static_cast<gid_t>(-1)) == 0, "Cannot provision storage collection ownership");
            check(fchmod(pinned.value, 0700) == 0, "Cannot restrict storage collection permissions");
            require(subvolume(pinned.value) == BTRFS_FS_TREE_OBJECTID, "Unexpected storage collection subvolume");
        }
        check(fchown(root.value, config.broker_uid(), static_cast<gid_t>(-1)) == 0 && fchmod(root.value, 0700) == 0,
              "Cannot provision bounded broker metadata ownership");
        sync(root.value); checked_pool();
    }
    const pb::WorkspaceStoragePolicy& selected(const std::string& workspace) const {
        for (const auto& value : config.workspaces()) if (value.workspace() == workspace) return value;
        return config.default_policy();
    }
    std::uint64_t next(unsigned level) {
        Query query(db, "SELECT next FROM metadata"); require(query.row(), "Missing storage allocator"); const auto value = query.number(0);
        require(value < 8192 && level > 0 && level < 3, "Storage identity budget exhausted"); sql(db, "UPDATE metadata SET next=next+1"); return (std::uint64_t(level) << BTRFS_QGROUP_LEVEL_SHIFT) | value;
    }
    void checked_pool() {
        healthy(root.value);
        checked_limit(root.value, pool, capacity);
        checked_limit(root.value, BTRFS_FS_TREE_OBJECTID, config.control_plane_bytes());
    }
    std::uint64_t checked_group(std::uint64_t group) {
        checked_pool();
        const auto level = group >> BTRFS_QGROUP_LEVEL_SHIFT;
        if (level == 2) {
            Query domain(db, "SELECT maximum FROM domains WHERE id=?"); domain.number(1, group);
            if (!domain.row()) throw Error(pb::StorageOwnerError::UNCERTAIN, "Missing storage domain catalog entry");
            const auto maximum = domain.number(0);
            checked_limit(root.value, group, maximum); checked_membership(root.value, group, pool);
            return maximum;
        }
        if (level == 1) {
            Query actor(db, "SELECT actors.maximum,domains.id FROM actors JOIN domains ON actors.workspace=domains.workspace WHERE actors.id=?");
            actor.number(1, group);
            if (!actor.row()) throw Error(pb::StorageOwnerError::UNCERTAIN, "Missing actor storage catalog entry");
            const auto maximum = actor.number(0), parent = actor.number(1);
            checked_limit(root.value, group, maximum); checked_membership(root.value, group, parent);
            return std::min(maximum, checked_group(parent));
        }
        throw Error(pb::StorageOwnerError::UNCERTAIN, "Unexpected storage quota parent");
    }
    Object object(const pb::StorageObject& value) {
        (void)name(value); Query query(db, "SELECT domain,subvolume,maximum,payload,state FROM objects WHERE kind=? AND id=?"); query.number(1, value.kind()); query.text(2, value.id());
        if (!query.row()) throw Error(pb::StorageOwnerError::UNAVAILABLE, "Storage object is not prepared");
        Object result{query.number(0), query.number(1), query.number(2), query.blob(3), query.text(4)};
        if (result.state != "ready") throw Error(pb::StorageOwnerError::UNCERTAIN, "Storage allocation requires checked recovery");
        return result;
    }
    SnapshotRecord snapshot_record(const std::string& id) {
        Query q(db, "SELECT domain,source_kind,source_id,source_subvolume,source_uuid,operation_id,request_digest,snapshot_subvolume,state,retire_operation_id,snapshot_uuid FROM snapshots WHERE snapshot_id=?"); q.text(1,id);
        if (!q.row()) throw Error(pb::StorageOwnerError::UNAVAILABLE, "Snapshot receipt is not registered");
        return SnapshotRecord{q.number(0),q.number(3),q.number(7),static_cast<int>(q.number(1)),q.text(2),q.blob(4),q.blob(5),q.blob(6),q.text(8),q.blob(9),q.blob(10)};
    }
    btrfs_ioctl_get_subvol_info_args subvol_info(int fd, const char* message) {
        btrfs_ioctl_get_subvol_info_args value{}; check(ioctl(fd,BTRFS_IOC_GET_SUBVOL_INFO,&value)>=0,message); return value;
    }
    Fd source_directory(const pb::StorageObject& source) {
        Fd parent(openat(root.value,collection(source.kind()).c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
        check(parent.value>=0,"Cannot pin registered snapshot source collection");
        const int child=openat(parent.value,name(source).c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        check(child>=0,"Cannot pin registered snapshot source object"); return Fd(child);
    }
    Fd snapshot_collection() {
        const int directory=openat(root.value,"snapshots",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        check(directory>=0,"Cannot pin snapshot collection"); return Fd(directory);
    }
    bool snapshot_name_exists(int directory,const std::string& component) {
        struct stat status{}; if(fstatat(directory,component.c_str(),&status,AT_SYMLINK_NOFOLLOW)==0) return true;
        if(errno==ENOENT) return false;
        check(false,"Cannot inspect exact snapshot name"); return false;
    }
    Fd open_snapshot(int directory,const std::string& component) {
        const int child=openat(directory,component.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        check(child>=0,"Cannot pin exact snapshot subvolume"); return Fd(child);
    }
    void uncertain_snapshot(const std::string& id) {
        Transaction tx(db); Query a(db,"UPDATE snapshots SET state='uncertain' WHERE snapshot_id=?"); a.text(1,id); a.row();
        Query b(db,"UPDATE objects SET state='uncertain' WHERE kind=? AND id=?"); b.number(1,pb::StorageObject::SNAPSHOT); b.text(2,id); b.row(); tx.commit();
    }
    bool physical_snapshot_matches(const SnapshotRecord& r,const std::string& component) {
        auto directory=snapshot_collection(); if(!snapshot_name_exists(directory.value,component)) return false;
        auto child=open_snapshot(directory.value,component); const auto si=subvol_info(child.value,"Cannot inspect snapshot identity");
        std::uint64_t flags=0; if(ioctl(child.value,BTRFS_IOC_SUBVOL_GETFLAGS,&flags)<0) return false;
        return si.treeid==r.snapshot_subvolume &&
            !r.snapshot_uuid.empty() &&
            std::string(reinterpret_cast<const char*>(si.uuid),sizeof(si.uuid))==r.snapshot_uuid &&
            std::string(reinterpret_cast<const char*>(si.parent_uuid),sizeof(si.parent_uuid))==r.source_uuid &&
            (le64toh(flags)&BTRFS_SUBVOL_RDONLY) &&
            relation(root.value,si.treeid,r.domain) && relation(root.value,r.domain,si.treeid);
    }
    pb::StorageObjectState snapshot_state(const pb::StorageObject& value,const SnapshotRecord& r,bool readonly) {
        pb::StorageObjectState result; *result.mutable_object()=value;
        result.set_subvolume_id(r.snapshot_subvolume); result.set_quota_group(r.domain);
        result.set_maximum_bytes(checked_group(r.domain));
        if (readonly) {
            checked_limit(root.value, r.snapshot_subvolume, 0);
            checked_membership(root.value, r.snapshot_subvolume, r.domain);
            const auto accounting = item<btrfs_qgroup_info_item>(root.value, 0, BTRFS_QGROUP_INFO_KEY, r.snapshot_subvolume);
            if (!accounting) throw Error(pb::StorageOwnerError::UNCERTAIN, "Snapshot quota receipt is missing");
            result.set_referenced_bytes(le64toh(accounting->rfer));
            result.set_exclusive_bytes(le64toh(accounting->excl));
        }
        result.set_accounting_consistent(true);
        result.mutable_source_object()->set_kind(static_cast<pb::StorageObject::Kind>(r.source_kind));
        result.mutable_source_object()->set_id(r.source_id); result.set_source_subvolume_id(r.source_subvolume);
        result.set_read_only(readonly); result.set_operation_digest(r.request_digest); return result;
    }
    pb::StorageObjectState snapshot_inspect(const pb::StorageObject& value) {
        require(value.kind()==pb::StorageObject::SNAPSHOT,"Expected a snapshot identity"); (void)name(value);
        const auto r=snapshot_record(value.id());
        if(r.state=="retired") throw Error(pb::StorageOwnerError::UNAVAILABLE,"Snapshot has been retired");
        if(r.state!="ready") throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot requires checked recovery");
        pb::StorageObject source; source.set_kind(static_cast<pb::StorageObject::Kind>(r.source_kind)); source.set_id(r.source_id);
        const auto src=object(source); if(src.subvolume!=r.source_subvolume) throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot source registration changed");
        auto source_fd=source_directory(source); const auto si=subvol_info(source_fd.value,"Cannot inspect registered snapshot source");
        if(si.treeid!=r.source_subvolume || std::string(reinterpret_cast<const char*>(si.uuid),sizeof(si.uuid))!=r.source_uuid)
            throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot source identity cannot be proven");
        if(!physical_snapshot_matches(r,name(value))) { uncertain_snapshot(value.id()); throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot physical receipt does not match its registered source"); }
        checked_limit(root.value,r.snapshot_subvolume,0);
        checked_membership(root.value,r.snapshot_subvolume,r.domain);
        if(!item<btrfs_qgroup_info_item>(root.value,0,BTRFS_QGROUP_INFO_KEY,r.snapshot_subvolume))
            throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot quota receipt is missing");
        return snapshot_state(value,r,true);
    }
    pb::StorageObjectState snapshot_prepare(const pb::StorageSnapshotPrepare& request) {
        require(request.has_source_object()&&request.has_snapshot_object()&&
            request.source_object().kind()!=pb::StorageObject::UNSPECIFIED&&request.source_object().kind()!=pb::StorageObject::RETIRED&&
            request.source_object().kind()!=pb::StorageObject::SNAPSHOT&&request.source_object().kind()!=pb::StorageObject::COPY_STAGING&&
            request.snapshot_object().kind()==pb::StorageObject::SNAPSHOT&&request.source_object().id()!=request.snapshot_object().id()&&
            request.operation_id().size()==16&&request.request_digest().size()==32,"Incomplete immutable snapshot request");
        (void)name(request.source_object()); (void)name(request.snapshot_object());
        const auto src=object(request.source_object()); const auto max=checked_group(src.group);
        auto source_fd=source_directory(request.source_object()); const auto si=subvol_info(source_fd.value,"Cannot inspect registered snapshot source");
        require(si.treeid==src.subvolume,"Snapshot source catalog identity changed");
        const std::string uuid(reinterpret_cast<const char*>(si.uuid),sizeof(si.uuid));
        const auto id=request.snapshot_object().id(); const auto op=request.operation_id(); const auto digest=request.request_digest();
        SnapshotRecord r; bool found=false;
        {
            Query q(db,"SELECT domain,source_kind,source_id,source_subvolume,source_uuid,operation_id,request_digest,snapshot_subvolume,state,retire_operation_id,snapshot_uuid FROM snapshots WHERE snapshot_id=?"); q.text(1,id);
            if(q.row()){ found=true; r=SnapshotRecord{q.number(0),q.number(3),q.number(7),static_cast<int>(q.number(1)),q.text(2),q.blob(4),q.blob(5),q.blob(6),q.text(8),q.blob(9),q.blob(10)}; }
        }
        if(found){
            if(r.source_kind!=request.source_object().kind()||r.source_id!=request.source_object().id()||r.source_subvolume!=src.subvolume||r.source_uuid!=uuid||r.operation_id!=op||r.request_digest!=digest)
                throw Error(pb::StorageOwnerError::CONFLICT,"Snapshot identity is bound to a different source or operation digest");
            if(r.state=="retired") throw Error(pb::StorageOwnerError::CONFLICT,"Retired snapshot identity cannot be reused");
            if(r.state=="uncertain") throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot requires explicit repair");
            if(r.state=="ready") return snapshot_inspect(request.snapshot_object());
            if(r.state!="pending") throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot effect requires checked recovery");
        } else {
            Query opq(db,"SELECT snapshot_id FROM snapshots WHERE operation_id=?"); opq.blob(1,op);
            if(opq.row()) throw Error(pb::StorageOwnerError::CONFLICT,"Snapshot operation identity is already bound");
            Query collision(db,"SELECT payload FROM objects WHERE kind=? AND id=?"); collision.number(1,pb::StorageObject::SNAPSHOT); collision.text(2,id);
            if(collision.row()) throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot object lacks its immutable operation receipt");
            auto existing_directory=snapshot_collection();
            if(snapshot_name_exists(existing_directory.value,name(request.snapshot_object())))
                throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot path exists without a durable operation receipt");
            Query count(db,"SELECT count(*) FROM snapshots WHERE domain=? AND state!='retired'"); count.number(1,src.group);
            require(count.row(),"Cannot inspect retained snapshot quota");
            const auto count_limit=std::min(snapshot_owner_limit,std::max<std::uint64_t>(1,max/snapshot_min_charge));
            if(count.number(0)>=count_limit) throw Error(pb::StorageOwnerError::EXHAUSTED,"Retained snapshot count quota is exhausted");
            Transaction tx(db);
            Query a(db,"INSERT INTO snapshots(snapshot_id,domain,source_kind,source_id,source_subvolume,source_uuid,operation_id,request_digest,snapshot_subvolume,state,retire_operation_id) VALUES(?,?,?,?,?,?,?,?,0,'pending',NULL)");
            a.text(1,id); a.number(2,src.group); a.number(3,request.source_object().kind()); a.text(4,request.source_object().id()); a.number(5,src.subvolume); a.blob(6,uuid); a.blob(7,op); a.blob(8,digest); a.row();
            Query b(db,"INSERT INTO objects(kind,id,domain,subvolume,maximum,payload,state) VALUES(?,?,?,0,0,?,'pending')");
            b.number(1,pb::StorageObject::SNAPSHOT); b.text(2,id); b.number(3,src.group); b.blob(4,request.SerializeAsString()); b.row(); tx.commit();
            r=SnapshotRecord{src.group,src.subvolume,0,request.source_object().kind(),request.source_object().id(),uuid,op,digest,"pending",{},{}};
        }
        auto directory=snapshot_collection(); const auto component=name(request.snapshot_object());
        if(!snapshot_name_exists(directory.value,component)){
            std::array<std::uint64_t,(sizeof(btrfs_qgroup_inherit)+sizeof(std::uint64_t)+7)/8> inherited{};
            auto* member=reinterpret_cast<btrfs_qgroup_inherit*>(inherited.data()); member->num_qgroups=1; member->qgroups[0]=src.group;
            btrfs_ioctl_vol_args_v2 create{}; create.fd=source_fd.value; create.flags=BTRFS_SUBVOL_RDONLY|BTRFS_SUBVOL_QGROUP_INHERIT;
            create.size=sizeof(btrfs_qgroup_inherit)+sizeof(std::uint64_t); create.qgroup_inherit=member; std::memcpy(create.name,component.c_str(),component.size()+1);
            if(ioctl(directory.value,BTRFS_IOC_SNAP_CREATE_V2,&create)<0&&errno!=EEXIST){
                const int failure=errno; if(failure==EDQUOT||failure==ENOSPC) throw Error(pb::StorageOwnerError::EXHAUSTED,"Snapshot storage quota is exhausted");
                if(!snapshot_name_exists(directory.value,component)){ uncertain_snapshot(id); throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot creation outcome cannot be reconciled"); }
            }
        }
        try{
            auto child=open_snapshot(directory.value,component); const auto ci=subvol_info(child.value,"Cannot verify created snapshot");
            const auto si2=subvol_info(source_fd.value,"Cannot reverify registered snapshot source"); std::uint64_t flags=0;
            check(ioctl(child.value,BTRFS_IOC_SUBVOL_GETFLAGS,&flags)==0,"Cannot verify immutable snapshot flags");
            if(ci.treeid==0||std::string(reinterpret_cast<const char*>(ci.parent_uuid),sizeof(ci.parent_uuid))!=uuid||
                !(le64toh(flags)&BTRFS_SUBVOL_RDONLY)||si2.treeid!=r.source_subvolume||
                std::string(reinterpret_cast<const char*>(si2.uuid),sizeof(si2.uuid))!=r.source_uuid||
                !relation(root.value,ci.treeid,src.group)||!relation(root.value,src.group,ci.treeid)){
                uncertain_snapshot(id); throw Error(pb::StorageOwnerError::UNCERTAIN,"Created snapshot does not match its immutable source receipt");
            }
            r.snapshot_subvolume=ci.treeid;
            r.snapshot_uuid=std::string(reinterpret_cast<const char*>(ci.uuid),sizeof(ci.uuid));
            sync(root.value);
            Transaction tx(db);
            Query a(db,"UPDATE snapshots SET snapshot_subvolume=?,snapshot_uuid=?,state='ready' WHERE snapshot_id=? AND state IN ('pending','ready')");
            a.number(1,ci.treeid); a.blob(2,r.snapshot_uuid); a.text(3,id); a.row();
            Query b(db,"UPDATE objects SET subvolume=?,state='ready' WHERE kind=? AND id=? AND state IN ('pending','ready')"); b.number(1,ci.treeid); b.number(2,pb::StorageObject::SNAPSHOT); b.text(3,id); b.row(); tx.commit();
            return snapshot_inspect(request.snapshot_object());
        }catch(const Error& e){ if(e.code==pb::StorageOwnerError::EXHAUSTED||e.code==pb::StorageOwnerError::UNCERTAIN) throw; uncertain_snapshot(id); throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot recovery could not prove its immutable source receipt"); }
        catch(...){ uncertain_snapshot(id); throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot recovery could not prove its immutable source receipt"); }
    }
    pb::StorageObjectState snapshot_retire(const pb::StorageSnapshotRetire& request) {
        require(request.has_snapshot_object()&&request.snapshot_object().kind()==pb::StorageObject::SNAPSHOT&&request.operation_id().size()==16,"Incomplete snapshot retirement request");
        const auto value=request.snapshot_object(); (void)name(value); auto r=snapshot_record(value.id());
        Query reused(db,"SELECT snapshot_id FROM snapshots WHERE retire_operation_id=?"); reused.blob(1,request.operation_id());
        if(reused.row()&&reused.text(0)!=value.id())
            throw Error(pb::StorageOwnerError::CONFLICT,"Snapshot retirement operation identity is already bound to another snapshot");
        if(r.state=="retired"){ if(r.retire_operation_id!=request.operation_id()) throw Error(pb::StorageOwnerError::CONFLICT,"Snapshot retirement identity is already bound"); return snapshot_state(value,r,false); }
        if(r.state=="uncertain") throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot requires explicit repair");
        if(r.state!="ready"&&r.state!="retiring") throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot retirement requires a ready receipt");
        if(!r.retire_operation_id.empty()&&r.retire_operation_id!=request.operation_id()) throw Error(pb::StorageOwnerError::CONFLICT,"Snapshot already has a different retirement operation");
        if(r.retire_operation_id.empty()){
            (void)snapshot_inspect(value); Transaction tx(db);
            Query a(db,"UPDATE snapshots SET state='retiring',retire_operation_id=? WHERE snapshot_id=? AND state='ready'"); a.blob(1,request.operation_id()); a.text(2,value.id()); a.row();
            Query b(db,"UPDATE objects SET state='retiring' WHERE kind=? AND id=?"); b.number(1,pb::StorageObject::SNAPSHOT); b.text(2,value.id()); b.row(); tx.commit();
            r.retire_operation_id=request.operation_id(); r.state="retiring";
        }
        auto directory=snapshot_collection(); const auto component=name(value);
        if(snapshot_name_exists(directory.value,component)){
            if(!physical_snapshot_matches(r,component)){ uncertain_snapshot(value.id()); throw Error(pb::StorageOwnerError::UNCERTAIN,"Refusing to retire a snapshot without its matching immutable receipt"); }
            btrfs_ioctl_vol_args_v2 destroy{}; std::memcpy(destroy.name,component.c_str(),component.size()+1);
            if(ioctl(directory.value,BTRFS_IOC_SNAP_DESTROY_V2,&destroy)<0&&errno!=ENOENT){
                const int failure=errno; if(failure==EDQUOT||failure==ENOSPC) throw Error(pb::StorageOwnerError::EXHAUSTED,"Snapshot retirement was blocked by storage quota");
                throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot retirement outcome requires checked recovery");
            }
            sync(root.value);
        }
        if(snapshot_name_exists(directory.value,component)){ uncertain_snapshot(value.id()); throw Error(pb::StorageOwnerError::UNCERTAIN,"Snapshot retirement receipt remains physically present"); }
        Transaction tx(db); Query a(db,"UPDATE snapshots SET state='retired' WHERE snapshot_id=? AND state='retiring'"); a.text(1,value.id()); a.row();
        Query b(db,"UPDATE objects SET state='retired' WHERE kind=? AND id=?"); b.number(1,pb::StorageObject::SNAPSHOT); b.text(2,value.id()); b.row(); tx.commit();
        r.state="retired"; return snapshot_state(value,r,false);
    }
    pb::StorageObjectState inspect(const pb::StorageObject& value) {
        if(value.kind()==pb::StorageObject::SNAPSHOT) return snapshot_inspect(value);
        const auto record = object(value); const auto parent_maximum = checked_group(record.group);
        checked_limit(root.value, record.subvolume, record.maximum);
        checked_membership(root.value, record.subvolume, record.group);
        const auto info = item<btrfs_qgroup_info_item>(root.value, 0, BTRFS_QGROUP_INFO_KEY, record.subvolume);
        require(info.has_value(), "Storage object quota disappeared");
        pb::StorageObjectState result; *result.mutable_object() = value; result.set_subvolume_id(record.subvolume);
        result.set_quota_group(record.group); result.set_referenced_bytes(le64toh(info->rfer)); result.set_exclusive_bytes(le64toh(info->excl)); result.set_maximum_bytes(record.maximum ? std::min(record.maximum, parent_maximum) : parent_maximum); result.set_accounting_consistent(true); return result;
    }
    std::uint64_t domain(const pb::StorageObject& repository) {
        require(repository.kind() == pb::StorageObject::REPOSITORY, "Expected repository storage identity"); const auto group = object(repository).group; (void)checked_group(group); return group;
    }
    pb::StorageObjectState allocate(const pb::StorageObject& value, std::uint64_t group, std::uint64_t maximum, const std::string& payload) {
        const auto component = name(value); (void)checked_group(group);
        { Query existing(db, "SELECT payload,state FROM objects WHERE kind=? AND id=?"); existing.number(1, value.kind()); existing.text(2, value.id());
          if (existing.row()) { if (existing.blob(0) != payload) throw Error(pb::StorageOwnerError::CONFLICT, "Storage object binds different context"); return inspect(value); } }
        { Transaction transaction(db); Query insert(db, "INSERT INTO objects VALUES(?,?,?,0,?,?, 'pending')"); insert.number(1, value.kind()); insert.text(2, value.id()); insert.number(3, group); insert.number(4, maximum); insert.blob(5, payload); insert.row(); transaction.commit(); }
        Fd parent(openat(root.value, collection(value.kind()).c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)); check(parent.value >= 0, "Cannot pin storage allocation parent");
        std::array<std::uint64_t, (sizeof(btrfs_qgroup_inherit)+sizeof(std::uint64_t)+7)/8> inherited{};
        auto* membership = reinterpret_cast<btrfs_qgroup_inherit*>(inherited.data()); membership->num_qgroups = 1; membership->qgroups[0] = group;
        btrfs_ioctl_vol_args_v2 create{}; create.flags = BTRFS_SUBVOL_QGROUP_INHERIT; create.size = sizeof(btrfs_qgroup_inherit)+sizeof(std::uint64_t); create.qgroup_inherit = membership;
        std::memcpy(create.name, component.c_str(), component.size()+1);
        check(ioctl(parent.value, BTRFS_IOC_SUBVOL_CREATE_V2, &create) == 0, "Cannot create bounded storage object");
        Fd child(openat(parent.value, component.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)); check(child.value >= 0, "Cannot pin prepared storage object"); const auto id = subvolume(child.value);
        if (maximum) limit(root.value, id, maximum);
        check(fchown(child.value, config.broker_uid(), static_cast<gid_t>(-1)) == 0 && fchmod(child.value, 0700) == 0, "Cannot provision storage object ownership");
        sync(root.value);
        Query complete(db, "UPDATE objects SET subvolume=?,state='ready' WHERE kind=? AND id=?"); complete.number(1, id); complete.number(2, value.kind()); complete.text(3, value.id()); complete.row(); return inspect(value);
    }
    pb::StorageObjectState repository(const pb::StorageRepositoryPrepare& request) {
        require(request.has_object() && request.object().kind() == pb::StorageObject::REPOSITORY, "Expected repository allocation"); (void)name(request.object());
        std::uint64_t group = 0;
        { Query found(db, "SELECT id FROM domains WHERE repo=?"); found.text(1, request.object().id()); if (found.row()) group = found.number(0); }
        if (!group) {
            Transaction transaction(db); Query usage(db, "SELECT coalesce(sum(maximum),0) FROM domains"); require(usage.row(), "Cannot inspect reserved storage"); const auto reserved = usage.number(0), maximum = config.default_policy().total_bytes();
            if (reserved > capacity || maximum > capacity-reserved) throw Error(pb::StorageOwnerError::EXHAUSTED, "Volume storage capacity is reserved");
            group = next(2); Query insert(db, "INSERT INTO domains VALUES(?,?,?,NULL)"); insert.number(1, group); insert.text(2, request.object().id()); insert.number(3, maximum); insert.row(); transaction.commit();
            create_group(root.value, group); limit(root.value, group, maximum); assign(root.value, group, pool);
        }
        return allocate(request.object(), group, 0, request.SerializeAsString());
    }
    pb::StorageObjectState bind(const pb::StorageWorkspaceBind& request) {
        require(identifier(request.workspace()) && request.has_repository() && request.has_project(), "Incomplete workspace storage binding");
        const auto group = domain(request.repository()); require(request.project().kind() == pb::StorageObject::PROJECT && object(request.project()).group == group, "Workspace project and repository must share storage authority");
        Query previous(db, "SELECT workspace,maximum FROM domains WHERE id=?"); previous.number(1, group); require(previous.row(), "Missing storage domain");
        if (!previous.text(0).empty()) { if (previous.text(0) != request.workspace()) throw Error(pb::StorageOwnerError::CONFLICT, "Storage domain already has a workspace owner"); return inspect(request.project()); }
        Query existing(db, "SELECT id FROM domains WHERE workspace=?"); existing.text(1, request.workspace()); if (existing.row()) throw Error(pb::StorageOwnerError::CONFLICT, "Workspace already has storage authority");
        const auto maximum = selected(request.workspace()).total_bytes();
        Query usage(db, "SELECT coalesce(sum(maximum),0) FROM domains"); require(usage.row(), "Cannot inspect volume reservations"); const auto old = previous.number(1), reserved = usage.number(0);
        require(reserved >= old, "Invalid storage reservation accounting");
        if (maximum > capacity-(reserved-old)) throw Error(pb::StorageOwnerError::EXHAUSTED, "Workspace storage capacity is unavailable");
        const auto current = item<btrfs_qgroup_info_item>(root.value, 0, BTRFS_QGROUP_INFO_KEY, group); require(current.has_value(), "Missing workspace quota");
        if (le64toh(current->rfer) > maximum) throw Error(pb::StorageOwnerError::EXHAUSTED, "Existing data exceeds workspace storage policy");
        limit(root.value, group, maximum); sync(root.value);
        Query update(db, "UPDATE domains SET workspace=?,maximum=? WHERE id=?"); update.text(1, request.workspace()); update.number(2, maximum); update.number(3, group); update.row(); return inspect(request.project());
    }
    pb::StorageObjectState application(const pb::StorageApplicationPrepare& request) {
        require(identifier(request.workspace()) && request.has_object() && request.object().kind() == pb::StorageObject::APPLICATION_STATE && identifier(request.profile()), "Incomplete application storage request");
        const auto key = subject(request); const auto& rules = selected(request.workspace());
        auto actor_maximum = std::min(rules.total_bytes(), rules.actor_state_default_bytes());
        for (const auto& actor : rules.actors()) if (subject(actor) == key) actor_maximum = std::min(rules.total_bytes(), actor.state_bytes());
        std::uint64_t maximum = 0;
        for (const auto& profile : rules.profiles()) if (profile.name() == request.profile()) maximum = std::min(actor_maximum, profile.state_bytes());
        require(maximum > 0, "Application storage profile is unavailable");
        Query workspace(db, "SELECT id FROM domains WHERE workspace=?"); workspace.text(1, request.workspace()); if (!workspace.row()) throw Error(pb::StorageOwnerError::UNAVAILABLE, "Workspace storage is not bound");
        std::uint64_t actor_group = 0;
        { Query actor(db, "SELECT id FROM actors WHERE workspace=? AND subject=?"); actor.text(1, request.workspace()); actor.blob(2, key); if (actor.row()) actor_group = actor.number(0); }
        if (!actor_group) {
            Transaction transaction(db); actor_group = next(1); Query insert(db, "INSERT INTO actors VALUES(?,?,?,?)"); insert.text(1, request.workspace()); insert.blob(2, key); insert.number(3, actor_group); insert.number(4, actor_maximum); insert.row(); transaction.commit();
            create_group(root.value, actor_group); limit(root.value, actor_group, actor_maximum); assign(root.value, actor_group, workspace.number(0));
        }
        return allocate(request.object(), actor_group, maximum, request.SerializeAsString());
    }

    pb::StorageCopyStageReceipt copy_stage_receipt(const std::string& id) {
        Query q(db, "SELECT request_digest,snapshot_id,destination_kind,destination_id,source_subvolume,destination_subvolume,state,actual_bytes,actual_entries,content_digest,maximum_bytes,maximum_entries FROM copy_stages WHERE operation_id=?");
        q.blob(1,id); require(q.row(),"Unknown copy stage"); pb::StorageCopyStageReceipt r; r.set_operation_id(id);
        r.set_request_digest(q.blob(0)); r.mutable_snapshot_object()->set_kind(pb::StorageObject::SNAPSHOT); r.mutable_snapshot_object()->set_id(q.text(1));
        r.mutable_destination_object()->set_kind(static_cast<pb::StorageObject::Kind>(q.number(2))); r.mutable_destination_object()->set_id(q.text(3));
        r.set_source_subvolume_id(q.number(4)); r.set_destination_subvolume_id(q.number(5)); r.set_state(static_cast<pb::StorageCopyStageReceipt::State>(q.number(6)));
        r.set_actual_bytes(q.number(7)); r.set_actual_entries(q.number(8)); r.set_content_digest(q.blob(9)); r.set_maximum_bytes(q.number(10)); r.set_maximum_entries(q.number(11)); return r;
    }
    pb::StorageCopyStageReceipt copy_stage_begin(const pb::StorageCopyStageBegin& r) {
        require(r.snapshot_object().kind()==pb::StorageObject::SNAPSHOT &&
            r.destination_object().kind()==pb::StorageObject::COPY_STAGING && r.operation_id().size()==16 && r.request_digest().size()==32 && r.maximum_bytes()>0 &&
            r.maximum_bytes()<=4ULL*1024*1024*1024 && r.maximum_entries()>0 && r.maximum_entries()<=200000,
            "Invalid copy stage intent");
        auto source=snapshot_inspect(r.snapshot_object()); auto dest=inspect(r.destination_object());
        require(source.read_only() && !dest.read_only() && source.subvolume_id()!=dest.subvolume_id(),"Copy source and destination are not independent");
        Query q(db,"SELECT request_digest,snapshot_id,destination_kind,destination_id,source_subvolume,destination_subvolume,maximum_bytes,maximum_entries FROM copy_stages WHERE operation_id=?");
        q.blob(1,r.operation_id());
        if(q.row()) {
            require(q.blob(0)==r.request_digest() && q.text(1)==r.snapshot_object().id() && q.number(2)==static_cast<std::uint64_t>(r.destination_object().kind()) &&
                q.text(3)==r.destination_object().id() && q.number(4)==source.subvolume_id() && q.number(5)==dest.subvolume_id() &&
                q.number(6)==r.maximum_bytes() && q.number(7)==r.maximum_entries(),"Copy stage operation conflict");
            return copy_stage_receipt(r.operation_id());
        }
        Transaction tx(db); Query ins(db,"INSERT INTO copy_stages(operation_id,request_digest,snapshot_id,destination_kind,destination_id,source_subvolume,destination_subvolume,maximum_bytes,maximum_entries,state) VALUES(?,?,?,?,?,?,?,?,?,1)");
        ins.blob(1,r.operation_id()); ins.blob(2,r.request_digest()); ins.text(3,r.snapshot_object().id()); ins.number(4,r.destination_object().kind());
        ins.text(5,r.destination_object().id()); ins.number(6,source.subvolume_id()); ins.number(7,dest.subvolume_id()); ins.number(8,r.maximum_bytes()); ins.number(9,r.maximum_entries()); ins.row(); tx.commit(); sync(root.value);
        return copy_stage_receipt(r.operation_id());
    }
    pb::StorageCopyStageReceipt copy_stage_start(const pb::StorageCopyStageStart& r) {
        require(r.operation_id().size()==16 && r.request_digest().size()==32,"Invalid copy stage claim");
        auto receipt=copy_stage_receipt(r.operation_id()); require(receipt.request_digest()==r.request_digest(),"Copy stage digest conflict");
        if(receipt.state()!=pb::StorageCopyStageReceipt::PREPARED) { receipt.set_claimed(false); return receipt; }
        Query up(db,"UPDATE copy_stages SET state=2 WHERE operation_id=? AND state=1"); up.blob(1,r.operation_id()); up.row();
        receipt=copy_stage_receipt(r.operation_id()); receipt.set_claimed(sqlite3_changes(db)==1 && receipt.state()==pb::StorageCopyStageReceipt::COPYING); sync(root.value); return receipt;
    }
    pb::StorageCopyStageReceipt copy_stage_seal(const pb::StorageCopyStageSeal& r) {
        require(r.operation_id().size()==16 && r.request_digest().size()==32 && r.content_digest().size()==32,"Invalid copy stage seal");
        auto receipt=copy_stage_receipt(r.operation_id());
        require(receipt.request_digest()==r.request_digest() && receipt.snapshot_object().SerializeAsString()==r.snapshot_object().SerializeAsString() &&
            receipt.destination_object().SerializeAsString()==r.destination_object().SerializeAsString(),"Copy stage seal binding conflict");
        if(receipt.state()==pb::StorageCopyStageReceipt::SEALED) {
            require(receipt.actual_bytes()==r.actual_bytes() && receipt.actual_entries()==r.actual_entries() && receipt.content_digest()==r.content_digest(),"Copy stage retry conflict"); return receipt;
        }
        require(receipt.state()==pb::StorageCopyStageReceipt::COPYING,"Copy stage is uncertain");
        auto source=snapshot_inspect(r.snapshot_object()); auto dest=inspect(r.destination_object());
        require(source.read_only() && source.subvolume_id()==receipt.source_subvolume_id() && dest.subvolume_id()==receipt.destination_subvolume_id() && !dest.read_only(),"Copy stage object changed");
        Query lim(db,"SELECT maximum_bytes,maximum_entries FROM copy_stages WHERE operation_id=?"); lim.blob(1,r.operation_id()); require(lim.row(),"Copy stage disappeared");
        require(r.actual_bytes()<=lim.number(0) && r.actual_entries()<=lim.number(1) && r.actual_bytes()<=dest.maximum_bytes(),"Copy worker exceeded its bounds");
        sync(root.value); Query up(db,"UPDATE copy_stages SET state=3,actual_bytes=?,actual_entries=?,content_digest=? WHERE operation_id=? AND state=2");
        up.number(1,r.actual_bytes()); up.number(2,r.actual_entries()); up.blob(3,r.content_digest()); up.blob(4,r.operation_id()); up.row();
        require(sqlite3_changes(db)==1,"Copy stage seal raced"); sync(root.value); return copy_stage_receipt(r.operation_id());
    }
    pb::StorageCopyStageReceipt copy_stage_uncertain(const pb::StorageCopyStageUncertain& r) {
        require(r.operation_id().size()==16 && r.request_digest().size()==32,"Invalid uncertain copy stage");
        auto receipt=copy_stage_receipt(r.operation_id()); require(receipt.request_digest()==r.request_digest(),"Copy stage digest conflict");
        if(receipt.state()==pb::StorageCopyStageReceipt::SEALED || receipt.state()==pb::StorageCopyStageReceipt::UNCERTAIN) return receipt;
        require(receipt.state()==pb::StorageCopyStageReceipt::COPYING,"Copy stage was not launched");
        Query up(db,"UPDATE copy_stages SET state=4 WHERE operation_id=? AND state=2"); up.blob(1,r.operation_id()); up.row(); sync(root.value); return copy_stage_receipt(r.operation_id());
    }
    pb::StorageCopyStageReceipt copy_stage_inspect(const pb::StorageCopyStageInspect& r) {
        require(r.operation_id().size()==16 && r.request_digest().size()==32,"Invalid copy stage inspect");
        auto receipt=copy_stage_receipt(r.operation_id()); require(receipt.request_digest()==r.request_digest(),"Copy stage digest conflict"); return receipt;
    }
    pb::StorageCopyCatalogReply copy_catalog(const pb::StorageCopyCatalogQuery& request) {
        if (!config.has_copy_catalog())
            throw Error(pb::StorageOwnerError::UNAVAILABLE,"Workspace copy catalog is not configured");
        require(identifier(request.workspace_id()) &&
                identifier(request.destination_profile()),
                "Workspace copy query identity is invalid");
        if (request.expected_catalog_revision()!=0 &&
            request.expected_catalog_revision()!=config.copy_catalog().revision())
            throw Error(pb::StorageOwnerError::CONFLICT,"Workspace copy catalog revision changed");
        const pb::WorkspaceCopyDestinationProfile* profile=nullptr;
        for (const auto& candidate : config.copy_destination_profiles())
            if (candidate.name()==request.destination_profile()) profile=&candidate;
        if (!profile || profile->revision()!=request.expected_destination_profile_revision())
            throw Error(pb::StorageOwnerError::CONFLICT,"Workspace copy destination profile revision changed");
        require(request.data_classes_size()>=2 && request.data_classes_size()<=5,
                "Workspace copy query requires bounded selected classes");
        pb::StorageCopyCatalogReply reply; reply.set_catalog_revision(config.copy_catalog().revision());
        *reply.mutable_destination_profile()=*profile;
        std::set<int> seen;
        for (const auto selected : request.data_classes()) {
            require(selected>=workspace::copy::v1::PROJECT_TREE &&
                    selected<=workspace::copy::v1::ENVIRONMENT_CONFIG &&
                    seen.insert(static_cast<int>(selected)).second,
                    "Workspace copy query has unknown or duplicate class");
            bool allowed=false;
            for (const auto value : profile->allowed_classes()) if (value==selected) allowed=true;
            if (!allowed) throw Error(pb::StorageOwnerError::UNAUTHORIZED,"Destination profile denies a selected copy class");
            const pb::WorkspaceCopyCatalogEntry* match=nullptr;
            for (const auto& entry : config.copy_catalog().entries())
                if (entry.workspace_id()==request.workspace_id() && entry.data_class()==selected) match=&entry;
            if (!match) throw Error(pb::StorageOwnerError::UNAVAILABLE,"Selected workspace copy class is not registered");
            const auto registered=object(match->source_object());
            Query domain_query(db,"SELECT workspace FROM domains WHERE id=?"); domain_query.number(1,registered.group);
            if (!domain_query.row() || domain_query.text(0)!=request.workspace_id())
                throw Error(pb::StorageOwnerError::UNAVAILABLE,"Copy object is not registered to the requested workspace");
            *reply.add_entries()=*match;
        }
        bool project=false,git=false;
        for (const auto item : request.data_classes()) {
            if (item==workspace::copy::v1::PROJECT_TREE) project=true;
            if (item==workspace::copy::v1::GIT_HISTORY) git=true;
        }
        require(project && git,"Workspace copy must include project and Git history");
        return reply;
    }

};
StorageOwner::StorageOwner(workspace::v1::StorageOwnerConfig configuration, bool require_kernel_io)
    : impl_(std::make_unique<Impl>(std::move(configuration), require_kernel_io)) {}
StorageOwner::~StorageOwner() = default;
const workspace::v1::StorageOwnerConfig& StorageOwner::configuration() const { return impl_->config; }
workspace::v1::StorageOwnerReply StorageOwner::handle(const workspace::v1::StorageOwnerRequest& request) {
    pb::StorageOwnerReply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        validate_message(request); require(request.protocol_version() == 1, "Unsupported storage protocol"); impl_->checked_pool();
        if (request.has_repository_prepare()) *reply.mutable_state() = impl_->repository(request.repository_prepare());
        else if (request.has_project_prepare()) {
            const auto& operation = request.project_prepare(); require(operation.has_object() && operation.object().kind() == pb::StorageObject::PROJECT && operation.has_repository(), "Incomplete project allocation");
            *reply.mutable_state() = impl_->allocate(operation.object(), impl_->domain(operation.repository()), 0, operation.SerializeAsString());
        } else if (request.has_workspace_bind()) *reply.mutable_state() = impl_->bind(request.workspace_bind());
        else if (request.has_application_prepare()) *reply.mutable_state() = impl_->application(request.application_prepare());
        else if (request.has_retirement_prepare()) {
            const auto& operation = request.retirement_prepare(); require(operation.has_object() && operation.object().kind() == pb::StorageObject::RETIRED && operation.has_repository() && operation.has_project(), "Incomplete retirement allocation");
            const auto group = impl_->domain(operation.repository()); require(operation.project().kind() == pb::StorageObject::PROJECT && impl_->object(operation.project()).group == group, "Retirement changes storage authority");
            *reply.mutable_state() = impl_->allocate(operation.object(), group, 0, operation.SerializeAsString());
        } else if (request.has_snapshot_prepare()) *reply.mutable_state() = impl_->snapshot_prepare(request.snapshot_prepare());
        else if (request.has_snapshot_inspect()) *reply.mutable_state() = impl_->snapshot_inspect(request.snapshot_inspect().snapshot_object());
        else if (request.has_snapshot_retire()) *reply.mutable_state() = impl_->snapshot_retire(request.snapshot_retire());
        else if (request.has_copy_stage_prepare()) {
            const auto& op=request.copy_stage_prepare();
            require(op.has_object() && op.object().kind()==pb::StorageObject::COPY_STAGING && op.has_repository(),
                "Incomplete copy staging allocation");
            *reply.mutable_state()=impl_->allocate(op.object(),impl_->domain(op.repository()),0,op.SerializeAsString());
        }
        else if (request.has_copy_stage_begin()) *reply.mutable_copy_stage() = impl_->copy_stage_begin(request.copy_stage_begin());
        else if (request.has_copy_stage_start()) *reply.mutable_copy_stage() = impl_->copy_stage_start(request.copy_stage_start());
        else if (request.has_copy_stage_seal()) *reply.mutable_copy_stage() = impl_->copy_stage_seal(request.copy_stage_seal());
        else if (request.has_copy_stage_inspect()) *reply.mutable_copy_stage() = impl_->copy_stage_inspect(request.copy_stage_inspect());
        else if (request.has_copy_stage_uncertain()) *reply.mutable_copy_stage() = impl_->copy_stage_uncertain(request.copy_stage_uncertain());
        else if (request.has_copy_catalog_query()) *reply.mutable_copy_catalog() = impl_->copy_catalog(request.copy_catalog_query());
        else if (request.has_inspect()) *reply.mutable_state() = impl_->inspect(request.inspect().object());
        else if (request.has_hello()) {
            auto* identity = reply.mutable_identity(); identity->set_filesystem_id(impl_->config.filesystem_id()); identity->set_process_id(getpid()); identity->set_volume_bytes(impl_->config.volume_bytes()); identity->set_accounting_consistent(true);
        } else throw Error(pb::StorageOwnerError::INVALID_ARGUMENT, "Unknown storage operation");
    } catch (const Error& error) { reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what()); }
      catch (const std::invalid_argument&) { reply.mutable_error()->set_code(pb::StorageOwnerError::INVALID_ARGUMENT); reply.mutable_error()->set_message("Invalid storage protobuf"); }
    return reply;
}
}
