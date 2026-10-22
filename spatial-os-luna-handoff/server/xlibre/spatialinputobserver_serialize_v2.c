#include "spatialinputobserver_serialize_v2.h"

#include <string.h>

#define MAX_GRAPH_NODES 4096u
#define MAX_GRAPH_DEPTH 32u

static size_t
varint_size(size_t value)
{
    size_t size = 1;
    while (value > 127) {
        value >>= 7;
        ++size;
    }
    return size;
}

static size_t
write_varint(uint8_t *output, size_t value)
{
    size_t written = 0;
    while (value > 127) {
        output[written++] = (uint8_t)((value & 127u) | 128u);
        value >>= 7;
    }
    output[written++] = (uint8_t)value;
    return written;
}

int
SpatialObserverMapNativeRoleV2(
    int native_role,
    Spatial__Testing__V2__ClientIdentity__Role *wire_role)
{
    if (wire_role == NULL)
        return 0;

    switch (native_role) {
    case SPATIAL_NATIVE_APPLICATION:
        *wire_role = SPATIAL__TESTING__V2__CLIENT_IDENTITY__ROLE__APPLICATION;
        return 1;
    case SPATIAL_NATIVE_SUPERVISOR:
        *wire_role = SPATIAL__TESTING__V2__CLIENT_IDENTITY__ROLE__SUPERVISOR;
        return 1;
    case SPATIAL_NATIVE_COMPOSITOR:
        *wire_role = SPATIAL__TESTING__V2__CLIENT_IDENTITY__ROLE__COMPOSITOR;
        return 1;
    default:
        return 0;
    }
}

static int
validate_graph_node(const ProtobufCMessage *message,
                    const ProtobufCMessageDescriptor *expected,
                    size_t depth,
                    size_t *node_count)
{
    if (message == NULL || expected == NULL ||
        depth > MAX_GRAPH_DEPTH || ++*node_count > MAX_GRAPH_NODES ||
        message->descriptor != expected || message->n_unknown_fields != 0)
        return 0;

    const uint8_t *base = (const uint8_t *)message;
    for (unsigned field_index = 0;
         field_index < expected->n_fields; ++field_index) {
        const ProtobufCFieldDescriptor *field =
            &expected->fields[field_index];
        const void *value = base + field->offset;
        size_t count = 1;
        int active_oneof = 0;

        if ((field->flags & PROTOBUF_C_FIELD_FLAG_ONEOF) != 0) {
            uint32_t active_case =
                *(const uint32_t *)(base + field->quantifier_offset);
            active_oneof = active_case == field->id;
            if (!active_oneof)
                continue;
        } else if (field->label == PROTOBUF_C_LABEL_REPEATED) {
            count = *(const size_t *)(base + field->quantifier_offset);
            if (count > MAX_GRAPH_NODES)
                return 0;
            if (count == 0)
                continue;
            if (*(const void *const *)value == NULL)
                return 0;
        }

        if (field->type == PROTOBUF_C_TYPE_MESSAGE) {
            if (field->label == PROTOBUF_C_LABEL_REPEATED) {
                const ProtobufCMessage *const *items =
                    *(const ProtobufCMessage *const *const *)value;
                for (size_t i = 0; i < count; ++i)
                    if (!validate_graph_node(items[i], field->descriptor,
                                             depth + 1, node_count))
                        return 0;
            } else {
                const ProtobufCMessage *child =
                    *(const ProtobufCMessage *const *)value;
                if (child == NULL) {
                    if (active_oneof ||
                        field->label == PROTOBUF_C_LABEL_REQUIRED)
                        return 0;
                } else if (!validate_graph_node(child, field->descriptor,
                                                depth + 1, node_count)) {
                    return 0;
                }
            }
        } else if (field->type == PROTOBUF_C_TYPE_BYTES) {
            if (field->label == PROTOBUF_C_LABEL_REPEATED) {
                const ProtobufCBinaryData *items =
                    *(const ProtobufCBinaryData *const *)value;
                for (size_t i = 0; i < count; ++i)
                    if (items[i].len != 0 && items[i].data == NULL)
                        return 0;
            } else {
                const ProtobufCBinaryData *bytes =
                    (const ProtobufCBinaryData *)value;
                if (bytes->len != 0 && bytes->data == NULL)
                    return 0;
            }
        } else if (field->type == PROTOBUF_C_TYPE_STRING) {
            const char *string = *(const char *const *)value;
            if (active_oneof && string == NULL)
                return 0;
            if (field->label == PROTOBUF_C_LABEL_REQUIRED && string == NULL)
                return 0;
        }
    }
    return 1;
}

static int
validate_record_graph(
    const Spatial__Testing__V2__NativeInputObservationRecordV2 *record)
{
    size_t node_count = 0;
    return record != NULL &&
           validate_graph_node(
               &record->base,
               &spatial__testing__v2__native_input_observation_record_v2__descriptor,
               0, &node_count);
}

int
SpatialObserverSerializeServerRecordsV2(
    const Spatial__Testing__V2__NativeInputObservationRecordV2 *const records[3],
    uint8_t *output,
    size_t capacity,
    size_t *output_length)
{
    uint8_t encoded[3][SPATIAL_OBSERVER_V2_MAX_FRAME_BYTES];
    size_t encoded_lengths[3];
    size_t required = 0;
    size_t position = 0;
    static const int payloads[] = {
        SPATIAL__TESTING__V2__NATIVE_INPUT_OBSERVATION_RECORD_V2__PAYLOAD_ARM,
        SPATIAL__TESTING__V2__NATIVE_INPUT_OBSERVATION_RECORD_V2__PAYLOAD_NATIVE_TRANSITION,
        SPATIAL__TESTING__V2__NATIVE_INPUT_OBSERVATION_RECORD_V2__PAYLOAD_OBSERVER_PRESS
    };

    if (output_length != NULL)
        *output_length = 0;
    if (records == NULL || output == NULL || output_length == NULL)
        return 0;

    for (size_t index = 0; index < 3; ++index) {
        const Spatial__Testing__V2__NativeInputObservationRecordV2 *record =
            records[index];

        if (!validate_record_graph(record))
            return 0;
        if (record->protocol_version != 2 ||
            record->stream_sequence != index + 1 ||
            record->native_session_epoch == 0 ||
            record->session_id.len != 16 ||
            record->session_id.data == NULL ||
            record->arm_id.len != 16 ||
            record->arm_id.data == NULL ||
            (int)record->payload_case != payloads[index])
            return 0;

        if (index != 0 &&
            (record->native_session_epoch != records[0]->native_session_epoch ||
             memcmp(record->session_id.data, records[0]->session_id.data, 16) != 0 ||
             memcmp(record->arm_id.data, records[0]->arm_id.data, 16) != 0))
            return 0;

        if (index == 0) {
            if (record->arm->accepted_command == NULL)
                return 0;
            const Spatial__Testing__V2__ObserverArmV2 *command =
                record->arm->accepted_command;
            if (command->session_id.len != 16 ||
                command->session_id.data == NULL ||
                command->arm_id.len != 16 ||
                command->arm_id.data == NULL ||
                memcmp(command->session_id.data, record->session_id.data, 16) != 0 ||
                memcmp(command->arm_id.data, record->arm_id.data, 16) != 0)
                return 0;
        } else if (index == 1) {
            const Spatial__Testing__V2__NativeTransition *transition =
                record->native_transition;
            static const int phases[] = {1, 2, 3, 4};

            if (transition->n_snapshots != 4 ||
                transition->snapshots == NULL)
                return 0;
            for (size_t snapshot_index = 0; snapshot_index < 4;
                 ++snapshot_index) {
                const Spatial__Testing__V2__TransitionSnapshot *snapshot =
                    transition->snapshots[snapshot_index];
                if (snapshot == NULL ||
                    (int)snapshot->phase != phases[snapshot_index] ||
                    !snapshot->complete)
                    return 0;
            }
        } else {
            const Spatial__Testing__V2__ObserverPress *press =
                record->observer_press;
            if (press->observer_ordinal == 0 ||
                press->overflow || press->disconnected)
                return 0;
        }

        encoded_lengths[index] =
            spatial__testing__v2__native_input_observation_record_v2__get_packed_size(
                record);
        if (encoded_lengths[index] == 0 ||
            encoded_lengths[index] > sizeof(encoded[index]))
            return 0;
        if (spatial__testing__v2__native_input_observation_record_v2__pack(
                record, encoded[index]) != encoded_lengths[index])
            return 0;

        size_t framed_length =
            varint_size(encoded_lengths[index]) + encoded_lengths[index];
        if (framed_length >
            SPATIAL_OBSERVER_V2_MAX_OUTPUT_BYTES - required)
            return 0;
        required += framed_length;
    }

    if (required > capacity ||
        required > SPATIAL_OBSERVER_V2_MAX_OUTPUT_BYTES)
        return 0;

    for (size_t index = 0; index < 3; ++index) {
        position += write_varint(output + position, encoded_lengths[index]);
        memcpy(output + position, encoded[index], encoded_lengths[index]);
        position += encoded_lengths[index];
    }

    *output_length = position;
    return 1;
}

int
SpatialObserverWriteServerRecordsV2(
    const Spatial__Testing__V2__NativeInputObservationRecordV2 *const records[3],
    SpatialObserverV2Sink sink,
    void *context)
{
    uint8_t output[SPATIAL_OBSERVER_V2_MAX_OUTPUT_BYTES];
    size_t output_length = 0;

    if (sink == NULL ||
        !SpatialObserverSerializeServerRecordsV2(
            records, output, sizeof(output), &output_length))
        return 0;

    return sink(context, output, output_length) != 0;
}
