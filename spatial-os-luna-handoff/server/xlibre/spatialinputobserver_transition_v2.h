/* Private qualification-only synchronous native transition capture. */
#ifndef SPATIAL_INPUT_OBSERVER_TRANSITION_V2_H
#define SPATIAL_INPUT_OBSERVER_TRANSITION_V2_H
#include "spatialinputobserver_lifetime.h"
#include "native_input_observer_v2.pb-c.h"
typedef struct {
    uint64_t id, target_generation, focus_generation, controller;
    XID target, focus;
    uint8_t keys[32];
    uint32_t buttons;
    Bool application;
    SpatialObserverNativeClientPin owner;
    SpatialObserverDeviceSetPin devices;
} SpatialObserverSeatStateV2;
Bool SpatialObserverTransitionInitV2(void);
void SpatialObserverTransitionResetV2(void);
void SpatialObserverTransitionShutdownV2(void);
void SpatialObserverTransitionCaptureV2(unsigned phase,
    const SpatialObserverSeatStateV2 *old, const SpatialObserverSeatStateV2 *peer,
    const SpatialObserverWindowPin *target, uint64_t generation, uint64_t controller);
#endif
