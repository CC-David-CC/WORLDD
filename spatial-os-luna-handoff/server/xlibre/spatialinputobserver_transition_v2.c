/* SPDX-License-Identifier: MIT
 * Copy live native state during serialized dispatch. The probe owns its receipts.
 */
#include <dix-config.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <X11/extensions/XI2.h>
#include <X11/extensions/XI2proto.h>
#include "dixstruct.h"
#include "extnsionst.h"
#include "spatialinputobserver_transition_v2.h"
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
typedef Spatial__Testing__V2__NativeInputObservationRecordV2 Record;

static Bool registered, running, failed, complete;
static unsigned captured;
static uint64_t ordinal;
static int xi_opcode;
static struct {
    uint64_t ns, generation, controller;
    SpatialObserverSeatStateV2 old, peer;
    SpatialObserverWindowPin target;
    SpatialObserverNativeState native_old, native_peer;
    SpatialObserverContextV2 context_old, context_peer;
} samples[4];

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

void SpatialObserverTransitionResetV2(void)
{
    captured = 0; ordinal = 0; failed = complete = FALSE;
    memset(samples, 0, sizeof(samples));
}

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

void SpatialObserverTransitionCaptureV2(unsigned phase,
    const SpatialObserverSeatStateV2 *old, const SpatialObserverSeatStateV2 *peer,
    const SpatialObserverWindowPin *target, uint64_t generation, uint64_t controller)
{
    const Arm *arm = SpatialObserverEndpointArmV2();
    if (!arm || failed || complete || (phase < 4 && captured >= 3)) return;
    if (running || phase != captured + 1 || phase > 4 || !old || !peer || !target) {
        refuse("native transition order or context"); return;
    }
    uint64_t ns = now_ns();
    if (!ns || ns < arm->arm_monotonic_ns || ns >= arm->deadline_monotonic_ns ||
        old->id != arm->old_native_seat_id || peer->id != arm->peer_native_seat_id ||
        target->session != SpatialObserverLifetimeSession()) {
        refuse("native transition deadline or seat identity"); return;
    }
    samples[captured].ns = ns; samples[captured].generation = generation;
    samples[captured].controller = controller; samples[captured].old = *old;
    samples[captured].peer = *peer; samples[captured].target = *target;
    if (!SpatialObserverSnapshotDevices(&old->devices, &samples[captured].native_old) ||
        !SpatialObserverSnapshotContextV2(&old->devices, target, &samples[captured].context_old) ||
        (phase == 4 && (!SpatialObserverSnapshotDevices(&peer->devices, &samples[captured].native_peer) ||
        !SpatialObserverSnapshotContextV2(&peer->devices, target, &samples[captured].context_peer)))) {
        refuse("native transition snapshot incomplete"); return;
    }
    ++captured;
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

static Bool serialize(const Arm *arm, const xXIDeviceEvent *event,
                       const SpatialObserverNativeClientPin *recipient, uint64_t ns)
{
    Snapshot snapshots[4], *snapshot_ptrs[4];
    Seat old[4], peer; Client seat_owners[5]; App seat_apps[5]; WireWindow windows[4];
    WireDevice devices[4][8]; DeviceSnapshot *device_ptrs[4][8];
    for (unsigned phase = 0; phase < 4; ++phase) {
        spatial__testing__v2__transition_snapshot__init(&snapshots[phase]);
        if (!window_wire(&windows[phase], &samples[phase].target, samples[phase].generation) ||
            !seat_wire(&old[phase], &seat_owners[phase], &seat_apps[phase], &samples[phase].old, arm, &windows[phase], FALSE)) return FALSE;
        snapshots[phase].phase = phase + 1; snapshots[phase].monotonic_ns = samples[phase].ns;
        snapshots[phase].old_seat = &old[phase]; snapshots[phase].application = &windows[phase].app;
        snapshots[phase].controller_native_seat_id = samples[phase].controller;
        snapshots[phase].global_pending = samples[phase].native_old.any_pending_events;
        snapshots[phase].replay_pending = samples[phase].native_old.replay_pending;
        snapshots[phase].replay_in_progress = samples[phase].native_old.replay_in_progress;
        unsigned count = phase == 3 ? 8 : 4;
        for (unsigned i = 0; i < count; ++i) {
            const SpatialObserverNativeState *native = i < 4 ? &samples[phase].native_old : &samples[phase].native_peer;
            const SpatialObserverContextV2 *context = i < 4 ? &samples[phase].context_old : &samples[phase].context_peer;
            if (!snapshot_device(&devices[phase][i], &native->devices[i % 4], &context->devices[i % 4], samples[phase].generation)) return FALSE;
            device_ptrs[phase][i] = &devices[phase][i].wire;
        }
        snapshots[phase].n_devices = count; snapshots[phase].devices = device_ptrs[phase]; snapshots[phase].complete = TRUE;
        snapshot_ptrs[phase] = &snapshots[phase];
    }
    if (!seat_wire(&peer, &seat_owners[4], &seat_apps[4], &samples[3].peer, arm, &windows[3], TRUE)) return FALSE;
    snapshots[3].peer_seat = &peer;
    Spatial__Testing__V2__Arm accepted = SPATIAL__TESTING__V2__ARM__INIT;
    Spatial__Testing__V2__NativeTransition transition = SPATIAL__TESTING__V2__NATIVE_TRANSITION__INIT;
    Spatial__Testing__V2__ObserverPress press = SPATIAL__TESTING__V2__OBSERVER_PRESS__INIT;
    Client probe; App probe_app;
    if (!client_wire(&probe, &probe_app, recipient)) return FALSE;
    Device device, source;
    const SpatialObserverNativeState *peer_native = &samples[3].native_peer;
    device_wire(&device, &peer_native->devices[SPATIAL_OBSERVER_POINTER_MASTER]);
    device_wire(&source, &peer_native->devices[SPATIAL_OBSERVER_POINTER_SLAVE]);
    if (event->deviceid != device.device_id || event->sourceid != source.device_id ||
        !same(&probe.base, &arm->expected_recipient->base) ||
        !same(&windows[3].wire.base, &arm->expected_window->base)) return FALSE;
    accepted.accepted_command = (Arm *)arm; transition.n_snapshots = 4; transition.snapshots = snapshot_ptrs;
    press.observer_ordinal = ordinal; press.callback_monotonic_ns = ns;
    press.recipient = &probe; press.window = &windows[3].wire; press.device = &device; press.source = &source;
    press.xi2_event_type = event->evtype; press.detail = event->detail; press.event_time_ms = event->time;
    Record records[3], *record_ptrs[3];
    for (unsigned i = 0; i < 3; ++i) {
        spatial__testing__v2__native_input_observation_record_v2__init(&records[i]);
        records[i].protocol_version = 2; records[i].session_id = arm->session_id; records[i].arm_id = arm->arm_id;
        records[i].stream_sequence = i + 1; records[i].native_session_epoch = SpatialObserverLifetimeSession();
        records[i].payload_case = 10 + i; record_ptrs[i] = &records[i];
    }
    records[0].arm = &accepted; records[1].native_transition = &transition; records[2].observer_press = &press;
    uint8_t output[SPATIAL_OBSERVER_V2_MAX_OUTPUT_BYTES]; size_t length;
    const Record *inputs[3] = { record_ptrs[0], record_ptrs[1], record_ptrs[2] };
    return SpatialObserverSerializeServerRecordsV2(inputs, output, sizeof(output), &length) &&
        SpatialObserverEndpointOutputV2(output, length);
}

static void outgoing(CallbackListPtr *list, void *closure, void *data)
{
    (void)list; (void)closure;
    const Arm *arm = SpatialObserverEndpointArmV2();
    if (!arm || failed || complete) return;
    if (running || ordinal == UINT64_MAX) { refuse("native output callback reentry or overflow"); return; }
    if (!xi_opcode) {
        ExtensionEntry *xi = CheckExtension("XInputExtension");
        if (!xi || xi->base <= 0 || xi->base > UINT8_MAX) { refuse("native XI extension missing"); return; }
        xi_opcode = xi->base;
    }
    EventInfoRec *info = data;
    if (!info || !info->client || !info->events || info->count <= 0 || info->count > 64) {
        refuse("native output callback shape"); return;
    }
    for (int i = 0; i < info->count; ++i) {
        const xEvent *raw = &info->events[i];
        if ((raw->u.u.type & 0x7f) != GenericEvent) continue;
        if (info->count != 1) { refuse("native output callback batch"); return; }
        const xGenericEvent *generic = (const xGenericEvent *)raw;
        if (generic->extension != xi_opcode || generic->evtype != XI_ButtonPress) return;
        uint64_t bytes = sizeof(xEvent) + (uint64_t)generic->length * 4;
        if (bytes < sizeof(xXIDeviceEvent) || bytes > 4096) { refuse("native output callback length"); return; }
        xXIDeviceEvent event; memcpy(&event, raw, sizeof(event));
        Bool peer = FALSE;
        for (size_t j = 0; j < arm->n_peer_actor_devices; ++j)
            if (event.deviceid == arm->peer_actor_devices[j]->device_id || event.sourceid == arm->peer_actor_devices[j]->device_id) peer = TRUE;
        if (!peer) return;
        ++ordinal; /* Count relevant peer attempts, not old releases or unrelated events. */
        if (event.buttons_len != DOWN_LENGTH / 4 || event.valuators_len != (MAX_VALUATORS + 31) / 32) {
            refuse("native peer output mask shape"); return;
        }
        uint64_t minimum = sizeof(event) + (uint64_t)(event.buttons_len + event.valuators_len) * 4;
        if (minimum > bytes) { refuse("native peer output masks truncated"); return; }
        const uint8_t *mask = (const uint8_t *)raw + sizeof(event) + event.buttons_len * 4;
        unsigned values = 0;
        for (unsigned bit = 0; bit < event.valuators_len * 32; ++bit) {
            if (!(mask[bit / 8] & (1u << (bit % 8)))) continue;
            if (bit >= MAX_VALUATORS) { refuse("native peer output valuator range"); return; }
            ++values;
        }
        if (minimum + (uint64_t)values * sizeof(FP3232) != bytes) {
            refuse("native peer output valuator length"); return;
        }
        uint64_t ns = now_ns(); SpatialObserverNativeClientPin recipient;
        if (raw->u.u.type != GenericEvent || captured != 4 || event.event != arm->expected_window->xid ||
            event.detail != arm->expected_detail || !ns || ns >= arm->deadline_monotonic_ns ||
            !SpatialObserverPinNativeClient(info->client->index, &recipient) ||
            !SpatialObserverEndpointPinsCurrentV2()) { refuse("native peer output tuple or lifetime"); return; }
        running = TRUE;
        Bool ok = serialize(arm, &event, &recipient, ns);
        running = FALSE;
        if (!ok) refuse("native records serialization or output");
        else complete = TRUE;
        return;
    }
}

Bool SpatialObserverTransitionInitV2(void)
{
    if (registered || running) return FALSE;
    xi_opcode = 0;
    registered = AddCallback(&EventCallback, outgoing, NULL);
    SpatialObserverTransitionResetV2();
    return registered;
}

void SpatialObserverTransitionShutdownV2(void)
{
    if (running) { refuse("native transition shutdown during callback"); return; }
    if (registered && DeleteCallback(&EventCallback, outgoing, NULL)) registered = FALSE;
    SpatialObserverTransitionResetV2();
}
