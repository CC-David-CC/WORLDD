/* SPDX-License-Identifier: MIT
 * One parent-owned AF_UNIX endpoint. It never grants application authority.
 */
#include <dix-config.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
#include "os.h"
#include "spatialsecurity.h"
#include "spatialinputobserver_lifetime.h"
#include "spatialinputobserver_endpoint.h"

#define MAX_PACKET 4096u
#define STARTUP_BUDGET_NS UINT64_C(10000000000)
#define MAX_NATIVE_OUTPUT (3u * (4096u + 2u))
static int output_fd = -1;
static Bool output_configured, output_registered, output_started, output_complete;
static uint8_t output_bytes[MAX_NATIVE_OUTPUT];
static size_t output_length, output_offset;
static int endpoint = -1;
static Bool configured, registered, setup_seen, arm_seen, timer_running;
static Spatial__Testing__V3__LifecycleControlEnvelopeV3 *setup_control, *accepted_control;
static Spatial__Testing__V3__LifecycleEndpointReplyV3 *lifecycle_pins;
static uint8_t lifecycle_bytes[MAX_PACKET];
static size_t lifecycle_length;
static Spatial__Testing__V2__ObserverEndpointReplyV2 *setup_pins;
static uint64_t setup_monotonic_ns;
static uint8_t setup_bytes[MAX_PACKET];
static size_t setup_length;
static struct ucred parent;
static uint8_t session_id[16];
static uint64_t startup_deadline;
static OsTimerPtr deadline_timer;

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) || now.tv_sec < 0 ||
        now.tv_nsec < 0 || now.tv_nsec >= 1000000000L ||
        (uint64_t)now.tv_sec > (UINT64_MAX - (uint64_t)now.tv_nsec) / UINT64_C(1000000000))
        return 0;
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

static void close_endpoint(const char *reason)
{
    if (output_registered) RemoveNotifyFd(output_fd);
    output_registered = FALSE;
    if (output_fd >= 0) close(output_fd);
    output_fd = -1; output_length = output_offset = 0;
    memset(output_bytes, 0, sizeof(output_bytes));
    if (endpoint >= 0) {
        if (registered) RemoveNotifyFd(endpoint);
        registered = FALSE;
        close(endpoint);
        endpoint = -1;
        if (reason) ErrorF("Spatial observer endpoint rejected: %s\n", reason);
    }
    if (deadline_timer && !timer_running) TimerCancel(deadline_timer);
    if (setup_control) spatial__testing__v3__lifecycle_control_envelope_v3__free_unpacked(setup_control, NULL);
    if (accepted_control) spatial__testing__v3__lifecycle_control_envelope_v3__free_unpacked(accepted_control, NULL);
    if (setup_pins) spatial__testing__v2__observer_endpoint_reply_v2__free_unpacked(setup_pins, NULL);
    if (lifecycle_pins) spatial__testing__v3__lifecycle_endpoint_reply_v3__free_unpacked(lifecycle_pins, NULL);
    lifecycle_pins = NULL; lifecycle_length = 0;
    memset(lifecycle_bytes, 0, sizeof(lifecycle_bytes));
    setup_control = accepted_control = NULL;
    setup_pins = NULL;
    setup_length = 0;
    memset(setup_bytes, 0, sizeof(setup_bytes));
}

void SpatialObserverEndpointOutputConfigureV2(const char *descriptor)
{
    char *end = NULL; struct stat status;
    if (output_configured || !descriptor || !*descriptor)
        FatalError("Spatial observer output requires one inherited descriptor\n");
    output_configured = TRUE; errno = 0;
    long value = strtol(descriptor, &end, 10);
    if (errno || *end || value < 3 || value > INT_MAX)
        FatalError("Invalid spatial observer output descriptor\n");
    output_fd = (int)value;
    int flags = fcntl(output_fd, F_GETFL);
    if (fstat(output_fd, &status) || !S_ISFIFO(status.st_mode) || flags < 0 ||
        (flags & O_ACCMODE) != O_WRONLY)
        FatalError("Spatial observer output requires a write-only anonymous FIFO\n");
    int descriptor_flags = fcntl(output_fd, F_GETFD);
    if (descriptor_flags < 0 || fcntl(output_fd, F_SETFD, descriptor_flags | FD_CLOEXEC) < 0 ||
        fcntl(output_fd, F_SETFL, flags | O_NONBLOCK) < 0)
        FatalError("Cannot secure spatial observer output descriptor\n");
}

static Bool arm_available(void)
{
    uint64_t now = monotonic_ns();
    return endpoint >= 0 && output_fd >= 0 && !output_started && accepted_control &&
        now && now < startup_deadline;
}

static const Spatial__Testing__V2__ObserverSetupRequestV2 *common_setup(void)
{
    if (!setup_control) return NULL;
    if (setup_control->message_case == SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LEGACY_SETUP_REQUEST)
        return setup_control->legacy_setup_request;
    return setup_control->message_case == SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LIFECYCLE_SETUP_REQUEST &&
        setup_control->lifecycle_setup_request ? setup_control->lifecycle_setup_request->common : NULL;
}

const Spatial__Testing__V3__LifecycleArmV3 *SpatialObserverEndpointArmV3(void)
{
    return arm_available() && accepted_control->message_case ==
        SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LIFECYCLE_ARM ? accepted_control->lifecycle_arm : NULL;
}

const Spatial__Testing__V2__ObserverArmV2 *SpatialObserverEndpointArmV2(void)
{
    return arm_available() && accepted_control->message_case ==
        SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LEGACY_ARM ? accepted_control->legacy_arm : NULL;
}

Bool SpatialObserverEndpointPinsCurrentV2(void)
{
    uint8_t fresh[MAX_PACKET]; size_t length = 0;
    return SpatialObserverEndpointArmV2() && common_setup() &&
        SpatialInputObserverSetup(common_setup(), fresh, sizeof(fresh), &length) &&
        length == setup_length && !memcmp(fresh, setup_bytes, length);
}

void SpatialObserverEndpointFailV2(const char *reason)
{
    close_endpoint(reason);
}

static void output_ready(int fd, int ready, void *data)
{
    (void)ready; (void)data;
    uint64_t now = monotonic_ns();
    if (!now || now >= startup_deadline) { close_endpoint("native output deadline"); return; }
    ssize_t count = write(fd, output_bytes + output_offset, output_length - output_offset);
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return;
    if (count <= 0) { close_endpoint("native output write failure"); return; }
    output_offset += (size_t)count;
    if (output_offset == output_length) { output_complete = TRUE; close_endpoint(NULL); }
}

Bool SpatialObserverEndpointOutputV2(const uint8_t *bytes, size_t length)
{
    if (!SpatialObserverEndpointArmV2() || !bytes || !length || length > sizeof(output_bytes)) return FALSE;
    output_started = TRUE; output_length = length; output_offset = 0;
    memcpy(output_bytes, bytes, length);
    output_registered = SetNotifyFd(output_fd, output_ready, X_NOTIFY_WRITE, NULL);
    if (!output_registered) { close_endpoint("native output notify registration"); return FALSE; }
    output_ready(output_fd, X_NOTIFY_WRITE, NULL);
    return output_complete || output_fd >= 0;
}

static CARD32 expired(OsTimerPtr timer, CARD32 now, void *data)
{
    (void)timer; (void)now; (void)data;
    timer_running = TRUE;
    close_endpoint("deadline");
    timer_running = FALSE;
    return 0;
}

void SpatialObserverEndpointReset(void)
{
    close_endpoint(NULL);
    if (deadline_timer) TimerFree(deadline_timer);
    deadline_timer = NULL;
    memset(session_id, 0, sizeof(session_id));
    /* configured and setup_seen are process-local consumption latches. */
}

void SpatialObserverEndpointConfigure(const char *descriptor)
{
    char *end = NULL;
    long value;
    struct sockaddr_un address;
    socklen_t size;
    int kind, flags, enabled = 1;
    if (configured || !descriptor || !*descriptor)
        FatalError("Spatial observer requires one inherited endpoint\n");
    configured = TRUE;
    errno = 0;
    value = strtol(descriptor, &end, 10);
    if (errno || *end || value < 3 || value > INT_MAX)
        FatalError("Invalid spatial observer endpoint descriptor\n");
    endpoint = (int)value;
    size = sizeof(kind);
    if (getsockopt(endpoint, SOL_SOCKET, SO_TYPE, &kind, &size) ||
        size != sizeof(kind) || kind != SOCK_SEQPACKET)
        FatalError("Spatial observer endpoint must be a connected Unix seqpacket socket\n");
    size = sizeof(address);
    if (getpeername(endpoint, (struct sockaddr *)&address, &size) ||
        address.sun_family != AF_UNIX)
        FatalError("Spatial observer endpoint must be a connected Unix socket\n");
    size = sizeof(parent);
    if (getsockopt(endpoint, SOL_SOCKET, SO_PEERCRED, &parent, &size) ||
        size != sizeof(parent) || parent.pid != getppid() || parent.pid <= 1 ||
        parent.uid != getuid() || parent.gid != getgid())
        FatalError("Spatial observer endpoint must belong to the launching parent\n");
    flags = fcntl(endpoint, F_GETFD);
    if (flags < 0 || fcntl(endpoint, F_SETFD, flags | FD_CLOEXEC) < 0 ||
        (flags = fcntl(endpoint, F_GETFL)) < 0 ||
        fcntl(endpoint, F_SETFL, flags | O_NONBLOCK) < 0 ||
        setsockopt(endpoint, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)))
        FatalError("Cannot secure spatial observer endpoint\n");
}

/* Generated unpacked graphs have finite, schema-owned descriptors. */
static Bool checked(const ProtobufCMessage *message, unsigned depth, unsigned *nodes)
{
    if (!message || depth > 16 || ++*nodes > 256 || message->n_unknown_fields ||
        !protobuf_c_message_check(message)) return FALSE;
    const uint8_t *base = (const uint8_t *)message;
    for (unsigned i = 0; i < message->descriptor->n_fields; ++i) {
        const ProtobufCFieldDescriptor *field = &message->descriptor->fields[i];
        if (field->type != PROTOBUF_C_TYPE_MESSAGE) continue;
        if ((field->flags & PROTOBUF_C_FIELD_FLAG_ONEOF) &&
            *(const unsigned *)(base + field->quantifier_offset) != field->id) continue;
        if (field->label == PROTOBUF_C_LABEL_REPEATED) {
            size_t count = *(const size_t *)(base + field->quantifier_offset);
            ProtobufCMessage *const *items = *(ProtobufCMessage *const *const *)(base + field->offset);
            if (count > 256 || (count && !items)) return FALSE;
            for (size_t n = 0; n < count; ++n)
                if (!checked(items[n], depth + 1, nodes)) return FALSE;
        } else {
            ProtobufCMessage *child = *(ProtobufCMessage *const *)(base + field->offset);
            if (child && !checked(child, depth + 1, nodes)) return FALSE;
        }
    }
    return TRUE;
}

static Bool authenticated(struct msghdr *message)
{
    Bool valid = !(message->msg_flags & (MSG_TRUNC | MSG_CTRUNC));
    unsigned credentials = 0;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(message); c; c = CMSG_NXTHDR(message, c)) {
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS) {
            if (c->cmsg_len >= CMSG_LEN(0)) {
                size_t count = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
                const int *fds = (const int *)CMSG_DATA(c);
                for (size_t i = 0; i < count; ++i) close(fds[i]);
            }
            valid = FALSE;
        } else if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_CREDENTIALS &&
                   c->cmsg_len == CMSG_LEN(sizeof(struct ucred))) {
            struct ucred sender;
            memcpy(&sender, CMSG_DATA(c), sizeof(sender));
            ++credentials;
            if (sender.pid != parent.pid || sender.uid != parent.uid || sender.gid != parent.gid)
                valid = FALSE;
        } else valid = FALSE;
    }
    return valid && credentials == 1;
}

static Bool send_packet(const uint8_t *bytes, size_t size)
{
    if (!size || size > MAX_PACKET || endpoint < 0) return FALSE;
    ssize_t sent;
    do { sent = send(endpoint, bytes, size, MSG_DONTWAIT | MSG_NOSIGNAL); }
    while (sent < 0 && errno == EINTR);
    return sent == (ssize_t)size;
}

static Bool same_message(const ProtobufCMessage *left, const ProtobufCMessage *right)
{
    uint8_t a[MAX_PACKET], b[MAX_PACKET];
    if (!left || !right || left->descriptor != right->descriptor) return FALSE;
    size_t size = protobuf_c_message_get_packed_size(left);
    return size <= sizeof(a) && size == protobuf_c_message_get_packed_size(right) &&
        protobuf_c_message_pack(left, a) == size && protobuf_c_message_pack(right, b) == size &&
        !memcmp(a, b, size);
}

static Bool same_bytes(ProtobufCBinaryData left, ProtobufCBinaryData right)
{
    return left.len == right.len && (!left.len || !memcmp(left.data, right.data, left.len));
}

static Bool same_devices(size_t count, Spatial__Testing__V2__DeviceIdentity **devices,
                         size_t expected_count, Spatial__Testing__V2__DeviceIdentity **expected)
{
    if (count != 4 || expected_count != 4) return FALSE;
    for (size_t i = 0; i < count; ++i) {
        unsigned matches = 0;
        if (!devices[i]) return FALSE;
        for (size_t j = 0; j < expected_count; ++j)
            if (same_message(&devices[i]->base, &expected[j]->base)) ++matches;
        if (matches != 1) return FALSE;
        for (size_t j = 0; j < i; ++j)
            if (devices[i]->device_id == devices[j]->device_id) return FALSE;
    }
    return TRUE;
}

static Bool valid_arm(const Spatial__Testing__V2__ObserverArmV2 *arm, uint64_t now)
{
    if (!arm || !setup_control || !setup_pins || !setup_pins->setup_reply) return FALSE;
    const Spatial__Testing__V2__ObserverSetupReplyV2 *pins = setup_pins->setup_reply;
    const Spatial__Testing__V2__ObserverSetupRequestV2 *request = common_setup();
    if (!request) return FALSE;
    if (!same_bytes(arm->session_id, request->session_id) || !same_bytes(arm->arm_id, request->arm_id) ||
        arm->arm_monotonic_ns < setup_monotonic_ns || arm->arm_monotonic_ns > now ||
        arm->deadline_monotonic_ns != request->deadline_monotonic_ns ||
        arm->deadline_monotonic_ns <= now ||
        arm->deadline_monotonic_ns - arm->arm_monotonic_ns > STARTUP_BUDGET_NS ||
        !arm->old_actor || !arm->peer_actor ||
        arm->old_actor->issuer_id.len != 32 || arm->old_actor->actor_id.len != 32 ||
        arm->peer_actor->issuer_id.len != 32 || arm->peer_actor->actor_id.len != 32 ||
        same_message(&arm->old_actor->base, &arm->peer_actor->base) ||
        !arm->old_world_actor_id || !arm->peer_world_actor_id ||
        arm->old_world_actor_id == arm->peer_world_actor_id ||
        arm->old_native_seat_id != pins->old_native_seat_id ||
        arm->peer_native_seat_id != pins->peer_native_seat_id ||
        arm->presentation_generation != request->presentation_generation ||
        !arm->expected_application || !arm->expected_window || !arm->expected_seat_owner ||
        !arm->expected_window_owner || !arm->expected_recipient || !arm->expected_grab_owner ||
        !same_message(&arm->expected_application->base, &request->expected_application->base) ||
        !same_message(&arm->expected_window->base, &pins->app_target->base) ||
        !same_message(&arm->expected_seat_owner->base, &pins->compositor_seat_owner->base) ||
        !same_message(&arm->expected_window_owner->base, &pins->app_target_owner->base) ||
        !same_message(&arm->expected_recipient->base, &pins->probe_marker->privileged_owner->base) ||
        !same_message(&arm->expected_grab_owner->base, &arm->expected_recipient->base) ||
        !same_devices(arm->n_old_actor_devices, arm->old_actor_devices,
                      pins->n_old_actor_devices, pins->old_actor_devices) ||
        !same_devices(arm->n_peer_actor_devices, arm->peer_actor_devices,
                      pins->n_peer_actor_devices, pins->peer_actor_devices) ||
        arm->expected_xi2_event_type != 4 || !arm->expected_detail || arm->expected_detail >= 32 ||
        arm->n_expected_held_keys < 2 || arm->n_expected_held_buttons < 1 ||
        arm->n_expected_held_keys + arm->n_expected_held_buttons + 1 > 32) return FALSE;
    Bool modifier = FALSE;
    for (size_t i = 0; i < arm->n_expected_held_keys; ++i) {
        uint32_t code = arm->expected_held_keys[i];
        if (code < 8 || code >= 256 || (i && code <= arm->expected_held_keys[i - 1])) return FALSE;
        if (code == arm->expected_modifier_keycode) modifier = TRUE;
    }
    for (size_t i = 0; i < arm->n_expected_held_buttons; ++i) {
        uint32_t button = arm->expected_held_buttons[i];
        if (!button || button >= 32 || (i && button <= arm->expected_held_buttons[i - 1])) return FALSE;
    }
    if (!modifier || !SpatialInputObserverArm(arm)) return FALSE;
    /* Resolve all native pins again. Reused XIDs, device IDs and client slots
     * must not inherit the previously acknowledged object's identity. */
    uint8_t fresh[MAX_PACKET];
    size_t length = 0;
    return SpatialInputObserverSetup(request, fresh, sizeof(fresh), &length) &&
        length == setup_length && !memcmp(fresh, setup_bytes, length);
}

static void receive_packet(int fd, int ready, void *data)
{
    (void)ready; (void)data;
    uint8_t bytes[MAX_PACKET], canonical[MAX_PACKET], output[MAX_PACKET];
    union { struct cmsghdr alignment; uint8_t bytes[CMSG_SPACE(sizeof(struct ucred)) + CMSG_SPACE(16 * sizeof(int))]; } ancillary;
    struct iovec iov = { .iov_base = bytes, .iov_len = sizeof(bytes) };
    struct msghdr message = { .msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = ancillary.bytes, .msg_controllen = sizeof(ancillary.bytes) };
    ssize_t size = recvmsg(fd, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
    if (size < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) return;
    if (size <= 0) { close_endpoint("EOF or read failure"); return; }
    if (!authenticated(&message)) { close_endpoint("sender or ancillary data"); return; }
    uint64_t now = monotonic_ns();
    if (!now || now >= startup_deadline) { close_endpoint("deadline"); return; }
    if (arm_seen) { close_endpoint("one-shot arm replay"); return; }
    const Bool initial = !setup_seen;
    if (initial) setup_seen = TRUE;
    else arm_seen = TRUE; /* Consume before parsing or checking live pins. */
    Spatial__Testing__V3__LifecycleControlEnvelopeV3 *envelope =
        spatial__testing__v3__lifecycle_control_envelope_v3__unpack(NULL, (size_t)size, bytes);
    unsigned nodes = 0;
    Bool valid = envelope && checked(&envelope->base, 0, &nodes) &&
        spatial__testing__v3__lifecycle_control_envelope_v3__get_packed_size(envelope) == (size_t)size;
    if (valid) valid = spatial__testing__v3__lifecycle_control_envelope_v3__pack(envelope, canonical) == (size_t)size &&
                      !memcmp(bytes, canonical, (size_t)size);
    const char *reason = initial ? "invalid setup or native pin" : "invalid arm or native pin";
    if (valid && !initial && (envelope->message_case ==
        SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LEGACY_SETUP_REQUEST ||
        envelope->message_case == SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LIFECYCLE_SETUP_REQUEST)) {
        valid = FALSE;
        reason = "one-shot setup replay";
    }
    if (valid && initial) {
        const Spatial__Testing__V3__LifecycleSetupRequestV3 *lifecycle = envelope->message_case ==
            SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LIFECYCLE_SETUP_REQUEST ? envelope->lifecycle_setup_request : NULL;
        const Spatial__Testing__V2__ObserverSetupRequestV2 *request = lifecycle ? lifecycle->common :
            (envelope->message_case == SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LEGACY_SETUP_REQUEST ?
             envelope->legacy_setup_request : NULL);
        valid = request && request->session_id.len == sizeof(session_id) &&
            !memcmp(request->session_id.data, session_id, sizeof(session_id)) &&
            request->arm_id.len == 16 && request->deadline_monotonic_ns > now &&
            request->deadline_monotonic_ns - now <= STARTUP_BUDGET_NS;
        size_t length = 0;
        if (valid) valid = SpatialInputObserverSetup(request, output, sizeof(output), &length);
        if (valid) {
            setup_pins = spatial__testing__v2__observer_endpoint_reply_v2__unpack(NULL, length, output);
            valid = setup_pins && setup_pins->setup_reply;
        }
        if (valid) {
            setup_monotonic_ns = now;
            setup_control = envelope; envelope = NULL;
            memcpy(setup_bytes, output, length); setup_length = length;
            if (lifecycle) {
                valid = SpatialInputObserverLifecycleSetup(lifecycle, output, sizeof(output), &length);
                if (valid) {
                    lifecycle_pins = spatial__testing__v3__lifecycle_endpoint_reply_v3__unpack(NULL, length, output);
                    valid = lifecycle_pins && lifecycle_pins->lifecycle_setup_reply;
                }
                if (valid) { memcpy(lifecycle_bytes, output, length); lifecycle_length = length; }
            }
            if (request->deadline_monotonic_ns < startup_deadline) startup_deadline = request->deadline_monotonic_ns;
            uint64_t remaining_ms = (startup_deadline - now + UINT64_C(999999)) / UINT64_C(1000000);
            valid = valid && TimerSet(deadline_timer, 0, (CARD32)remaining_ms, expired, NULL) != NULL &&
                send_packet(output, length);
        }
    } else if (valid) {
        const Spatial__Testing__V3__LifecycleArmV3 *lifecycle = envelope->message_case ==
            SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LIFECYCLE_ARM ? envelope->lifecycle_arm : NULL;
        const Spatial__Testing__V2__ObserverArmV2 *common = lifecycle ? lifecycle->common :
            (envelope->message_case == SPATIAL__TESTING__V3__LIFECYCLE_CONTROL_ENVELOPE_V3__MESSAGE_LEGACY_ARM ? envelope->legacy_arm : NULL);
        valid = (lifecycle_pins != NULL) == (lifecycle != NULL) && valid_arm(common, now);
        if (valid && lifecycle) {
            const Spatial__Testing__V3__LifecycleSetupReplyV3 *pins = lifecycle_pins->lifecycle_setup_reply;
            size_t fresh_length = 0;
            valid = lifecycle->peer_window && lifecycle->cause == pins->accepted_request->cause &&
                same_message(&lifecycle->peer_window->base, &pins->peer_window->base) &&
                SpatialInputObserverLifecycleSetup(setup_control->lifecycle_setup_request,
                    output, sizeof(output), &fresh_length) &&
                fresh_length == lifecycle_length && !memcmp(output, lifecycle_bytes, fresh_length);
        }
        if (valid) {
            Spatial__Testing__V2__Arm arm = SPATIAL__TESTING__V2__ARM__INIT;
            Spatial__Testing__V2__ObserverEndpointReplyV2 reply = SPATIAL__TESTING__V2__OBSERVER_ENDPOINT_REPLY_V2__INIT;
            arm.accepted_command = (Spatial__Testing__V2__ObserverArmV2 *)common;
            reply.message_case = SPATIAL__TESTING__V2__OBSERVER_ENDPOINT_REPLY_V2__MESSAGE_ARM_ACCEPTED;
            reply.arm_accepted = &arm;
            size_t length = spatial__testing__v2__observer_endpoint_reply_v2__get_packed_size(&reply);
            if (lifecycle) {
                Spatial__Testing__V3__LifecycleArmAcceptedV3 accepted = SPATIAL__TESTING__V3__LIFECYCLE_ARM_ACCEPTED_V3__INIT;
                Spatial__Testing__V3__LifecycleEndpointReplyV3 lifecycle_reply = SPATIAL__TESTING__V3__LIFECYCLE_ENDPOINT_REPLY_V3__INIT;
                accepted.accepted_command = envelope->lifecycle_arm;
                lifecycle_reply.message_case = SPATIAL__TESTING__V3__LIFECYCLE_ENDPOINT_REPLY_V3__MESSAGE_LIFECYCLE_ARM_ACCEPTED;
                lifecycle_reply.lifecycle_arm_accepted = &accepted;
                length = spatial__testing__v3__lifecycle_endpoint_reply_v3__get_packed_size(&lifecycle_reply);
                valid = length <= sizeof(output) &&
                    spatial__testing__v3__lifecycle_endpoint_reply_v3__pack(&lifecycle_reply, output) == length;
            } else valid = length <= sizeof(output) &&
                spatial__testing__v2__observer_endpoint_reply_v2__pack(&reply, output) == length;
            valid = valid && send_packet(output, length);
            if (valid) { accepted_control = envelope; envelope = NULL; }
        }
    }
    if (envelope) spatial__testing__v3__lifecycle_control_envelope_v3__free_unpacked(envelope, NULL);
    if (!valid) close_endpoint(reason);
}

Bool SpatialObserverEndpointInit(void)
{
    if (!configured) return !output_configured;
    if (endpoint < 0 || (output_configured && (output_fd < 0 || output_fd == endpoint)) || registered || !SpatialSecurityEnabled() || !SpatialObserverLifetimeSession())
        return FALSE;
    size_t offset = 0;
    while (offset < sizeof(session_id)) {
        ssize_t got = getrandom(session_id + offset, sizeof(session_id) - offset, 0);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) return FALSE;
        offset += (size_t)got;
    }
    uint64_t now = monotonic_ns();
    if (!now || now > UINT64_MAX - STARTUP_BUDGET_NS) return FALSE;
    startup_deadline = now + STARTUP_BUDGET_NS;
    if (!SetNotifyFd(endpoint, receive_packet, X_NOTIFY_READ, NULL)) return FALSE;
    registered = TRUE;
    deadline_timer = TimerSet(NULL, 0, 10000, expired, NULL);
    if (!deadline_timer) { close_endpoint(NULL); return FALSE; }
    Spatial__Testing__V2__ObserverEndpointHelloV2 hello = SPATIAL__TESTING__V2__OBSERVER_ENDPOINT_HELLO_V2__INIT;
    Spatial__Testing__V2__ObserverEndpointReplyV2 reply = SPATIAL__TESTING__V2__OBSERVER_ENDPOINT_REPLY_V2__INIT;
    hello.protocol_version = 2;
    hello.session_id.data = session_id; hello.session_id.len = sizeof(session_id);
    hello.native_session_epoch = SpatialObserverLifetimeSession();
    hello.server_pid = (uint32_t)getpid(); hello.server_uid = (uint32_t)getuid(); hello.server_gid = (uint32_t)getgid();
    reply.message_case = SPATIAL__TESTING__V2__OBSERVER_ENDPOINT_REPLY_V2__MESSAGE_HELLO;
    reply.hello = &hello;
    uint8_t bytes[MAX_PACKET];
    size_t size = spatial__testing__v2__observer_endpoint_reply_v2__get_packed_size(&reply);
    if (size > sizeof(bytes) || spatial__testing__v2__observer_endpoint_reply_v2__pack(&reply, bytes) != size ||
        !send_packet(bytes, size)) { close_endpoint(NULL); return FALSE; }
    return TRUE;
}
