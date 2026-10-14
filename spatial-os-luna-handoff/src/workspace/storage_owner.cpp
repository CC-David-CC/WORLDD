#include "workspace/storage_owner.hpp"
#include "workspace/copy_collections.hpp"
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
#include <openssl/evp.h>
#include <set>
#include <map>
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
std::string sha256(const std::string& bytes) {
    std::array<unsigned char,32> result{};unsigned count=0;
    check(EVP_Digest(bytes.data(),bytes.size(),result.data(),&count,EVP_sha256(),nullptr)==1 && count==32,"Cannot digest owner receipt");
    return {reinterpret_cast<const char*>(result.data()),result.size()};
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
void validate_copy_configuration(const pb::StorageOwnerConfig& config,bool schema_only_legacy_state) {
    const auto& catalog = config.copy_catalog();
    require(config.copy_destination_profiles_size() <= 64, "Too many workspace copy destination profiles");
    if (!config.has_copy_catalog()) {
        require(config.copy_destination_profiles_size() == 0, "Copy profiles require a versioned copy catalog");
        return;
    }
    require((catalog.protocol_version() == 1 || catalog.protocol_version()==2) && catalog.revision() > 0 &&
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
        const bool collection=entry.data_class()==Data::APPLICATION_DEFINITION || entry.data_class()==Data::APPLICATION_STATE;
        require(entry.data_class()!=Data::APPLICATION_STATE || entry.schema_version()==2 || schema_only_legacy_state,
                "Raw application-state schema1 is unsafe; explicit stopped catalog registration of schema2 data/config is required");
        require(entry.schema_id() == schema &&
                (entry.schema_version()==1 || (catalog.protocol_version()==2 && collection && entry.schema_version()==2)) &&
                entry.source_object().kind() == expected &&
                entry.source_object().id().size() == 32 &&
                entry.source_object().id().find_first_not_of("0123456789abcdef") == std::string::npos &&
                entries.emplace(entry.workspace_id(), static_cast<int>(entry.data_class())).second,
                "Workspace copy class must map to a unique exact registered object");
        if(entry.schema_version()==2) {
            require(!entry.typed_export().empty() && entry.typed_export().size()<=65536,"Collection registration requires bounded typed export");
            if(entry.data_class()==Data::APPLICATION_DEFINITION) {
                pb::CopyApplicationDefinitionCollection value;
                require(value.ParseFromString(entry.typed_export()),"Malformed configured definitions");
                copy_collections::definitions(value);
            } else {
                pb::CopyApplicationStateCollection value;
                require(value.ParseFromString(entry.typed_export()),"Malformed configured state contracts");
                copy_collections::states(value,false);
            }
        } else if(!entry.typed_export().empty()) {
            require(entry.data_class()==Data::ENVIRONMENT_CONFIG && catalog.protocol_version()==2 &&
                    entry.typed_export().size()<=65536,"Only known typed domains permit operator export registration");
            pb::CopyEnvironmentConfiguration value;require(value.ParseFromString(entry.typed_export()),"Malformed environment export");validate_message(value);
            require(value.schema_version()==1 && value.dependencies_size()<=256,"Unsupported reproducible environment export");
            std::set<std::string> dependencies;
            for(const auto& dependency:value.dependencies())require(!dependency.name().empty() && dependency.name().size()<=128 &&
                dependencies.insert(dependency.name()).second && !dependency.version().empty() && dependency.version().size()<=128 &&
                dependency.content_digest().size()==32,"Malformed reproducible dependency");
        }
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
void validate_storage_policy(const workspace::v1::StorageOwnerConfig& config, bool require_broker, bool schema_only_legacy_state) {
    validate_message(config);
    require(config.protocol_version() == 1 && config.filesystem_id().size() == BTRFS_FSID_SIZE &&
            std::filesystem::path(config.volume_root()).is_absolute() && config.volume_root().find('\0') == std::string::npos,
            "Invalid fixed storage volume identity");
    require(config.has_broker_uid() && config.broker_uid() > 0 && config.broker_uid() != UINT32_MAX &&
            (!require_broker || (config.has_broker_pid() && config.broker_pid() > 1 && config.broker_pid() <= INT32_MAX)),
            "Storage owner requires an exact unprivileged broker process");
    require(config.has_volume_bytes() && config.has_control_plane_bytes() && config.has_filesystem_reserve_bytes(), "Storage volume requires explicit capacity reservations");
    bytes(config.volume_bytes()); bytes(config.control_plane_bytes()); bytes(config.filesystem_reserve_bytes());
    require(config.control_plane_bytes() < config.volume_bytes() && config.filesystem_reserve_bytes() < config.volume_bytes()-config.control_plane_bytes(), "Storage reservations exceed volume capacity");
    require(config.has_default_policy() && config.workspaces_size() <= 64, "Expected bounded default storage policy");
    policy(config.default_policy(), true); validate_copy_configuration(config,schema_only_legacy_state); std::set<std::string> workspaces;
    for (const auto& selected : config.workspaces()) { policy(selected, false); require(workspaces.insert(selected.workspace()).second, "Duplicate workspace storage policy"); }
}
struct StorageOwner::Impl {
    pb::StorageOwnerConfig config;
    Fd root, metadata, lock, stopped_broker;
    std::unique_ptr<CatalogVfs> catalog_vfs;
    Database db;
    std::uint64_t capacity = 0;
    explicit Impl(pb::StorageOwnerConfig value, bool require_kernel_io, bool migrate_catalog, unsigned migrate_schema_from) : config(std::move(value)) {
        const bool stopped_migration=migrate_catalog || migrate_schema_from!=0;
        require(!(migrate_catalog && migrate_schema_from) && migrate_schema_from<=5,"Select one exact stopped storage migration");
        require(getuid() == 0 && geteuid() == 0, "Storage owner requires operator privilege"); validate_storage_policy(config, !stopped_migration,migrate_schema_from!=0);
        root.reset(open(config.volume_root().c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)); check(root.value >= 0, "Cannot pin storage volume");
        struct statfs filesystem{}; check(fstatfs(root.value, &filesystem) == 0, "Cannot inspect storage filesystem");
        require(filesystem.f_type == BTRFS_SUPER_MAGIC && subvolume(root.value) == BTRFS_FS_TREE_OBJECTID, "Storage owner requires the dedicated Btrfs top-level volume");
        btrfs_ioctl_fs_info_args info{}; check(ioctl(root.value, BTRFS_IOC_FS_INFO, &info) == 0, "Cannot inspect storage volume identity");
        require(info.num_devices == 1 && std::string(reinterpret_cast<char*>(info.fsid), BTRFS_FSID_SIZE) == config.filesystem_id(), "Storage volume identity differs from operator configuration");
        btrfs_ioctl_dev_info_args device{}; device.devid = info.max_id;
        check(ioctl(root.value, BTRFS_IOC_DEV_INFO, &device) == 0, "Cannot inspect storage device capacity");
        require(device.total_bytes == config.volume_bytes(), "Storage device capacity differs from its reservation");
        if (require_kernel_io) validate_current_storage_io(root.value);
        if (stopped_migration) {
            // Schema and catalog updates both require the owner and execution authority locks.
            struct stat registry{};
            require(fstatat(root.value,"execution.db",&registry,AT_SYMLINK_NOFOLLOW)==0 &&
                    S_ISREG(registry.st_mode) && registry.st_uid==config.broker_uid(),
                    "Catalog migration requires an existing stopped execution registry");
            stopped_broker.reset(openat(root.value,"execution.lock",O_RDWR|O_CLOEXEC|O_NOFOLLOW));
            struct stat broker_lock{};
            require(stopped_broker.value>=0 && fstat(stopped_broker.value,&broker_lock)==0 &&
                    S_ISREG(broker_lock.st_mode) && broker_lock.st_uid==config.broker_uid() &&
                    broker_lock.st_nlink==1 && flock(stopped_broker.value,LOCK_EX|LOCK_NB)==0,
                    "Catalog migration requires the broker to be stopped");
        }
        if(stopped_migration) {
            struct stat existing{};
            require(fstatat(root.value,".storage-owner",&existing,AT_SYMLINK_NOFOLLOW)==0 && S_ISDIR(existing.st_mode),
                    "Stopped migration requires an existing storage owner registry");
        }
        const bool fresh = !stopped_migration && mkdirat(root.value, ".storage-owner", 0700) == 0;
        if (!fresh && !stopped_migration) check(errno == EEXIST, "Cannot create private storage catalog");
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
        std::unique_ptr<Transaction> schema_migration;
        unsigned predecessor=0;
        if (fresh) {
            sql(db, "CREATE TABLE metadata(next INTEGER NOT NULL,policy BLOB NOT NULL);CREATE TABLE domains(id INTEGER PRIMARY KEY,repo TEXT UNIQUE NOT NULL,maximum INTEGER NOT NULL,workspace TEXT UNIQUE);CREATE TABLE objects(kind INTEGER NOT NULL,id TEXT NOT NULL,domain INTEGER NOT NULL,subvolume INTEGER NOT NULL,maximum INTEGER NOT NULL,payload BLOB NOT NULL,state TEXT NOT NULL,PRIMARY KEY(kind,id));CREATE TABLE actors(workspace TEXT NOT NULL,subject BLOB NOT NULL,id INTEGER UNIQUE NOT NULL,maximum INTEGER NOT NULL,PRIMARY KEY(workspace,subject));CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,domain INTEGER NOT NULL,source_kind INTEGER NOT NULL,source_id TEXT NOT NULL,source_subvolume INTEGER NOT NULL,source_uuid BLOB NOT NULL,operation_id BLOB NOT NULL UNIQUE,request_digest BLOB NOT NULL,snapshot_subvolume INTEGER NOT NULL DEFAULT 0,snapshot_uuid BLOB NOT NULL DEFAULT X'',state TEXT NOT NULL,retire_operation_id BLOB UNIQUE );CREATE TABLE copy_stages(operation_id BLOB PRIMARY KEY CHECK(length(operation_id)=16),request_digest BLOB NOT NULL CHECK(length(request_digest)=32),snapshot_id TEXT NOT NULL,destination_kind INTEGER NOT NULL,destination_id TEXT NOT NULL,source_subvolume INTEGER NOT NULL,destination_subvolume INTEGER NOT NULL,maximum_bytes INTEGER NOT NULL,maximum_entries INTEGER NOT NULL,state INTEGER NOT NULL,actual_bytes INTEGER NOT NULL DEFAULT 0,actual_entries INTEGER NOT NULL DEFAULT 0,content_digest BLOB NOT NULL DEFAULT X'');PRAGMA user_version=4;");
            auto stable = config; stable.clear_broker_pid();
            Query insert(db, "INSERT INTO metadata VALUES(100,?)"); insert.blob(1, stable.SerializeAsString()); insert.row();
            create_group(root.value, pool); limit(root.value, pool, capacity); limit(root.value, BTRFS_FS_TREE_OBJECTID, config.control_plane_bytes());
        } else {
            Query version(db, "PRAGMA user_version"); require(version.row(), "Storage catalog has no schema version");
            const auto catalog_version = version.number(0);
            predecessor=static_cast<unsigned>(catalog_version);
            require(catalog_version >= 1 && catalog_version <= 6, "Storage catalog requires an explicit supported migration");
            require(catalog_version==6 || migrate_schema_from==catalog_version,
                    "Storage catalog requires its exact stopped --migrate-v1/2/3/4/5 migration");
            require(!migrate_schema_from || migrate_schema_from==catalog_version,
                    "Storage schema migration does not match the existing version");
            if(migrate_schema_from)schema_migration=std::make_unique<Transaction>(db);
            auto stable = config; stable.clear_broker_pid(); Query previous(db, "SELECT policy FROM metadata");
            require(previous.row(),"Storage policy record is absent");
            const auto previous_bytes=previous.blob(0);
            if (previous_bytes!=stable.SerializeAsString()) {
                require(migrate_catalog,"Storage policy changes require a stopped explicit migration");
                pb::StorageOwnerConfig old;
                require(old.ParseFromString(previous_bytes),"Stored storage policy is malformed");
                auto old_base=old, new_base=stable;
                old_base.clear_copy_catalog();old_base.clear_copy_destination_profiles();
                new_base.clear_copy_catalog();new_base.clear_copy_destination_profiles();
                require(old_base.SerializeAsString()==new_base.SerializeAsString(),
                        "Copy catalog migration cannot change storage or execution policy");
                const auto old_revision=old.has_copy_catalog()?old.copy_catalog().revision():0;
                require(old_revision<INT64_MAX && config.has_copy_catalog() &&
                        config.copy_catalog().revision()==old_revision+1,
                        "Copy catalog migration requires the next exact catalog revision");
                register_typed_exports();
                for(const auto& entry:config.copy_catalog().entries()) {
                    const auto registered=object(entry.source_object());
                    require(registered_workspace(registered.group)==entry.workspace_id(),
                            "Catalog migration requires exact registered workspace objects");
                }
                Transaction migration(db);
                Query update(db,"UPDATE metadata SET policy=? WHERE policy=?");
                update.blob(1,stable.SerializeAsString());update.blob(2,previous_bytes);update.row();
                require(sqlite3_changes(db)==1,"Catalog migration raced with another policy update");
                migration.commit();
            }
            if (catalog_version == 1 || catalog_version == 2) {
                if (catalog_version == 1)
                    sql(db, "CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,domain INTEGER NOT NULL,source_kind INTEGER NOT NULL,source_id TEXT NOT NULL,source_subvolume INTEGER NOT NULL,source_uuid BLOB NOT NULL,operation_id BLOB NOT NULL UNIQUE,request_digest BLOB NOT NULL,snapshot_subvolume INTEGER NOT NULL DEFAULT 0,snapshot_uuid BLOB NOT NULL DEFAULT X'',state TEXT NOT NULL,retire_operation_id BLOB UNIQUE);");
                sql(db, "CREATE TABLE copy_stages(operation_id BLOB PRIMARY KEY CHECK(length(operation_id)=16),request_digest BLOB NOT NULL CHECK(length(request_digest)=32),snapshot_id TEXT NOT NULL,destination_kind INTEGER NOT NULL,destination_id TEXT NOT NULL,source_subvolume INTEGER NOT NULL,destination_subvolume INTEGER NOT NULL,maximum_bytes INTEGER NOT NULL,maximum_entries INTEGER NOT NULL,state INTEGER NOT NULL,actual_bytes INTEGER NOT NULL DEFAULT 0,actual_entries INTEGER NOT NULL DEFAULT 0,content_digest BLOB NOT NULL DEFAULT X'');PRAGMA user_version=4;");
                if(catalog_version<3) sql(db, "UPDATE copy_stages SET state=4 WHERE state=2;");
                else sql(db, "UPDATE copy_stages SET state=CASE state WHEN 1 THEN 4 WHEN 2 THEN 3 WHEN 3 THEN 4 ELSE state END; PRAGMA user_version=4;");
            } else if (catalog_version == 3) {
                sql(db, "UPDATE copy_stages SET state=CASE state WHEN 1 THEN 4 WHEN 2 THEN 3 WHEN 3 THEN 4 ELSE state END; PRAGMA user_version=4;");
            }
        }
        if(fresh || migrate_schema_from) {
            if(fresh)schema_migration=std::make_unique<Transaction>(db);
            if(fresh || predecessor<5)
                sql(db,"CREATE TABLE copy_destinations(operation_id BLOB PRIMARY KEY CHECK(length(operation_id)=16),intent BLOB NOT NULL,state INTEGER NOT NULL,receipt BLOB NOT NULL DEFAULT X'',publication BLOB NOT NULL DEFAULT X'');");
            sql(db,"CREATE TABLE configured_state_revisions(workspace TEXT PRIMARY KEY,revision INTEGER NOT NULL CHECK(revision>0));"
                   "CREATE TABLE configured_states(workspace TEXT NOT NULL,key TEXT NOT NULL,object TEXT UNIQUE NOT NULL,contract BLOB NOT NULL,revision INTEGER NOT NULL,payload BLOB NOT NULL,state TEXT NOT NULL,PRIMARY KEY(workspace,key));"
                   "CREATE TABLE configured_state_uses(workspace TEXT NOT NULL,key TEXT NOT NULL,request_digest BLOB NOT NULL,old_group INTEGER NOT NULL,new_group INTEGER NOT NULL,maximum INTEGER NOT NULL,state TEXT NOT NULL,receipt BLOB NOT NULL DEFAULT X'',PRIMARY KEY(workspace,key,request_digest));"
                   "CREATE TABLE copy_exports(workspace TEXT NOT NULL,data_class INTEGER NOT NULL,revision INTEGER NOT NULL,object TEXT NOT NULL,payload BLOB NOT NULL,state TEXT NOT NULL,receipt BLOB NOT NULL DEFAULT X'',PRIMARY KEY(workspace,data_class,revision));"
                   "CREATE TABLE configured_launch_allocations(runtime TEXT PRIMARY KEY,intent BLOB NOT NULL,state TEXT NOT NULL,receipt BLOB NOT NULL DEFAULT X'');PRAGMA user_version=6;");
        }
        {Query shape(db,"SELECT intent,state,publication FROM copy_destinations LIMIT 0");}
        {Query shape(db,"SELECT workspace,revision FROM configured_state_revisions LIMIT 0");}
        {Query shape(db,"SELECT workspace,key,object,contract,revision,payload,state FROM configured_states LIMIT 0");}
        {Query shape(db,"SELECT workspace,key,request_digest,old_group,new_group,maximum,state,receipt FROM configured_state_uses LIMIT 0");}
        {Query shape(db,"SELECT workspace,data_class,revision,object,payload,state,receipt FROM copy_exports LIMIT 0");}
        {Query shape(db,"SELECT runtime,intent,state,receipt FROM configured_launch_allocations LIMIT 0");}
        // An interrupted allocating or publishing effect needs exact inspection. Never replay allocation.
        sql(db,"UPDATE copy_destinations SET state=3 WHERE state IN (0,4);");
        if (!fresh) sql(db, "UPDATE copy_stages SET state=4 WHERE state=2;");
        if(schema_migration)schema_migration->commit();
        sql(db,"UPDATE configured_states SET state='uncertain' WHERE state='pending';UPDATE configured_state_uses SET state='uncertain' WHERE state='pending';UPDATE configured_launch_allocations SET state='uncertain' WHERE state='pending'");
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
        Query copied(db,"SELECT intent FROM copy_destinations WHERE state=2");
        while(copied.row()) {
            pb::StorageCopyDestinationPrepare allocation;require(allocation.ParseFromString(copied.blob(0)),"Stored destination policy binding is malformed");
            if(allocation.workspace_id()!=workspace)continue;
            for(const auto& profile:config.copy_destination_profiles())
                if(profile.name()==allocation.profile_name() && profile.revision()==allocation.profile_revision())return profile.storage_policy();
            throw Error(pb::StorageOwnerError::UNAVAILABLE,"Copied workspace independent storage profile is unavailable");
        }
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
        checked_limit(root.value,src.subvolume,src.maximum);
        checked_membership(root.value,src.subvolume,src.group);
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
            // Full-mode Btrfs snapshots inherit the source's individual quota.
            // Only this exact pending effect may initialize its intended zero limit.
            // A retry after the clear is valid; ready receipts never take this path.
            const auto inherited=item<btrfs_qgroup_limit_item>(root.value,0,BTRFS_QGROUP_LIMIT_KEY,ci.treeid);
            const auto expected_flags=src.maximum?BTRFS_QGROUP_LIMIT_MAX_RFER:0;
            const bool cleared=inherited && le64toh(inherited->flags)==0 && le64toh(inherited->max_rfer)==0;
            const bool expected=inherited && le64toh(inherited->flags)==expected_flags &&
                le64toh(inherited->max_rfer)==src.maximum;
            if(!inherited || (!cleared&&!expected) || le64toh(inherited->max_excl)!=0 ||
                le64toh(inherited->rsv_rfer)!=0 || le64toh(inherited->rsv_excl)!=0) {
                uncertain_snapshot(id);
                throw Error(pb::StorageOwnerError::UNCERTAIN,"Pending snapshot has an unexpected individual quota limit");
            }
            checked_limit(root.value,src.subvolume,src.maximum);
            if(!cleared) {
                // BTRFS_IOC_QGROUP_LIMIT uses UINT64_MAX to clear MAX_RFER.
                // Passing zero would install a zero-byte limit instead.
                limit(root.value,ci.treeid,UINT64_MAX);
            }
            sync(root.value);
            checked_limit(root.value,ci.treeid,0);
            checked_limit(root.value,src.subvolume,src.maximum);
            checked_membership(root.value,src.subvolume,src.group);
            checked_membership(root.value,ci.treeid,src.group);
            (void)checked_group(src.group);
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
    std::uint64_t state_mapping_revision(const std::string& workspace) {
        Query q(db,"SELECT revision FROM configured_state_revisions WHERE workspace=?");q.text(1,workspace);
        return q.row()?q.number(0):0;
    }
    std::uint64_t next_state_mapping_revision(const std::string& workspace) {
        const auto old=state_mapping_revision(workspace);require(old<INT64_MAX,"Configured state mapping revision exhausted");
        Query up(db,"INSERT INTO configured_state_revisions VALUES(?,?) ON CONFLICT(workspace) DO UPDATE SET revision=excluded.revision WHERE revision=?");
        up.text(1,workspace);up.number(2,old+1);up.number(3,old);up.row();require(sqlite3_changes(db)==1,"Configured state mapping revision raced");return old+1;
    }
    std::string read_typed_export(const pb::StorageObject& object) {
        (void)inspect(object);auto directory=source_directory(object);
        Fd file(openat(directory.value,"export.pb",O_RDONLY|O_NOFOLLOW|O_CLOEXEC));struct stat record{};
        require(file.value>=0 && fstat(file.value,&record)==0 && S_ISREG(record.st_mode) && record.st_nlink==1 &&
                record.st_size>0 && record.st_size<=65536,"Published export is not a bounded typed record");
        std::string bytes(static_cast<std::size_t>(record.st_size),'\0');std::size_t offset=0;
        while(offset<bytes.size()) {
            const auto n=pread(file.value,bytes.data()+offset,bytes.size()-offset,offset);
            if(n<0 && errno==EINTR)continue;
            require(n>0,"Cannot read published typed export");offset+=static_cast<std::size_t>(n);
        }
        return bytes;
    }
    pb::WorkspaceCopyCatalogEntry resolved_copy_entry(const std::string& workspace,int data_class) {
        for(const auto& entry:config.copy_catalog().entries())
            if(entry.workspace_id()==workspace && entry.data_class()==data_class)return entry;
        // Published owner receipts register fresh export objects independently of operator source bindings.
        Query published(db,"SELECT intent FROM copy_destinations WHERE state=2");
        while(published.row()) {
            pb::StorageCopyDestinationPrepare allocation;
            require(allocation.ParseFromString(published.blob(0)),"Stored copy allocation is malformed");
            if(allocation.workspace_id()!=workspace)continue;
            for(const auto& domain:allocation.domains()) {
                if(domain.data_class()!=data_class)continue;
                const auto& profile=copy_profile(allocation);
                pb::WorkspaceCopyCatalogEntry entry;entry.set_workspace_id(workspace);
                entry.set_data_class(domain.data_class());*entry.mutable_source_object()=domain.destination();
                entry.set_maximum_bytes(profile.maximum_total_bytes());entry.set_maximum_entries(profile.maximum_entries());
                entry.set_consistency(pb::CRASH_CONSISTENT_AFTER_QUIESCENCE);entry.set_schema_version(1);
                const char* schema=nullptr;
                switch(data_class) {
                case workspace::copy::v1::PROJECT_TREE:schema="spatial.workspace.project-tree";break;
                case workspace::copy::v1::GIT_HISTORY:schema="spatial.workspace.git-history";break;
                case workspace::copy::v1::APPLICATION_DEFINITION:schema="spatial.workspace.application-definition";break;
                case workspace::copy::v1::APPLICATION_STATE:schema="spatial.workspace.application-state";break;
                case workspace::copy::v1::ENVIRONMENT_CONFIG:schema="spatial.workspace.environment-config";break;
                default:throw Error(pb::StorageOwnerError::INVALID_ARGUMENT,"Unknown published copy class");
                }
                entry.set_schema_id(schema);
                if(data_class==workspace::copy::v1::APPLICATION_DEFINITION) {
                    const auto bytes=read_typed_export(domain.destination());
                    pb::CopyApplicationDefinitionCollection collection;
                    if(collection.ParseFromString(bytes) && collection.schema_version()==2) {
                        copy_collections::definitions(collection);entry.set_schema_version(2);
                    } else {
                        pb::CopyApplicationDefinition definition;require(definition.ParseFromString(bytes),"Malformed published definition");
                        copy_collections::definition(definition);entry.set_schema_version(1);
                    }
                    entry.set_typed_export(bytes);
                } else if(data_class==workspace::copy::v1::APPLICATION_STATE) {
                    pb::CopyApplicationStateCollection collection;
                    require(collection.ParseFromString(read_typed_export(domain.destination())),"Malformed published state descriptor");
                    copy_collections::states(collection,true);
                    // Historical publication digests describe that copy. Live state is resolved under its current mapping generation.
                    pb::CopyApplicationStateCollection descriptor;descriptor.set_schema_version(2);
                    for(const auto& slot:collection.slots()) {
                        auto* fresh=descriptor.add_slots();fresh->set_key(slot.key());*fresh->mutable_contract()=slot.contract();
                    }
                    copy_collections::states(descriptor,false);entry.set_schema_version(2);
                    entry.set_typed_export(descriptor.SerializeAsString());
                } else if(data_class==workspace::copy::v1::ENVIRONMENT_CONFIG)entry.set_typed_export(read_typed_export(domain.destination()));
                return entry;
            }
        }
        throw Error(pb::StorageOwnerError::UNAVAILABLE,"Selected workspace copy class is not registered");
    }
    pb::CopyApplicationDefinitionCollection configured_definitions(const std::string& workspace) {
        for(const auto& entry:config.copy_catalog().entries()) {
            if(entry.workspace_id()!=workspace || entry.data_class()!=workspace::copy::v1::APPLICATION_DEFINITION || entry.schema_version()!=2)continue;
            pb::CopyApplicationDefinitionCollection definitions;
            require(definitions.ParseFromString(entry.typed_export()),"Registered definition collection is malformed");
            copy_collections::definitions(definitions);return definitions;
        }
        Query published(db,"SELECT intent FROM copy_destinations WHERE state=2");
        while(published.row()) {
            pb::StorageCopyDestinationPrepare allocation;require(allocation.ParseFromString(published.blob(0)),"Stored copy allocation is malformed");
            if(allocation.workspace_id()!=workspace)continue;
            for(const auto& domain:allocation.domains()) {
                if(domain.data_class()!=workspace::copy::v1::APPLICATION_DEFINITION)continue;
                (void)inspect(domain.destination());auto directory=source_directory(domain.destination());
                Fd file(openat(directory.value,"export.pb",O_RDONLY|O_NOFOLLOW|O_CLOEXEC));struct stat record{};
                require(file.value>=0 && fstat(file.value,&record)==0 && S_ISREG(record.st_mode) && record.st_nlink==1 &&
                        record.st_size>0 && record.st_size<=65536,"Published definitions are not a bounded typed collection");
                std::string bytes(static_cast<std::size_t>(record.st_size),'\0');std::size_t offset=0;
                while(offset<bytes.size()) {
                    const auto n=pread(file.value,bytes.data()+offset,bytes.size()-offset,offset);
                    if(n<0 && errno==EINTR)continue;
                    require(n>0,"Cannot read published definitions");offset+=static_cast<std::size_t>(n);
                }
                pb::CopyApplicationDefinitionCollection definitions;
                require(definitions.ParseFromString(bytes),"Published configured definitions are malformed");copy_collections::definitions(definitions);
                for(const auto& app:definitions.applications())
                    require(app.profile()==copy_profile(allocation).application_profile(),"Copied definition did not receive its independent destination profile");
                return definitions;
            }
        }
        throw Error(pb::StorageOwnerError::UNAVAILABLE,"Configured application definitions are not registered");
    }
    pb::ConfiguredApplicationDefinition configured_definition(const std::string& workspace,const std::string& key) {
        require(copy_collections::key(key),"Invalid configured application key");
        const auto definitions=configured_definitions(workspace);
        for(const auto& app:definitions.applications())if(app.key()==key) {
            bool allowed=false;for(const auto& profile:selected(workspace).profiles())if(profile.name()==app.profile())allowed=true;
            require(allowed,"Configured application profile is absent from the independently assigned storage policy");
            return app;
        }
        throw Error(pb::StorageOwnerError::UNAVAILABLE,"Configured application key is not registered");
    }
    pb::StorageConfiguredApplicationReply configured_application(const pb::StorageConfiguredApplicationQuery& request) {
        require(identifier(request.workspace_id()),"Invalid configured application workspace");
        pb::StorageConfiguredApplicationReply reply;reply.set_catalog_revision(config.copy_catalog().revision());
        *reply.mutable_definition()=configured_definition(request.workspace_id(),request.key());
        reply.set_state_mapping_revision(state_mapping_revision(request.workspace_id()));
        *reply.mutable_storage_policy()=selected(request.workspace_id());
        Query mapping(db,"SELECT object,contract,state FROM configured_states WHERE workspace=? AND key=?");
        mapping.text(1,request.workspace_id());mapping.text(2,request.key());
        if(mapping.row()) {
            Query unresolved(db,"SELECT count(*) FROM configured_state_uses WHERE workspace=? AND key=? AND state!='ready'");
            unresolved.text(1,request.workspace_id());unresolved.text(2,request.key());
            require(unresolved.row(),"Cannot inspect configured state effect receipts");
            if(mapping.text(2)!="ready" || unresolved.number(0)!=0)throw Error(pb::StorageOwnerError::UNCERTAIN,"Configured application state needs exact recovery");
            require(mapping.blob(1)==reply.definition().state_contract().SerializeAsString(),"Configured state contract changed without explicit state migration");
            pb::StorageObject object;object.set_kind(pb::StorageObject::APPLICATION_STATE);object.set_id(mapping.text(0));
            require(registered_workspace(this->object(object).group)==request.workspace_id(),"Configured state belongs to another workspace");
            *reply.mutable_state()=inspect(object);
        }
        return reply;
    }
    pb::ConfiguredStateMapping configured_state_mapping(const std::string& workspace,const pb::CopyApplicationStateCollection& descriptor) {
        copy_collections::states(descriptor,false);
        pb::ConfiguredStateMapping mapping;mapping.set_workspace_id(workspace);mapping.set_revision(state_mapping_revision(workspace));
        std::map<std::string,std::string> expected;
        for(const auto& slot:descriptor.slots())expected.emplace(slot.key(),slot.contract().SerializeAsString());
        Query q(db,"SELECT key,object,contract,state FROM configured_states WHERE workspace=? ORDER BY key");q.text(1,workspace);
        while(q.row()) {
            const auto wanted=expected.find(q.text(0));
            require(wanted!=expected.end() && wanted->second==q.blob(2),"Configured state mapping contains an undeclared or substituted contract");
            Query unresolved(db,"SELECT count(*) FROM configured_state_uses WHERE workspace=? AND key=? AND state!='ready'");
            unresolved.text(1,workspace);unresolved.text(2,q.text(0));require(unresolved.row(),"Cannot inspect configured state mapping effects");
            if(q.text(3)!="ready" || unresolved.number(0)!=0)throw Error(pb::StorageOwnerError::UNCERTAIN,"Configured state mapping has an unresolved allocation");
            auto* entry=mapping.add_entries();entry->set_key(q.text(0));
            require(entry->mutable_contract()->ParseFromString(q.blob(2)),"Configured state mapping contract is malformed");
            auto* object=entry->mutable_state_object();object->set_kind(pb::StorageObject::APPLICATION_STATE);object->set_id(q.text(1));
            require(registered_workspace(this->object(*object).group)==workspace,"Configured state object belongs to another workspace");
            (void)inspect(*object);expected.erase(wanted);
        }
        require(expected.empty(),"Configured state mapping is missing an actual application state object");
        copy_collections::mapping(mapping);return mapping;
    }
    pb::StorageObjectState configured_state_prepare(const pb::StorageApplicationPrepare& request,
            std::uint64_t group,std::uint64_t maximum,const pb::StorageConfiguredApplicationReply& configured) {
        const auto& key=request.configured_application_key();const auto contract=configured.definition().state_contract().SerializeAsString();
        if(!configured.has_state()) {
            {Transaction tx(db);const auto revision=next_state_mapping_revision(request.workspace());
             Query intent(db,"INSERT INTO configured_states VALUES(?,?,?,?,?,?,'pending')");
             intent.text(1,request.workspace());intent.text(2,key);intent.text(3,request.object().id());intent.blob(4,contract);
             intent.number(5,revision);intent.blob(6,request.SerializeAsString());intent.row();tx.commit();}
            try {
                const auto receipt=allocate(request.object(),group,maximum,request.SerializeAsString());
                Query done(db,"UPDATE configured_states SET state='ready' WHERE workspace=? AND key=? AND object=? AND state='pending'");
                done.text(1,request.workspace());done.text(2,key);done.text(3,request.object().id());done.row();
                require(sqlite3_changes(db)==1,"Configured state allocation lost its exact intent");return receipt;
            } catch(...) {
                Query uncertain(db,"UPDATE configured_states SET state='uncertain' WHERE workspace=? AND key=?");
                uncertain.text(1,request.workspace());uncertain.text(2,key);uncertain.row();throw;
            }
        }
        require(configured.state().object().SerializeAsString()==request.object().SerializeAsString(),"Configured application state identity changed");
        const auto previous=object(request.object());
        if(previous.group==group && previous.maximum==maximum)return inspect(request.object());
        const auto digest=sha256(request.SerializeAsString());
        {Query old(db,"SELECT state FROM configured_state_uses WHERE workspace=? AND key=? AND request_digest=?");
         old.text(1,request.workspace());old.text(2,key);old.blob(3,digest);
         if(old.row())throw Error(pb::StorageOwnerError::UNCERTAIN,"Configured state quota transfer needs exact recovery");}
        {Transaction tx(db);Query intent(db,"INSERT INTO configured_state_uses(workspace,key,request_digest,old_group,new_group,maximum,state) VALUES(?,?,?,?,?,?,'pending')");
         intent.text(1,request.workspace());intent.text(2,key);intent.blob(3,digest);intent.number(4,previous.group);
         intent.number(5,group);intent.number(6,maximum);intent.row();
         Query mark(db,"UPDATE configured_states SET state='pending' WHERE workspace=? AND key=? AND state='ready'");
         mark.text(1,request.workspace());mark.text(2,key);mark.row();require(sqlite3_changes(db)==1,"Configured state is already in use by another effect");tx.commit();}
        try {
            limit(root.value,previous.subvolume,maximum);
            if(previous.group!=group) {
                assign(root.value,previous.subvolume,group);
                btrfs_ioctl_qgroup_assign_args detach{};detach.assign=0;detach.src=previous.subvolume;detach.dst=previous.group;
                check(ioctl(root.value,BTRFS_IOC_QGROUP_ASSIGN,&detach)==0,"Cannot detach previous configured state quota");
            }
            sync(root.value);
            {Transaction tx(db);Query object(db,"UPDATE objects SET domain=?,maximum=?,payload=? WHERE kind=? AND id=? AND domain=?");
             object.number(1,group);object.number(2,maximum);object.blob(3,request.SerializeAsString());object.number(4,pb::StorageObject::APPLICATION_STATE);
             object.text(5,request.object().id());object.number(6,previous.group);object.row();require(sqlite3_changes(db)==1,"Configured state quota receipt lost its object");
             const auto revision=next_state_mapping_revision(request.workspace());Query ready(db,"UPDATE configured_states SET state='ready',revision=? WHERE workspace=? AND key=?");
             ready.number(1,revision);ready.text(2,request.workspace());ready.text(3,key);ready.row();tx.commit();}
            const auto receipt=inspect(request.object());Query done(db,"UPDATE configured_state_uses SET state='ready',receipt=? WHERE workspace=? AND key=? AND request_digest=?");
            done.blob(1,receipt.SerializeAsString());done.text(2,request.workspace());done.text(3,key);done.blob(4,digest);done.row();return receipt;
        } catch(...) {
            Query uncertain(db,"UPDATE configured_states SET state='uncertain' WHERE workspace=? AND key=?");uncertain.text(1,request.workspace());uncertain.text(2,key);uncertain.row();throw;
        }
    }
    pb::StorageObjectState application(const pb::StorageApplicationPrepare& request,bool configured_batch=false) {
        require(request.configured_application_key().empty() || configured_batch,"Configured state requires a whole runtime/persistent allocation batch");
        require(identifier(request.workspace()) && request.has_object() && request.object().kind() == pb::StorageObject::APPLICATION_STATE && identifier(request.profile()), "Incomplete application storage request");
        Query copied(db,"SELECT intent FROM copy_destinations WHERE state=2");
        while(copied.row()) {
            pb::StorageCopyDestinationPrepare allocation;require(allocation.ParseFromString(copied.blob(0)),"Stored copy policy is malformed");
            if(allocation.workspace_id()==request.workspace()) {
                const auto& profile=copy_profile(allocation);
                if(profile.application_profile()!=request.profile())throw Error(pb::StorageOwnerError::UNAUTHORIZED,"Copied workspace application profile is not assigned");
            }
        }
        std::optional<pb::StorageConfiguredApplicationReply> configured;
        if(!request.configured_application_key().empty()) {
            pb::StorageConfiguredApplicationQuery query;query.set_workspace_id(request.workspace());query.set_key(request.configured_application_key());
            configured=configured_application(query);
            require(request.expected_catalog_revision()==configured->catalog_revision() &&
                    request.expected_state_mapping_revision()==configured->state_mapping_revision() &&
                    request.profile()==configured->definition().profile(),"Configured application policy or mapping changed before preparation");
        } else require(request.expected_catalog_revision()==0 && request.expected_state_mapping_revision()==0,"Unconfigured application supplied a mapping revision");
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
        if(configured)return configured_state_prepare(request,actor_group,maximum,*configured);
        return allocate(request.object(), actor_group, maximum, request.SerializeAsString());
    }

    pb::StorageConfiguredApplicationAllocation configured_application_prepare(const pb::StorageConfiguredApplicationPrepare& request) {
        require(request.has_persistent() && request.has_runtime_object() &&
                request.runtime_object().kind()==pb::StorageObject::APPLICATION_STATE &&
                request.persistent().object().kind()==pb::StorageObject::APPLICATION_STATE &&
                request.runtime_object().id()!=request.persistent().object().id() &&
                copy_collections::key(request.persistent().configured_application_key()),"Incomplete configured runtime/persistent allocation");
        (void)name(request.runtime_object());(void)name(request.persistent().object());
        const auto exact=request.SerializeAsString();
        {Query previous(db,"SELECT intent,state,receipt FROM configured_launch_allocations WHERE runtime=?");
         previous.text(1,request.runtime_object().id());
         if(previous.row()) {
             if(previous.blob(0)!=exact)throw Error(pb::StorageOwnerError::CONFLICT,"Configured runtime identity binds another allocation");
             if(previous.text(1)!="ready")throw Error(pb::StorageOwnerError::UNCERTAIN,"Configured runtime allocation needs exact recovery");
             pb::StorageConfiguredApplicationAllocation receipt;require(receipt.ParseFromString(previous.blob(2)),"Stored configured allocation receipt is malformed");return receipt;
         }}
        const auto& persistent=request.persistent();
        pb::StorageConfiguredApplicationQuery query;query.set_workspace_id(persistent.workspace());query.set_key(persistent.configured_application_key());
        const auto configured=configured_application(query);
        require(configured.catalog_revision()==persistent.expected_catalog_revision() &&
                configured.state_mapping_revision()==persistent.expected_state_mapping_revision() &&
                configured.definition().profile()==persistent.profile(),"Configured allocation mapping/profile changed");
        const auto& policy=selected(persistent.workspace());const auto actor=subject(persistent);
        std::uint64_t actor_maximum=std::min(policy.total_bytes(),policy.actor_state_default_bytes()),object_maximum=0;
        for(const auto& selected:policy.actors())if(subject(selected)==actor)actor_maximum=std::min(policy.total_bytes(),selected.state_bytes());
        for(const auto& profile:policy.profiles())if(profile.name()==persistent.profile())object_maximum=std::min(actor_maximum,profile.state_bytes());
        require(object_maximum>0 && object_maximum==persistent.expected_state_maximum_bytes() &&
                persistent.expected_storage_policy_digest()==sha256(policy.SerializeAsString()),
                "Configured allocation differs from its pinned independent storage policy/ceiling");
        const auto reservation=object_maximum*(configured.has_state()?1ULL:2ULL);
        Query workspace(db,"SELECT id FROM domains WHERE workspace=?");workspace.text(1,persistent.workspace());
        require(workspace.row(),"Configured allocation requires bound workspace storage");
        const auto workspace_group=workspace.number(0);const auto workspace_limit=checked_group(workspace_group);
        auto workspace_usage=item<btrfs_qgroup_info_item>(root.value,0,BTRFS_QGROUP_INFO_KEY,workspace_group);
        require(workspace_usage.has_value(),"Cannot inspect configured workspace reservation");
        if(le64toh(workspace_usage->rfer)>workspace_limit || reservation>workspace_limit-le64toh(workspace_usage->rfer))
            throw Error(pb::StorageOwnerError::EXHAUSTED,"Whole runtime/persistent reservation exceeds workspace capacity");
        Query actor_group(db,"SELECT id FROM actors WHERE workspace=? AND subject=?");actor_group.text(1,persistent.workspace());actor_group.blob(2,actor);
        std::uint64_t actor_used=0;
        if(actor_group.row()) {const auto usage=item<btrfs_qgroup_info_item>(root.value,0,BTRFS_QGROUP_INFO_KEY,actor_group.number(0));
            require(usage.has_value(),"Cannot inspect configured actor reservation");actor_used=le64toh(usage->rfer);}
        if(actor_used>actor_maximum || reservation>actor_maximum-actor_used)
            throw Error(pb::StorageOwnerError::EXHAUSTED,"Whole runtime/persistent reservation exceeds actor capacity");
        {Query intent(db,"INSERT INTO configured_launch_allocations(runtime,intent,state) VALUES(?,?,'pending')");
         intent.text(1,request.runtime_object().id());intent.blob(2,exact);intent.row();}
        try {
            pb::StorageConfiguredApplicationAllocation receipt;
            *receipt.mutable_persistent()=application(persistent,true);
            pb::StorageApplicationPrepare runtime=persistent;*runtime.mutable_object()=request.runtime_object();
            runtime.clear_configured_application_key();runtime.clear_expected_catalog_revision();runtime.clear_expected_state_mapping_revision();
            runtime.clear_expected_state_maximum_bytes();runtime.clear_expected_storage_policy_digest();
            *receipt.mutable_runtime()=application(runtime);
            receipt.set_state_mapping_revision(state_mapping_revision(persistent.workspace()));receipt.set_request_digest(sha256(exact));
            Query done(db,"UPDATE configured_launch_allocations SET state='ready',receipt=? WHERE runtime=? AND intent=? AND state='pending'");
            done.blob(1,receipt.SerializeAsString());done.text(2,request.runtime_object().id());done.blob(3,exact);done.row();
            require(sqlite3_changes(db)==1,"Configured allocation lost its exact durable intent");return receipt;
        } catch(...) {
            Query uncertain(db,"UPDATE configured_launch_allocations SET state='uncertain' WHERE runtime=?");uncertain.text(1,request.runtime_object().id());uncertain.row();throw;
        }
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
            if(!(q.blob(0)==r.request_digest() && q.text(1)==r.snapshot_object().id() && q.number(2)==static_cast<std::uint64_t>(r.destination_object().kind()) &&
                q.text(3)==r.destination_object().id() && q.number(4)==source.subvolume_id() && q.number(5)==dest.subvolume_id() &&
                q.number(6)==r.maximum_bytes() && q.number(7)==r.maximum_entries()))
                throw Error(pb::StorageOwnerError::CONFLICT,"Copy stage operation conflict");
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
    std::string registered_workspace(std::uint64_t group) {
        Query q(db,"SELECT workspace FROM domains WHERE id=? UNION ALL SELECT workspace FROM actors WHERE id=?");
        q.number(1,group);q.number(2,group);
        require(q.row(),"Registered storage object has no workspace ownership");
        const auto result=q.text(0);require(!result.empty() && !q.row(),"Storage ownership is ambiguous");return result;
    }
    void register_typed_exports() {
        // Validate every reservation before the first object allocation.
        std::map<std::uint64_t,std::uint64_t> charges;
        for(const auto& entry:config.copy_catalog().entries()) {
            if(entry.typed_export().empty())continue;
            pb::StorageObject repository;
            for(const auto& candidate:config.copy_catalog().entries())
                if(candidate.workspace_id()==entry.workspace_id() && candidate.data_class()==workspace::copy::v1::GIT_HISTORY)
                    repository=candidate.source_object();
            require(repository.kind()==pb::StorageObject::REPOSITORY,"Typed export registration requires the actual registered repository");
            const auto group=domain(repository);
            require(registered_workspace(group)==entry.workspace_id(),"Typed export registration changes workspace ownership");
            Query old(db,"SELECT state FROM objects WHERE kind=? AND id=?");old.number(1,entry.source_object().kind());old.text(2,entry.source_object().id());
            if(!old.row())charges[group]+=snapshot_min_charge+entry.typed_export().size();
        }
        for(const auto& [group,charge]:charges) {
            const auto maximum=checked_group(group);
            const auto used=item<btrfs_qgroup_info_item>(root.value,0,BTRFS_QGROUP_INFO_KEY,group);
            require(used.has_value(),"Typed export reservation cannot inspect workspace usage");
            if(le64toh(used->rfer)>maximum || charge>maximum-le64toh(used->rfer))
                throw Error(pb::StorageOwnerError::EXHAUSTED,"Typed export registration exceeds the whole workspace reservation");
        }
        for(const auto& entry:config.copy_catalog().entries()) {
            if(entry.typed_export().empty())continue;
            const auto revision=config.copy_catalog().revision();const auto payload=entry.SerializeAsString();
            {Query previous(db,"SELECT payload FROM copy_exports WHERE workspace=? AND data_class=? AND revision=?");
             previous.text(1,entry.workspace_id());previous.number(2,entry.data_class());previous.number(3,revision);
             if(previous.row())require(previous.blob(0)==payload,"Typed export revision binds different registration bytes");
             else {Query intent(db,"INSERT INTO copy_exports(workspace,data_class,revision,object,payload,state) VALUES(?,?,?,?,?,'pending')");
                 intent.text(1,entry.workspace_id());intent.number(2,entry.data_class());intent.number(3,revision);
                 intent.text(4,entry.source_object().id());intent.blob(5,payload);intent.row();}}
            pb::StorageCopyDomainPrepare request;request.set_workspace_id(entry.workspace_id());*request.mutable_object()=entry.source_object();
            for(const auto& candidate:config.copy_catalog().entries())
                if(candidate.workspace_id()==entry.workspace_id() && candidate.data_class()==workspace::copy::v1::GIT_HISTORY)
                    *request.mutable_repository()=candidate.source_object();
            const auto allocated=copy_domain_prepare(request);auto directory=source_directory(entry.source_object());
            Fd output(openat(directory.value,"export.pb",O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));
            if(output.value>=0) {
                const auto& bytes=entry.typed_export();std::size_t offset=0;
                while(offset<bytes.size()) {const auto n=write(output.value,bytes.data()+offset,bytes.size()-offset);
                    if(n<0 && errno==EINTR){continue;}
                    check(n>0,"Cannot write typed export registration");offset+=static_cast<std::size_t>(n);}
                check(fchown(output.value,config.broker_uid(),static_cast<gid_t>(-1))==0 && fsync(output.value)==0 &&
                      fsync(directory.value)==0,"Cannot persist typed export registration");
            } else require(errno==EEXIST,"Cannot create typed export registration");
            Fd input(openat(directory.value,"export.pb",O_RDONLY|O_NOFOLLOW|O_CLOEXEC));struct stat record{};
            require(input.value>=0 && fstat(input.value,&record)==0 && S_ISREG(record.st_mode) && record.st_nlink==1 &&
                    record.st_size==static_cast<off_t>(entry.typed_export().size()),"Typed registration file differs from its intent");
            std::string bytes(entry.typed_export().size(),'\0');std::size_t offset=0;
            while(offset<bytes.size()){const auto n=pread(input.value,bytes.data()+offset,bytes.size()-offset,offset);
                if(n<0 && errno==EINTR){continue;}
                require(n>0,"Cannot inspect typed export registration");offset+=static_cast<std::size_t>(n);}
            require(bytes==entry.typed_export(),"Typed registration content differs; allocate a fresh export object for changed content");
            Query done(db,"UPDATE copy_exports SET state='ready',receipt=? WHERE workspace=? AND data_class=? AND revision=? AND payload=?");
            done.blob(1,allocated.SerializeAsString());done.text(2,entry.workspace_id());done.number(3,entry.data_class());
            done.number(4,revision);done.blob(5,payload);done.row();require(sqlite3_changes(db)==1,"Typed registration receipt lost its exact intent");
        }
    }
    pb::StorageObjectState copy_domain_prepare(const pb::StorageCopyDomainPrepare& request) {
        require(identifier(request.workspace_id()) && request.has_repository() && request.has_object() &&
                (request.object().kind()==pb::StorageObject::APPLICATION_DEFINITION ||
                 request.object().kind()==pb::StorageObject::APPLICATION_STATE ||
                 request.object().kind()==pb::StorageObject::ENVIRONMENT_CONFIG),"Unsupported registered export domain");
        const auto group=domain(request.repository());
        require(registered_workspace(group)==request.workspace_id(),"Export domain belongs to another workspace");
        return allocate(request.object(),group,0,request.SerializeAsString());
    }
    const pb::WorkspaceCopyDestinationProfile& copy_profile(const pb::StorageCopyDestinationPrepare& request) {
        require(config.has_copy_catalog() && config.copy_catalog().revision()==request.catalog_revision(),"Copy catalog changed");
        for(const auto& profile:config.copy_destination_profiles())
            if(profile.name()==request.profile_name() && profile.revision()==request.profile_revision())return profile;
        throw Error(pb::StorageOwnerError::CONFLICT,"Copy destination profile changed");
    }
    std::uint64_t copy_state_maximum(const pb::StorageCopyDestinationPrepare& request) {
        const auto& policy=copy_profile(request).storage_policy();
        for(const auto& profile:policy.profiles())if(profile.name()==copy_profile(request).application_profile())
            return std::min(policy.total_bytes(),profile.state_bytes());
        throw Error(pb::StorageOwnerError::UNAVAILABLE,"Independent destination state quota is absent");
    }
    void validate_copy_allocation(const pb::StorageCopyDestinationPrepare& request) {
        require(request.operation_id().size()==16 && request.request_digest().size()==32 &&
                request.workspace_id().size()==32 && request.workspace_id().find_first_not_of("0123456789abcdef")==std::string::npos &&
                request.domains_size()>=2 && request.domains_size()<=5,"Invalid copy allocation identity");
        const auto& profile=copy_profile(request);
        std::set<int> classes;std::set<std::string> ids;
        for(const auto& selected:request.domains()) {
            const auto data_class=selected.data_class();
            require(data_class>=workspace::copy::v1::PROJECT_TREE && data_class<=workspace::copy::v1::ENVIRONMENT_CONFIG &&
                    classes.insert(data_class).second,"Unknown or duplicate copy allocation class");
            bool allowed=false;for(const auto value:profile.allowed_classes())if(value==data_class)allowed=true;
            require(allowed,"Destination profile denies a selected copy class");
            const auto expected=data_class==workspace::copy::v1::PROJECT_TREE?pb::StorageObject::PROJECT:
                data_class==workspace::copy::v1::GIT_HISTORY?pb::StorageObject::REPOSITORY:
                data_class==workspace::copy::v1::APPLICATION_DEFINITION?pb::StorageObject::APPLICATION_DEFINITION:
                data_class==workspace::copy::v1::APPLICATION_STATE?pb::StorageObject::APPLICATION_STATE:pb::StorageObject::ENVIRONMENT_CONFIG;
            require(selected.staging().kind()==pb::StorageObject::COPY_STAGING && selected.destination().kind()==expected &&
                    selected.snapshot().kind()==pb::StorageObject::SNAPSHOT,"Copy allocation class/kind mismatch");
            for(const auto* object:{&selected.staging(),&selected.destination(),&selected.snapshot()}) {
                (void)name(*object);require(ids.insert(object->id()).second,"Copy allocation reuses storage identities");
            }
        }
        require(classes.contains(workspace::copy::v1::PROJECT_TREE) && classes.contains(workspace::copy::v1::GIT_HISTORY),
                "Copy allocation requires project and Git history");
        require(request.state_companions_size()<=32,"Copy allocation exceeds companion bound");
        if(request.state_companions_size()==0) {
            require(request.state_mapping_revision()==0,"Empty state allocation supplied a mapping revision");
        } else {
            require(classes.contains(workspace::copy::v1::APPLICATION_STATE) && identifier(request.source_workspace_id()) &&
                    request.state_mapping_revision()>0 && state_mapping_revision(request.source_workspace_id())==request.state_mapping_revision(),
                    "Copy allocation state mapping revision changed");
            const auto state_catalog=resolved_copy_entry(request.source_workspace_id(),workspace::copy::v1::APPLICATION_STATE);
            require(state_catalog.schema_version()==2,"Companion allocation lacks its registered state collection");
            pb::CopyApplicationStateCollection descriptor;require(descriptor.ParseFromString(state_catalog.typed_export()),"Malformed companion descriptor");
            const auto mapping=configured_state_mapping(request.source_workspace_id(),descriptor);
            require(mapping.entries_size()==request.state_companions_size(),"Companion allocation omitted or added configured states");
            for(int index=0;index<request.state_companions_size();++index) {
                const auto& plan=request.state_companions(index);
                require(plan.source().SerializeAsString()==mapping.entries(index).SerializeAsString() &&
                        plan.snapshot().kind()==pb::StorageObject::SNAPSHOT && plan.staging().kind()==pb::StorageObject::COPY_STAGING &&
                        plan.destination().kind()==pb::StorageObject::APPLICATION_STATE,"Companion source/contract or destination kind differs");
                for(const auto* object:{&plan.snapshot(),&plan.staging(),&plan.destination()}) {
                    (void)name(*object);require(ids.insert(object->id()).second,"Companion allocation reuses an object identity");
                    require(object->id()!=plan.source().state_object().id(),"Companion allocation reuses source state identity");
                }
                const auto snapshot=snapshot_inspect(plan.snapshot());
                require(snapshot.source_object().SerializeAsString()==plan.source().state_object().SerializeAsString() && snapshot.read_only(),
                        "Companion snapshot belongs to another state object");
            }
            const auto maximum=copy_state_maximum(request);
            require(maximum>0 && maximum<=profile.storage_policy().total_bytes() &&
                    static_cast<std::uint64_t>(request.state_companions_size())<=profile.storage_policy().total_bytes()/maximum,
                    "Independent destination companion quotas exceed whole workspace policy");
        }
        const auto objects=static_cast<std::uint64_t>(request.domains_size()+request.state_companions_size());
        require(objects<=37 && objects<=profile.storage_policy().total_bytes()/snapshot_min_charge,
                "Whole destination object reservation exceeds its independent storage policy");
    }
    pb::StorageCopyDestinationReceipt destination_receipt(const pb::StorageCopyDestinationPrepare& intent,int state) {
        pb::StorageCopyDestinationReceipt result;*result.mutable_allocation()=intent;
        result.set_allocation_digest(sha256(intent.SerializeAsString()));
        result.set_state(static_cast<pb::StorageCopyDestinationReceipt::State>(state));
        if(state==pb::StorageCopyDestinationReceipt::UNCERTAIN)return result;
        if(state==pb::StorageCopyDestinationReceipt::PUBLISHED) {
            Query published(db,"SELECT publication,state,receipt FROM copy_destinations WHERE operation_id=?");published.blob(1,intent.operation_id());
            pb::StorageCopyDestinationPublish publication;
            require(published.row() && publication.ParseFromString(published.blob(0)) && publication.has_validation() &&
                    publication.allocation().SerializeAsString()==intent.SerializeAsString(),
                    "Published destination omitted its exact allocation/final validation binding");
            if(published.number(1)==pb::StorageCopyDestinationReceipt::PUBLISHED) {
                pb::StorageCopyDestinationReceipt stored;
                require(stored.ParseFromString(published.blob(2)) && stored.state()==pb::StorageCopyDestinationReceipt::PUBLISHED &&
                        stored.allocation().SerializeAsString()==intent.SerializeAsString() &&
                        stored.allocation_digest()==sha256(intent.SerializeAsString()) &&
                        stored.publication_digest()==sha256(publication.SerializeAsString()) &&
                        stored.validation_digest()==sha256(publication.validation().SerializeAsString()),
                        "Durable publication receipt differs from its exact immutable allocation/validation");
                validate_message(stored);return stored;
            }
            require(published.number(1)==4,"Cannot construct a publication receipt outside its durable publication intent");
            result.set_publication_digest(sha256(publication.SerializeAsString()));result.set_validation_digest(sha256(publication.validation().SerializeAsString()));
            if(intent.state_companions_size()>0)result.set_destination_state_mapping_revision(state_mapping_revision(intent.workspace_id()));
        }
        for(const auto& domain:intent.domains()) {
            auto object=inspect(state==pb::StorageCopyDestinationReceipt::PUBLISHED?domain.destination():domain.staging());
            object.set_operation_digest(state==pb::StorageCopyDestinationReceipt::PUBLISHED?result.publication_digest():result.allocation_digest());*result.add_objects()=object;
        }
        for(const auto& companion:intent.state_companions()) {
            auto object=inspect(state==pb::StorageCopyDestinationReceipt::PUBLISHED?companion.destination():companion.staging());
            object.set_operation_digest(state==pb::StorageCopyDestinationReceipt::PUBLISHED?result.publication_digest():result.allocation_digest());
            *result.add_state_companions()=object;
        }
        return result;
    }
    pb::StorageCopyDestinationReceipt copy_destination_inspect(const pb::StorageCopyDestinationInspect& request) {
        require(request.operation_id().size()==16 && request.request_digest().size()==32,"Malformed copy destination inspect");
        Query old(db,"SELECT intent,state FROM copy_destinations WHERE operation_id=?");old.blob(1,request.operation_id());
        if(!old.row())throw Error(pb::StorageOwnerError::UNAVAILABLE,"Copy destination has no exact owner intent");
        pb::StorageCopyDestinationPrepare intent;
        require(intent.ParseFromString(old.blob(0)) && intent.request_digest()==request.request_digest(),"Copy destination inspection binding differs");
        return destination_receipt(intent,static_cast<int>(old.number(1)));
    }
    pb::StorageCopyDestinationReceipt copy_destination_prepare(const pb::StorageCopyDestinationPrepare& request) {
        require(request.operation_id().size()==16 && request.request_digest().size()==32,"Malformed destination allocation identity");
        std::optional<int> existing_state;
        {Query old(db,"SELECT intent,state FROM copy_destinations WHERE operation_id=?");old.blob(1,request.operation_id());
         if(old.row()) {
             if(old.blob(0)!=request.SerializeAsString())throw Error(pb::StorageOwnerError::CONFLICT,"Copy destination ID binds different bytes");
             existing_state=static_cast<int>(old.number(1));
         }}
        if(existing_state==pb::StorageCopyDestinationReceipt::PUBLISHED)
            return destination_receipt(request,pb::StorageCopyDestinationReceipt::PUBLISHED);
        validate_copy_allocation(request);
        if(existing_state)return destination_receipt(request,*existing_state);
        const auto preflight_identity=[&](const pb::StorageObject& object) {
            Query existing(db,"SELECT count(*) FROM objects WHERE id=?");existing.text(1,object.id());
            require(existing.row() && existing.number(0)==0,"Fresh copy staging/destination identity is already registered");
        };
        for(const auto& domain:request.domains()){preflight_identity(domain.staging());preflight_identity(domain.destination());}
        for(const auto& companion:request.state_companions()){preflight_identity(companion.staging());preflight_identity(companion.destination());}
        const auto& profile=copy_profile(request);std::string repository;
        for(const auto& domain:request.domains())if(domain.data_class()==workspace::copy::v1::GIT_HISTORY)repository=domain.destination().id();
        std::uint64_t group=0;
        {
            Transaction tx(db);
            Query usage(db,"SELECT coalesce(sum(maximum),0) FROM domains");require(usage.row(),"Cannot inspect copy reservation");
            const auto used=usage.number(0),maximum=profile.storage_policy().total_bytes();
            if(used>capacity || maximum>capacity-used)throw Error(pb::StorageOwnerError::EXHAUSTED,"Copy destination capacity is unavailable");
            Query owner(db,"SELECT count(*) FROM domains WHERE repo=? OR workspace=?");owner.text(1,repository);owner.text(2,request.workspace_id());
            require(owner.row() && owner.number(0)==0,"Copy destination identity already exists");
            Query insert(db,"INSERT INTO copy_destinations(operation_id,intent,state) VALUES(?,?,0)");
            insert.blob(1,request.operation_id());insert.blob(2,request.SerializeAsString());insert.row();
            group=next(2);Query domain(db,"INSERT INTO domains VALUES(?,?,?,?)");domain.number(1,group);domain.text(2,repository);
            domain.number(3,maximum);domain.text(4,request.workspace_id());domain.row();tx.commit();
        }
        try {
            create_group(root.value,group);limit(root.value,group,profile.storage_policy().total_bytes());assign(root.value,group,pool);
            for(const auto& domain:request.domains())allocate(domain.staging(),group,0,request.SerializeAsString()+domain.staging().SerializeAsString());
            for(const auto& companion:request.state_companions())
                allocate(companion.staging(),group,copy_state_maximum(request),request.SerializeAsString()+companion.staging().SerializeAsString());
            const auto receipt=destination_receipt(request,pb::StorageCopyDestinationReceipt::ALLOCATED);
            Query done(db,"UPDATE copy_destinations SET state=1,receipt=? WHERE operation_id=? AND state=0");
            done.blob(1,receipt.SerializeAsString());done.blob(2,request.operation_id());done.row();return receipt;
        }catch(...) {Query uncertain(db,"UPDATE copy_destinations SET state=3 WHERE operation_id=?");uncertain.blob(1,request.operation_id());uncertain.row();throw;}
    }
    pb::StorageCopyDestinationReceipt copy_destination_publish(const pb::StorageCopyDestinationPublish& request) {
        const auto& intent=request.allocation();
        require(intent.operation_id().size()==16 && intent.request_digest().size()==32,"Malformed publication identity");
        {
            Query old(db,"SELECT intent,state,publication FROM copy_destinations WHERE operation_id=?");old.blob(1,intent.operation_id());
            if(!old.row())throw Error(pb::StorageOwnerError::UNAVAILABLE,"Publication requires its exact owner allocation");
            if(old.blob(0)!=intent.SerializeAsString())throw Error(pb::StorageOwnerError::CONFLICT,"Publication allocation bytes changed");
            if(old.number(1)==pb::StorageCopyDestinationReceipt::PUBLISHED) {
                if(old.blob(2)!=request.SerializeAsString())throw Error(pb::StorageOwnerError::CONFLICT,"Publication retry changed its exact final validation bytes");
                return destination_receipt(intent,pb::StorageCopyDestinationReceipt::PUBLISHED);
            }
            if(old.number(1)!=pb::StorageCopyDestinationReceipt::ALLOCATED)
                throw Error(pb::StorageOwnerError::UNCERTAIN,"Publication outcome requires inspection");
        }
        validate_copy_allocation(intent);
        require(request.has_validation() && request.validation().domains_size()==intent.domains_size(),"Publication requires exact post-rewrite destination validation");
        std::set<int> expected,validated;std::uint64_t bytes=0;std::uint32_t entries=0;
        for(const auto& domain:intent.domains())expected.insert(domain.data_class());
        for(const auto& domain:request.validation().domains()) {
            require(validated.insert(domain.data_class()).second && domain.content_digest().size()==32 &&
                    domain.actual_bytes()<=copy_profile(intent).maximum_total_bytes()-bytes &&
                    domain.actual_entries()<=copy_profile(intent).maximum_entries()-entries,"Final validation exceeds destination ceilings");
            bytes+=domain.actual_bytes();entries+=domain.actual_entries();
        }
        require(expected==validated && !request.validation().head().empty() &&
                ((request.validation().head_kind()==workspace::copyworker::v1::SYMBOLIC_BRANCH && !request.validation().branch().empty()) ||
                 (request.validation().head_kind()==workspace::copyworker::v1::DETACHED_COMMIT && request.validation().branch().empty())),
                "Publication final validation classes or HEAD kind differ");
        require(request.validation().state_companions_size()==intent.state_companions_size(),"Publication companion count differs");
        std::set<std::string> state_keys;
        for(int index=0;index<intent.state_companions_size();++index) {
            const auto& plan=intent.state_companions(index);const auto& validation=request.validation().state_companions(index);
            require(state_keys.insert(validation.key()).second && validation.key()==plan.source().key() &&
                    validation.contract_digest()==sha256(plan.source().contract().SerializeAsString()) &&
                    validation.source_storage_object()==plan.source().state_object().SerializeAsString() &&
                    validation.snapshot_storage_object()==plan.snapshot().SerializeAsString() &&
                    validation.destination_storage_object()==plan.destination().SerializeAsString() &&
                    validation.content_digest().size()==32 && validation.actual_bytes()<=copy_state_maximum(intent) &&
                    validation.actual_bytes()<=copy_profile(intent).maximum_total_bytes()-bytes &&
                    validation.actual_entries()<=copy_profile(intent).maximum_entries()-entries,
                    "Publication companion source/contract/snapshot/destination evidence differs");
            bytes+=validation.actual_bytes();entries+=validation.actual_entries();
            Query sealed(db,"SELECT snapshot_id,actual_bytes,actual_entries,content_digest,source_subvolume,destination_subvolume FROM copy_stages WHERE destination_kind=? AND destination_id=? AND state=3");
            sealed.number(1,pb::StorageObject::COPY_STAGING);sealed.text(2,plan.staging().id());
            require(sealed.row() && sealed.text(0)==plan.snapshot().id() && sealed.number(1)==validation.actual_bytes() &&
                    sealed.number(2)==validation.actual_entries() && sealed.blob(3)==validation.content_digest(),
                    "Publication companion does not match its exact sealed export receipt");
            const auto source=snapshot_inspect(plan.snapshot()),destination=inspect(plan.staging());
            require(source.subvolume_id()==sealed.number(4) && destination.subvolume_id()==sealed.number(5) &&
                    destination.maximum_bytes()<=copy_state_maximum(intent),"Publication companion physical roots or independent quota changed");
            require(!sealed.row(),"Companion staging has multiple sealed receipts");
        }
        // Require every source stage's exact sealed receipt before any publication rename.
        for(const auto& domain:intent.domains()) {
            Query sealed(db,"SELECT count(*) FROM copy_stages WHERE destination_id=? AND destination_kind=? AND state=3");
            sealed.text(1,domain.staging().id());sealed.number(2,pb::StorageObject::COPY_STAGING);
            require(sealed.row() && sealed.number(0)==1,"Publication requires one sealed receipt for every domain");
        }
        {Query mark(db,"UPDATE copy_destinations SET state=4,publication=? WHERE operation_id=? AND state=1");mark.blob(1,request.SerializeAsString());mark.blob(2,intent.operation_id());mark.row();}
        try {
            const auto publish_object=[&](const pb::StorageObject& staging,const pb::StorageObject& destination) {
                const auto registered=object(staging);
                Fd from(openat(root.value,"staging",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
                Fd to(openat(root.value,collection(destination.kind()).c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
                check(from.value>=0 && to.value>=0,"Cannot pin copy publication collections");
                // RENAME_NOREPLACE prevents an unregistered path from being overwritten.
                check(renameat2(from.value,name(staging).c_str(),to.value,name(destination).c_str(),RENAME_NOREPLACE)==0,
                      "Copy destination publication rename failed");
                sync(root.value);
                Transaction tx(db);
                Query add(db,"INSERT INTO objects VALUES(?,?,?,?,?,?,'ready')");
                add.number(1,destination.kind());add.text(2,destination.id());add.number(3,registered.group);
                add.number(4,registered.subvolume);add.number(5,registered.maximum);add.blob(6,intent.SerializeAsString());add.row();
                Query retire(db,"UPDATE objects SET state='published' WHERE kind=? AND id=?");
                retire.number(1,pb::StorageObject::COPY_STAGING);retire.text(2,staging.id());retire.row();tx.commit();
            };
            for(const auto& domain:intent.domains())publish_object(domain.staging(),domain.destination());
            for(const auto& companion:intent.state_companions())publish_object(companion.staging(),companion.destination());
            Transaction publication(db);
            if(intent.state_companions_size()>0) {
                require(state_mapping_revision(intent.workspace_id())==0,"Destination already has configured state authority");
                const auto revision=next_state_mapping_revision(intent.workspace_id());
                for(const auto& companion:intent.state_companions()) {
                    Query state(db,"INSERT INTO configured_states VALUES(?,?,?,?,?,?,'ready')");
                    state.text(1,intent.workspace_id());state.text(2,companion.source().key());state.text(3,companion.destination().id());
                    state.blob(4,companion.source().contract().SerializeAsString());state.number(5,revision);
                    state.blob(6,request.SerializeAsString());state.row();
                }
            }
            const auto receipt=destination_receipt(intent,pb::StorageCopyDestinationReceipt::PUBLISHED);
            Query done(db,"UPDATE copy_destinations SET state=2,receipt=? WHERE operation_id=? AND state=4");
            done.blob(1,receipt.SerializeAsString());done.blob(2,intent.operation_id());done.row();
            require(sqlite3_changes(db)==1,"Publication final receipt lost its durable intent");
            publication.commit();return receipt;
        } catch(...) {Query uncertain(db,"UPDATE copy_destinations SET state=3 WHERE operation_id=?");uncertain.blob(1,intent.operation_id());uncertain.row();throw;}
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
            const auto resolved=resolved_copy_entry(request.workspace_id(),selected);const auto* match=&resolved;
            const auto registered=object(match->source_object());
            if (registered_workspace(registered.group)!=request.workspace_id())
                throw Error(pb::StorageOwnerError::UNAVAILABLE,"Copy object is not registered to the requested workspace");
            *reply.add_entries()=*match;
            if(selected==workspace::copy::v1::APPLICATION_STATE && match->schema_version()==2) {
                pb::CopyApplicationStateCollection descriptor;require(descriptor.ParseFromString(match->typed_export()),"Malformed registered state descriptor");
                *reply.mutable_state_mapping()=configured_state_mapping(request.workspace_id(),descriptor);
                if(request.expected_state_mapping_revision()!=0 && request.expected_state_mapping_revision()!=reply.state_mapping().revision())
                    throw Error(pb::StorageOwnerError::CONFLICT,"Configured application state mapping revision changed");
            }
        }
        require(reply.has_state_mapping() || request.expected_state_mapping_revision()==0,"Unexpected state mapping revision without selected collection");
        bool project=false,git=false;
        for (const auto item : request.data_classes()) {
            if (item==workspace::copy::v1::PROJECT_TREE) project=true;
            if (item==workspace::copy::v1::GIT_HISTORY) git=true;
        }
        require(project && git,"Workspace copy must include project and Git history");
        return reply;
    }

};
StorageOwner::StorageOwner(workspace::v1::StorageOwnerConfig configuration, bool require_kernel_io, bool migrate_copy_catalog, unsigned migrate_schema_from)
    : impl_(std::make_unique<Impl>(std::move(configuration), require_kernel_io, migrate_copy_catalog, migrate_schema_from)) {}
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
        else if(request.has_copy_domain_prepare()) *reply.mutable_state()=impl_->copy_domain_prepare(request.copy_domain_prepare());
        else if(request.has_copy_destination_prepare()) *reply.mutable_copy_destination()=impl_->copy_destination_prepare(request.copy_destination_prepare());
        else if(request.has_copy_destination_publish()) *reply.mutable_copy_destination()=impl_->copy_destination_publish(request.copy_destination_publish());
        else if(request.has_copy_destination_inspect()) *reply.mutable_copy_destination()=impl_->copy_destination_inspect(request.copy_destination_inspect());
        else if (request.has_copy_catalog_query()) *reply.mutable_copy_catalog() = impl_->copy_catalog(request.copy_catalog_query());
        else if(request.has_configured_application_query()) *reply.mutable_configured_application()=impl_->configured_application(request.configured_application_query());
        else if(request.has_configured_application_prepare()) *reply.mutable_configured_allocation()=impl_->configured_application_prepare(request.configured_application_prepare());
        else if (request.has_inspect()) *reply.mutable_state() = impl_->inspect(request.inspect().object());
        else if (request.has_hello()) {
            auto* identity = reply.mutable_identity(); identity->set_filesystem_id(impl_->config.filesystem_id()); identity->set_process_id(getpid()); identity->set_volume_bytes(impl_->config.volume_bytes()); identity->set_accounting_consistent(true);
        } else throw Error(pb::StorageOwnerError::INVALID_ARGUMENT, "Unknown storage operation");
    } catch (const Error& error) { reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what()); }
      catch (const std::invalid_argument&) { reply.mutable_error()->set_code(pb::StorageOwnerError::INVALID_ARGUMENT); reply.mutable_error()->set_message("Invalid storage protobuf"); }
    return reply;
}
}
