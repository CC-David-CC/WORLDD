/* SPDX-License-Identifier: MIT
 * Additive native lifetime observations. No production authority or routing path.
 */
#include <dix-config.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <X11/extensions/XI2.h>
#include <X11/extensions/XI2proto.h>
#include "dixstruct.h"
#include "extnsionst.h"
#include "spatialinputobserver_lifecycle_v3.h"
#include "spatialinputobserver_endpoint.h"
#include "spatialinputobserver_serialize_v2.h"

typedef Spatial__Testing__V2__ObserverArmV2 Arm;
typedef Spatial__Testing__V2__ApplicationIdentity App;
typedef Spatial__Testing__V2__ClientIdentity Client;
typedef Spatial__Testing__V2__WindowIdentity ObserverWindow;
typedef Spatial__Testing__V2__DeviceIdentity Device;
typedef Spatial__Testing__V2__SeatContext Seat;
typedef Spatial__Testing__V2__DeviceSnapshot DeviceSnapshot;
typedef Spatial__Testing__V2__TransitionSnapshot Snapshot;
typedef Spatial__Testing__V3__LifecycleArmV3 LifecycleArm;
typedef Spatial__Testing__V3__NativeInputLifecycleRecordV3 Record;
#define CAUSE(name) SPATIAL__TESTING__V3__LIFECYCLE_CAUSE_V3__##name
#define MAX_FRAME 8192u
#define MAX_OUTPUT (3u * (MAX_FRAME + 2u))

typedef struct {
    uint64_t ns, generation;
    SpatialObserverLifecycleRoutingV3 routing;
    SpatialObserverWindowPin target;
    SpatialObserverNativeState native_old, native_peer;
    SpatialObserverContextV2 context_old, context_peer;
} Sample;
static Sample samples[4];
static struct {
    uint64_t ns, resource_free_ns, generation_ns, generation, destroy_ns;
    Bool destroy_observed, destroy_result, window_absent, seat_absent, devices_absent, complete;
    unsigned removed;
    struct { int id, result; uint64_t ns; } removals[4];
} retirement;
static Bool registered, running, failed, complete;
static unsigned captured;
static uint64_t ordinal;
static int xi_opcode;

static uint64_t now_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) || now.tv_sec < 0 || now.tv_nsec < 0 ||
        now.tv_nsec >= 1000000000L ||
        (uint64_t)now.tv_sec > (UINT64_MAX - (uint64_t)now.tv_nsec) / UINT64_C(1000000000)) return 0;
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

static void refuse(const char *reason)
{
    failed = TRUE;
    SpatialObserverEndpointFailV2(reason);
}

/* Keep the v2 wire builders and native validation semantics unchanged. */
static void app_wire(App *wire, const SpatialSelectionNamespace *label)
{
    spatial__testing__v2__application_identity__init(wire);
    wire->workspace_id.data = (uint8_t *)label->workspace;
    wire->workspace_id.len = label->workspace_size;
    wire->application_id.data = (uint8_t *)label->application;
    wire->application_id.len = label->application_size;
}

static Bool client_wire(Client *wire, App *app, const SpatialObserverNativeClientPin *pin)
{
    spatial__testing__v2__client_identity__init(wire);
    if (!pin || !pin->epoch || pin->index <= 0 ||
        !SpatialObserverMapNativeRoleV2(pin->role, &wire->role)) return FALSE;
    wire->client_id = pin->index; wire->native_object_epoch = pin->epoch;
    if (pin->role == SPATIAL_OBSERVER_NATIVE_CLIENT_APPLICATION) {
        app_wire(app, &pin->label); wire->application = app;
    }
    return TRUE;
}

typedef struct {
    ObserverWindow wire; Client owner; App app;
    /* Protobuf bytes borrow this label until the entire record graph is packed. */
    SpatialObserverNativeClientPin owner_pin;
} WireWindow;
static Bool window_wire(WireWindow *wire, const SpatialObserverWindowPin *pin, uint64_t generation)
{
    spatial__testing__v2__window_identity__init(&wire->wire);
    wire->owner_pin = (SpatialObserverNativeClientPin) { .session = pin->owner.session,
        .epoch = pin->owner.epoch, .index = pin->owner.index,
        .role = SPATIAL_OBSERVER_NATIVE_CLIENT_APPLICATION, .label = pin->owner.label };
    if (!pin->xid || !pin->epoch || !generation ||
        !client_wire(&wire->owner, &wire->app, &wire->owner_pin)) return FALSE;
    wire->wire.xid = pin->xid; wire->wire.native_object_epoch = pin->epoch;
    wire->wire.spatial_generation = generation; wire->wire.application = &wire->app;
    wire->wire.owner = &wire->owner;
    return TRUE;
}

static Bool same(const ProtobufCMessage *a, const ProtobufCMessage *b)
{
    uint8_t left[4096], right[4096];
    if (!a || !b || a->descriptor != b->descriptor) return FALSE;
    size_t n = protobuf_c_message_get_packed_size(a);
    return n <= sizeof(left) && n == protobuf_c_message_get_packed_size(b) &&
        protobuf_c_message_pack(a, left) == n && protobuf_c_message_pack(b, right) == n &&
        !memcmp(left, right, n);
}

static void device_wire(Device *wire, const SpatialObserverNativeDeviceState *state)
{
    spatial__testing__v2__device_identity__init(wire);
    wire->device_id = state->identity.id; wire->native_object_epoch = state->identity.epoch;
    wire->kind = state->has_button_class ? 1 : 2;
    wire->role = state->identity.role == SPATIAL_OBSERVER_POINTER_MASTER ||
        state->identity.role == SPATIAL_OBSERVER_KEYBOARD_MASTER ? 1 : 2;
    wire->paired_or_attached = state->paired_or_attached;
}

typedef struct {
    DeviceSnapshot wire; Device identity;
    Spatial__Testing__V2__GrabIdentity grab;
    Client grab_owner; App grab_app;
    WireWindow grab_window, confine, focus;
} WireDevice;

static Bool snapshot_device(WireDevice *out, const SpatialObserverNativeDeviceState *native,
                             const SpatialObserverDeviceContextV2 *context, uint64_t generation)
{
    spatial__testing__v2__device_snapshot__init(&out->wire);
    device_wire(&out->identity, native); out->wire.identity = &out->identity;
    out->wire.bitmap_valid = TRUE; out->wire.bitmap_bits = 256;
    out->wire.down_bitmap.len = out->wire.postdown_bitmap.len = 32;
    out->wire.down_bitmap.data = (uint8_t *)(native->has_button_class ? native->button_down : native->key_down);
    out->wire.postdown_bitmap.data = (uint8_t *)(native->has_button_class ? native->button_postdown : native->key_postdown);
    out->wire.num_buttons = native->num_buttons; out->wire.buttons_down = native->buttons_down;
    out->wire.button_state = native->button_state; out->wire.active_grab = native->active_grab;
    out->wire.native_frozen = native->native_frozen; out->wire.frozen_by_other_grab = native->frozen_by_other_grab;
    out->wire.native_sync_state = native->native_sync_state; out->wire.pending_events = native->pending_events;
    out->wire.replay_device = native->replay_device; out->wire.has_focus_class = native->has_focus_class;
    out->wire.focus_kind = native->focus_kind; out->wire.focus_revert = native->focus_revert;
    if (!context->complete || native->identity.id != context->device.id ||
        native->identity.epoch != context->device.epoch || native->active_grab != context->active_grab ||
        native->has_focus_class != context->has_focus_class || native->focus_kind != context->focus_kind ||
        native->focus_revert != context->focus_revert) return FALSE;
    if (native->active_grab) {
        spatial__testing__v2__grab_identity__init(&out->grab);
        if (!client_wire(&out->grab_owner, &out->grab_app, &context->grab_owner) ||
            !window_wire(&out->grab_window, &context->grab_window, generation)) return FALSE;
        out->grab.resource = context->grab_resource; out->grab.owner = &out->grab_owner;
        out->grab.window = &out->grab_window.wire; out->grab.device = &out->identity;
        if (context->has_confine_window) {
            if (!window_wire(&out->confine, &context->confine_window, generation)) return FALSE;
            out->grab.confine_to = &out->confine.wire;
        }
        out->wire.grab = &out->grab;
    }
    if (context->focus_is_target) {
        if (!window_wire(&out->focus, &context->focus_window, generation)) return FALSE;
        out->wire.native_focus = &out->focus.wire;
    }
    out->wire.grab_complete = out->wire.context_complete = out->wire.focus_complete = TRUE;
    return TRUE;
}

static Bool seat_wire(Seat *wire, Client *owner, App *owner_app,
    const SpatialObserverSeatStateV2 *seat, const Arm *arm, WireWindow *target, Bool peer)
{
    spatial__testing__v2__seat_context__init(wire);
    if (!client_wire(owner, owner_app, &seat->owner)) return FALSE;
    wire->actor = peer ? arm->peer_actor : arm->old_actor;
    wire->world_actor_id = peer ? arm->peer_world_actor_id : arm->old_world_actor_id;
    wire->native_seat_id = seat->id; wire->owner = owner;
    wire->presentation_generation = arm->presentation_generation;
    wire->tracked_keys.data = (uint8_t *)seat->keys; wire->tracked_keys.len = sizeof(seat->keys);
    wire->tracked_buttons = seat->buttons;
    if (seat->target) {
        if (seat->target != target->wire.xid || seat->target_generation != target->wire.spatial_generation) return FALSE;
        wire->target = &target->wire;
    }
    if (seat->focus) {
        if (seat->focus != target->wire.xid || seat->focus_generation != target->wire.spatial_generation) return FALSE;
        wire->focus = &target->wire;
    }
    if (seat->application) { wire->application = &target->app; wire->application_controller_native_seat_id = seat->controller; }
    wire->complete = TRUE;
    return TRUE;
}

static Bool device_pins_match(const SpatialObserverNativeState *native,
                              size_t count, Device **expected)
{
    if (!native || !native->valid || count != 4) return FALSE;
    for (unsigned i = 0; i < 4; ++i) {
        Device wire; unsigned matches = 0;
        device_wire(&wire, &native->devices[i]);
        for (size_t j = 0; j < count; ++j)
            if (expected[j] && same(&wire.base, &expected[j]->base)) ++matches;
        if (matches != 1) return FALSE;
    }
    return TRUE;
}

static Bool window_matches(const SpatialObserverWindowPin *pin, uint64_t generation,
                           const ObserverWindow *expected)
{
    WireWindow wire;
    return expected && window_wire(&wire, pin, generation) && same(&wire.wire.base, &expected->base);
}

static Bool interval(const LifecycleArm *arm, uint64_t ns)
{
    return ns && ns >= arm->common->arm_monotonic_ns && ns < arm->common->deadline_monotonic_ns;
}

static Bool old_clear(Sample *sample, const LifecycleArm *arm)
{
    return sample->routing.old_present && sample->routing.old.id == arm->common->old_native_seat_id &&
        SpatialObserverSnapshotDevices(&sample->routing.old.devices, &sample->native_old) &&
        device_pins_match(&sample->native_old, arm->common->n_old_actor_devices, arm->common->old_actor_devices) &&
        SpatialObserverSnapshotClearContextV3(&sample->routing.old.devices, &sample->context_old);
}

void SpatialObserverLifecycleCaptureV3(unsigned phase,
    const SpatialObserverLifecycleRoutingV3 *routing,
    const SpatialObserverWindowPin *borrowed_pin, WindowPtr borrowed_window)
{
    const LifecycleArm *arm = SpatialObserverEndpointArmV3();
    if (!arm || failed || complete || (phase < 4 && captured >= 3)) return;
    /* The freeing callback samples PRE_CANCEL before its epoch is invalidated.
     * The later cancellation hook must neither replace it nor retain a pointer. */
    if (phase == 1 && arm->cause == CAUSE(TARGET_DESTRUCTION) && !borrowed_window) {
        if (captured == 1 && retirement.resource_free_ns) return;
        refuse("lifecycle destruction lacks callback-local pre-cancel"); return;
    }
    if (running || phase != captured + 1 || phase > 4 || !routing ||
        routing->peer.id != arm->common->peer_native_seat_id ||
        !window_matches(&routing->peer_window, routing->peer_generation, arm->peer_window) ||
        (phase == 4 && !retirement.complete)) {
        refuse("lifecycle transition order or identity"); return;
    }
    Sample *sample = &samples[captured];
    memset(sample, 0, sizeof(*sample));
    sample->ns = now_ns(); sample->routing = *routing;
    if (!interval(arm, sample->ns)) { refuse("lifecycle capture deadline"); return; }
    if (phase == 1) {
        sample->generation = arm->common->expected_window->spatial_generation;
        if (!routing->old_present || routing->old.id != arm->common->old_native_seat_id ||
            (borrowed_window && (!borrowed_pin || arm->cause != CAUSE(TARGET_DESTRUCTION)))) {
            refuse("lifecycle pre-cancel seat or borrowed identity"); return;
        }
        if (borrowed_pin) sample->target = *borrowed_pin;
        else if (!SpatialObserverPinWindow(arm->common->expected_window->xid, &sample->target)) {
            refuse("lifecycle pre-cancel live window missing"); return;
        }
        if (!window_matches(&sample->target, sample->generation, arm->common->expected_window) ||
            !SpatialObserverSnapshotDevices(&routing->old.devices, &sample->native_old) ||
            !device_pins_match(&sample->native_old, arm->common->n_old_actor_devices, arm->common->old_actor_devices) ||
            !(borrowed_window ? SpatialObserverSnapshotBorrowedContextV3(&routing->old.devices,
                  &sample->target, borrowed_window, &sample->context_old) :
              SpatialObserverSnapshotContextV2(&routing->old.devices, &sample->target, &sample->context_old))) {
            refuse("lifecycle pre-cancel snapshot incomplete"); return;
        }
        if (borrowed_window) retirement.resource_free_ns = sample->ns;
    } else if (phase < 4) {
        sample->target = samples[0].target; sample->generation = samples[0].generation;
        if (!old_clear(sample, arm)) { refuse("lifecycle cancellation native snapshot incomplete"); return; }
    } else {
        sample->target = routing->peer_window; sample->generation = routing->peer_generation;
        if ((arm->cause == CAUSE(TARGET_DESTRUCTION) && !old_clear(sample, arm)) ||
            (arm->cause != CAUSE(TARGET_DESTRUCTION) && routing->old_present) ||
            !SpatialObserverSnapshotDevices(&routing->peer.devices, &sample->native_peer) ||
            !device_pins_match(&sample->native_peer, arm->common->n_peer_actor_devices, arm->common->peer_actor_devices) ||
            !SpatialObserverSnapshotClearContextV3(&routing->peer.devices, &sample->context_peer)) {
            refuse("lifecycle peer-bind snapshot incomplete"); return;
        }
    }
    ++captured;
}

void SpatialObserverLifecycleGenerationV3(XID xid, uint64_t generation)
{
    const LifecycleArm *arm = SpatialObserverEndpointArmV3();
    if (!arm || failed || complete || arm->cause != CAUSE(TARGET_DESTRUCTION) ||
        xid != arm->common->expected_window->xid || retirement.generation_ns) return;
    uint64_t ns = now_ns();
    if (captured != 3 || !retirement.resource_free_ns || !interval(arm, ns) ||
        generation <= arm->common->expected_window->spatial_generation ||
        generation <= arm->peer_window->spatial_generation) {
        refuse("lifecycle spatial generation boundary"); return;
    }
    retirement.generation_ns = ns; retirement.generation = generation;
}

void SpatialObserverLifecycleDestroyReturnedV3(XID xid, Bool result)
{
    const LifecycleArm *arm = SpatialObserverEndpointArmV3();
    if (!arm || failed || complete || arm->cause != CAUSE(TARGET_DESTRUCTION) ||
        xid != arm->common->expected_window->xid || retirement.destroy_observed) return;
    retirement.destroy_ns = retirement.ns = now_ns();
    retirement.destroy_observed = TRUE; retirement.destroy_result = result;
    retirement.window_absent = SpatialObserverWindowAbsentV3(xid);
    retirement.complete = captured == 3 && retirement.resource_free_ns && retirement.generation_ns &&
        retirement.generation_ns <= retirement.ns && interval(arm, retirement.ns) && result && retirement.window_absent;
    if (!retirement.complete) refuse("lifecycle destruction completion missing");
}

void SpatialObserverLifecycleDeviceRemovedV3(uint64_t seat, int device_id, int result)
{
    const LifecycleArm *arm = SpatialObserverEndpointArmV3();
    if (!arm || failed || complete || arm->cause == CAUSE(TARGET_DESTRUCTION) ||
        seat != arm->common->old_native_seat_id) return;
    uint64_t ns = now_ns(); unsigned matches = 0;
    for (unsigned i = 0; i < 4; ++i)
        if (samples[2].native_old.devices[i].identity.id == device_id) ++matches;
    Bool duplicate = FALSE;
    for (unsigned i = 0; i < retirement.removed; ++i)
        if (retirement.removals[i].id == device_id) duplicate = TRUE;
    if (captured != 3 || retirement.removed >= 4 || matches != 1 || duplicate ||
        !interval(arm, ns) || ns < samples[2].ns) {
        refuse("lifecycle device removal identity or order"); return;
    }
    unsigned i = retirement.removed++;
    retirement.removals[i].id = device_id; retirement.removals[i].result = result;
    retirement.removals[i].ns = ns;
    if (result != Success) refuse("lifecycle native RemoveDevice failed");
}

void SpatialObserverLifecycleSeatRemovedV3(uint64_t seat)
{
    const LifecycleArm *arm = SpatialObserverEndpointArmV3();
    if (!arm || failed || complete || arm->cause == CAUSE(TARGET_DESTRUCTION) ||
        seat != arm->common->old_native_seat_id) return;
    retirement.ns = now_ns();
    retirement.seat_absent = SpatialInputObserverSeatAbsentV3(seat);
    retirement.devices_absent = SpatialObserverDeviceIdsAbsentV3(&samples[2].routing.old.devices);
    retirement.complete = captured == 3 && retirement.removed == 4 && retirement.seat_absent &&
        retirement.devices_absent && interval(arm, retirement.ns) &&
        retirement.removals[3].ns <= retirement.ns;
    if (!retirement.complete) refuse("lifecycle native retirement incomplete");
}

typedef struct {
    Snapshot wire;
    Seat old, peer; Client owners[2]; App owner_apps[2]; WireWindow window;
    WireDevice devices[8]; DeviceSnapshot *device_ptrs[8];
} WireSnapshot;

static Bool snapshot_wire(WireSnapshot *out, const Sample *sample, const Arm *arm,
                          unsigned phase, Bool include_old, Bool include_peer)
{
    spatial__testing__v2__transition_snapshot__init(&out->wire);
    if (!window_wire(&out->window, &sample->target, sample->generation)) return FALSE;
    out->wire.phase = phase; out->wire.monotonic_ns = sample->ns;
    out->wire.application = &out->window.app;
    out->wire.controller_native_seat_id = sample->routing.controller;
    unsigned count = 0;
    if (include_old) {
        if (!sample->routing.old_present || !seat_wire(&out->old, &out->owners[0], &out->owner_apps[0],
            &sample->routing.old, arm, &out->window, FALSE)) return FALSE;
        out->wire.old_seat = &out->old;
    }
    if (include_peer) {
        if (!seat_wire(&out->peer, &out->owners[1], &out->owner_apps[1],
            &sample->routing.peer, arm, &out->window, TRUE)) return FALSE;
        out->wire.peer_seat = &out->peer;
    }
    for (unsigned side = 0; side < 2; ++side) {
        if (!(side ? include_peer : include_old)) continue;
        const SpatialObserverNativeState *native = side ? &sample->native_peer : &sample->native_old;
        const SpatialObserverContextV2 *context = side ? &sample->context_peer : &sample->context_old;
        if (!native->valid || !context->valid) return FALSE;
        out->wire.global_pending |= native->any_pending_events;
        out->wire.replay_pending |= native->replay_pending;
        out->wire.replay_in_progress |= native->replay_in_progress;
        for (unsigned i = 0; i < 4; ++i) {
            if (!snapshot_device(&out->devices[count], &native->devices[i], &context->devices[i], sample->generation))
                return FALSE;
            out->device_ptrs[count] = &out->devices[count].wire; ++count;
        }
    }
    out->wire.n_devices = count; out->wire.devices = out->device_ptrs; out->wire.complete = TRUE;
    return TRUE;
}

static Bool output_records(Record records[3])
{
    uint8_t output[MAX_OUTPUT]; size_t sizes[3], length = 0;
    for (unsigned i = 0; i < 3; ++i) {
        if (!protobuf_c_message_check(&records[i].base)) return FALSE;
        sizes[i] = spatial__testing__v3__native_input_lifecycle_record_v3__get_packed_size(&records[i]);
        if (!sizes[i] || sizes[i] > MAX_FRAME) return FALSE;
    }
    for (unsigned i = 0; i < 3; ++i) {
        size_t value = sizes[i];
        while (value > 127) { output[length++] = (uint8_t)((value & 127) | 128); value >>= 7; }
        output[length++] = (uint8_t)value;
        if (length + sizes[i] > sizeof(output) ||
            spatial__testing__v3__native_input_lifecycle_record_v3__pack(&records[i], output + length) != sizes[i]) return FALSE;
        length += sizes[i];
    }
    return SpatialObserverEndpointOutputV3(output, length);
}

static Bool serialize(const LifecycleArm *arm, const xXIDeviceEvent *event,
                       const SpatialObserverNativeClientPin *recipient, uint64_t ns)
{
    const Arm *common = arm->common;
    WireSnapshot cancellation[3], peer_bind, fresh_old;
    Snapshot *cancel_ptrs[3];
    for (unsigned i = 0; i < 3; ++i) {
        if (!snapshot_wire(&cancellation[i], &samples[i], common, i + 1, TRUE, FALSE)) return FALSE;
        cancel_ptrs[i] = &cancellation[i].wire;
    }
    Bool destruction = arm->cause == CAUSE(TARGET_DESTRUCTION);
    if (!snapshot_wire(&peer_bind, &samples[3], common, 4, destruction, TRUE)) return FALSE;
    Spatial__Testing__V3__LifecycleRetirementV3 retired = SPATIAL__TESTING__V3__LIFECYCLE_RETIREMENT_V3__INIT;
    retired.monotonic_ns = retirement.ns; retired.cause = arm->cause;
    retired.old_window = &cancellation[0].window.wire; retired.old_native_seat_id = common->old_native_seat_id;
    retired.resource_free_monotonic_ns = retirement.resource_free_ns;
    retired.destroy_hook_return_monotonic_ns = retirement.destroy_ns;
    retired.old_window_resource_absent = retirement.window_absent;
    retired.old_native_seat_absent = retirement.seat_absent; retired.all_old_device_ids_absent = retirement.devices_absent;
    retired.spatial_generation_invalidated_monotonic_ns = retirement.generation_ns;
    retired.invalidated_spatial_generation = retirement.generation;
    retired.destroy_hook_observed = retirement.destroy_observed; retired.destroy_hook_result = retirement.destroy_result;
    retired.complete = retirement.complete;
    Spatial__Testing__V3__DeviceRemovalV3 removals[4], *removal_ptrs[4];
    Device old_identities[4], *identity_ptrs[4];
    for (unsigned i = 0; i < 4; ++i) {
        device_wire(&old_identities[i], &samples[2].native_old.devices[i]);
        identity_ptrs[i] = &old_identities[i];
    }
    if (!destruction) {
        if (retirement.removed != 4) return FALSE;
        for (unsigned i = 0; i < 4; ++i) {
            spatial__testing__v3__device_removal_v3__init(&removals[i]);
            for (unsigned j = 0; j < 4; ++j)
                if (old_identities[j].device_id == (uint32_t)retirement.removals[i].id) removals[i].identity = &old_identities[j];
            if (!removals[i].identity) return FALSE;
            removals[i].remove_result = retirement.removals[i].result;
            removals[i].monotonic_ns = retirement.removals[i].ns; removals[i].removal_observed = TRUE;
            removal_ptrs[i] = &removals[i];
        }
        retired.n_removals = 4; retired.removals = removal_ptrs;
    }
    Spatial__Testing__V3__LifecyclePeerStateV3 state = SPATIAL__TESTING__V3__LIFECYCLE_PEER_STATE_V3__INIT;
    Spatial__Testing__V3__DeviceRetirementCheckV3 check = SPATIAL__TESTING__V3__DEVICE_RETIREMENT_CHECK_V3__INIT;
    Sample fresh; memset(&fresh, 0, sizeof(fresh)); fresh.ns = ns;
    if (!SpatialInputObserverLifecycleRoutingV3(&fresh.routing) ||
        fresh.routing.controller != common->peer_native_seat_id ||
        !window_matches(&fresh.routing.peer_window, fresh.routing.peer_generation, arm->peer_window)) return FALSE;
    SpatialObserverNativeState peer_now;
    if (!SpatialObserverSnapshotDevices(&fresh.routing.peer.devices, &peer_now) ||
        !device_pins_match(&peer_now, common->n_peer_actor_devices, common->peer_actor_devices)) return FALSE;
    if (destruction) {
        fresh.target = samples[0].target; fresh.generation = samples[0].generation;
        if (!old_clear(&fresh, arm) || !SpatialObserverWindowAbsentV3(common->expected_window->xid) ||
            !snapshot_wire(&fresh_old, &fresh, common, 4, TRUE, FALSE)) return FALSE;
        state.old_state_case = SPATIAL__TESTING__V3__LIFECYCLE_PEER_STATE_V3__OLD_STATE_LIVE_OLD;
        state.live_old = &fresh_old.wire; state.old_window_resource_absent = TRUE;
    } else {
        check.monotonic_ns = ns; check.old_native_seat_id = common->old_native_seat_id;
        check.old_native_seat_absent = !fresh.routing.old_present && SpatialInputObserverSeatAbsentV3(common->old_native_seat_id);
        check.all_old_device_ids_absent = SpatialObserverDeviceIdsAbsentV3(&samples[2].routing.old.devices);
        if (!check.old_native_seat_absent || !check.all_old_device_ids_absent) return FALSE;
        check.n_retired_devices = 4; check.retired_devices = identity_ptrs; check.complete = TRUE;
        state.old_state_case = SPATIAL__TESTING__V3__LIFECYCLE_PEER_STATE_V3__OLD_STATE_RETIRED_OLD;
        state.retired_old = &check;
    }
    state.monotonic_ns = ns; state.complete = TRUE;
    Client probe; App probe_app; Device device, source;
    if (!client_wire(&probe, &probe_app, recipient)) return FALSE;
    device_wire(&device, &peer_now.devices[SPATIAL_OBSERVER_POINTER_MASTER]);
    device_wire(&source, &peer_now.devices[SPATIAL_OBSERVER_POINTER_SLAVE]);
    if (event->deviceid != device.device_id || event->sourceid != source.device_id ||
        !same(&probe.base, &common->expected_recipient->base) ||
        !same(&peer_bind.window.wire.base, &arm->peer_window->base)) return FALSE;
    Spatial__Testing__V2__ObserverPress press = SPATIAL__TESTING__V2__OBSERVER_PRESS__INIT;
    press.observer_ordinal = ordinal; press.callback_monotonic_ns = ns;
    press.recipient = &probe; press.window = &peer_bind.window.wire; press.device = &device; press.source = &source;
    press.xi2_event_type = event->evtype; press.detail = event->detail; press.event_time_ms = event->time;
    Spatial__Testing__V3__LifecyclePeerPressV3 peer_press = SPATIAL__TESTING__V3__LIFECYCLE_PEER_PRESS_V3__INIT;
    peer_press.press = &press; peer_press.state = &state;
    Spatial__Testing__V3__LifecycleArmAcceptedV3 accepted = SPATIAL__TESTING__V3__LIFECYCLE_ARM_ACCEPTED_V3__INIT;
    accepted.accepted_command = (LifecycleArm *)arm;
    Spatial__Testing__V3__LifecycleTransitionV3 transition = SPATIAL__TESTING__V3__LIFECYCLE_TRANSITION_V3__INIT;
    transition.n_cancellation = 3; transition.cancellation = cancel_ptrs;
    transition.retirement = &retired; transition.peer_bind = &peer_bind.wire;
    Record records[3];
    for (unsigned i = 0; i < 3; ++i) {
        spatial__testing__v3__native_input_lifecycle_record_v3__init(&records[i]);
        records[i].protocol_version = 3; records[i].session_id = common->session_id; records[i].arm_id = common->arm_id;
        records[i].stream_sequence = i + 1; records[i].native_session_epoch = SpatialObserverLifetimeSession();
        records[i].payload_case = 10 + i;
    }
    records[0].arm = &accepted; records[1].native_transition = &transition; records[2].observer_press = &peer_press;
    return output_records(records);
}

static void outgoing(CallbackListPtr *list, void *closure, void *data)
{
    (void)list; (void)closure;
    const LifecycleArm *arm = SpatialObserverEndpointArmV3();
    if (!arm || failed || complete) return;
    if (running || ordinal == UINT64_MAX) { refuse("lifecycle callback reentry or overflow"); return; }
    if (!xi_opcode) {
        ExtensionEntry *xi = CheckExtension("XInputExtension");
        if (!xi || xi->base <= 0 || xi->base > UINT8_MAX) { refuse("lifecycle XI extension missing"); return; }
        xi_opcode = xi->base;
    }
    EventInfoRec *info = data;
    if (!info || !info->client || !info->events || info->count <= 0 || info->count > 64) {
        refuse("lifecycle output callback shape"); return;
    }
    for (int i = 0; i < info->count; ++i) {
        const xEvent *raw = &info->events[i];
        if ((raw->u.u.type & 0x7f) != GenericEvent) continue;
        if (info->count != 1) { refuse("lifecycle output callback batch"); return; }
        const xGenericEvent *generic = (const xGenericEvent *)raw;
        if (generic->extension != xi_opcode || generic->evtype != XI_ButtonPress) return;
        uint64_t bytes = sizeof(xEvent) + (uint64_t)generic->length * 4;
        if (bytes < sizeof(xXIDeviceEvent) || bytes > 4096) { refuse("lifecycle callback length"); return; }
        xXIDeviceEvent event; memcpy(&event, raw, sizeof(event));
        Bool peer = FALSE;
        for (size_t j = 0; j < arm->common->n_peer_actor_devices; ++j)
            if (event.deviceid == arm->common->peer_actor_devices[j]->device_id ||
                event.sourceid == arm->common->peer_actor_devices[j]->device_id) peer = TRUE;
        if (!peer) return;
        ++ordinal;
        if (event.buttons_len != DOWN_LENGTH / 4 || event.valuators_len != (MAX_VALUATORS + 31) / 32) {
            refuse("lifecycle peer output mask shape"); return;
        }
        uint64_t minimum = sizeof(event) + (uint64_t)(event.buttons_len + event.valuators_len) * 4;
        if (minimum > bytes) { refuse("lifecycle peer masks truncated"); return; }
        const uint8_t *mask = (const uint8_t *)raw + sizeof(event) + event.buttons_len * 4;
        unsigned values = 0;
        for (unsigned bit = 0; bit < event.valuators_len * 32; ++bit) {
            if (!(mask[bit / 8] & (1u << (bit % 8)))) continue;
            if (bit >= MAX_VALUATORS) { refuse("lifecycle peer valuator range"); return; }
            ++values;
        }
        if (minimum + (uint64_t)values * sizeof(FP3232) != bytes) {
            refuse("lifecycle peer valuator length"); return;
        }
        uint64_t ns = now_ns(); SpatialObserverNativeClientPin recipient;
        if (raw->u.u.type != GenericEvent || captured != 4 || !retirement.complete ||
            event.event != arm->peer_window->xid || event.detail != arm->common->expected_detail ||
            !interval(arm, ns) || !SpatialObserverPinNativeClient(info->client->index, &recipient)) {
            refuse("lifecycle peer output tuple or lifetime"); return;
        }
        running = TRUE;
        Bool ok = serialize(arm, &event, &recipient, ns);
        running = FALSE;
        if (!ok) refuse("lifecycle first-peer snapshot or serialization");
        else complete = TRUE;
        return;
    }
}

Bool SpatialObserverLifecycleInitV3(void)
{
    if (registered || running) return FALSE;
    captured = 0; ordinal = 0; xi_opcode = 0; failed = complete = FALSE;
    memset(samples, 0, sizeof(samples)); memset(&retirement, 0, sizeof(retirement));
    registered = AddCallback(&EventCallback, outgoing, NULL);
    return registered;
}

void SpatialObserverLifecycleShutdownV3(void)
{
    if (running) { refuse("lifecycle shutdown during callback"); return; }
    if (registered && DeleteCallback(&EventCallback, outgoing, NULL)) registered = FALSE;
    captured = 0; ordinal = 0; failed = complete = FALSE;
    memset(samples, 0, sizeof(samples)); memset(&retirement, 0, sizeof(retirement));
}
