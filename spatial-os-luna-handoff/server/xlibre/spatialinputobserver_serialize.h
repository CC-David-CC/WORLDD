/* SPDX-License-Identifier: MIT
 * Qualification-only server-local protobuf writer. No client or socket API.
 */
#ifndef SPATIAL_INPUT_OBSERVER_SERIALIZE_H
#define SPATIAL_INPUT_OBSERVER_SERIALIZE_H
#include <stddef.h>
#include <stdint.h>
#include "spatialinputobserver_capture.h"

#define SPATIAL_OBSERVER_TOKEN_BYTES 16u
#define SPATIAL_OBSERVER_ACTOR_BYTES 32u
#define SPATIAL_OBSERVER_MAX_RECORD_BYTES 4096u
#define SPATIAL_OBSERVER_MAX_OUTPUT_BYTES (2u * (SPATIAL_OBSERVER_MAX_RECORD_BYTES + 2u))

typedef struct {
    uint8_t session_id[SPATIAL_OBSERVER_TOKEN_BYTES];
    uint8_t arm_id[SPATIAL_OBSERVER_TOKEN_BYTES];
    uint8_t actor_id[SPATIAL_OBSERVER_ACTOR_BYTES];
    uint8_t issuer_id[SPATIAL_OBSERVER_ACTOR_BYTES];
    uint8_t peer_actor_id[SPATIAL_OBSERVER_ACTOR_BYTES];
    uint8_t peer_issuer_id[SPATIAL_OBSERVER_ACTOR_BYTES];
    uint64_t spatial_generation;
    uint64_t prearm_receipt_watermark;
    uint64_t deadline_monotonic_ns;
} SpatialObserverTrustedContext;

/* Emits only Arm(seq 1) and ObserverPress(seq 2). The supervisor supplies the
 * independent ProbeReceiptBatch. Context fields are trusted inputs, not signed.
 * TRUE means encoded output, never native control-release success.
 */
Bool SpatialObserverSerialize(const SpatialObserverCaptureRecord *capture,
                              const SpatialObserverTrustedContext *context,
                              uint8_t *output, size_t output_capacity,
                              size_t *output_length);
#endif
