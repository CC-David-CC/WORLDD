/* SPDX-License-Identifier: MIT
 * Qualification-only, server-local helpers. No wire or ordinary-client API.
 * Initialize during SpatialInputExtensionInit, before application creation.
 * Pin values stay inside this server process. Never deserialize them from a client.
 * All calls run synchronously in server dispatch. Do not retain returned pointers.
 */
#ifndef SPATIAL_INPUT_OBSERVER_LIFETIME_H
#define SPATIAL_INPUT_OBSERVER_LIFETIME_H
#include <stdint.h>
#include "inputstr.h"
#include "spatialsecurity.h"

/* Qualification-only snapshot budget. Exceeding it invalidates all evidence. */
#define SPATIAL_OBSERVER_MAX_PENDING_RECORDS 1024u

enum SpatialObserverDeviceRole {
    SPATIAL_OBSERVER_POINTER_MASTER,
    SPATIAL_OBSERVER_POINTER_SLAVE,
    SPATIAL_OBSERVER_KEYBOARD_MASTER,
    SPATIAL_OBSERVER_KEYBOARD_SLAVE,
    SPATIAL_OBSERVER_DEVICE_COUNT
};

typedef struct {
    int id;
    enum SpatialObserverDeviceRole role;
    uint64_t epoch;
} SpatialObserverDevicePin;

typedef struct {
    uint64_t session;
    SpatialObserverDevicePin devices[SPATIAL_OBSERVER_DEVICE_COUNT];
} SpatialObserverDeviceSetPin;

/* Classification by pointer equality only. OTHER does not assert a live window. */
enum SpatialObserverFocusKind {
    SPATIAL_OBSERVER_FOCUS_NOT_CAPTURED,
    SPATIAL_OBSERVER_FOCUS_NONE,
    SPATIAL_OBSERVER_FOCUS_POINTER_ROOT,
    SPATIAL_OBSERVER_FOCUS_FOLLOW_KEYBOARD,
    SPATIAL_OBSERVER_FOCUS_OTHER
};

typedef struct {
    SpatialObserverDevicePin identity;
    int paired_or_attached;
    /* Only the class corresponding to identity.role is valid. */
    Bool has_key_class, has_button_class;
    CARD8 key_down[DOWN_LENGTH], key_postdown[DOWN_LENGTH];
    CARD8 button_down[DOWN_LENGTH], button_postdown[DOWN_LENGTH];
    unsigned num_buttons, buttons_down, button_state;
    Bool active_grab;
    Bool native_frozen, frozen_by_other_grab;
    int native_sync_state; /* Copy the native value; do not coerce an enum. */
    unsigned pending_events;
    Bool replay_device;
    Bool has_focus_class;
    enum SpatialObserverFocusKind focus_kind;
    int focus_revert;
} SpatialObserverNativeDeviceState;

typedef struct {
    Bool valid;
    uint64_t session;
    Bool any_pending_events, replay_pending, replay_in_progress;
    SpatialObserverNativeDeviceState devices[SPATIAL_OBSERVER_DEVICE_COUNT];
} SpatialObserverNativeState;

typedef struct {
    uint64_t session, epoch;
    int index;
    SpatialSelectionNamespace label;
} SpatialObserverClientPin;

typedef struct {
    uint64_t session, epoch;
    XID xid;
    SpatialObserverClientPin owner;
} SpatialObserverWindowPin;

/* V2 pins distinguish native connection role from the target APP owner. */
enum SpatialObserverNativeClientRole {
    SPATIAL_OBSERVER_NATIVE_CLIENT_INVALID,
    SPATIAL_OBSERVER_NATIVE_CLIENT_APPLICATION,
    SPATIAL_OBSERVER_NATIVE_CLIENT_SUPERVISOR,
    SPATIAL_OBSERVER_NATIVE_CLIENT_COMPOSITOR
};

typedef struct {
    uint64_t session, epoch;
    int index;
    enum SpatialObserverNativeClientRole role;
    SpatialSelectionNamespace label; /* populated only for APPLICATION */
} SpatialObserverNativeClientPin;

typedef struct {
    uint64_t session, epoch;
    XID xid;
    SpatialObserverNativeClientPin owner;
} SpatialObserverMarkerPin;
Bool SpatialObserverPinMarker(XID xid, SpatialObserverMarkerPin *pin);


typedef struct {
    Bool complete;
    SpatialObserverDevicePin device;
    Bool active_grab;
    XID grab_resource;
    SpatialObserverNativeClientPin grab_owner;
    SpatialObserverWindowPin grab_window;
    Bool has_confine_window;
    SpatialObserverWindowPin confine_window;
    Bool has_focus_class;
    enum SpatialObserverFocusKind focus_kind;
    int focus_revert;
    Bool focus_is_target;
    SpatialObserverWindowPin focus_window;
} SpatialObserverDeviceContextV2;

typedef struct {
    Bool valid;
    uint64_t session;
    SpatialObserverDeviceContextV2 devices[SPATIAL_OBSERVER_DEVICE_COUNT];
} SpatialObserverContextV2;

Bool SpatialObserverLifetimeInit(void);
void SpatialObserverLifetimeReset(void);
uint64_t SpatialObserverLifetimeSession(void);
Bool SpatialObserverPinDevices(const int ids[SPATIAL_OBSERVER_DEVICE_COUNT],
                               SpatialObserverDeviceSetPin *pin);
/* TRUE means a complete native snapshot, never that input is released.
 * FALSE leaves valid == FALSE and nonzero bitmap sentinels. Reject that record.
 */
Bool SpatialObserverSnapshotDevices(const SpatialObserverDeviceSetPin *pin,
                                    SpatialObserverNativeState *state);
Bool SpatialObserverPinClient(int index, SpatialObserverClientPin *pin);
Bool SpatialObserverClientMatches(const SpatialObserverClientPin *pin,
                                  ClientPtr recipient);
Bool SpatialObserverPinWindow(XID xid, SpatialObserverWindowPin *pin);
Bool SpatialObserverWindowMatches(const SpatialObserverWindowPin *pin,
                                  ClientPtr recipient);
Bool SpatialObserverPinNativeClient(int index, SpatialObserverNativeClientPin *pin);
Bool SpatialObserverSnapshotContextV2(const SpatialObserverDeviceSetPin *devices,
                                      const SpatialObserverWindowPin *target,
                                      SpatialObserverContextV2 *context);
#endif
