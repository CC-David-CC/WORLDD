/* Private inherited qualification endpoint. Ordinary X clients have no API. */
#ifndef SPATIAL_INPUT_OBSERVER_ENDPOINT_H
#define SPATIAL_INPUT_OBSERVER_ENDPOINT_H
#include <stddef.h>
#include <stdint.h>
#include "misc.h"
#include "native_input_observer_control_v2.pb-c.h"
void SpatialObserverEndpointConfigure(const char *descriptor);
Bool SpatialObserverEndpointInit(void);
void SpatialObserverEndpointReset(void);
/* Read live server identities. Never trust client-supplied object epochs. */
/* Read-only held-input precondition. No routing or authority changes. */
Bool SpatialInputObserverArm(const Spatial__Testing__V2__ObserverArmV2 *arm);
Bool SpatialInputObserverSetup(const Spatial__Testing__V2__ObserverSetupRequestV2 *request,
                              uint8_t *output, size_t capacity, size_t *length);
#endif
