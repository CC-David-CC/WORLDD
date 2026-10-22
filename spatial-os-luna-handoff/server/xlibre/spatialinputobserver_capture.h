/* SPDX-License-Identifier: MIT
 * Qualification-only internal API. Pins/requests are trusted server-local values.
 * No public X operation, FD, serialized arm capability, or protobuf writer exists.
 */
#ifndef SPATIAL_INPUT_OBSERVER_CAPTURE_H
#define SPATIAL_INPUT_OBSERVER_CAPTURE_H
#include <X11/extensions/XI2proto.h>
#include "spatialinputobserver_lifetime.h"

#define SPATIAL_CAPTURE_MAX_EVENT_BATCH 64u
#define SPATIAL_CAPTURE_MAX_PACKET_BYTES 4096u

enum SpatialObserverCapturePhase {
    SPATIAL_CAPTURE_IDLE, SPATIAL_CAPTURE_ARMED,
    SPATIAL_CAPTURE_COLLECTED, SPATIAL_CAPTURE_FAILED
};
enum SpatialObserverCaptureFailure {
    SPATIAL_CAPTURE_NO_FAILURE, SPATIAL_CAPTURE_BAD_ARM,
    SPATIAL_CAPTURE_SESSION_CHANGED, SPATIAL_CAPTURE_LIFETIME_CHANGED,
    SPATIAL_CAPTURE_BAD_EVENT, SPATIAL_CAPTURE_EVENT_BUDGET,
    SPATIAL_CAPTURE_SYNTHETIC_EVENT, SPATIAL_CAPTURE_WRONG_TUPLE,
    SPATIAL_CAPTURE_SNAPSHOT_FAILED, SPATIAL_CAPTURE_COUNTER_EXHAUSTED
};

typedef struct {
    int old_devices[SPATIAL_OBSERVER_DEVICE_COUNT];
    int peer_devices[SPATIAL_OBSERVER_DEVICE_COUNT];
    int recipient_index;
    XID popup;
    uint32_t button;
    SpatialSelectionNamespace expected_application;
} SpatialObserverCaptureRequest;

typedef struct {
    enum SpatialObserverCapturePhase phase;
    enum SpatialObserverCaptureFailure failure;
    uint64_t session, arm_serial, callback_ordinal;
    uint64_t arm_monotonic_ns, callback_monotonic_ns;
    SpatialObserverDeviceSetPin old_devices, peer_devices;
    SpatialObserverWindowPin popup;
    uint32_t expected_button;
    Bool event_seen, recipient_swapped;
    int recipient_index;
    uint32_t packet_bytes;
    /* Native-order fixed XI2 header, copied before client swapping. No pointers. */
    xXIDeviceEvent event;
    SpatialObserverNativeState old_native, peer_native;
} SpatialObserverCaptureRecord;

/* Init follows LifetimeInit during static extension startup. No hot-loading. */
Bool SpatialObserverCaptureInit(void);
/* Reset disarms and permits a new arm. It never silently follows a failed arm. */
void SpatialObserverCaptureReset(void);
/* Shutdown precedes LifetimeReset and removes every registered callback. */
void SpatialObserverCaptureShutdown(void);
Bool SpatialObserverCaptureArm(const SpatialObserverCaptureRequest *request);
/* TRUE means a current record was copied, not collection or release success.
 * Check phase, event_seen, session, identities, and complete native valid flags.
 * COLLECTED is an output-attempt snapshot. Independent probe receipt is required.
 */
Bool SpatialObserverCaptureRead(SpatialObserverCaptureRecord *record);
#endif
