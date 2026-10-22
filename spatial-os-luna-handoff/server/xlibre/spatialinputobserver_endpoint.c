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
static int endpoint = -1;
static Bool configured, registered, setup_seen, timer_running;
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
    if (endpoint >= 0) {
        if (registered) RemoveNotifyFd(endpoint);
        registered = FALSE;
        close(endpoint);
        endpoint = -1;
        if (reason) ErrorF("Spatial observer endpoint rejected: %s\n", reason);
    }
    if (deadline_timer && !timer_running) TimerCancel(deadline_timer);
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
    if (setup_seen) { close_endpoint("one-shot setup replay"); return; }
    setup_seen = TRUE; /* Consume before parsing or live pin resolution. */
    Spatial__Testing__V2__ObserverControlEnvelopeV2 *envelope =
        spatial__testing__v2__observer_control_envelope_v2__unpack(NULL, (size_t)size, bytes);
    unsigned nodes = 0;
    Bool valid = envelope && checked(&envelope->base, 0, &nodes) &&
        spatial__testing__v2__observer_control_envelope_v2__get_packed_size(envelope) == (size_t)size;
    if (valid) valid = spatial__testing__v2__observer_control_envelope_v2__pack(envelope, canonical) == (size_t)size &&
                      !memcmp(bytes, canonical, (size_t)size);
    if (valid) valid = envelope->message_case ==
        SPATIAL__TESTING__V2__OBSERVER_CONTROL_ENVELOPE_V2__MESSAGE_SETUP_REQUEST;
    if (valid) {
        const Spatial__Testing__V2__ObserverSetupRequestV2 *request = envelope->setup_request;
        valid = request && request->session_id.len == sizeof(session_id) &&
            !memcmp(request->session_id.data, session_id, sizeof(session_id)) &&
            request->arm_id.len == 16 && request->deadline_monotonic_ns > now &&
            request->deadline_monotonic_ns - now <= STARTUP_BUDGET_NS;
        size_t length = 0;
        if (valid) valid = SpatialInputObserverSetup(request, output, sizeof(output), &length) &&
                           send_packet(output, length);
    }
    if (envelope) spatial__testing__v2__observer_control_envelope_v2__free_unpacked(envelope, NULL);
    if (!valid) close_endpoint("invalid setup or native pin");
}

Bool SpatialObserverEndpointInit(void)
{
    if (!configured) return TRUE;
    if (endpoint < 0 || registered || !SpatialSecurityEnabled() || !SpatialObserverLifetimeSession())
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
