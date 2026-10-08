/* SPDX-License-Identifier: MIT
 * Authentication binds immutable native connection identities before DIX use.
 * Resource authorization is a separate policy layer, not implied by a cookie.
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
#include <unistd.h>
#include "dix/dix_priv.h"
#include "dix/request_priv.h"
#include "extnsionst.h"
#include "privates.h"
#include "spatialsecurity.h"
#include "security.pb-c.h"

typedef Spatial__Security__V1__Request Request;
typedef Spatial__Security__V1__Reply Reply;
typedef Spatial__Security__V1__Identity Identity;
typedef Spatial__Security__V1__Credential Credential;
typedef Spatial__Security__V1__Role Role;
#define ROLE(name) SPATIAL__SECURITY__V1__ROLE__##name
#define CODE(name) SPATIAL__SECURITY__V1__CODE__##name
#define OP(name) SPATIAL__SECURITY__V1__REQUEST__OPERATION_##name
#define OUT(name) SPATIAL__SECURITY__V1__REPLY__OUTCOME_##name
#define KEY_BYTES 32
#define LABEL_BYTES 256
#define MAX_CREDENTIALS 256
#define MAX_PAYLOAD 4096

typedef struct {
    Role role;
    size_t workspace_size, application_size;
    uint8_t workspace[LABEL_BYTES], application[LABEL_BYTES], credential[KEY_BYTES];
} Label;

typedef struct {
    Bool active;
    Label label;
    uint8_t cookie[KEY_BYTES], parent[KEY_BYTES];
    uint64_t issuer;
} Grant;

typedef struct {
    Label label;
    uint64_t connection, sequence;
    Bool closed;
} SpatialConnection;

static DevPrivateKeyRec client_key;
static Grant grants[MAX_CREDENTIALS];
static uint8_t bootstrap[KEY_BYTES], server_id[KEY_BYTES];
static Bool configured, active, draining;
static uint64_t next_connection;

static Bool checked(const ProtobufCMessage *message)
{
    if (!message || message->n_unknown_fields || !protobuf_c_message_check(message)) return FALSE;
    for (unsigned i = 0; i < message->descriptor->n_fields; ++i) {
        const ProtobufCFieldDescriptor *field = &message->descriptor->fields[i];
        const uint8_t *bytes = (const uint8_t *)message;
        if (field->type != PROTOBUF_C_TYPE_MESSAGE) continue;
        if ((field->flags & PROTOBUF_C_FIELD_FLAG_ONEOF) &&
            *(const unsigned *)(bytes + field->quantifier_offset) != field->id) continue;
        const ProtobufCMessage *child = *(ProtobufCMessage *const *)(bytes + field->offset);
        if (child && !checked(child)) return FALSE;
    }
    return TRUE;
}

static Bool random_bytes(uint8_t *output, size_t size)
{
    while (size) {
        ssize_t count = getrandom(output, size, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return FALSE;
        output += count; size -= count;
    }
    return TRUE;
}

static Bool same_key(const uint8_t *a, const uint8_t *b)
{
    unsigned difference = 0;
    for (unsigned i = 0; i < KEY_BYTES; ++i) difference |= a[i] ^ b[i];
    return difference == 0;
}

void SpatialSecurityConfigure(const char *descriptor)
{
    char *end;
    struct stat info;
    uint8_t payload[MAX_PAYLOAD];
    long number;
    if (configured || !descriptor || !*descriptor)
        FatalError("Spatial security requires one bootstrap descriptor\n");
    errno = 0; number = strtol(descriptor, &end, 10);
    if (errno || *end || number < 3 || number > INT_MAX)
        FatalError("Invalid spatial security descriptor\n");
    int fd = (int)number;
    const int required = F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE;
    int seals = fcntl(fd, F_GET_SEALS);
    if (seals < 0 || (seals & required) != required || fstat(fd, &info) != 0 ||
        !S_ISREG(info.st_mode) || info.st_nlink != 0 || info.st_size <= 0 || info.st_size > MAX_PAYLOAD)
        FatalError("Spatial security bootstrap must be a sealed anonymous file\n");
    size_t offset = 0;
    while (offset < (size_t)info.st_size) {
        ssize_t count = pread(fd, payload + offset, (size_t)info.st_size - offset, offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) FatalError("Cannot read spatial security bootstrap\n");
        offset += count;
    }
    close(fd);
    Spatial__Security__V1__Bootstrap *config =
        spatial__security__v1__bootstrap__unpack(NULL, offset, payload);
    if (!config || !checked(&config->base) || config->protocol_version != 1 ||
        config->supervisor_cookie.len != KEY_BYTES)
        FatalError("Invalid spatial security bootstrap\n");
    memcpy(bootstrap, config->supervisor_cookie.data, KEY_BYTES);
    memset(config->supervisor_cookie.data, 0, KEY_BYTES);
    spatial__security__v1__bootstrap__free_unpacked(config, NULL);
    memset(payload, 0, sizeof(payload));
    configured = TRUE;
}

Bool SpatialSecurityEnabled(void) { return configured; }

static SpatialConnection *connection(ClientPtr client)
{
    return dixLookupPrivate(&client->devPrivates, &client_key);
}

Bool SpatialSecurityPrivileged(ClientPtr client)
{
    if (!configured || client == serverClient) return TRUE;
    if (!active || !client) return FALSE;
    SpatialConnection *state = connection(client);
    return state->connection && !state->closed &&
        (state->label.role == ROLE(SUPERVISOR) || state->label.role == ROLE(COMPOSITOR));
}

Bool SpatialSecuritySameApplication(ClientPtr client, ClientPtr owner)
{
    if (!active || !client || !owner || client == serverClient || owner == serverClient) return FALSE;
    SpatialConnection *subject = connection(client), *object = connection(owner);
    if (!subject->connection || subject->closed || !object->connection || object->closed ||
        subject->label.role != ROLE(APPLICATION) || object->label.role != ROLE(APPLICATION)) return FALSE;
    return subject->label.workspace_size == object->label.workspace_size &&
        subject->label.application_size == object->label.application_size &&
        !memcmp(subject->label.workspace, object->label.workspace, subject->label.workspace_size) &&
        !memcmp(subject->label.application, object->label.application, subject->label.application_size);
}

Bool SpatialSecurityVisibleResource(ClientPtr client, XID id)
{
    return SpatialSecurityPrivileged(client) || SpatialSecuritySameApplication(client, dixLookupXIDOwner(id));
}

static Grant *find_grant(const uint8_t *id)
{
    for (unsigned i = 0; i < MAX_CREDENTIALS; ++i)
        if (grants[i].active && same_key(grants[i].label.credential, id)) return &grants[i];
    return NULL;
}

const char *SpatialSecurityAuthorize(ClientPtr client, unsigned name_size,
                                     const char *name, unsigned cookie_size,
                                     const char *cookie)
{
    static const char method[] = "MIT-MAGIC-COOKIE-1";
    static const char denial[] = "Spatial connection authorization failed";
    if (!active || name_size != sizeof(method) - 1 || memcmp(name, method, name_size) ||
        cookie_size != KEY_BYTES || next_connection == UINT64_MAX) return denial;
    SpatialConnection *state = connection(client);
    if (state->connection) return denial;
    Grant *match = NULL;
    /* Scan the entire bounded table; never compare secret bytes with strcmp. */
    for (unsigned i = 0; i < MAX_CREDENTIALS; ++i)
        if (same_key(grants[i].cookie, (const uint8_t *)cookie) && grants[i].active) match = &grants[i];
    if (!match) return denial;
    state->label = match->label;
    state->connection = ++next_connection;
    return NULL;
}

static void revoke_dependents(void)
{
    Bool changed;
    do {
        changed = FALSE;
        for (unsigned i = 1; i < MAX_CREDENTIALS; ++i) {
            Grant *grant = &grants[i];
            if (grant->active && !find_grant(grant->parent)) {
                grant->active = FALSE; changed = TRUE;
            }
        }
    } while (changed);
}

static void close_revoked(void)
{
    revoke_dependents();
    if (draining) return;
    draining = TRUE;
    /* CloseDownClient invokes client_state. It may revoke more descendants. */
    for (int i = 1; i < currentMaxClients; ++i) {
        ClientPtr client = clients[i];
        if (!client) continue;
        SpatialConnection *state = connection(client);
        if (state->connection && !state->closed && !find_grant(state->label.credential)) {
            client->closeDownMode = DestroyAll;
            CloseDownClient(client);
            i = 0;
        }
    }
    for (unsigned i = 1; i < MAX_CREDENTIALS; ++i)
        if (!grants[i].active) memset(&grants[i], 0, sizeof(grants[i]));
    draining = FALSE;
}

static void client_state(CallbackListPtr *list, void *unused, void *data)
{
    NewClientInfoRec *info = data;
    (void)list; (void)unused;
    if (!active || (info->client->clientState != ClientStateGone &&
                    info->client->clientState != ClientStateRetained)) return;
    SpatialConnection *state = connection(info->client);
    if (!state->connection || state->closed) return;
    state->closed = TRUE;
    for (unsigned i = 1; i < MAX_CREDENTIALS; ++i)
        if (grants[i].active && grants[i].issuer == state->connection) grants[i].active = FALSE;
    close_revoked();
}

static Bool valid_label(ProtobufCBinaryData value)
{
    return value.len > 0 && value.len <= LABEL_BYTES && !memchr(value.data, 0, value.len);
}

static void issue(SpatialConnection *state, const Spatial__Security__V1__Issue *request,
                  Reply *reply, Credential *result)
{
    Role role = request->role;
    if ((state->label.role != ROLE(SUPERVISOR) && state->label.role != ROLE(BROKER)) ||
        (state->label.role == ROLE(BROKER) && role != ROLE(APPLICATION)) || role == ROLE(SUPERVISOR)) {
        reply->code = CODE(FORBIDDEN); return;
    }
    if ((role != ROLE(APPLICATION) && role != ROLE(BROKER) && role != ROLE(COMPOSITOR)) ||
        (role == ROLE(APPLICATION) && (!valid_label(request->workspace) || !valid_label(request->application))) ||
        (role != ROLE(APPLICATION) && (request->workspace.len || request->application.len))) {
        reply->code = CODE(INVALID_ARGUMENT); return;
    }
    Grant *slot = NULL;
    for (unsigned i = 1; i < MAX_CREDENTIALS; ++i)
        if (!grants[i].active) { slot = &grants[i]; break; }
    if (!slot) { reply->code = CODE(RESOURCE_EXHAUSTED); return; }
    Grant value = {0};
    if (!random_bytes(value.cookie, KEY_BYTES) || !random_bytes(value.label.credential, KEY_BYTES)) {
        reply->code = CODE(UNAVAILABLE); return;
    }
    for (unsigned i = 0; i < MAX_CREDENTIALS; ++i)
        if (grants[i].active && (same_key(value.cookie, grants[i].cookie) ||
            same_key(value.label.credential, grants[i].label.credential))) {
            reply->code = CODE(UNAVAILABLE); return;
        }
    value.active = TRUE; value.label.role = role; value.issuer = state->connection;
    memcpy(value.parent, state->label.credential, KEY_BYTES);
    value.label.workspace_size = request->workspace.len;
    value.label.application_size = request->application.len;
    if (request->workspace.len) memcpy(value.label.workspace, request->workspace.data, request->workspace.len);
    if (request->application.len) memcpy(value.label.application, request->application.data, request->application.len);
    *slot = value;
    result->id = (ProtobufCBinaryData){KEY_BYTES, slot->label.credential};
    result->cookie = (ProtobufCBinaryData){KEY_BYTES, slot->cookie};
    result->role = role;
    result->workspace = (ProtobufCBinaryData){slot->label.workspace_size, slot->label.workspace};
    result->application = (ProtobufCBinaryData){slot->label.application_size, slot->label.application};
    reply->outcome_case = OUT(CREDENTIAL); reply->credential = result;
}

static void operate(ClientPtr client, const Request *request, Reply *reply,
                    Identity *identity, Credential *credential)
{
    if (!checked(&request->base) || !request->request_id || request->operation_case == 0) {
        reply->code = CODE(INVALID_ARGUMENT); return;
    }
    if (request->protocol_version != 1) { reply->code = CODE(UNSUPPORTED_VERSION); return; }
    if (!active) { reply->code = CODE(UNAVAILABLE); return; }
    SpatialConnection *state = connection(client);
    if (!state->connection || state->closed || !find_grant(state->label.credential)) {
        reply->code = CODE(FORBIDDEN); return;
    }
    if (request->request_id <= state->sequence) { reply->code = CODE(STALE_SEQUENCE); return; }
    state->sequence = request->request_id;
    switch (request->operation_case) {
    case OP(INSPECT):
        identity->role = state->label.role;
        identity->workspace = (ProtobufCBinaryData){state->label.workspace_size, state->label.workspace};
        identity->application = (ProtobufCBinaryData){state->label.application_size, state->label.application};
        identity->credential_id = (ProtobufCBinaryData){KEY_BYTES, state->label.credential};
        identity->connection_id = state->connection;
        identity->server_id = (ProtobufCBinaryData){KEY_BYTES, server_id};
        reply->outcome_case = OUT(IDENTITY); reply->identity = identity;
        break;
    case OP(ISSUE): issue(state, request->issue, reply, credential); break;
    case OP(REVOKE): {
        if (state->label.role != ROLE(SUPERVISOR) && state->label.role != ROLE(BROKER)) {
            reply->code = CODE(FORBIDDEN); break;
        }
        if (request->revoke->credential_id.len != KEY_BYTES) {
            reply->code = CODE(INVALID_ARGUMENT); break;
        }
        Grant *grant = find_grant(request->revoke->credential_id.data);
        if (!grant) { reply->code = CODE(NOT_FOUND); break; }
        if (grant == &grants[0] || (state->label.role != ROLE(SUPERVISOR) &&
                                  grant->issuer != state->connection)) {
            reply->code = CODE(FORBIDDEN); break;
        }
        grant->active = FALSE; close_revoked();
        reply->outcome_case = OUT(REVOKED); reply->revoked = TRUE;
        break;
    }
    default: reply->code = CODE(INVALID_ARGUMENT); break;
    }
}

typedef struct { CARD8 reqType, operation; CARD16 length; CARD32 bytes; } SpatialRequest;
typedef struct { BYTE type, pad; CARD16 sequence; CARD32 length, bytes, unused[5]; } SpatialReply;

static int dispatch(ClientPtr client)
{
    X_REQUEST_HEAD_AT_LEAST(SpatialRequest);
    X_REQUEST_FIELD_CARD32(bytes);
    if (stuff->operation != 0 || stuff->bytes > MAX_PAYLOAD) return BadValue;
    REQUEST_FIXED_SIZE(SpatialRequest, stuff->bytes);
    Request *request = spatial__security__v1__request__unpack(NULL, stuff->bytes, (const uint8_t *)(stuff + 1));
    Reply reply = SPATIAL__SECURITY__V1__REPLY__INIT;
    Identity identity = SPATIAL__SECURITY__V1__IDENTITY__INIT;
    Credential credential = SPATIAL__SECURITY__V1__CREDENTIAL__INIT;
    reply.protocol_version = 1;
    if (request) { reply.request_id = request->request_id; operate(client, request, &reply, &identity, &credential); }
    else reply.code = CODE(INVALID_ARGUMENT);
    uint8_t payload[MAX_PAYLOAD] = {0};
    size_t bytes = spatial__security__v1__reply__get_packed_size(&reply);
    if (bytes > sizeof(payload)) FatalError("Spatial security reply exceeds its bound\n");
    spatial__security__v1__reply__pack(&reply, payload);
    SpatialReply header = {.type = X_Reply, .sequence = client->sequence, .length = (bytes + 3) / 4, .bytes = bytes};
    if (client->swapped) { swaps(&header.sequence); swapl(&header.length); swapl(&header.bytes); }
    WriteToClient(client, sizeof(header), &header);
    WriteToClient(client, (bytes + 3) & ~3u, payload);
    memset(payload, 0, sizeof(payload));
    if (request) spatial__security__v1__request__free_unpacked(request, NULL);
    return Success;
}

static void reset(ExtensionEntry *extension)
{
    (void)extension;
    active = FALSE;
    SpatialSecurityPolicyReset();
    DeleteCallback(&ClientStateCallback, client_state, NULL);
    memset(grants, 0, sizeof(grants));
    memset(server_id, 0, sizeof(server_id));
}

void SpatialSecurityExtensionInit(void)
{
    if (!configured) return;
    memset(grants, 0, sizeof(grants));
    next_connection = 0; draining = FALSE;
    if (!random_bytes(server_id, KEY_BYTES) || !random_bytes(grants[0].label.credential, KEY_BYTES) ||
        !dixRegisterPrivateKey(&client_key, PRIVATE_CLIENT, sizeof(SpatialConnection)) ||
        !AddCallback(&ClientStateCallback, client_state, NULL) ||
        !AddExtension("SPATIAL-SECURITY", 0, 0, dispatch, dispatch, reset, StandardMinorOpcode))
        FatalError("Cannot initialize spatial security\n");
    memcpy(grants[0].cookie, bootstrap, KEY_BYTES);
    grants[0].label.role = ROLE(SUPERVISOR); grants[0].active = TRUE;
    active = TRUE;
    SpatialSecurityPolicyInit();
}
