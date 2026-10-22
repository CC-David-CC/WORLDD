/* SPDX-License-Identifier: MIT */
#include <dix-config.h>
#include <stdint.h>
#include <time.h>
#include <string.h>
#include <X11/extensions/XI2.h>
#include "dix/dix_priv.h"
#include "dix/input_priv.h"
#include "dix/resource_priv.h"
#include "dixstruct.h"
#include "extnsionst.h"
#include "spatialinputobserver_capture.h"

static Bool initialized, event_registered, client_registered, resource_registered;
static Bool event_running;
static uint64_t arm_serial;
static int xi_opcode;
static SpatialObserverCaptureRecord capture;
static Bool monotonic_ns(uint64_t *out)
{
    struct timespec now;
    if (!out || clock_gettime(CLOCK_MONOTONIC, &now) != 0 || now.tv_sec < 0 ||
        now.tv_nsec < 0 || now.tv_nsec >= 1000000000L ||
        (uint64_t)now.tv_sec > (UINT64_MAX - (uint64_t)now.tv_nsec) / UINT64_C(1000000000))
        return FALSE;
    *out = (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
    return *out != 0;
}

static void fail(enum SpatialObserverCaptureFailure reason);

static void invalid_native(SpatialObserverNativeState *state)
{
    memset(state, 0xff, sizeof(*state));
    state->valid = FALSE; state->session = 0;
}

static void empty_record(SpatialObserverCaptureRecord *record)
{
    memset(record, 0, sizeof(*record));
    invalid_native(&record->old_native); invalid_native(&record->peer_native);
}

void SpatialObserverCaptureReset(void)
{
    if (event_running) { fail(SPATIAL_CAPTURE_BAD_EVENT); return; }
    empty_record(&capture);
    capture.session = initialized ? SpatialObserverLifetimeSession() : 0;
    xi_opcode = 0;
}

static void fail(enum SpatialObserverCaptureFailure reason)
{
    /* Preserve the first failure even if a nested callback attempts another. */
    if (capture.phase == SPATIAL_CAPTURE_FAILED || capture.phase == SPATIAL_CAPTURE_COLLECTED) return;
    capture.phase = SPATIAL_CAPTURE_FAILED;
    capture.failure = reason;
}

static Bool label_equal(const SpatialSelectionNamespace *a,
                        const SpatialSelectionNamespace *b)
{
    return a->workspace_size && a->application_size &&
        a->workspace_size <= sizeof(a->workspace) && a->application_size <= sizeof(a->application) &&
        a->workspace_size == b->workspace_size && a->application_size == b->application_size &&
        !memcmp(a->workspace, b->workspace, a->workspace_size) &&
        !memcmp(a->application, b->application, a->application_size);
}

static Bool unfrozen(const SpatialObserverNativeState *state)
{
    if (!state->valid || state->any_pending_events || state->replay_pending || state->replay_in_progress)
        return FALSE;
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        const SpatialObserverNativeDeviceState *device = &state->devices[i];
        if (device->native_frozen || device->frozen_by_other_grab || device->pending_events ||
            device->replay_device ||
            (device->native_sync_state != GRAB_STATE_NOT_GRABBED &&
             device->native_sync_state != GRAB_STATE_THAWED)) return FALSE;
    }
    return TRUE;
}

Bool SpatialObserverCaptureArm(const SpatialObserverCaptureRequest *request)
{
    SpatialObserverNativeState old_state, peer_state;
    if (!initialized || capture.phase != SPATIAL_CAPTURE_IDLE) return FALSE;
    capture.session = SpatialObserverLifetimeSession();
    if (!capture.session) { fail(SPATIAL_CAPTURE_SESSION_CHANGED); return FALSE; }
    if (arm_serial == UINT64_MAX) { fail(SPATIAL_CAPTURE_COUNTER_EXHAUSTED); return FALSE; }
    capture.arm_serial = ++arm_serial;
    ExtensionEntry *xi = CheckExtension("XInputExtension");
    if (!request || !xi || xi->base <= 0 || xi->base > UINT8_MAX || !request->button ||
        !xorg_list_is_empty(&syncEvents.pending) || syncEvents.replayDev || syncEvents.playingEvents ||
        !SpatialObserverPinDevices(request->old_devices, &capture.old_devices) ||
        !SpatialObserverPinDevices(request->peer_devices, &capture.peer_devices) ||
        !SpatialObserverPinWindow(request->popup, &capture.popup) ||
        capture.popup.owner.index != request->recipient_index ||
        !label_equal(&request->expected_application, &capture.popup.owner.label)) {
        fail(SPATIAL_CAPTURE_BAD_ARM); return FALSE;
    }
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i)
        for (unsigned j = 0; j < SPATIAL_OBSERVER_DEVICE_COUNT; ++j)
            if (capture.old_devices.devices[i].id == capture.peer_devices.devices[j].id) {
                fail(SPATIAL_CAPTURE_BAD_ARM); return FALSE;
            }
    if (!SpatialObserverSnapshotDevices(&capture.old_devices, &old_state) ||
        !SpatialObserverSnapshotDevices(&capture.peer_devices, &peer_state) ||
        !unfrozen(&old_state) || !unfrozen(&peer_state) ||
        request->button > peer_state.devices[SPATIAL_OBSERVER_POINTER_MASTER].num_buttons ||
        request->button > peer_state.devices[SPATIAL_OBSERVER_POINTER_SLAVE].num_buttons) {
        fail(SPATIAL_CAPTURE_BAD_ARM); return FALSE;
    }
    capture.expected_button = request->button;
    xi_opcode = xi->base;
    if (!monotonic_ns(&capture.arm_monotonic_ns)) {
        fail(SPATIAL_CAPTURE_BAD_ARM); return FALSE;
    }
    capture.phase = SPATIAL_CAPTURE_ARMED;
    return TRUE;
}

static Bool peer_identity(unsigned id)
{
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i)
        if (id == (unsigned)capture.peer_devices.devices[i].id) return TRUE;
    return FALSE;
}

static Bool relevant(ClientPtr client, const xXIDeviceEvent *event)
{
    SpatialSelectionNamespace label;
    return peer_identity(event->deviceid) || peer_identity(event->sourceid) ||
        event->event == capture.popup.xid || client->index == capture.popup.owner.index ||
        (SpatialSecuritySameApplication(client, client) &&
         SpatialSecuritySelectionNamespace(client, &label) && label_equal(&label, &capture.popup.owner.label));
}

static Bool decode_device_event(const xEvent *raw, xXIDeviceEvent *event, uint32_t *bytes)
{
    const xGenericEvent *generic = (const xGenericEvent *)raw;
    uint64_t total = sizeof(xEvent) + (uint64_t)generic->length * 4;
    if (total < sizeof(*event) || total > SPATIAL_CAPTURE_MAX_PACKET_BYTES) return FALSE;
    /* EventCallback has no allocation-size argument. The server producer must
     * own an allocation covering its declared length; this is not a client parser.
     */
    memcpy(event, raw, sizeof(*event));
    if (event->buttons_len != DOWN_LENGTH / 4 ||
        event->valuators_len != (MAX_VALUATORS + 31) / 32) return FALSE;
    unsigned button_bytes = event->buttons_len * 4;
    unsigned mask_bytes = event->valuators_len * 4;
    uint64_t minimum = sizeof(*event) + button_bytes + mask_bytes;
    if (minimum > total) return FALSE;
    const unsigned char *mask = (const unsigned char *)raw + sizeof(*event) + button_bytes;
    unsigned values = 0;
    for (unsigned bit = 0; bit < mask_bytes * 8; ++bit) {
        if (mask[bit / 8] & (1u << (bit % 8))) {
            if (bit >= MAX_VALUATORS) return FALSE;
            ++values;
        }
    }
    if (minimum + (uint64_t)values * sizeof(FP3232) != total) return FALSE;
    *bytes = (uint32_t)total;
    return TRUE;
}

static void inspect_outgoing(EventInfoRec *info)
{
    if (!initialized || capture.phase != SPATIAL_CAPTURE_ARMED) return;
    if (capture.session != SpatialObserverLifetimeSession()) {
        fail(SPATIAL_CAPTURE_SESSION_CHANGED); return;
    }
    if (capture.callback_ordinal == UINT64_MAX) {
        fail(SPATIAL_CAPTURE_COUNTER_EXHAUSTED); return;
    }
    ++capture.callback_ordinal;
    if (!info || !info->client || !info->events || info->count <= 0) {
        fail(SPATIAL_CAPTURE_BAD_EVENT); return;
    }
    if ((unsigned)info->count > SPATIAL_CAPTURE_MAX_EVENT_BATCH) {
        fail(SPATIAL_CAPTURE_EVENT_BUDGET); return;
    }
    for (int i = 0; i < info->count; ++i) {
        const xEvent *raw = &info->events[i];
        if ((raw->u.u.type & 0x7f) != GenericEvent) continue;
        /* GenericEvent is variable-sized; never step through a mixed batch. */
        if (info->count != 1) { fail(SPATIAL_CAPTURE_BAD_EVENT); return; }
        const xGenericEvent *generic = (const xGenericEvent *)raw;
        if (generic->extension != xi_opcode || generic->evtype != XI_ButtonPress) return;
        /* Never trust an extended payload on any synthetic event. */
        if (raw->u.u.type != GenericEvent) { fail(SPATIAL_CAPTURE_SYNTHETIC_EVENT); return; }
        xXIDeviceEvent event;
        uint32_t bytes;
        if (!decode_device_event(raw, &event, &bytes)) { fail(SPATIAL_CAPTURE_BAD_EVENT); return; }
        if (!relevant(info->client, &event)) return;
        capture.event_seen = TRUE; capture.event = event; capture.packet_bytes = bytes;
        if (!monotonic_ns(&capture.callback_monotonic_ns)) {
            fail(SPATIAL_CAPTURE_BAD_EVENT); return;
        }
        capture.recipient_index = info->client->index;
        capture.recipient_swapped = info->client->swapped;
        if (event.deviceid != capture.peer_devices.devices[SPATIAL_OBSERVER_POINTER_MASTER].id ||
            event.sourceid != capture.peer_devices.devices[SPATIAL_OBSERVER_POINTER_SLAVE].id ||
            event.event != capture.popup.xid || event.detail != capture.expected_button ||
            info->client->index != capture.popup.owner.index) {
            fail(SPATIAL_CAPTURE_WRONG_TUPLE); return;
        }
        SpatialObserverNativeState old_state, peer_state;
        if (!SpatialObserverWindowMatches(&capture.popup, info->client)) {
            fail(SPATIAL_CAPTURE_LIFETIME_CHANGED); return;
        }
        if (!SpatialObserverSnapshotDevices(&capture.old_devices, &old_state) ||
            !SpatialObserverSnapshotDevices(&capture.peer_devices, &peer_state)) {
            fail(SPATIAL_CAPTURE_SNAPSHOT_FAILED); return;
        }
        if (capture.phase != SPATIAL_CAPTURE_ARMED) return;
        capture.old_native = old_state; capture.peer_native = peer_state;
        capture.phase = SPATIAL_CAPTURE_COLLECTED;
        return;
    }
}

static void outgoing(CallbackListPtr *list, void *closure, void *data)
{
    (void)list; (void)closure;
    if (!initialized || capture.phase != SPATIAL_CAPTURE_ARMED) return;
    if (event_running) { fail(SPATIAL_CAPTURE_BAD_EVENT); return; }
    event_running = TRUE;
    inspect_outgoing(data);
    event_running = FALSE;
}

static void client_changed(CallbackListPtr *list, void *closure, void *data)
{
    NewClientInfoRec *info = data;
    (void)list; (void)closure;
    if (!initialized || capture.phase != SPATIAL_CAPTURE_ARMED || !info || !info->client) return;
    if (info->client->index == capture.popup.owner.index &&
        (info->client->clientState == ClientStateRetained || info->client->clientState == ClientStateGone))
        fail(SPATIAL_CAPTURE_LIFETIME_CHANGED);
}

static void resource_changed(CallbackListPtr *list, void *closure, void *data)
{
    ResourceStateInfoRec *info = data;
    (void)list; (void)closure;
    if (!initialized || capture.phase != SPATIAL_CAPTURE_ARMED || !info) return;
    if (info->state == ResourceStateFreeing && info->type == X11_RESTYPE_WINDOW &&
        info->id == capture.popup.xid) fail(SPATIAL_CAPTURE_LIFETIME_CHANGED);
}

void SpatialObserverCaptureShutdown(void)
{
    if (event_running) { fail(SPATIAL_CAPTURE_BAD_EVENT); return; }
    initialized = FALSE;
    if (event_registered && DeleteCallback(&EventCallback, outgoing, NULL)) event_registered = FALSE;
    if (client_registered && DeleteCallback(&ClientStateCallback, client_changed, NULL)) client_registered = FALSE;
    if (resource_registered && DeleteCallback(&ResourceStateCallback, resource_changed, NULL)) resource_registered = FALSE;
    /* Failed removal prevents reinit, rather than registering duplicate callbacks. */
    SpatialObserverCaptureReset();
}

Bool SpatialObserverCaptureInit(void)
{
    if (event_running || initialized || event_registered || client_registered || resource_registered ||
        !SpatialObserverLifetimeSession()) return FALSE;
    event_registered = AddCallback(&EventCallback, outgoing, NULL);
    if (event_registered) client_registered = AddCallback(&ClientStateCallback, client_changed, NULL);
    if (client_registered) resource_registered = AddCallback(&ResourceStateCallback, resource_changed, NULL);
    if (!event_registered || !client_registered || !resource_registered) {
        SpatialObserverCaptureShutdown(); return FALSE;
    }
    initialized = TRUE;
    SpatialObserverCaptureReset();
    return TRUE;
}

Bool SpatialObserverCaptureRead(SpatialObserverCaptureRecord *record)
{
    if (!record) return FALSE;
    empty_record(record);
    if (!initialized || !capture.session || capture.session != SpatialObserverLifetimeSession()) return FALSE;
    *record = capture;
    return TRUE;
}
