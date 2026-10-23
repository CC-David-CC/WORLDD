/* Qualification-only lifecycle observations. Borrowed pointers never escape a call. */
#ifndef SPATIAL_INPUT_OBSERVER_LIFECYCLE_V3_H
#define SPATIAL_INPUT_OBSERVER_LIFECYCLE_V3_H
#include "spatialinputobserver_transition_v2.h"
#include "native_input_lifecycle_v3.pb-c.h"

typedef struct {
    Bool old_present;
    SpatialObserverSeatStateV2 old, peer;
    SpatialObserverWindowPin peer_window;
    uint64_t peer_generation, controller;
} SpatialObserverLifecycleRoutingV3;

Bool SpatialObserverLifecycleInitV3(void);
void SpatialObserverLifecycleShutdownV3(void);
void SpatialObserverLifecycleCaptureV3(unsigned phase,
    const SpatialObserverLifecycleRoutingV3 *routing,
    const SpatialObserverWindowPin *borrowed_pin, WindowPtr borrowed_window);
void SpatialObserverLifecycleGenerationV3(XID xid, uint64_t generation);
void SpatialObserverLifecycleDestroyReturnedV3(XID xid, Bool result);
void SpatialObserverLifecycleDeviceRemovedV3(uint64_t seat, int device_id, int result);
void SpatialObserverLifecycleSeatRemovedV3(uint64_t seat);

Bool SpatialInputObserverLifecycleRoutingV3(SpatialObserverLifecycleRoutingV3 *routing);
Bool SpatialInputObserverSeatAbsentV3(uint64_t seat);
void SpatialInputObserverResourceFreeV3(WindowPtr window, const SpatialObserverWindowPin *pin);

Bool SpatialObserverSnapshotBorrowedContextV3(const SpatialObserverDeviceSetPin *devices,
    const SpatialObserverWindowPin *target, WindowPtr borrowed_window, SpatialObserverContextV2 *context);
Bool SpatialObserverSnapshotClearContextV3(const SpatialObserverDeviceSetPin *devices,
    SpatialObserverContextV2 *context);
Bool SpatialObserverDeviceIdsAbsentV3(const SpatialObserverDeviceSetPin *devices);
Bool SpatialObserverWindowAbsentV3(XID xid);
#endif
