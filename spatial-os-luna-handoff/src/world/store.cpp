#include "world/store.hpp"
#include "world/presentation_geometry.hpp"
#include "core/presentation_identity.hpp"
#include "world/snapshot_size.hpp"
#include "world/storage_vfs.hpp"
#include "core/geometry.hpp"
#include <sqlite3.h>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#include <limits>
#include <cstdint>
#include <string>
#include <utility>

namespace spatial {
namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::invalid_argument(message);
}
void check(int code) {
    if (code != SQLITE_OK && code != SQLITE_ROW && code != SQLITE_DONE)
        throw WorldError(wire::STORAGE_ERROR, "SQLite operation failed; no success acknowledged");
}
struct File {
    int fd = -1;
    ~File() { if (fd >= 0) close(fd); }
};
struct Statement {
    sqlite3_stmt* value = nullptr;
    Statement(sqlite3* db, const char* sql) { check(sqlite3_prepare_v2(db, sql, -1, &value, nullptr)); }
    ~Statement() { sqlite3_finalize(value); }
    void text(int i, const std::string& text) {
        check(sqlite3_bind_text(value, i, text.data(), static_cast<int>(text.size()), SQLITE_TRANSIENT));
    }
    void integer(int i, std::uint64_t number) {
        require(number <= static_cast<std::uint64_t>(INT64_MAX), "Revision exceeds storage range");
        check(sqlite3_bind_int64(value, i, static_cast<sqlite3_int64>(number)));
    }
    void blob(int i, const std::string& bytes) {
        require(bytes.size() <= static_cast<std::size_t>(INT_MAX), "SQLite blob exceeds storage range");
        check(sqlite3_bind_blob(value, i, bytes.data(), static_cast<int>(bytes.size()), SQLITE_TRANSIENT));
    }
    int step() { const int result = sqlite3_step(value); check(result); return result; }
    std::string text(int i) {
        const auto* bytes = sqlite3_column_text(value, i);
        return bytes ? std::string(reinterpret_cast<const char*>(bytes), sqlite3_column_bytes(value, i)) : "";
    }
    std::string blob(int i) {
        const auto* bytes = static_cast<const char*>(sqlite3_column_blob(value, i));
        return bytes ? std::string(bytes, sqlite3_column_bytes(value, i)) : "";
    }
};
void execute(sqlite3* db, const char* sql) { check(sqlite3_exec(db, sql, nullptr, nullptr, nullptr)); }
struct CommitFault {
    const bool enabled;
    wire::FaultPoint point = wire::FAULT_POINT_UNSPECIFIED;
    void before_commit() {
        // Exit without stack unwinding or SQLite cleanup at the actual commit boundary.
        if (point == wire::FAULT_WORLD_BEFORE_COMMIT) _exit(86);
    }
    void after_commit() {
        if (point == wire::FAULT_WORLD_AFTER_COMMIT_BEFORE_REPLY) _exit(87);
    }
};
struct Transaction {
    sqlite3* db;
    CommitFault* fault;
    bool committed = false;
    explicit Transaction(sqlite3* db, bool write = false, CommitFault* fault = nullptr)
        : db(db), fault(write ? fault : nullptr) { execute(db, write ? "BEGIN IMMEDIATE" : "BEGIN"); }
    ~Transaction() { if (!committed) sqlite3_exec(db, "ROLLBACK", nullptr, nullptr, nullptr); }
    void commit() {
        if (fault) fault->before_commit();
        execute(db, "COMMIT"); committed = true;
        if (fault) fault->after_commit();
    }
};
void valid_id(const std::string& id) {
    require(!id.empty() && id.size() <= 256 && id.find('\0') == std::string::npos, "Invalid entity ID");
}
void valid_pose(const wire::Pose& pose) {
    require(pose.has_position() && pose.has_orientation(), "Incomplete entity pose");
    const auto& p = pose.position(); const auto& q = pose.orientation();
    validate_pose({{p.x(), p.y(), p.z()}, {q.x(), q.y(), q.z(), q.w()}});
}
void valid_entity(const wire::Entity& entity) {
    validate_message(entity);
    valid_id(entity.id());
    require(entity.has_kind() && wire::EntityKind_IsValid(entity.kind()) && entity.kind() != wire::ENTITY_KIND_UNSPECIFIED,
            "Missing or invalid entity kind");
    require(entity.label().size() <= 65536, "Entity label exceeds size limit");
    if (entity.kind() == wire::ROOM || entity.kind() == wire::PANEL || entity.kind() == wire::SURFACE_SLOT)
        require(entity.has_label(), "Missing entity label");
    if (entity.kind() == wire::ROOM) {
        require(!entity.has_room() && !entity.has_pose(), "Rooms do not have an owning room or pose");
    } else {
        valid_id(entity.room());
        require(entity.has_pose(), "Missing entity pose"); valid_pose(entity.pose());
    }
    const bool has_size = entity.kind() == wire::PANEL || entity.kind() == wire::PORTAL || entity.kind() == wire::SURFACE_SLOT;
    require(entity.has_size() == has_size, "Unexpected or missing size");
    if (has_size) require(entity.size().x() > 0 && entity.size().y() > 0, "Nonpositive entity size");
    require(entity.has_color() == (entity.kind() == wire::PANEL), "Unexpected or missing color");
    if (entity.has_color()) require(entity.color().red() <= 255 && entity.color().green() <= 255 && entity.color().blue() <= 255,
                                    "Invalid RGB color");
    require(entity.has_bounds() == (entity.kind() == wire::BARRIER), "Unexpected or missing bounds");
    if (entity.has_bounds()) {
        const auto& bounds = entity.bounds();
        require(bounds.has_min() && bounds.has_max() && bounds.min().x() < bounds.max().x() &&
                bounds.min().y() < bounds.max().y() && bounds.min().z() < bounds.max().z(), "Invalid bounds");
    }
    require(entity.has_destination() == (entity.kind() == wire::PORTAL), "Unexpected or missing portal destination");
    if (entity.has_destination()) {
        valid_id(entity.destination().room());
        require(entity.destination().has_pose(), "Missing destination pose"); valid_pose(entity.destination().pose());
    }
}
}

namespace {
const char* workspace_context_schema =
    "CREATE TABLE world_workspaces(id TEXT PRIMARY KEY,available INTEGER NOT NULL CHECK(available IN (0,1)));"
    "CREATE TABLE actor_workspaces(actor TEXT PRIMARY KEY REFERENCES entities(id),workspace TEXT NOT NULL REFERENCES world_workspaces(id));"
    "CREATE TABLE workspace_presentations(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,room_id TEXT NOT NULL REFERENCES entities(id),pose BLOB NOT NULL,generation INTEGER NOT NULL CHECK(generation>0));"
    "CREATE TABLE workspace_presentation_ops(operation_id TEXT PRIMARY KEY,principal TEXT NOT NULL,request_digest TEXT NOT NULL,kind INTEGER NOT NULL,result BLOB NOT NULL);"
    "PRAGMA user_version=3";
}
struct WorldStore::Impl {
    File lock;
    CommitFault fault;
    std::unique_ptr<StorageVfs> storage_vfs;
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    explicit Impl(const std::filesystem::path& directory, bool migrate_v1, bool fixture_faults, bool migrate_v2) : fault{fixture_faults} {
        require((!migrate_v1 && !migrate_v2) || !fixture_faults, "Migration cannot enable fixture faults");
        require(!(migrate_v1 && migrate_v2), "Select one world schema migration");
        require(directory.is_absolute(), "State directory must be absolute");
        require(!std::filesystem::is_symlink(directory), "State directory must not be a symlink");
        if (migrate_v1) require(std::filesystem::is_regular_file(directory / "world.db"), "Migration requires an existing world database");
        std::filesystem::create_directories(directory);
        lock.fd = open((directory / "world.lock").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (lock.fd < 0 || flock(lock.fd, LOCK_EX | LOCK_NB) != 0)
            throw WorldError(wire::IN_USE, "World state already has an authority or cannot be locked");
        for (const auto* name : {"world.db", "world.db-journal", "world.db-wal", "world.db-shm"})
            require(!std::filesystem::is_symlink(directory / name), "Database files must not be symlinks");
        sqlite3* connection = nullptr;
        if (fixture_faults) storage_vfs = std::make_unique<StorageVfs>();
        const int opened = sqlite3_open_v2((directory / "world.db").c_str(), &connection,
                                           SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_NOMUTEX,
                                           storage_vfs ? storage_vfs->name() : nullptr);
        db.reset(connection); check(opened); check(sqlite3_busy_timeout(db.get(), 2000));
        int version;
        { Statement q(db.get(), "PRAGMA user_version"); q.step(); version = sqlite3_column_int(q.value, 0); }
        if (version == 1 && !migrate_v1) throw WorldError(wire::STORAGE_ERROR, "World schema v1 requires worldd --state-dir PATH --migrate-v1 while the world is stopped");
        if (version == 2 && !migrate_v2) throw WorldError(wire::STORAGE_ERROR, "World schema v2 requires worldd --state-dir PATH --migrate-v2 while the world is stopped");
        if (migrate_v1 && version != 1) throw WorldError(wire::STORAGE_ERROR, "--migrate-v1 requires world schema v1");
        if (migrate_v2 && version != 2) throw WorldError(wire::STORAGE_ERROR, "--migrate-v2 requires world schema v2");
        if (version < 0 || version > 3 || (migrate_v1 && version == 0)) throw WorldError(wire::STORAGE_ERROR, "Unsupported world schema; explicit migration required");
        if (version == 0) {
            Statement q(db.get(), "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'");
            q.step();
            if (sqlite3_column_int(q.value, 0) != 0) throw WorldError(wire::STORAGE_ERROR, "Refusing to initialize an unknown database");
        }
        execute(db.get(), "PRAGMA foreign_keys=ON; PRAGMA synchronous=EXTRA; PRAGMA trusted_schema=OFF");
        { Statement q(db.get(), "PRAGMA journal_mode=DELETE"); q.step();
          if (q.text(0) != "delete") throw WorldError(wire::STORAGE_ERROR, "Required SQLite journal mode unavailable"); }
        if (version == 0) {
            Transaction transaction(db.get(), true);
            execute(db.get(), "CREATE TABLE entities(id TEXT PRIMARY KEY, kind INTEGER NOT NULL, room TEXT REFERENCES entities(id),"
                              "destination TEXT REFERENCES entities(id), revision INTEGER NOT NULL CHECK(revision>0), data BLOB NOT NULL);"
                              "CREATE TABLE metadata(singleton INTEGER PRIMARY KEY CHECK(singleton=1),world_id TEXT NOT NULL,"
                              "entry_room TEXT NOT NULL REFERENCES entities(id),world_revision INTEGER NOT NULL CHECK(world_revision>0));"
                              "CREATE INDEX entities_room ON entities(room); CREATE INDEX entities_destination ON entities(destination)");
            std::string world_id;
            { Statement q(db.get(), "SELECT lower(hex(randomblob(16)))"); q.step(); world_id = q.text(0); }
            wire::Entity entry;
            entry.set_id("entry-" + world_id); entry.set_kind(wire::ROOM); entry.set_label("Entry");
            entry.set_revision(1); entry.set_world_revision(1); insert(entry);
            Statement q(db.get(), "INSERT INTO metadata VALUES(1,?,?,1)");
            q.text(1, world_id); q.text(2, entry.id()); q.step();
            execute(db.get(), workspace_context_schema); transaction.commit();
        }
        { Statement q(db.get(), "PRAGMA quick_check");
          if (q.step() != SQLITE_ROW || q.text(0) != "ok") throw WorldError(wire::STORAGE_ERROR, "World database integrity check failed"); }
        { Statement q(db.get(), "PRAGMA foreign_key_check");
          if (q.step() != SQLITE_DONE) throw WorldError(wire::STORAGE_ERROR, "World database reference check failed"); }
        const auto state = info();
        if (state.world_id().empty() || get(state.entry_room()).kind() != wire::ROOM)
            throw WorldError(wire::STORAGE_ERROR, "Invalid durable world identity or entry room");
        Statement all(db.get(), "SELECT id FROM entities ORDER BY id");
        while (all.step() == SQLITE_ROW) {
            const auto entity = get(all.text(0));
            if (is_reserved_presentation_geometry_id(entity.id()))
                throw WorldError(wire::STORAGE_ERROR, "World entity occupies the reserved presentation geometry namespace");
            references(entity);
            if (entity.world_revision() > state.world_revision())
                throw WorldError(wire::STORAGE_ERROR, "Entity revision exceeds world revision");
        }
        if (version == 1) {
            Transaction transaction(db.get(), true); execute(db.get(), workspace_context_schema); transaction.commit();
        }
        if (version == 2) {
            Transaction transaction(db.get(), true);
            execute(db.get(), "CREATE TABLE workspace_presentations(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,room_id TEXT NOT NULL REFERENCES entities(id),pose BLOB NOT NULL,generation INTEGER NOT NULL CHECK(generation>0));"
                            "CREATE TABLE workspace_presentation_ops(operation_id TEXT PRIMARY KEY,principal TEXT NOT NULL,request_digest TEXT NOT NULL,kind INTEGER NOT NULL,result BLOB NOT NULL); PRAGMA user_version=3");
            transaction.commit();
        }
        Statement context(db.get(), "SELECT actor FROM actor_workspaces JOIN entities ON actor=entities.id JOIN world_workspaces ON workspace=world_workspaces.id WHERE kind<>? OR available<>1");
        context.integer(1, wire::ACTOR);
        if (context.step() != SQLITE_DONE) throw WorldError(wire::STORAGE_ERROR, "Invalid durable actor workspace context");
    }
    wire::WorldInfoReply info() {
        Statement q(db.get(), "SELECT world_id,entry_room,world_revision FROM metadata WHERE singleton=1");
        if (q.step() != SQLITE_ROW) throw WorldError(wire::STORAGE_ERROR, "Missing world metadata");
        const auto revision = sqlite3_column_int64(q.value, 2);
        if (revision < 1) throw WorldError(wire::STORAGE_ERROR, "Invalid world revision");
        wire::WorldInfoReply out;
        out.set_world_id(q.text(0)); out.set_entry_room(q.text(1)); out.set_world_revision(revision); return out;
    }
    wire::Entity get(const std::string& id) {
        valid_id(id);
        Statement q(db.get(), "SELECT data,kind,room,destination,revision FROM entities WHERE id=?"); q.text(1, id);
        if (q.step() != SQLITE_ROW) throw WorldError(wire::NOT_FOUND, "Entity not found");
        wire::Entity out;
        if (!out.ParseFromArray(sqlite3_column_blob(q.value, 0), sqlite3_column_bytes(q.value, 0)))
            throw WorldError(wire::STORAGE_ERROR, "Invalid stored entity protobuf");
        try { valid_entity(out); } catch (const std::exception&) {
            throw WorldError(wire::STORAGE_ERROR, "Invalid stored entity values");
        }
        if (out.id() != id || out.kind() != sqlite3_column_int(q.value, 1) || out.room() != q.text(2) ||
            out.destination().room() != q.text(3) || out.revision() == 0 || out.world_revision() == 0 ||
            out.revision() != static_cast<std::uint64_t>(sqlite3_column_int64(q.value, 4)))
            throw WorldError(wire::STORAGE_ERROR, "Stored entity identity or revision mismatch");
        return out;
    }
    void references(const wire::Entity& entity) {
        if (entity.has_room() && get(entity.room()).kind() != wire::ROOM)
            throw std::invalid_argument("Entity room must reference a room");
        if (entity.has_destination() && get(entity.destination().room()).kind() != wire::ROOM)
            throw std::invalid_argument("Portal destination must reference a room");
    }
    void bind(Statement& q, const wire::Entity& entity) {
        q.text(1, entity.id()); q.integer(2, entity.kind());
        if (entity.has_room()) q.text(3, entity.room()); else check(sqlite3_bind_null(q.value, 3));
        if (entity.has_destination()) q.text(4, entity.destination().room()); else check(sqlite3_bind_null(q.value, 4));
        q.integer(5, entity.revision());
        const auto bytes = entity.SerializeAsString();
        check(sqlite3_bind_blob(q.value, 6, bytes.data(), static_cast<int>(bytes.size()), SQLITE_TRANSIENT));
    }
    void insert(const wire::Entity& entity) {
        Statement q(db.get(), "INSERT INTO entities(id,kind,room,destination,revision,data) VALUES(?,?,?,?,?,?)");
        bind(q, entity); q.step();
    }
    std::uint64_t advance() {
        const auto revision = info().world_revision();
        if (revision == INT64_MAX) throw WorldError(wire::RESOURCE_EXHAUSTED, "World revision exhausted");
        execute(db.get(), "UPDATE metadata SET world_revision=world_revision+1 WHERE singleton=1"); return revision + 1;
    }
};

WorldStore::WorldStore(const std::filesystem::path& directory, bool migrate_v1, bool fixture_faults, bool migrate_v2)
    : impl_(std::make_unique<Impl>(directory, migrate_v1, fixture_faults, migrate_v2)) {}
WorldStore::~WorldStore() = default;
bool WorldStore::fixture_faults() const { return impl_->fault.enabled; }
wire::FaultArmReply WorldStore::arm_fault(const wire::FaultArmRequest& request, const std::function<void()>& arm_git) {
    if (!fixture_faults()) throw WorldError(wire::FORBIDDEN, "Fixture faults are disabled");
    validate_message(request);
    require(request.point() != wire::FAULT_POINT_UNSPECIFIED && request.action() != wire::FAULT_ACTION_UNSPECIFIED,
            "Missing fault point or action");
    if (request.point() == wire::FAULT_STORAGE_NEXT_WRITE)
        require(request.action() == wire::FAULT_ERROR && request.has_storage_error() &&
                request.storage_error() == wire::STORAGE_FAULT_ENOSPC, "Invalid storage fault");
    else require(request.action() == wire::FAULT_EXIT && !request.has_storage_error(), "Invalid exit fault");
    if (request.point() != wire::FAULT_WORLD_BEFORE_COMMIT &&
        request.point() != wire::FAULT_WORLD_AFTER_COMMIT_BEFORE_REPLY && request.point() != wire::FAULT_STORAGE_NEXT_WRITE &&
        request.point() != wire::FAULT_GIT_AFTER_WORKTREE_BEFORE_READY)
        throw WorldError(wire::UNIMPLEMENTED, "Requested fault boundary is not implemented");
    if (impl_->fault.point != wire::FAULT_POINT_UNSPECIFIED || impl_->storage_vfs->armed())
        throw WorldError(wire::BUSY, "A fault is already armed");
    if (request.point() == wire::FAULT_GIT_AFTER_WORKTREE_BEFORE_READY) {
        if (!arm_git) throw WorldError(wire::UNIMPLEMENTED, "Workspace fault authority unavailable");
        // Reserve locally only after the broker acknowledges its private arm request.
        arm_git();
    }
    if (request.point() == wire::FAULT_STORAGE_NEXT_WRITE) impl_->storage_vfs->arm();
    else impl_->fault.point = request.point();
    wire::FaultArmReply result; result.set_armed(true); return result;
}
wire::WorldInfoReply WorldStore::info() { return impl_->info(); }
wire::Entity WorldStore::get(const std::string& id) { return impl_->get(id); }
wire::Entity WorldStore::create(const wire::Entity& input) {
    require(input.kind() != wire::ACTOR, "Actors require the actor management boundary");
    return insert_entity(input);
}
wire::Entity WorldStore::create_actor(const wire::Entity& input) {
    require(input.kind() == wire::ACTOR, "Expected actor kind");
    return insert_entity(input);
}
wire::Entity WorldStore::insert_entity(const wire::Entity& input) {
    valid_entity(input);
    if (is_reserved_presentation_geometry_id(input.id()))
        throw WorldError(wire::INVALID_ARGUMENT, "Entity ID uses a reserved presentation geometry namespace");
    require(!input.has_revision() && !input.has_world_revision(), "Revisions are assigned by the world");
    Transaction transaction(impl_->db.get(), true, &impl_->fault);
    { Statement q(impl_->db.get(), "SELECT 1 FROM entities WHERE id=?"); q.text(1, input.id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::ALREADY_EXISTS, "Entity ID already exists"); }
    impl_->references(input);
    wire::Entity result = input; result.set_revision(1); result.set_world_revision(impl_->advance());
    impl_->insert(result); transaction.commit(); return result;
}
wire::Entity WorldStore::update(const wire::WorldUpdateRequest& request) {
    validate_message(request);
    require(request.has_expected_revision() && request.expected_revision() > 0 && request.has_patch(), "Missing revision or patch");
    const auto& patch = request.patch();
    require(!patch.has_id() && !patch.has_kind() && !patch.has_revision() && !patch.has_world_revision(), "Identity, kind, and revisions are immutable");
    require(patch.ByteSizeLong() != 0, "Empty patch");
    Transaction transaction(impl_->db.get(), true, &impl_->fault);
    auto result = impl_->get(request.id());
    require(result.kind() != wire::ACTOR, "Use actor actions and checkpoints to change actor state");
    if (request.expected_revision() != result.revision()) throw WorldError(wire::REVISION_CONFLICT, "Stale entity revision");
    // Replace complete message-valued fields. Protobuf MergeFrom would retain
    // old components when a caller explicitly supplies zero-valued components.
    if (patch.has_label()) result.set_label(patch.label());
    if (patch.has_room()) result.set_room(patch.room());
    if (patch.has_pose()) *result.mutable_pose() = patch.pose();
    if (patch.has_size()) *result.mutable_size() = patch.size();
    if (patch.has_color()) *result.mutable_color() = patch.color();
    if (patch.has_bounds()) *result.mutable_bounds() = patch.bounds();
    if (patch.has_destination()) *result.mutable_destination() = patch.destination();
    valid_entity(result); impl_->references(result);
    if (result.revision() == INT64_MAX) throw WorldError(wire::RESOURCE_EXHAUSTED, "Entity revision exhausted");
    result.set_revision(result.revision()+1); result.set_world_revision(impl_->advance());
    Statement q(impl_->db.get(), "UPDATE entities SET kind=?2,room=?3,destination=?4,revision=?5,data=?6 WHERE id=?1 AND revision=?7");
    impl_->bind(q, result); q.integer(7, request.expected_revision()); q.step();
    if (sqlite3_changes(impl_->db.get()) != 1) throw WorldError(wire::REVISION_CONFLICT, "Concurrent entity edit");
    transaction.commit(); return result;
}
wire::WorldDeleteReply WorldStore::erase(const wire::WorldDeleteRequest& request, bool live_reference) {
    require(request.has_expected_revision() && request.expected_revision() > 0, "Missing expected revision");
    Transaction transaction(impl_->db.get(), true, &impl_->fault);
    const auto entity = impl_->get(request.id());
    require(entity.kind() != wire::ACTOR, "Actor removal requires identity revocation");
    if (entity.revision() != request.expected_revision()) throw WorldError(wire::REVISION_CONFLICT, "Stale entity revision");
    if (live_reference) throw WorldError(wire::IN_USE, "Entity contains a live actor");
    if (request.id() == impl_->info().entry_room()) throw WorldError(wire::IN_USE, "The world entry room cannot be deleted");
    { Statement q(impl_->db.get(), "SELECT 1 FROM entities WHERE room=?1 OR destination=?1 LIMIT 1"); q.text(1, request.id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::IN_USE, "Entity is referenced"); }
    { Statement q(impl_->db.get(), "SELECT 1 FROM workspace_presentations WHERE room_id=?1 LIMIT 1"); q.text(1, request.id());
      if (q.step() == SQLITE_ROW) throw WorldError(wire::IN_USE, "Room contains a workspace presentation"); }
    Statement q(impl_->db.get(), "DELETE FROM entities WHERE id=? AND revision=?");
    q.text(1, request.id()); q.integer(2, request.expected_revision()); q.step();
    if (sqlite3_changes(impl_->db.get()) != 1) throw WorldError(wire::REVISION_CONFLICT, "Concurrent entity edit");
    wire::WorldDeleteReply result; result.set_deleted(true); result.set_world_revision(impl_->advance());
    transaction.commit(); return result;
}
wire::WorldSnapshotReply WorldStore::snapshot() {
    Transaction transaction(impl_->db.get());
    const auto info = impl_->info(); wire::WorldSnapshotReply result;
    result.set_world_id(info.world_id()); result.set_entry_room(info.entry_room()); result.set_world_revision(info.world_revision());
    constexpr std::size_t snapshot_limit = max_frame_bytes - 64;
    std::size_t accounted_bytes = result.ByteSizeLong();
    if (accounted_bytes > snapshot_limit)
        throw WorldError(wire::RESOURCE_EXHAUSTED, "Snapshot exceeds frame limit; no partial snapshot returned");
    Statement q(impl_->db.get(), "SELECT id FROM entities ORDER BY id COLLATE BINARY");
    while (q.step() == SQLITE_ROW) {
        auto entity = impl_->get(q.text(0));
        if (!add_snapshot_bytes(accounted_bytes, snapshot_entity_field_size(entity), snapshot_limit))
            throw WorldError(wire::RESOURCE_EXHAUSTED, "Snapshot exceeds frame limit; no partial snapshot returned");
        *result.add_entities() = std::move(entity);
    }
    transaction.commit(); return result;
}
std::vector<wire::Entity> WorldStore::actors() {
    std::vector<wire::Entity> result;
    Statement q(impl_->db.get(), "SELECT id FROM entities WHERE kind=? ORDER BY id");
    q.integer(1, wire::ACTOR);
    while (q.step() == SQLITE_ROW) result.push_back(impl_->get(q.text(0)));
    return result;
}
std::vector<wire::Entity> WorldStore::checkpoint_actors(const std::vector<wire::Entity>& entities) {
    Transaction transaction(impl_->db.get(), true, &impl_->fault);
    std::vector<wire::Entity> changed;
    for (const auto& entity : entities) {
        const auto previous = impl_->get(entity.id());
        require(previous.kind() == wire::ACTOR && entity.kind() == wire::ACTOR, "Checkpoint requires actors");
        require(entity.revision() == previous.revision(), "Actor checkpoint revision mismatch");
        auto next = previous;
        next.set_room(entity.room()); *next.mutable_pose() = entity.pose();
        valid_entity(next); impl_->references(next);
        if (next.SerializeAsString() == previous.SerializeAsString()) continue;
        if (next.revision() == INT64_MAX) throw WorldError(wire::RESOURCE_EXHAUSTED, "Actor revision exhausted");
        next.set_revision(next.revision() + 1); changed.push_back(std::move(next));
    }
    if (!changed.empty()) {
        const auto revision = impl_->advance();
        for (auto& entity : changed) {
            entity.set_world_revision(revision);
            Statement q(impl_->db.get(), "UPDATE entities SET kind=?2,room=?3,destination=?4,revision=?5,data=?6 WHERE id=?1 AND revision=?7");
            impl_->bind(q, entity); q.integer(7, entity.revision() - 1); q.step();
            if (sqlite3_changes(impl_->db.get()) != 1) throw WorldError(wire::REVISION_CONFLICT, "Concurrent actor checkpoint");
        }
    }
    transaction.commit(); return changed;
}
bool WorldStore::workspace_available(const std::string& id) {
    valid_id(id); Statement q(impl_->db.get(), "SELECT available FROM world_workspaces WHERE id=?"); q.text(1, id);
    return q.step() == SQLITE_ROW && sqlite3_column_int(q.value, 0) == 1;
}
void WorldStore::workspace_available(const std::string& id, bool available) {
    valid_id(id); Transaction transaction(impl_->db.get(), true, &impl_->fault);
    const bool previous = workspace_available(id);
    Statement q(impl_->db.get(), "INSERT INTO world_workspaces VALUES(?,?) ON CONFLICT(id) DO UPDATE SET available=excluded.available");
    q.text(1, id); q.integer(2, available); q.step();
    if (!available) { Statement clear(impl_->db.get(), "DELETE FROM actor_workspaces WHERE workspace=?"); clear.text(1, id); clear.step(); }
    if (previous != available) impl_->advance();
    transaction.commit();
}
std::string WorldStore::selected_workspace(const std::string& actor) {
    valid_id(actor); Statement q(impl_->db.get(), "SELECT workspace FROM actor_workspaces WHERE actor=?"); q.text(1, actor);
    return q.step() == SQLITE_ROW ? q.text(0) : "";
}
void WorldStore::select_workspace(const std::string& actor, const std::string& workspace) {
    valid_id(actor); valid_id(workspace); Transaction transaction(impl_->db.get(), true, &impl_->fault);
    require(impl_->get(actor).kind() == wire::ACTOR, "Workspace selection requires an actor");
    if (!workspace_available(workspace)) throw WorldError(wire::UNAVAILABLE, "Workspace is unavailable in this world");
    if (selected_workspace(actor) != workspace) {
        Statement q(impl_->db.get(), "INSERT INTO actor_workspaces VALUES(?,?) ON CONFLICT(actor) DO UPDATE SET workspace=excluded.workspace");
        q.text(1, actor); q.text(2, workspace); q.step(); impl_->advance();
    }
    transaction.commit();
}
void WorldStore::clear_workspace(const std::string& actor) {
    valid_id(actor); Transaction transaction(impl_->db.get(), true, &impl_->fault);
    Statement q(impl_->db.get(), "DELETE FROM actor_workspaces WHERE actor=?"); q.text(1, actor); q.step();
    if (sqlite3_changes(impl_->db.get())) impl_->advance();
    transaction.commit();
}
void WorldStore::revoke_workspace_selection(const std::string& workspace, const std::string& actor) {
    valid_id(workspace); valid_id(actor); Transaction transaction(impl_->db.get(), true, &impl_->fault);
    Statement q(impl_->db.get(), "DELETE FROM actor_workspaces WHERE actor=? AND workspace=?"); q.text(1, actor); q.text(2, workspace); q.step();
    if (sqlite3_changes(impl_->db.get())) impl_->advance();
    transaction.commit();
}
} // namespace spatial

namespace spatial {
namespace {
constexpr int presentation_create_op = 1;
constexpr int presentation_move_op = 2;
constexpr int presentation_remove_op = 3;

std::string presentation_hex(const std::string& bytes) {
    constexpr char digits[] = "0123456789abcdef";
    std::string result; result.reserve(bytes.size()*2);
    for (unsigned char byte : bytes) { result.push_back(digits[byte>>4]); result.push_back(digits[byte&15]); }
    return result;
}
std::string presentation_unhex(const std::string& hex) {
    if (hex.size()%2) throw WorldError(wire::STORAGE_ERROR, "Invalid encoded presentation identity");
    auto nibble=[](char c)->unsigned char { if(c>='0'&&c<='9')return c-'0'; if(c>='a'&&c<='f')return c-'a'+10; throw WorldError(wire::STORAGE_ERROR,"Invalid encoded presentation identity"); };
    std::string result; result.reserve(hex.size()/2);
    for(std::size_t i=0;i<hex.size();i+=2) result.push_back(static_cast<char>((nibble(hex[i])<<4)|nibble(hex[i+1])));
    return result;
}
void validate_presentation_id(const std::string& value, const char* message) { require(value.size()==16,message); }
void validate_operation(const std::string& operation_id, const std::string& digest, const std::string& principal) {
    validate_presentation_id(operation_id, "Operation ID must contain exactly 16 bytes");
    require(digest.size()==32, "Presentation digest must be SHA-256 bytes");
    require(principal.size()==64, "Canonical presentation principal must contain two 32-byte identities");
}

void append_field(std::string& bytes, const std::string& field) {
    if (field.size() > UINT32_MAX)
        throw WorldError(wire::RESOURCE_EXHAUSTED, "Presentation field too large");
    const auto size = static_cast<std::uint32_t>(field.size());
    for (int shift = 24; shift >= 0; shift -= 8)
        bytes.push_back(static_cast<char>((size >> shift) & 0xff));
    bytes.append(field);
}

std::string take_field(const std::string& bytes, std::size_t& offset) {
    if (offset > bytes.size() || bytes.size() - offset < 4)
        throw WorldError(wire::STORAGE_ERROR, "Invalid presentation journal record");
    std::uint32_t size = 0;
    for (int i = 0; i < 4; ++i)
        size = (size << 8) | static_cast<unsigned char>(bytes[offset++]);
    if (size > bytes.size() - offset)
        throw WorldError(wire::STORAGE_ERROR, "Invalid presentation journal field");
    std::string field = bytes.substr(offset, size);
    offset += size;
    return field;
}

WorkspacePresentation decode_presentation(const std::string& bytes) {
    WorkspacePresentation result;
    std::size_t offset = 0;
    result.id = take_field(bytes, offset);
    result.workspace_id = take_field(bytes, offset);
    result.room_id = take_field(bytes, offset);
    const auto pose_bytes = take_field(bytes, offset);
    if (offset > bytes.size() || bytes.size() - offset != 8)
        throw WorldError(wire::STORAGE_ERROR, "Invalid presentation journal generation");
    std::uint64_t generation = 0;
    for (int i = 0; i < 8; ++i)
        generation = (generation << 8) | static_cast<unsigned char>(bytes[offset++]);
    if (!result.pose.ParseFromString(pose_bytes) || generation == 0)
        throw WorldError(wire::STORAGE_ERROR, "Invalid presentation journal pose");
    result.generation = generation;
    validate_presentation_id(result.id, "Invalid presentation identity");
    valid_id(result.workspace_id);
    valid_id(result.room_id);
    valid_pose(result.pose);
    return result;
}

std::string encode_presentation(const WorkspacePresentation& value) {
    std::string result;
    append_field(result, value.id);
    append_field(result, value.workspace_id);
    append_field(result, value.room_id);
    append_field(result, value.pose.SerializeAsString());
    for (int shift = 56; shift >= 0; shift -= 8)
        result.push_back(static_cast<char>((value.generation >> shift) & 0xff));
    return result;
}
std::string encode_presentation_receipt(const WorkspacePresentation& value, const std::string& source_room, bool removed) {
    std::string result="WPR1";
    append_field(result, source_room); append_field(result, removed ? std::string(1, '\1') : std::string(1, '\0'));
    append_field(result, encode_presentation(value)); return result;
}
PresentationOperationReceipt decode_presentation_receipt(const std::string& bytes, int kind) {
    if (bytes.empty()) {
        if (kind==presentation_remove_op) return {{}, {}, true}; // Legacy tombstone has no trustworthy replay scope.
        throw WorldError(wire::STORAGE_ERROR, "Missing presentation operation receipt");
    }
    if (bytes.compare(0,4,"WPR1") != 0) {
        auto value=decode_presentation(bytes);
        if (kind==presentation_create_op) return {value, {}, false};
        if (kind==presentation_move_op) return {value, {}, false}; // Legacy move has no trustworthy source scope.
        throw WorldError(wire::STORAGE_ERROR, "Invalid legacy presentation receipt kind");
    }
    std::size_t offset=4; const auto source=take_field(bytes,offset); const auto removed=take_field(bytes,offset);
    const auto payload=take_field(bytes,offset);
    if (offset!=bytes.size() || removed.size()!=1 || (removed[0]!='\0' && removed[0]!='\1'))
        throw WorldError(wire::STORAGE_ERROR, "Invalid presentation operation receipt");
    auto value=decode_presentation(payload);
    const bool is_removed=removed[0]=='\1';
    if ((kind==presentation_remove_op && (!is_removed || source.empty())) ||
        ((kind==presentation_create_op || kind==presentation_move_op) && is_removed) ||
        (kind==presentation_create_op && !source.empty()) ||
        (kind==presentation_move_op && source.empty()) ||
        (kind!=presentation_create_op && kind!=presentation_move_op && kind!=presentation_remove_op))
        throw WorldError(wire::STORAGE_ERROR,"Invalid presentation receipt kind or scope");
    return {std::move(value),source,is_removed};
}
}

WorkspacePresentation WorldStore::presentation(const std::string& id) {
    validate_presentation_id(id, "Presentation ID must contain exactly 16 bytes");
    const auto stored_id = presentation_hex(id);
    Statement query(impl_->db.get(),
        "SELECT id,workspace_id,room_id,pose,generation FROM workspace_presentations WHERE id=?");
    query.text(1, stored_id);
    if (query.step() != SQLITE_ROW)
        throw WorldError(wire::NOT_FOUND, "Presentation not found");

    WorkspacePresentation result;
    result.id = presentation_unhex(query.text(0));
    result.workspace_id = query.text(1);
    result.room_id = query.text(2);
    if (!result.pose.ParseFromString(query.blob(3)))
        throw WorldError(wire::STORAGE_ERROR, "Invalid presentation pose");
    const auto generation = sqlite3_column_int64(query.value, 4);
    if (generation < 1)
        throw WorldError(wire::STORAGE_ERROR, "Invalid presentation generation");
    result.generation = static_cast<std::uint64_t>(generation);
    validate_presentation_id(result.id, "Invalid presentation identity");
    valid_id(result.workspace_id);
    valid_pose(result.pose);
    if (impl_->get(result.room_id).kind() != wire::ROOM)
        throw WorldError(wire::STORAGE_ERROR, "Presentation room is not a room");
    return result;
}

std::optional<PresentationOperationReceipt> WorldStore::presentation_operation(const std::string& operation_id,
    const std::string& digest, const std::string& principal, WorkspacePresentationOperation kind) {
    validate_operation(operation_id,digest,principal);
    const auto key=presentation_hex(operation_id);
    Statement query(impl_->db.get(),"SELECT principal,request_digest,kind,result FROM workspace_presentation_ops WHERE operation_id=?");
    query.text(1,key);
    if (query.step()!=SQLITE_ROW) return std::nullopt;
    if (query.text(0)!=presentation_hex(principal) || query.text(1)!=presentation_hex(digest) ||
        sqlite3_column_int(query.value,2)!=static_cast<int>(kind))
        throw WorldError(wire::REVISION_CONFLICT,"Presentation operation replay conflicts");
    return decode_presentation_receipt(query.blob(3),static_cast<int>(kind));
}

std::vector<WorkspacePresentation> WorldStore::presentations() {
    std::vector<WorkspacePresentation> result;
    Statement query(impl_->db.get(), "SELECT id FROM workspace_presentations ORDER BY id");
    while (query.step() == SQLITE_ROW)
        result.push_back(presentation(presentation_unhex(query.text(0))));
    return result;
}

WorkspacePresentation WorldStore::create_presentation(const WorkspacePresentation& requested,
    std::uint64_t expected_world_revision, const std::string& operation_id, const std::string& digest, const std::string& principal) {
    validate_operation(operation_id, digest, principal);
    validate_presentation_id(requested.id, "Presentation ID must contain exactly 16 bytes");
    const auto operation_key=presentation_hex(operation_id), principal_key=presentation_hex(principal), digest_key=presentation_hex(digest), requested_key=presentation_hex(requested.id);
    valid_id(requested.workspace_id);
    valid_id(requested.room_id);
    valid_pose(requested.pose);

    Transaction transaction(impl_->db.get(), true, &impl_->fault);
    Statement replay(impl_->db.get(),
        "SELECT principal,request_digest,kind,result FROM workspace_presentation_ops WHERE operation_id=?");
    replay.text(1, operation_key);
    if (replay.step() == SQLITE_ROW) {
        if (replay.text(0) != principal_key || replay.text(1) != digest_key || sqlite3_column_int(replay.value, 2) != presentation_create_op)
            throw WorldError(wire::REVISION_CONFLICT, "Presentation operation replay conflicts");
        auto result = decode_presentation_receipt(replay.blob(3), presentation_create_op).presentation;
        return result;
    }
    if (impl_->info().world_revision() != expected_world_revision)
        throw WorldError(wire::REVISION_CONFLICT, "World changed before presentation create");
    if (impl_->get(requested.room_id).kind() != wire::ROOM)
        throw WorldError(wire::INVALID_ARGUMENT, "Presentation room must be a room");
    {
        Statement duplicate(impl_->db.get(), "SELECT 1 FROM workspace_presentations WHERE id=?");
        duplicate.text(1, requested_key);
        if (duplicate.step() == SQLITE_ROW)
            throw WorldError(wire::ALREADY_EXISTS, "Presentation identity already exists");
    }

    // Bound this subsystem independently. Existing world entity limits remain unchanged.
    Statement budget(impl_->db.get(),
        "SELECT count(*),coalesce(sum(length(id)+length(workspace_id)+length(room_id)+length(pose)),0) FROM workspace_presentations");
    if (budget.step() != SQLITE_ROW) throw WorldError(wire::STORAGE_ERROR, "Cannot read presentation storage budget");
    constexpr std::int64_t presentation_count_limit = 4096;
    constexpr std::int64_t presentation_bytes_limit = 64 * 1024 * 1024;
    const auto count = sqlite3_column_int64(budget.value, 0);
    const auto bytes = sqlite3_column_int64(budget.value, 1);
    const auto incoming = static_cast<std::int64_t>(requested.id.size() + requested.workspace_id.size() +
        requested.room_id.size() + requested.pose.ByteSizeLong());
    if (count >= presentation_count_limit || incoming > presentation_bytes_limit - bytes)
        throw WorldError(wire::RESOURCE_EXHAUSTED, "World presentation storage limit reached");

    WorkspacePresentation result = requested;
    result.generation = 1;
    Statement insert(impl_->db.get(), "INSERT INTO workspace_presentations VALUES(?,?,?,?,?)");
    insert.text(1, requested_key);
    insert.text(2, result.workspace_id);
    insert.text(3, result.room_id);
    insert.blob(4, result.pose.SerializeAsString());
    insert.integer(5, result.generation);
    insert.step();

    Statement journal(impl_->db.get(), "INSERT INTO workspace_presentation_ops VALUES(?,?,?,?,?)");
    journal.text(1, operation_key);
    journal.text(2, principal_key);
    journal.text(3, digest_key);
    journal.integer(4, presentation_create_op);
    journal.blob(5, encode_presentation_receipt(result, {}, false));
    journal.step();
    impl_->advance();
    transaction.commit();
    return result;
}

WorkspacePresentation WorldStore::move_presentation(const std::string& id, const std::string& room,
    const wire::Pose& pose, std::uint64_t expected_generation, const std::string& operation_id,
    const std::string& digest, const std::string& principal, std::uint64_t expected_world_revision) {
    validate_operation(operation_id, digest, principal);
    const auto operation_key=presentation_hex(operation_id), principal_key=presentation_hex(principal), digest_key=presentation_hex(digest);
    validate_presentation_id(id, "Presentation ID must contain exactly 16 bytes");
    const auto id_key=presentation_hex(id);
    valid_id(room);
    valid_pose(pose);
    Transaction transaction(impl_->db.get(), true, &impl_->fault);
    Statement replay(impl_->db.get(),
        "SELECT principal,request_digest,kind,result FROM workspace_presentation_ops WHERE operation_id=?");
    replay.text(1, operation_key);
    if (replay.step() == SQLITE_ROW) {
        if (replay.text(0) != principal_key || replay.text(1) != digest_key || sqlite3_column_int(replay.value, 2) != presentation_move_op)
            throw WorldError(wire::REVISION_CONFLICT, "Presentation operation replay conflicts");
        auto result = decode_presentation_receipt(replay.blob(3), presentation_move_op).presentation;
        return result;
    }
    if (expected_world_revision && impl_->info().world_revision()!=expected_world_revision)
        throw WorldError(wire::REVISION_CONFLICT,"World changed during presentation placement validation");
    if (impl_->get(room).kind() != wire::ROOM)
        throw WorldError(wire::INVALID_ARGUMENT, "Presentation room must be a room");
    auto result = presentation(id);
    const auto source_room = result.room_id;
    if (result.generation != expected_generation)
        throw WorldError(wire::REVISION_CONFLICT, "Stale presentation generation");
    if (result.generation == INT64_MAX)
        throw WorldError(wire::RESOURCE_EXHAUSTED, "Presentation generation exhausted");
    result.room_id = room;
    result.pose = pose;
    ++result.generation;

    Statement update(impl_->db.get(),
        "UPDATE workspace_presentations SET room_id=?,pose=?,generation=? WHERE id=? AND generation=?");
    update.text(1, room);
    update.blob(2, pose.SerializeAsString());
    update.integer(3, result.generation);
    update.text(4, id_key);
    update.integer(5, expected_generation);
    update.step();
    if (sqlite3_changes(impl_->db.get()) != 1)
        throw WorldError(wire::REVISION_CONFLICT, "Concurrent presentation move");

    Statement journal(impl_->db.get(), "INSERT INTO workspace_presentation_ops VALUES(?,?,?,?,?)");
    journal.text(1, operation_key);
    journal.text(2, principal_key);
    journal.text(3, digest_key);
    journal.integer(4, presentation_move_op);
    journal.blob(5, encode_presentation_receipt(result, source_room, false));
    journal.step();
    impl_->advance();
    transaction.commit();
    return result;
}

void WorldStore::remove_presentation(const std::string& id, std::uint64_t expected_generation,
    const std::string& operation_id, const std::string& digest, const std::string& principal) {
    validate_operation(operation_id, digest, principal);
    const auto operation_key=presentation_hex(operation_id), principal_key=presentation_hex(principal), digest_key=presentation_hex(digest);
    validate_presentation_id(id, "Presentation ID must contain exactly 16 bytes");
    const auto id_key=presentation_hex(id);
    Transaction transaction(impl_->db.get(), true, &impl_->fault);
    Statement replay(impl_->db.get(),
        "SELECT principal,request_digest,kind,result FROM workspace_presentation_ops WHERE operation_id=?");
    replay.text(1, operation_key);
    if (replay.step() == SQLITE_ROW) {
        if (replay.text(0) != principal_key || replay.text(1) != digest_key || sqlite3_column_int(replay.value, 2) != presentation_remove_op)
            throw WorldError(wire::REVISION_CONFLICT, "Presentation operation replay conflicts");
        const auto receipt=decode_presentation_receipt(replay.blob(3),presentation_remove_op);
        (void)receipt;
        return;
    }
    const auto current = presentation(id);
    if (current.generation != expected_generation)
        throw WorldError(wire::REVISION_CONFLICT, "Stale presentation generation");

    Statement remove(impl_->db.get(), "DELETE FROM workspace_presentations WHERE id=? AND generation=?");
    remove.text(1, id_key);
    remove.integer(2, expected_generation);
    remove.step();
    if (sqlite3_changes(impl_->db.get()) != 1)
        throw WorldError(wire::REVISION_CONFLICT, "Concurrent presentation removal");

    Statement journal(impl_->db.get(), "INSERT INTO workspace_presentation_ops VALUES(?,?,?,?,?)");
    journal.text(1, operation_key);
    journal.text(2, principal_key);
    journal.text(3, digest_key);
    journal.integer(4, presentation_remove_op);
    journal.blob(5, encode_presentation_receipt(current, current.room_id, true));
    journal.step();
    impl_->advance();
    transaction.commit();
}
} // namespace spatial
