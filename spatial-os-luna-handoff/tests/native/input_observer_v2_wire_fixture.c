#include "spatialinputobserver_serialize_v2.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
    EXIT_USAGE = 2,
    EXIT_ROLE = 3,
    EXIT_FRAME = 4,
    EXIT_UNPACK = 5,
    EXIT_TRAILING = 6,
    EXIT_REJECTED = 7,
    EXIT_DIRECT_NEGATIVE_FAILED = 8
};

static int
rejecting_sink(void *context, const uint8_t *bytes, size_t length)
{
    (void)context;
    (void)bytes;
    (void)length;
    return 0;
}

static void
initialize_vector(
    Spatial__Testing__V2__NativeInputObservationRecordV2 records[3],
    Spatial__Testing__V2__ObserverArmV2 *command,
    Spatial__Testing__V2__Arm *arm,
    Spatial__Testing__V2__NativeTransition *transition,
    Spatial__Testing__V2__TransitionSnapshot snapshots[4],
    Spatial__Testing__V2__TransitionSnapshot *snapshot_pointers[4],
    Spatial__Testing__V2__ObserverPress *press,
    const uint8_t session_id[16],
    const uint8_t arm_id[16])
{
    spatial__testing__v2__observer_arm_v2__init(command);
    command->session_id.data = (uint8_t *)session_id;
    command->session_id.len = 16;
    command->arm_id.data = (uint8_t *)arm_id;
    command->arm_id.len = 16;

    spatial__testing__v2__arm__init(arm);
    arm->accepted_command = command;

    spatial__testing__v2__native_transition__init(transition);
    transition->n_snapshots = 4;
    transition->snapshots = snapshot_pointers;
    for (size_t i = 0; i < 4; ++i) {
        spatial__testing__v2__transition_snapshot__init(&snapshots[i]);
        snapshots[i].phase =
            (Spatial__Testing__V2__TransitionPhase)(i + 1);
        snapshots[i].complete = 1;
        snapshot_pointers[i] = &snapshots[i];
    }

    spatial__testing__v2__observer_press__init(press);
    press->observer_ordinal = 1;

    for (size_t i = 0; i < 3; ++i) {
        spatial__testing__v2__native_input_observation_record_v2__init(
            &records[i]);
        records[i].protocol_version = 2;
        records[i].session_id.data = (uint8_t *)session_id;
        records[i].session_id.len = 16;
        records[i].arm_id.data = (uint8_t *)arm_id;
        records[i].arm_id.len = 16;
        records[i].native_session_epoch = 9;
        records[i].stream_sequence = i + 1;
    }
    records[0].payload_case =
        SPATIAL__TESTING__V2__NATIVE_INPUT_OBSERVATION_RECORD_V2__PAYLOAD_ARM;
    records[0].arm = arm;
    records[1].payload_case =
        SPATIAL__TESTING__V2__NATIVE_INPUT_OBSERVATION_RECORD_V2__PAYLOAD_NATIVE_TRANSITION;
    records[1].native_transition = transition;
    records[2].payload_case =
        SPATIAL__TESTING__V2__NATIVE_INPUT_OBSERVATION_RECORD_V2__PAYLOAD_OBSERVER_PRESS;
    records[2].observer_press = press;
}

static int
write_constructed_vector(void)
{
    static const uint8_t session_id[16] = "session-vector1";
    static const uint8_t arm_id[16] = "arm-vector-ident";
    Spatial__Testing__V2__NativeInputObservationRecordV2 records[3];
    Spatial__Testing__V2__ObserverArmV2 command;
    Spatial__Testing__V2__Arm arm;
    Spatial__Testing__V2__NativeTransition transition;
    Spatial__Testing__V2__TransitionSnapshot snapshots[4];
    Spatial__Testing__V2__TransitionSnapshot *snapshot_pointers[4];
    Spatial__Testing__V2__ObserverPress press;
    const Spatial__Testing__V2__NativeInputObservationRecordV2 *record_pointers[3];
    uint8_t output[SPATIAL_OBSERVER_V2_MAX_OUTPUT_BYTES];
    size_t output_length = 0;

    initialize_vector(records, &command, &arm, &transition, snapshots,
                      snapshot_pointers, &press, session_id, arm_id);
    for (size_t i = 0; i < 3; ++i)
        record_pointers[i] = &records[i];

    if (!SpatialObserverSerializeServerRecordsV2(
            record_pointers, output, sizeof(output), &output_length))
        return EXIT_REJECTED;
    return fwrite(output, 1, output_length, stdout) == output_length
               ? 0 : EXIT_REJECTED;
}

static int
run_direct_negative(const char *name)
{
    static const uint8_t session_id[16] = "session-vector1";
    static const uint8_t arm_id[16] = "arm-vector-ident";
    Spatial__Testing__V2__NativeInputObservationRecordV2 records[3];
    Spatial__Testing__V2__ObserverArmV2 command;
    Spatial__Testing__V2__Arm arm;
    Spatial__Testing__V2__NativeTransition transition;
    Spatial__Testing__V2__TransitionSnapshot snapshots[4];
    Spatial__Testing__V2__TransitionSnapshot *snapshot_pointers[4];
    Spatial__Testing__V2__ObserverPress press;
    const Spatial__Testing__V2__NativeInputObservationRecordV2 *record_pointers[3];
    uint8_t output[SPATIAL_OBSERVER_V2_MAX_OUTPUT_BYTES];
    size_t output_length = 83;
    Spatial__Testing__V2__ApplicationIdentity app;

    initialize_vector(records, &command, &arm, &transition, snapshots,
                      snapshot_pointers, &press, session_id, arm_id);
    for (size_t i = 0; i < 3; ++i)
        record_pointers[i] = &records[i];
    memset(output, 0xa5, sizeof(output));

    spatial__testing__v2__application_identity__init(&app);

    if (strcmp(name, "null-session-data") == 0) {
        records[0].session_id.data = NULL;
    } else if (strcmp(name, "null-arm-data") == 0) {
        records[0].arm_id.data = NULL;
    } else if (strcmp(name, "null-command-data") == 0) {
        command.session_id.data = NULL;
    } else if (strcmp(name, "null-snapshots-array") == 0) {
        transition.snapshots = NULL;
    } else if (strcmp(name, "null-snapshot") == 0) {
        snapshot_pointers[2] = NULL;
    } else if (strcmp(name, "wrong-record-descriptor") == 0) {
        records[0].base.descriptor =
            &spatial__testing__v2__arm__descriptor;
    } else if (strcmp(name, "wrong-arm-descriptor") == 0) {
        arm.base.descriptor =
            &spatial__testing__v2__observer_arm_v2__descriptor;
    } else if (strcmp(name, "wrong-snapshot-descriptor") == 0) {
        snapshots[1].base.descriptor =
            &spatial__testing__v2__seat_context__descriptor;
    } else if (strcmp(name, "missing-payload") == 0) {
        records[0].arm = NULL;
    } else if (strcmp(name, "missing-accepted-command") == 0) {
        arm.accepted_command = NULL;
    } else if (strcmp(name, "nested-null-bytes") == 0) {
        command.expected_application = &app;
        app.workspace_id.len = 1;
        app.workspace_id.data = NULL;
    } else {
        return EXIT_USAGE;
    }

    if (SpatialObserverSerializeServerRecordsV2(
            record_pointers, output, sizeof(output), &output_length))
        return EXIT_DIRECT_NEGATIVE_FAILED;
    if (output_length != 0)
        return EXIT_DIRECT_NEGATIVE_FAILED;
    for (size_t i = 0; i < sizeof(output); ++i)
        if (output[i] != 0xa5)
            return EXIT_DIRECT_NEGATIVE_FAILED;
    return 0;
}

static int
read_frame_length(FILE *input, size_t *length)
{
    size_t value = 0;
    unsigned count = 0;
    int byte;

    do {
        byte = fgetc(input);
        if (byte == EOF || count >= (sizeof(size_t) * 8u + 6u) / 7u)
            return 0;
        unsigned payload = (unsigned)byte & 0x7fu;
        unsigned shift = count * 7u;
        if (shift >= sizeof(size_t) * 8u ||
            payload > (SIZE_MAX >> shift))
            return 0;
        value |= (size_t)payload << shift;
        ++count;
        if ((byte & 0x80) == 0)
            break;
    } while (1);

    if (count > 1 && value < ((size_t)1 << (7u * (count - 1u))))
        return 0;
    *length = value;
    return 1;
}

int
main(int argc, char **argv)
{
    Spatial__Testing__V2__NativeInputObservationRecordV2 *records[3] = {0};
    const Spatial__Testing__V2__NativeInputObservationRecordV2 *record_pointers[3];
    uint8_t input[SPATIAL_OBSERVER_V2_MAX_FRAME_BYTES];
    uint8_t output[SPATIAL_OBSERVER_V2_MAX_OUTPUT_BYTES];
    size_t output_length = 0;
    size_t capacity = sizeof(output);
    int use_rejecting_sink = 0;
    int result = EXIT_REJECTED;

    if (argc == 2 && strcmp(argv[1], "--construct") == 0)
        return write_constructed_vector();
    if (argc == 3 && strcmp(argv[1], "--direct-negative") == 0)
        return run_direct_negative(argv[2]);
    if (argc == 3 && strcmp(argv[1], "--map-role") == 0) {
        char *end = NULL;
        errno = 0;
        long native_role = strtol(argv[2], &end, 10);
        if (errno != 0 || end == argv[2] || *end != '\0' ||
            native_role < INT32_MIN || native_role > INT32_MAX)
            return EXIT_ROLE;
        Spatial__Testing__V2__ClientIdentity__Role wire_role;
        if (!SpatialObserverMapNativeRoleV2((int)native_role, &wire_role))
            return EXIT_ROLE;
        printf("%d\n", (int)wire_role);
        return 0;
    }

    for (int argument = 1; argument < argc; ++argument) {
        if (strcmp(argv[argument], "--sink-fail") == 0) {
            use_rejecting_sink = 1;
        } else if (strcmp(argv[argument], "--capacity") == 0 &&
                   argument + 1 < argc) {
            char *end = NULL;
            errno = 0;
            unsigned long long parsed =
                strtoull(argv[++argument], &end, 10);
            if (errno != 0 || end == argv[argument] || *end != '\0' ||
                parsed > SIZE_MAX)
                return EXIT_USAGE;
            capacity = (size_t)parsed;
        } else {
            return EXIT_USAGE;
        }
    }

    for (size_t index = 0; index < 3; ++index) {
        size_t frame_length = 0;
        if (!read_frame_length(stdin, &frame_length) ||
            frame_length == 0 ||
            frame_length > sizeof(input) ||
            fread(input, 1, frame_length, stdin) != frame_length) {
            result = EXIT_FRAME;
            goto cleanup;
        }

        records[index] =
            spatial__testing__v2__native_input_observation_record_v2__unpack(
                NULL, frame_length, input);
        if (records[index] == NULL) {
            result = EXIT_UNPACK;
            goto cleanup;
        }
    }

    if (fgetc(stdin) != EOF) {
        result = EXIT_TRAILING;
        goto cleanup;
    }

    for (size_t i = 0; i < 3; ++i)
        record_pointers[i] = records[i];

    if (use_rejecting_sink) {
        result = SpatialObserverWriteServerRecordsV2(
                     record_pointers, rejecting_sink, NULL)
                     ? 0 : EXIT_REJECTED;
        goto cleanup;
    }

    if (!SpatialObserverSerializeServerRecordsV2(
            record_pointers, output, capacity, &output_length)) {
        result = EXIT_REJECTED;
        goto cleanup;
    }

    result = fwrite(output, 1, output_length, stdout) == output_length
                 ? 0 : EXIT_REJECTED;

cleanup:
    for (size_t i = 0; i < 3; ++i)
        if (records[i] != NULL)
            spatial__testing__v2__native_input_observation_record_v2__free_unpacked(
                records[i], NULL);
    return result;
}
