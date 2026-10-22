/* Compile-only producer fixture. Run only on assigned acceptance hosts. */
#include <dix-config.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "spatialinputobserver_serialize.h"

static SpatialObserverCaptureRecord capture_record;
static SpatialObserverTrustedContext trusted;
static uint8_t output[SPATIAL_OBSERVER_MAX_OUTPUT_BYTES];

static void setup(void)
{
    capture_record.phase = SPATIAL_CAPTURE_COLLECTED;
    capture_record.failure = SPATIAL_CAPTURE_NO_FAILURE;
    capture_record.session = 9; capture_record.arm_serial = 1;
    capture_record.callback_ordinal = 1; capture_record.event_seen = TRUE;
    capture_record.recipient_index = 5;
    capture_record.expected_button = 1; capture_record.arm_monotonic_ns = 1000;
    capture_record.callback_monotonic_ns = 1100;
    capture_record.old_devices.session = capture_record.peer_devices.session = 9;
    capture_record.popup.session = capture_record.popup.owner.session = 9;
    capture_record.popup.epoch = 20; capture_record.popup.xid = 100;
    capture_record.popup.owner.epoch = 21; capture_record.popup.owner.index = 5;
    capture_record.popup.owner.label.workspace_size = 3;
    capture_record.popup.owner.label.application_size = 3;
    memcpy(capture_record.popup.owner.label.workspace, "ws1", 3);
    memcpy(capture_record.popup.owner.label.application, "app", 3);
    for (unsigned i = 0; i < 4; ++i) {
        SpatialObserverDevicePin *old = &capture_record.old_devices.devices[i];
        SpatialObserverDevicePin *peer = &capture_record.peer_devices.devices[i];
        old->id = 10 + (int)i; old->epoch = 30 + i; old->role = (enum SpatialObserverDeviceRole)i;
        peer->id = 20 + (int)i; peer->epoch = 40 + i; peer->role = (enum SpatialObserverDeviceRole)i;
        capture_record.old_native.devices[i].identity = *old;
        capture_record.old_native.devices[i].paired_or_attached = i == 0 || i == 3 ? 12 : 10;
        capture_record.peer_native.devices[i].identity = *peer;
        capture_record.peer_native.devices[i].paired_or_attached = i == 0 || i == 3 ? 22 : 20;
        if (i < 2) {
            capture_record.old_native.devices[i].has_button_class = TRUE;
            capture_record.peer_native.devices[i].has_button_class = TRUE;
        } else {
            capture_record.old_native.devices[i].has_key_class = TRUE;
            capture_record.peer_native.devices[i].has_key_class = TRUE;
        }
    }
    capture_record.old_native.valid = capture_record.peer_native.valid = TRUE;
    capture_record.old_native.session = capture_record.peer_native.session = 9;
    capture_record.event.type = GenericEvent; capture_record.event.extension = 1;
    capture_record.event.buttons_len = DOWN_LENGTH / 4;
    capture_record.event.valuators_len = (MAX_VALUATORS + 31) / 32;
    capture_record.packet_bytes = sizeof(capture_record.event) +
        capture_record.event.buttons_len * 4 + capture_record.event.valuators_len * 4;
    capture_record.event.length = (capture_record.packet_bytes - sizeof(xEvent)) / 4;
    capture_record.event.evtype = XI_ButtonPress; capture_record.event.deviceid = 20;
    capture_record.event.sourceid = 21; capture_record.event.event = 100;
    capture_record.event.detail = 1; capture_record.event.time = 77;
    for (unsigned i = 0; i < sizeof(trusted.session_id); ++i) {
        trusted.session_id[i] = (uint8_t)(i + 1); trusted.arm_id[i] = (uint8_t)(i + 33);
    }
    for (unsigned i = 0; i < sizeof(trusted.actor_id); ++i) {
        trusted.actor_id[i] = (uint8_t)(i + 1); trusted.issuer_id[i] = (uint8_t)(i + 41);
        trusted.peer_actor_id[i] = (uint8_t)(i + 81); trusted.peer_issuer_id[i] = (uint8_t)(i + 121);
    }
    trusted.spatial_generation = 3; trusted.prearm_receipt_watermark = 50;
    trusted.deadline_monotonic_ns = 1200;
}

int main(int argc, char **argv)
{
    size_t length = 777;
    size_t capacity = sizeof(output);
    int expect_reject = 0;
    if (argc < 2 || argc > 3) return 64;
    setup(); memset(output, 0xa5, sizeof(output));
    if (!strcmp(argv[1], "held-key")) capture_record.old_native.devices[2].key_down[4] = 0x20;
    else if (!strcmp(argv[1], "held-button")) capture_record.old_native.devices[0].button_down[2] = 0x08;
    else if (!strcmp(argv[1], "stale-session")) { capture_record.peer_devices.session++; expect_reject = 1; }
    else if (!strcmp(argv[1], "stale-pin")) { capture_record.old_devices.devices[0].epoch++; expect_reject = 1; }
    else if (!strcmp(argv[1], "bad-role")) { capture_record.peer_devices.devices[2].role = SPATIAL_OBSERVER_POINTER_SLAVE; expect_reject = 1; }
    else if (!strcmp(argv[1], "bad-label")) { capture_record.popup.owner.label.workspace_size = sizeof(capture_record.popup.owner.label.workspace) + 1; expect_reject = 1; }
    else if (!strcmp(argv[1], "bad-tuple")) { capture_record.event.sourceid++; expect_reject = 1; }
    else if (!strcmp(argv[1], "bad-time")) { capture_record.callback_monotonic_ns = 999; expect_reject = 1; }
    else if (!strcmp(argv[1], "bad-phase")) { capture_record.phase = SPATIAL_CAPTURE_ARMED; expect_reject = 1; }
    else if (!strcmp(argv[1], "synthetic-type")) { capture_record.event.type |= 0x80; expect_reject = 1; }
    else if (!strcmp(argv[1], "wrong-type")) { capture_record.event.type = KeyPress; expect_reject = 1; }
    else if (!strcmp(argv[1], "truncated-packet")) { capture_record.packet_bytes -= 4; expect_reject = 1; }
    else if (!strcmp(argv[1], "length-mismatch")) { capture_record.event.length++; expect_reject = 1; }
    else if (strcmp(argv[1], "valid") && strcmp(argv[1], "capacity-exact") && strcmp(argv[1], "capacity-short")) return 64;
    if (argc == 3) {
        char *end = NULL; unsigned long long parsed = strtoull(argv[2], &end, 10);
        if (!end || *end) return 64;
        capacity = (size_t)parsed; expect_reject = !strcmp(argv[1], "capacity-short");
    }
    Bool ok = SpatialObserverSerialize(&capture_record, &trusted, output, capacity, &length);
    if (expect_reject) {
        if (ok || length != 0) return 65;
        for (size_t i = 0; i < sizeof(output); ++i) if (output[i] != 0xa5) return 66;
        return 0;
    }
    if (!ok || !length) return 67;
    if (fwrite(output, 1, length, stdout) != length) return 68;
    return 0;
}
