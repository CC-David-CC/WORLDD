/* SPDX-License-Identifier: MIT */
#include <dix-config.h>
#include <limits.h>
#include <string.h>
#include "native_input_observer.pb-c.h"
#include "spatialinputobserver_serialize.h"

_Static_assert(DOWN_LENGTH == 32, "Native observer bitmap width changed");
_Static_assert(sizeof(((SpatialObserverNativeDeviceState *)0)->key_down) == 32, "Key bitmap width");
_Static_assert(sizeof(((SpatialObserverNativeDeviceState *)0)->button_down) == 32, "Button bitmap width");

typedef Spatial__Testing__V1__NativeInputObservationRecord Record;
typedef Spatial__Testing__V1__ActorDeviceIdentity Device;
typedef Spatial__Testing__V1__ApplicationLabel Label;
typedef Spatial__Testing__V1__ClientIdentity Client;
typedef Spatial__Testing__V1__WindowIdentity WindowIdentity;
typedef Spatial__Testing__V1__Arm Arm;
typedef Spatial__Testing__V1__NativeDeviceState NativeState;
typedef Spatial__Testing__V1__EventIdentity Event;
typedef Spatial__Testing__V1__ObserverPress Press;

static Spatial__Testing__V1__ActorDeviceIdentity__DeviceKind device_kind(enum SpatialObserverDeviceRole role)
{
    return role == SPATIAL_OBSERVER_POINTER_MASTER || role == SPATIAL_OBSERVER_POINTER_SLAVE
        ? SPATIAL__TESTING__V1__ACTOR_DEVICE_IDENTITY__DEVICE_KIND__POINTER
        : SPATIAL__TESTING__V1__ACTOR_DEVICE_IDENTITY__DEVICE_KIND__KEYBOARD;
}

static Spatial__Testing__V1__ActorDeviceIdentity__SeatRole seat_role(enum SpatialObserverDeviceRole role)
{
    return role == SPATIAL_OBSERVER_POINTER_MASTER || role == SPATIAL_OBSERVER_KEYBOARD_MASTER
        ? SPATIAL__TESTING__V1__ACTOR_DEVICE_IDENTITY__SEAT_ROLE__MASTER
        : SPATIAL__TESTING__V1__ACTOR_DEVICE_IDENTITY__SEAT_ROLE__SLAVE;
}

static int label_valid(const SpatialSelectionNamespace *label)
{
    if (!label || !label->workspace_size || !label->application_size ||
        label->workspace_size > sizeof(label->workspace) ||
        label->application_size > sizeof(label->application)) return 0;
    return !memchr(label->workspace, 0, label->workspace_size) &&
           !memchr(label->application, 0, label->application_size);
}

static void put_label(Label *out, const SpatialSelectionNamespace *in)
{
    spatial__testing__v1__application_label__init(out);
    out->workspace.data = (uint8_t *)in->workspace;
    out->workspace.len = in->workspace_size;
    out->application.data = (uint8_t *)in->application;
    out->application.len = in->application_size;
}

static void put_device(Device *out, const SpatialObserverDevicePin *pin,
                      const SpatialObserverDeviceSetPin *set)
{
    spatial__testing__v1__actor_device_identity__init(out);
    out->seat_role = seat_role(pin->role);
    out->kind = device_kind(pin->role);
    out->device_id = (uint32_t)pin->id;
    out->object_epoch = pin->epoch;
    switch (pin->role) {
    case SPATIAL_OBSERVER_POINTER_MASTER:
        out->paired_or_attached = (uint32_t)set->devices[SPATIAL_OBSERVER_KEYBOARD_MASTER].id; break;
    case SPATIAL_OBSERVER_POINTER_SLAVE:
        out->paired_or_attached = (uint32_t)set->devices[SPATIAL_OBSERVER_POINTER_MASTER].id; break;
    case SPATIAL_OBSERVER_KEYBOARD_MASTER:
        out->paired_or_attached = (uint32_t)set->devices[SPATIAL_OBSERVER_POINTER_MASTER].id; break;
    case SPATIAL_OBSERVER_KEYBOARD_SLAVE:
        out->paired_or_attached = (uint32_t)set->devices[SPATIAL_OBSERVER_KEYBOARD_MASTER].id; break;
    default: break;
    }
}

static int valid_set(const SpatialObserverDeviceSetPin *set)
{
    if (!set || !set->session) return 0;
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        const SpatialObserverDevicePin *p = &set->devices[i];
        if (p->id <= 0 || p->id > UINT16_MAX || p->role != (enum SpatialObserverDeviceRole)i || !p->epoch)
            return 0;
        for (unsigned j = 0; j < i; ++j)
            if (p->id == set->devices[j].id || p->epoch == set->devices[j].epoch) return 0;
    }
    return 1;
}

static int bitmap_valid(const SpatialObserverNativeState *state,
                        const SpatialObserverDeviceSetPin *set)
{
    if (!state || !state->valid || state->session != set->session) return 0;
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        const SpatialObserverNativeDeviceState *d = &state->devices[i];
        if (d->identity.id != set->devices[i].id || d->identity.role != set->devices[i].role ||
            d->identity.epoch != set->devices[i].epoch) return 0;
        if (i == SPATIAL_OBSERVER_KEYBOARD_MASTER || i == SPATIAL_OBSERVER_KEYBOARD_SLAVE) {
            if (!d->has_key_class || d->has_button_class) return 0;
        } else if (!d->has_button_class || d->has_key_class) return 0;
        int paired = i == SPATIAL_OBSERVER_POINTER_MASTER
            ? set->devices[SPATIAL_OBSERVER_KEYBOARD_MASTER].id
            : i == SPATIAL_OBSERVER_POINTER_SLAVE
                ? set->devices[SPATIAL_OBSERVER_POINTER_MASTER].id
                : i == SPATIAL_OBSERVER_KEYBOARD_MASTER
                    ? set->devices[SPATIAL_OBSERVER_POINTER_MASTER].id
                    : set->devices[SPATIAL_OBSERVER_KEYBOARD_MASTER].id;
        if (d->paired_or_attached != paired) return 0;
    }
    return 1;
}

static int same_application(const SpatialObserverClientPin *client,
                            const SpatialObserverWindowPin *window)
{
    const SpatialSelectionNamespace *a = &client->label, *b = &window->owner.label;
    return label_valid(a) && label_valid(b) && a->workspace_size == b->workspace_size &&
        a->application_size == b->application_size &&
        !memcmp(a->workspace, b->workspace, a->workspace_size) &&
        !memcmp(a->application, b->application, a->application_size);
}

static int validate_capture(const SpatialObserverCaptureRecord *c,
                            const SpatialObserverTrustedContext *ctx)
{
    if (!c || !ctx || c->phase != SPATIAL_CAPTURE_COLLECTED ||
        c->failure != SPATIAL_CAPTURE_NO_FAILURE || !c->session || !c->arm_serial ||
        !c->event_seen || !c->callback_ordinal || !c->packet_bytes ||
        c->packet_bytes > SPATIAL_CAPTURE_MAX_PACKET_BYTES ||
        c->session != c->old_devices.session || c->session != c->peer_devices.session ||
        c->session != c->popup.session || !valid_set(&c->old_devices) || !valid_set(&c->peer_devices) ||
        !bitmap_valid(&c->old_native, &c->old_devices) || !bitmap_valid(&c->peer_native, &c->peer_devices) ||
        !c->popup.epoch || !c->popup.xid || !c->popup.owner.epoch ||
        c->popup.owner.session != c->session || c->popup.owner.index <= 0 ||
        (uint64_t)c->popup.owner.index > UINT32_MAX || c->popup.xid > UINT32_MAX ||
        c->popup.epoch == c->popup.owner.epoch ||
        c->recipient_index != c->popup.owner.index ||
        !same_application(&c->popup.owner, &c->popup) ||
        !ctx->spatial_generation || !ctx->deadline_monotonic_ns ||
        !c->arm_monotonic_ns || c->arm_monotonic_ns > c->callback_monotonic_ns ||
        c->arm_monotonic_ns >= ctx->deadline_monotonic_ns ||
        c->callback_monotonic_ns > ctx->deadline_monotonic_ns ||
        c->expected_button == 0 || c->expected_button > UINT8_MAX ||
        c->event.type != GenericEvent || !c->event.extension ||
        c->event.length > (SPATIAL_CAPTURE_MAX_PACKET_BYTES - sizeof(xEvent)) / 4 ||
        c->event.buttons_len != DOWN_LENGTH / 4 ||
        c->event.valuators_len != (MAX_VALUATORS + 31) / 32 ||
        c->packet_bytes != sizeof(xEvent) + (uint64_t)c->event.length * 4 ||
        c->packet_bytes < sizeof(c->event) +
            (uint64_t)c->event.buttons_len * 4 + (uint64_t)c->event.valuators_len * 4 ||
        c->packet_bytes > sizeof(c->event) +
            (uint64_t)c->event.buttons_len * 4 + (uint64_t)c->event.valuators_len * 4 +
            (uint64_t)MAX_VALUATORS * sizeof(FP3232) ||
        c->event.evtype != XI_ButtonPress || c->event.deviceid != c->peer_devices.devices[SPATIAL_OBSERVER_POINTER_MASTER].id ||
        c->event.sourceid != c->peer_devices.devices[SPATIAL_OBSERVER_POINTER_SLAVE].id ||
        c->event.event != c->popup.xid || c->event.detail != c->expected_button)
        return 0;
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        for (unsigned j = 0; j < SPATIAL_OBSERVER_DEVICE_COUNT; ++j) {
            if (c->old_devices.devices[i].id == c->peer_devices.devices[j].id ||
                c->old_devices.devices[i].epoch == c->peer_devices.devices[j].epoch) return 0;
        }
        if (c->old_devices.devices[i].epoch == c->popup.epoch ||
            c->old_devices.devices[i].epoch == c->popup.owner.epoch ||
            c->peer_devices.devices[i].epoch == c->popup.epoch ||
            c->peer_devices.devices[i].epoch == c->popup.owner.epoch) return 0;
    }
    return 1;
}

static size_t varint_size(size_t n)
{
    size_t count = 1;
    while (n > 127) { n >>= 7; ++count; }
    return count;
}

static size_t write_varint(uint8_t *out, size_t n)
{
    size_t i = 0;
    while (n > 127) { out[i++] = (uint8_t)((n & 127) | 128); n >>= 7; }
    out[i++] = (uint8_t)n;
    return i;
}

Bool SpatialObserverSerialize(const SpatialObserverCaptureRecord *c,
                              const SpatialObserverTrustedContext *ctx,
                              uint8_t *output, size_t capacity, size_t *output_length)
{
    Record arm_record = SPATIAL__TESTING__V1__NATIVE_INPUT_OBSERVATION_RECORD__INIT;
    Record press_record = SPATIAL__TESTING__V1__NATIVE_INPUT_OBSERVATION_RECORD__INIT;
    Arm arm = SPATIAL__TESTING__V1__ARM__INIT;
    Press press = SPATIAL__TESTING__V1__OBSERVER_PRESS__INIT;
    Client client = SPATIAL__TESTING__V1__CLIENT_IDENTITY__INIT;
    WindowIdentity window = SPATIAL__TESTING__V1__WINDOW_IDENTITY__INIT;
    Label label = SPATIAL__TESTING__V1__APPLICATION_LABEL__INIT;
    Event event = SPATIAL__TESTING__V1__EVENT_IDENTITY__INIT;
    Device old_device[4], peer_device[4], state_device[4];
    NativeState native_state[4];
    Device *old_ptrs[4], *peer_ptrs[4];
    NativeState *state_ptrs[4];
    uint8_t arm_bytes[SPATIAL_OBSERVER_MAX_RECORD_BYTES];
    uint8_t press_bytes[SPATIAL_OBSERVER_MAX_RECORD_BYTES];
    uint8_t artifact[SPATIAL_OBSERVER_MAX_OUTPUT_BYTES];
    size_t arm_len, press_len, needed, pos = 0;
    if (output_length) *output_length = 0;
    if (!output_length || !output || !ctx || !c || !validate_capture(c, ctx))
        return FALSE;
    if (!memcmp(ctx->actor_id, ctx->peer_actor_id, sizeof(ctx->actor_id)) &&
        !memcmp(ctx->issuer_id, ctx->peer_issuer_id, sizeof(ctx->issuer_id))) return FALSE;
    arm.actor_id.data = (uint8_t *)ctx->actor_id; arm.actor_id.len = sizeof(ctx->actor_id);
    arm.issuer_id.data = (uint8_t *)ctx->issuer_id; arm.issuer_id.len = sizeof(ctx->issuer_id);
    arm.peer_actor_id.data = (uint8_t *)ctx->peer_actor_id; arm.peer_actor_id.len = sizeof(ctx->peer_actor_id);
    arm.peer_issuer_id.data = (uint8_t *)ctx->peer_issuer_id; arm.peer_issuer_id.len = sizeof(ctx->peer_issuer_id);
    arm.arm_monotonic_ns = c->arm_monotonic_ns;
    arm.deadline_monotonic_ns = ctx->deadline_monotonic_ns;
    arm.expected_xi2_event_type = XI_ButtonPress;
    arm.expected_detail = c->expected_button;
    arm.prearm_receipt_watermark = ctx->prearm_receipt_watermark;
    for (unsigned i = 0; i < 4; ++i) {
        put_device(&old_device[i], &c->old_devices.devices[i], &c->old_devices);
        put_device(&peer_device[i], &c->peer_devices.devices[i], &c->peer_devices);
        old_ptrs[i] = &old_device[i]; peer_ptrs[i] = &peer_device[i];
        const SpatialObserverNativeDeviceState *native = &c->old_native.devices[i];
        put_device(&state_device[i], &native->identity, &c->old_devices);
        spatial__testing__v1__native_device_state__init(&native_state[i]);
        native_state[i].identity = &state_device[i];
        native_state[i].bitmap_bits = DOWN_LENGTH * 8u;
        if (native->identity.role == SPATIAL_OBSERVER_KEYBOARD_MASTER ||
            native->identity.role == SPATIAL_OBSERVER_KEYBOARD_SLAVE) {
            native_state[i].bitmap_kind = SPATIAL__TESTING__V1__NATIVE_DEVICE_STATE__BITMAP_KIND__KEY_CLASS_DOWN;
            native_state[i].down_bitmap.data = (uint8_t *)native->key_down;
        } else {
            native_state[i].bitmap_kind = SPATIAL__TESTING__V1__NATIVE_DEVICE_STATE__BITMAP_KIND__BUTTON_CLASS_DOWN;
            native_state[i].down_bitmap.data = (uint8_t *)native->button_down;
        }
        native_state[i].down_bitmap.len = DOWN_LENGTH;
        native_state[i].state_complete = 1;
        state_ptrs[i] = &native_state[i];
    }
    arm.n_old_actor_devices = 4; arm.old_actor_devices = old_ptrs;
    arm.n_peer_actor_devices = 4; arm.peer_actor_devices = peer_ptrs;
    put_label(&label, &c->popup.owner.label);
    client.client_id = (uint32_t)c->popup.owner.index;
    client.object_epoch = c->popup.owner.epoch; client.application_label = &label;
    window.window_id = c->popup.xid; window.object_epoch = c->popup.epoch;
    window.application_label = &label; window.spatial_generation = ctx->spatial_generation;
    arm.expected_recipient = &client; arm.expected_window = &window;
    arm.expected_peer_device = &peer_device[SPATIAL_OBSERVER_POINTER_MASTER];
    arm.expected_peer_source = &peer_device[SPATIAL_OBSERVER_POINTER_SLAVE];
    arm_record.protocol_version = press_record.protocol_version = 1;
    arm_record.session_id.data = press_record.session_id.data = (uint8_t *)ctx->session_id;
    arm_record.session_id.len = press_record.session_id.len = SPATIAL_OBSERVER_TOKEN_BYTES;
    arm_record.arm_id.data = press_record.arm_id.data = (uint8_t *)ctx->arm_id;
    arm_record.arm_id.len = press_record.arm_id.len = SPATIAL_OBSERVER_TOKEN_BYTES;
    arm_record.stream_sequence = 1; press_record.stream_sequence = 2;
    arm_record.native_session = press_record.native_session = c->session;
    arm_record.payload_case = SPATIAL__TESTING__V1__NATIVE_INPUT_OBSERVATION_RECORD__PAYLOAD_ARM;
    arm_record.arm = &arm;
    press.observer_ordinal = 1; press.event = &event;
    press.n_old_actor_state = 4; press.old_actor_state = state_ptrs;
    press.snapshot_complete = 1; press.callback_monotonic_ns = c->callback_monotonic_ns;
    event.recipient = &client; event.window = &window;
    event.device = &peer_device[SPATIAL_OBSERVER_POINTER_MASTER];
    event.source = &peer_device[SPATIAL_OBSERVER_POINTER_SLAVE];
    event.xi2_event_type = c->event.evtype; event.detail = c->event.detail;
    event.event_time_ms = c->event.time;
    press_record.payload_case = SPATIAL__TESTING__V1__NATIVE_INPUT_OBSERVATION_RECORD__PAYLOAD_OBSERVER_PRESS;
    press_record.observer_press = &press;
    arm_len = spatial__testing__v1__native_input_observation_record__get_packed_size(&arm_record);
    press_len = spatial__testing__v1__native_input_observation_record__get_packed_size(&press_record);
    if (!arm_len || !press_len || arm_len > sizeof(arm_bytes) || press_len > sizeof(press_bytes)) return FALSE;
    if (spatial__testing__v1__native_input_observation_record__pack(&arm_record, arm_bytes) != arm_len ||
        spatial__testing__v1__native_input_observation_record__pack(&press_record, press_bytes) != press_len)
        return FALSE;
    needed = varint_size(arm_len) + arm_len + varint_size(press_len) + press_len;
    if (needed > sizeof(artifact) || needed > capacity) return FALSE;
    pos += write_varint(artifact + pos, arm_len); memcpy(artifact + pos, arm_bytes, arm_len); pos += arm_len;
    pos += write_varint(artifact + pos, press_len); memcpy(artifact + pos, press_bytes, press_len); pos += press_len;
    if (pos != needed) return FALSE;
    memcpy(output, artifact, needed); *output_length = needed;
    return TRUE;
}
