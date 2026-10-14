#include "world/snapshot_size.hpp"
#include "world/store.hpp"

#include <algorithm>
#include <filesystem>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {
void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

spatial::wire::Entity entity(std::string id, std::size_t label_size = 0) {
    spatial::wire::Entity value;
    value.set_id(std::move(id));
    value.set_kind(spatial::wire::OBJECT);
    value.set_room("entry-room");
    value.set_label(std::string(label_size, 'x'));
    value.set_revision(128);
    value.set_world_revision(16384);
    return value;
}

spatial::wire::WorldSnapshotReply metadata(std::uint64_t revision,
                                            std::size_t world_id_size,
                                            std::size_t entry_room_size) {
    spatial::wire::WorldSnapshotReply value;
    value.set_world_id(std::string(world_id_size, 'w'));
    value.set_world_revision(revision);
    value.set_entry_room(std::string(entry_room_size, 'r'));
    return value;
}

void check_accounting_boundaries() {
    const std::vector<std::uint64_t> revisions{127, 128, 16383, 16384};
    const std::vector<std::size_t> string_sizes{0, 1, 127, 128, 16383, 16384};
    for (const auto revision : revisions) {
        for (const auto world_id_size : string_sizes) {
            for (const auto entry_room_size : string_sizes) {
                auto reply = metadata(revision, world_id_size, entry_room_size);
                const auto base_size = reply.ByteSizeLong();
                check(base_size == reply.ByteSizeLong(), "zero-entity snapshot size differs");
                std::size_t accounted = base_size;
                const auto values = std::vector<spatial::wire::Entity>{
                    entity("first", 0), entity("second", 127), entity("third", 128)};
                for (const auto& value : values) {
                    const auto contribution = spatial::snapshot_entity_field_size(value);
                    check(spatial::add_snapshot_bytes(accounted, contribution,
                          std::numeric_limits<std::size_t>::max()), "unexpected size overflow");
                    *reply.add_entities() = value;
                }
                check(accounted == reply.ByteSizeLong(), "accounted snapshot size differs from protobuf size");
                check(base_size == metadata(revision, world_id_size, entry_room_size).ByteSizeLong(),
                      "metadata sizing changed unexpectedly");
            }
        }
    }

    for (const std::size_t payload_size : {127u, 128u, 16383u, 16384u}) {
        bool matched = false;
        for (std::size_t label_size = 0; label_size <= payload_size + 2; ++label_size) {
            const auto value = entity("nested", label_size);
            if (value.ByteSizeLong() != payload_size) continue;
            spatial::wire::WorldSnapshotReply full;
            *full.add_entities() = value;
            check(spatial::snapshot_entity_field_size(value) == full.ByteSizeLong(),
                  "generated repeated-field envelope size differs");
            matched = true;
            break;
        }
        check(matched, "could not construct entity payload at a length-prefix boundary");
    }

    auto base = metadata(16384, 128, 127);
    constexpr auto limit = spatial::max_frame_bytes - 64;
    std::vector<spatial::wire::Entity> prefix;
    for (int index = 0; index < 17; ++index) prefix.push_back(entity("bulk-" + std::to_string(index), 60000));
    std::size_t prefix_size = base.ByteSizeLong();
    for (const auto& value : prefix) {
        check(spatial::add_snapshot_bytes(prefix_size, spatial::snapshot_entity_field_size(value), limit),
              "boundary fixture prefix exceeded snapshot limit");
    }
    bool found[3]{};
    for (std::size_t label_size = 0; label_size <= 65536; ++label_size) {
        auto value = entity("boundary", label_size);
        const auto contribution = spatial::snapshot_entity_field_size(value);
        const auto total = prefix_size + contribution;
        for (const auto wanted : {limit - 1, limit, limit + 1}) {
            if (total != wanted) continue;
            std::size_t accounted = prefix_size;
            const bool accepted = spatial::add_snapshot_bytes(accounted, contribution, limit);
            check(accepted == (wanted <= limit), "snapshot bound comparison changed");
            if (accepted) check(accounted == wanted, "accepted boundary size differs");
            auto reply = base;
            for (const auto& prior : prefix) *reply.add_entities() = prior;
            *reply.add_entities() = value;
            check(reply.ByteSizeLong() == total, "aggregate accounting differs at a frame-limit boundary");
            found[wanted - (limit - 1)] = true;
        }
    }
    check(found[0] && found[1] && found[2], "could not construct aggregate limit-1/limit/limit+1 sizes");

    std::size_t value = 0;
    check(spatial::add_snapshot_bytes(value, limit - 1, limit) && value == limit - 1,
          "limit-1 should fit");
    value = 0;
    check(spatial::add_snapshot_bytes(value, limit, limit) && value == limit,
          "limit equality should fit");
    value = 0;
    check(!spatial::add_snapshot_bytes(value, limit + 1, limit), "limit+1 should reject");
    value = std::numeric_limits<std::size_t>::max() - 1;
    check(!spatial::add_snapshot_bytes(value, 2, std::numeric_limits<std::size_t>::max()),
          "checked addition should reject overflow");
    value = std::numeric_limits<std::size_t>::max();
    check(!spatial::add_snapshot_bytes(value, 1, std::numeric_limits<std::size_t>::max()),
          "oversized addition should reject without overflow");
}

void check_real_store_rejection() {
    const auto directory = std::filesystem::temp_directory_path() /
        ("spatial-snapshot-size-" + std::to_string(getpid()));
    std::filesystem::remove_all(directory);
    try {
        spatial::WorldStore store(directory);
        auto first = spatial::wire::Entity{};
        first.set_id("z-room"); first.set_kind(spatial::wire::ROOM); first.set_label("z");
        store.create(first);
        auto second = spatial::wire::Entity{};
        second.set_id("a-room"); second.set_kind(spatial::wire::ROOM); second.set_label("a");
        store.create(second);
        auto ordered = store.snapshot();
        check(ordered.entities_size() == 3, "small real snapshot lost entities");
        for (int index = 1; index < ordered.entities_size(); ++index)
            check(ordered.entities(index - 1).id() < ordered.entities(index).id(), "snapshot order changed");
        check(!ordered.world_id().empty() && ordered.world_revision() == store.info().world_revision() &&
              !ordered.entry_room().empty(), "snapshot metadata fields are incomplete");

        for (int index = 0; index < 18; ++index) {
            spatial::wire::Entity room;
            room.set_id("large-room-" + std::to_string(index));
            room.set_kind(spatial::wire::ROOM);
            room.set_label(std::string(60000, static_cast<char>('a' + (index % 26))));
            store.create(room);
        }
        const auto before = store.info();
        bool typed_rejection = false;
        try {
            (void)store.snapshot();
        } catch (const spatial::WorldError& error) {
            typed_rejection = error.code == spatial::wire::RESOURCE_EXHAUSTED &&
                std::string(error.what()) == "Snapshot exceeds frame limit; no partial snapshot returned";
        }
        check(typed_rejection, "real store did not return the typed snapshot rejection");
        const auto after = store.info();
        check(after.world_revision() == before.world_revision() && after.world_id() == before.world_id() &&
              after.entry_room() == before.entry_room(), "rejected snapshot changed world metadata");
        check(store.get("a-room").label() == "a" && store.get("large-room-17").label().size() == 60000,
              "store became unusable or lost data after rejection");
        auto final_room = spatial::wire::Entity{};
        final_room.set_id("after-rejection"); final_room.set_kind(spatial::wire::ROOM);
        final_room.set_label("still usable");
        const auto created = store.create(final_room);
        check(created.id() == "after-rejection" && store.info().world_revision() == before.world_revision() + 1,
              "store could not commit a mutation after snapshot rejection");
    } catch (...) {
        std::filesystem::remove_all(directory);
        throw;
    }
    std::filesystem::remove_all(directory);
}
} // namespace

int main() {
    try {
        check_accounting_boundaries();
        check_real_store_rejection();
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    return 0;
}
