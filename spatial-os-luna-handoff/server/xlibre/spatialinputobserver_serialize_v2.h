/* Private serializer for three server-origin v2 records.
 * Inputs must be accessible, protobuf-c initialized message graphs. This API
 * validates descriptor/field consistency; it cannot make arbitrary pointers safe.
 * The records grant no authority and establish no live native facts.
 */
#ifndef SPATIALINPUTOBSERVER_SERIALIZE_V2_H
#define SPATIALINPUTOBSERVER_SERIALIZE_V2_H
#include <stddef.h>
#include <stdint.h>
#include "native_input_observer_v2.pb-c.h"

#define SPATIAL_OBSERVER_V2_MAX_FRAME_BYTES 4096u
#define SPATIAL_OBSERVER_V2_MAX_OUTPUT_BYTES \
    (3u * (SPATIAL_OBSERVER_V2_MAX_FRAME_BYTES + 2u))

enum {
    SPATIAL_NATIVE_APPLICATION = 1,
    SPATIAL_NATIVE_SUPERVISOR = 2,
    SPATIAL_NATIVE_COMPOSITOR = 3
};

typedef int (*SpatialObserverV2Sink)(void *context,
                                     const uint8_t *bytes,
                                     size_t length);

/* On an unknown role or NULL output, returns 0 and leaves *wire_role unchanged. */
int SpatialObserverMapNativeRoleV2(
    int native_role,
    Spatial__Testing__V2__ClientIdentity__Role *wire_role);

/*
 * Encodes exactly Arm, NativeTransition, ObserverPress as length-delimited
 * protobuf messages. All validation precedes output writes. On failure,
 * returns 0 and sets *output_length to zero when output_length is non-NULL.
 */
int SpatialObserverSerializeServerRecordsV2(
    const Spatial__Testing__V2__NativeInputObservationRecordV2 *const records[3],
    uint8_t *output,
    size_t capacity,
    size_t *output_length);

/*
 * Calls sink once with the complete encoded stream. Sink must return nonzero
 * only after accepting every byte. A refusal or partial/error write is failure;
 * callers must discard any partially written stream and report no success.
 */
int SpatialObserverWriteServerRecordsV2(
    const Spatial__Testing__V2__NativeInputObservationRecordV2 *const records[3],
    SpatialObserverV2Sink sink,
    void *context);

#endif
