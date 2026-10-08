/* SPDX-License-Identifier: MIT
 * Native actor devices. All delivery uses the normal DIX/XKB event path.
 */
#include <dix-config.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include "dix/devices_priv.h"
#include "dix/dix_priv.h"
#include "dix/input_priv.h"
#include "dix/inpututils_priv.h"
#include "dix/request_priv.h"
#include "dix/resource_priv.h"
#include "dix/window_priv.h"
#include "mi/mi_priv.h"
#include "inputstr.h"
#include "eventstr.h"
#include "scrnintstr.h"
#include "extnsionst.h"
#include "privates.h"
#include "mipointer.h"
#include "mipointrst.h"
#include "exevents.h"
#include "xichangehierarchy.h"
#include "xkb/xkbsrv_priv.h"
#include "spatialinput.h"
#include "spatialsecurity.h"
#include "input.pb-c.h"

typedef Spatial__Input__V1__Request Request;
typedef Spatial__Input__V1__Reply Reply;
typedef Spatial__Input__V1__Target Target;
#define CODE(name) SPATIAL__INPUT__V1__CODE__##name
#define OP(name) SPATIAL__INPUT__V1__REQUEST__OPERATION_##name
#define MAX_SEATS 32
#define MAX_PAYLOAD 4096

typedef struct ApplicationInput ApplicationInput;

typedef struct SeatRec {
    struct SeatRec *next;
    ClientPtr owner;
    uint64_t id;
    DeviceIntPtr pointer, keyboard, pointer_slave, keyboard_slave;
    XID target, focus;
    uint64_t target_generation, focus_generation;
    int x, y;
    uint32_t buttons;
    uint8_t keys[32];
    InternalEvent *events;
    ApplicationInput *application;
} SeatRec;

typedef struct {
    ResizeWindowProcPtr resize;
    RealizeWindowProcPtr realize;
    UnrealizeWindowProcPtr unrealize;
    DestroyWindowProcPtr destroy;
} ScreenState;

struct ApplicationInput {
    DeviceIntPtr pointer, keyboard;
    unsigned connections;
    SeatRec *controller;
};

static DevPrivateKeyRec device_key, window_key, screen_key, client_key;
static DevPrivateKeyRec application_key, application_device_key;
static SeatRec *seats;
static uint64_t next_seat, next_generation;
static Bool active;
static void cancel(SeatRec *seat);

static DeviceIntPtr application_pointer(ApplicationInput *application)
{
    return application->controller ? application->controller->pointer : application->pointer;
}

static int retarget_passive_grabs(WindowPtr window, void *data)
{
    ApplicationInput *application = data;
    DeviceIntPtr pointer = application_pointer(application);
    DeviceIntPtr keyboard = GetMaster(pointer, MASTER_KEYBOARD);
    for (GrabPtr grab = wPassiveGrabs(window); grab; grab = grab->next) {
        ClientPtr owner = dixLookupXIDOwner(grab->resource);
        if (grab->grabtype != CORE || !owner ||
            dixLookupPrivate(&owner->devPrivates, &application_key) != application) continue;
        grab->device = (grab->type == KeyPress || grab->type == KeyRelease) ? keyboard : pointer;
        grab->modifierDevice = keyboard;
    }
    return WT_WALKCHILDREN;
}

static void rebind_application(ApplicationInput *application)
{
    for (int i = 1; i < currentMaxClients; ++i)
        if (clients[i] && dixLookupPrivate(&clients[i]->devPrivates, &application_key) == application)
            clients[i]->clientPtr = application_pointer(application);
    /* Keep passive grabs alive, but never leave pointers to a retired seat. */
    for (int i = 0; i < screenInfo.numScreens; ++i)
        WalkTree(screenInfo.screens[i], retarget_passive_grabs, application);
}

static void clear_device_context(DeviceIntPtr device)
{
    if (device->valuator) {
        device->valuator->first_motion = device->valuator->last_motion = 0;
        device->valuator->motionHintWindow = NULL;
    }
    if (device->key) {
        XkbEventCauseRec cause = {0};
        XkbClearAllLatchesAndLocks(device, device->key->xkbInfo, TRUE, &cause);
    }
}

/* Application identity, not a credential or a single X connection, owns the
 * idle device pair. Devices contain genuine DIX focus, grabs and pointer state.
 */
Bool SpatialInputAuthorizeClient(ClientPtr client)
{
    if (!active) return FALSE;
    if (!SpatialSecuritySameApplication(client, client)) {
        client->clientPtr = inputInfo.pointer;
        return TRUE;
    }
    ApplicationInput *application = NULL;
    for (int i = 1; i < currentMaxClients; ++i) {
        if (!clients[i] || clients[i] == client ||
            !SpatialSecuritySameApplication(client, clients[i])) continue;
        application = dixLookupPrivate(&clients[i]->devPrivates, &application_key);
        if (application) break;
    }
    if (!application) {
        application = calloc(1, sizeof(*application));
        if (!application) return FALSE;
        if (AllocDevicePair(serverClient, "Spatial application", &application->pointer,
                            &application->keyboard, CorePointerProc, CoreKeyboardProc,
                            TRUE) != Success) {
            free(application);
            return FALSE;
        }
        dixSetPrivate(&application->pointer->devPrivates, &application_device_key, application);
        dixSetPrivate(&application->keyboard->devPrivates, &application_device_key, application);
        if (ActivateDevice(application->pointer, FALSE) != Success ||
            ActivateDevice(application->keyboard, FALSE) != Success ||
            !EnableDevice(application->pointer, FALSE) ||
            !EnableDevice(application->keyboard, FALSE) ||
            SetInputFocus(serverClient, application->keyboard, None, RevertToNone,
                          CurrentTime, FALSE) != Success) {
            RemoveDevice(application->keyboard, FALSE);
            RemoveDevice(application->pointer, FALSE);
            free(application);
            return FALSE;
        }
    }
    ++application->connections;
    dixSetPrivate(&client->devPrivates, &application_key, application);
    client->clientPtr = application_pointer(application);
    return TRUE;
}

static void close_application_connection(ClientPtr client)
{
    ApplicationInput *application = dixLookupPrivate(&client->devPrivates, &application_key);
    if (!application) return;
    dixSetPrivate(&client->devPrivates, &application_key, NULL);
    client->clientPtr = NULL;
    if (--application->connections) return;
    if (application->controller) cancel(application->controller);
    RemoveDevice(application->keyboard, FALSE);
    RemoveDevice(application->pointer, FALSE);
    free(application);
}

static uint64_t generation(WindowPtr win)
{
    uint64_t *value = dixLookupPrivate(&win->devPrivates, &window_key);
    if (!*value) *value = ++next_generation;
    return *value;
}

static WindowPtr live_window(XID xid, uint64_t expected)
{
    WindowPtr win;
    if (!xid || dixLookupWindow(&win, xid, serverClient, DixReadAccess) != Success ||
        !win->realized || !win->parent || win->drawable.pScreen != screenInfo.screens[0] ||
        generation(win) != expected)
        return NULL;
    return win;
}

static Bool controller(ClientPtr client)
{
    WindowPtr root = screenInfo.screens[0]->root;
    for (OtherClients *other = wOtherClients(root); other; other = other->next)
        if ((other->mask & SubstructureRedirectMask) && dixClientForOtherClients(other) == client)
            return TRUE;
    return FALSE;
}

/* Called before DIX clamps ordinary desktop motion. This preserves a selected
 * surface's local pixels even when it overlaps another native window or lies
 * outside the physical screen. Descendants use the normal window input shapes.
 */
WindowPtr SpatialInputRoute(DeviceIntPtr device, DeviceEvent *event)
{
    SeatRec *seat;
    SpritePtr sprite;
    WindowPtr win, parent;
    int depth = 0;
    if (!active || !(seat = dixLookupPrivate(&device->devPrivates, &device_key)))
        return NULL;
    sprite = device->spriteInfo->sprite;
    win = live_window(seat->target, seat->target_generation);
    if (!win) {
        sprite->spriteTraceGood = 1;
        return sprite->spriteTrace[0];
    }
    for (parent = win; parent; parent = parent->parent) ++depth;
    if (depth > sprite->spriteTraceSize) {
        WindowPtr *trace = reallocarray(sprite->spriteTrace, depth, sizeof(*trace));
        if (!trace) {
            sprite->spriteTraceGood = 1;
            return sprite->spriteTrace[0];
        }
        sprite->spriteTrace = trace;
        sprite->spriteTraceSize = depth;
    }
    sprite->spriteTraceGood = depth;
    for (parent = win; parent; parent = parent->parent)
        sprite->spriteTrace[--depth] = parent;
    sprite->hot.x = win->drawable.x + seat->x;
    sprite->hot.y = win->drawable.y + seat->y;
    sprite->hot.pScreen = win->drawable.pScreen;
    sprite->hotPhys = sprite->hot;
    if (event) {
        event->root_x = sprite->hot.x;
        event->root_y = sprite->hot.y;
        event->root_x_frac = event->root_y_frac = 0;
    }
    return miSpriteTrace(sprite, sprite->hot.x, sprite->hot.y);
}

static void send_input(SeatRec *seat, int type, unsigned detail)
{
    InternalEvent *events = seat->events;
    DeviceIntPtr device = (type == KeyPress || type == KeyRelease) ?
        seat->keyboard_slave : seat->pointer_slave;
    int count;
    if (type == KeyPress || type == KeyRelease)
        count = GetKeyboardEvents(events, device, type, detail);
    else if (type == MotionNotify) {
        ValuatorMask position;
        WindowPtr win = live_window(seat->target, seat->target_generation);
        if (!win) return;
        valuator_mask_zero(&position);
        valuator_mask_set_double(&position, 0, win->drawable.x + seat->x);
        valuator_mask_set_double(&position, 1, win->drawable.y + seat->y);
        count = GetPointerEvents(events, device, type, 0,
                                 POINTER_NORAW | POINTER_ABSOLUTE | POINTER_SCREEN, &position);
    } else
        count = GetPointerEvents(events, device, type, detail, POINTER_NORAW, NULL);
    for (int i = 0; i < count; ++i)
        mieqProcessDeviceEvent(device, &events[i], screenInfo.screens[0]);
}

static void cancel(SeatRec *seat)
{
    /* Release through the original device and target before clearing focus. */
    for (unsigned i = 1; i < 32; ++i)
        if (seat->buttons & (1u << i)) send_input(seat, ButtonRelease, i);
    for (unsigned i = 8; i < 256; ++i)
        if (seat->keys[i / 8] & (1u << (i % 8))) send_input(seat, KeyRelease, i);
    seat->buttons = 0;
    memset(seat->keys, 0, sizeof(seat->keys));
    if (seat->pointer->deviceGrab.grab)
        seat->pointer->deviceGrab.DeactivateGrab(seat->pointer);
    if (seat->keyboard->deviceGrab.grab)
        seat->keyboard->deviceGrab.DeactivateGrab(seat->keyboard);
    SetInputFocus(serverClient, seat->keyboard, None, RevertToNone, CurrentTime, FALSE);
    if (seat->application) {
        ApplicationInput *application = seat->application;
        DeviceIntPtr devices[] = {seat->pointer_slave, seat->keyboard_slave, seat->pointer, seat->keyboard};
        for (unsigned i = 0; i < 4; ++i) clear_device_context(devices[i]);
        for (unsigned i = 0; i < 4; ++i)
            dixSetPrivate(&devices[i]->devPrivates, &application_device_key, NULL);
        seat->application = NULL;
        application->controller = NULL;
        rebind_application(application);
    }
    seat->target = seat->focus = None;
    seat->target_generation = seat->focus_generation = 0;
}

static int bind_application(SeatRec *seat, WindowPtr window)
{
    if (!SpatialSecurityEnabled()) return CODE(OK);
    ClientPtr owner = dixLookupXIDOwner(window->drawable.id);
    ApplicationInput *application = owner ? dixLookupPrivate(&owner->devPrivates, &application_key) : NULL;
    if (!application) return CODE(FORBIDDEN);
    if (application->controller && application->controller != seat) return CODE(BUSY);
    if (seat->application == application) return CODE(OK);
    cancel(seat);
    if (application->pointer->deviceGrab.grab)
        application->pointer->deviceGrab.DeactivateGrab(application->pointer);
    if (application->keyboard->deviceGrab.grab)
        application->keyboard->deviceGrab.DeactivateGrab(application->keyboard);
    SetInputFocus(serverClient, application->keyboard, None, RevertToNone, CurrentTime, FALSE);
    clear_device_context(application->pointer);
    clear_device_context(application->keyboard);
    application->controller = seat;
    seat->application = application;
    DeviceIntPtr devices[] = {seat->pointer, seat->keyboard, seat->pointer_slave, seat->keyboard_slave};
    for (unsigned i = 0; i < 4; ++i) {
        clear_device_context(devices[i]);
        dixSetPrivate(&devices[i]->devPrivates, &application_device_key, application);
    }
    rebind_application(application);
    return CODE(OK);
}

static void invalidate(WindowPtr win)
{
    for (SeatRec *seat = seats; seat; seat = seat->next)
        if (seat->target == win->drawable.id || seat->focus == win->drawable.id)
            cancel(seat);
    uint64_t *value = dixLookupPrivate(&win->devPrivates, &window_key);
    *value = ++next_generation;
}

#define WINDOW_HOOK(name, field, saved) \
static Bool name(WindowPtr win) { \
    ScreenPtr screen = win->drawable.pScreen; \
    ScreenState *state = dixLookupPrivate(&screen->devPrivates, &screen_key); \
    invalidate(win); \
    screen->field = state->saved; \
    Bool result = screen->field(win); \
    state->saved = screen->field; \
    screen->field = name; \
    return result; \
}
WINDOW_HOOK(realize_window, RealizeWindow, realize)
WINDOW_HOOK(unrealize_window, UnrealizeWindow, unrealize)
WINDOW_HOOK(destroy_window, DestroyWindow, destroy)

static void resize_window(WindowPtr win, int x, int y, unsigned width, unsigned height, WindowPtr sibling)
{
    ScreenPtr screen = win->drawable.pScreen;
    ScreenState *state = dixLookupPrivate(&screen->devPrivates, &screen_key);
    invalidate(win);
    screen->ResizeWindow = state->resize;
    screen->ResizeWindow(win, x, y, width, height, sibling);
    state->resize = screen->ResizeWindow;
    screen->ResizeWindow = resize_window;
}

static void close_seat(SeatRec *seat)
{
    cancel(seat);
    for (int i = 0; i < currentMaxClients; ++i)
        if (clients[i] && clients[i]->clientPtr == seat->pointer) clients[i]->clientPtr = NULL;
    /* Hierarchy events inspect every pair. Publish only the complete removal,
     * never the interval where a remaining master refers to a freed partner.
     * Slaves depend on the master sprite; the keyboard precedes the pointer.
     */
    DeviceIntPtr devices[] = {seat->pointer_slave, seat->keyboard_slave, seat->keyboard, seat->pointer};
    int flags[MAXDEVICES] = {0};
    for (unsigned i = 0; i < 4; ++i) {
        flags[devices[i]->id] = XIDeviceDisabled | (i < 2 ? XISlaveRemoved | XISlaveDetached : XIMasterRemoved);
        DisableDevice(devices[i], FALSE);
    }
    for (unsigned i = 0; i < 4; ++i) RemoveDevice(devices[i], FALSE);
    XISendDeviceHierarchyEvent(flags);
    SeatRec **link = &seats;
    while (*link && *link != seat) link = &(*link)->next;
    if (*link) *link = seat->next;
    FreeEventList(seat->events, GetMaximumEventsNum());
    free(seat);
}

static SeatRec *open_seat(ClientPtr client)
{
    int count = 0;
    for (SeatRec *item = seats; item; item = item->next) ++count;
    if (count >= MAX_SEATS) return NULL;
    SeatRec *seat = calloc(1, sizeof(*seat));
    if (!seat) return NULL;
    seat->events = InitEventList(GetMaximumEventsNum());
    if (!seat->events) { free(seat); return NULL; }
    seat->owner = client;
    seat->id = ++next_seat;
    char name[64];
    snprintf(name, sizeof(name), "Spatial actor %llu", (unsigned long long)seat->id);
    if (AllocDevicePair(serverClient, name, &seat->pointer, &seat->keyboard,
                        CorePointerProc, CoreKeyboardProc, TRUE) != Success) {
        FreeEventList(seat->events, GetMaximumEventsNum()); free(seat); return NULL;
    }
    if (AllocXTestDevice(serverClient, name, &seat->pointer_slave, &seat->keyboard_slave,
                         seat->pointer, seat->keyboard) != Success) {
        RemoveDevice(seat->keyboard, FALSE); RemoveDevice(seat->pointer, FALSE);
        FreeEventList(seat->events, GetMaximumEventsNum()); free(seat); return NULL;
    }
    DeviceIntPtr devices[] = {seat->pointer, seat->keyboard, seat->pointer_slave, seat->keyboard_slave};
    /* ActivateDevice returns an X error code, despite its Bool declaration. */
    for (unsigned i = 0; i < 4; ++i)
        if (ActivateDevice(devices[i], FALSE) != Success) goto failed;
    for (unsigned i = 0; i < 4; ++i)
        if (!EnableDevice(devices[i], FALSE)) goto failed;
    AttachDevice(serverClient, seat->pointer_slave, seat->pointer);
    AttachDevice(serverClient, seat->keyboard_slave, seat->keyboard);
    for (unsigned i = 0; i < 4; ++i) dixSetPrivate(&devices[i]->devPrivates, &device_key, seat);
    seat->next = seats; seats = seat;
    int flags[MAXDEVICES] = {0};
    flags[seat->pointer->id] = flags[seat->keyboard->id] = XIMasterAdded | XIDeviceEnabled;
    flags[seat->pointer_slave->id] = flags[seat->keyboard_slave->id] = XISlaveAdded | XISlaveAttached | XIDeviceEnabled;
    XISendDeviceHierarchyEvent(flags);
    return seat;
failed:
    /* No successful hierarchy addition was published for this seat. */
    RemoveDevice(seat->pointer_slave, FALSE); RemoveDevice(seat->keyboard_slave, FALSE);
    RemoveDevice(seat->keyboard, FALSE); RemoveDevice(seat->pointer, FALSE);
    FreeEventList(seat->events, GetMaximumEventsNum()); free(seat); return NULL;
}

static void device_access(CallbackListPtr *list, void *unused, void *data)
{
    DeviceAccessCallbackParam *access = data;
    if (SpatialSecurityEnabled()) {
        if (!access->client || SpatialSecurityPrivileged(access->client)) return;
        ApplicationInput *subject = dixLookupPrivate(&access->client->devPrivates, &application_key);
        ApplicationInput *object = dixLookupPrivate(&access->dev->devPrivates, &application_device_key);
        if (!subject || subject != object) access->status = BadAccess;
        return;
    }
    SeatRec *seat = dixLookupPrivate(&access->dev->devPrivates, &device_key);
    (void)list; (void)unused;
    if (!seat || access->client == serverClient) return;
    const Mask allowed = DixReadAccess | DixGetAttrAccess | DixGetFocusAccess |
                         DixListAccess | DixListPropAccess | DixGetPropAccess |
                         DixUseAccess | DixReceiveAccess;
    if (access->access_mode & ~allowed) access->status = BadAccess;
}

static void client_state(CallbackListPtr *list, void *unused, void *data)
{
    NewClientInfoRec *info = data;
    (void)list; (void)unused;
    if (info->client->clientState != ClientStateGone && info->client->clientState != ClientStateRetained) return;
    close_application_connection(info->client);
    for (SeatRec *seat = seats, *next; seat; seat = next) {
        next = seat->next;
        if (seat->owner == info->client) close_seat(seat);
    }
}

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

static void operate(ClientPtr client, const Request *request, Reply *reply, Target *target, Target *focus)
{
    SeatRec *seat = NULL;
    uint64_t id = 0;
    if (!checked(&request->base) || request->protocol_version != 1 || !request->request_id) {
        reply->code = CODE(INVALID_ARGUMENT); return;
    }
    if (!controller(client)) { reply->code = CODE(FORBIDDEN); return; }
    uint64_t *last = dixLookupPrivate(&client->devPrivates, &client_key);
    if (request->request_id <= *last) { reply->code = CODE(INVALID_ARGUMENT); return; }
    *last = request->request_id;
    if (request->operation_case == OP(INSPECT)) {
        WindowPtr win;
        if (!request->inspect || dixLookupWindow(&win, request->inspect->xid, client, DixReadAccess) != Success ||
            !win->realized || !win->parent || win->drawable.pScreen != screenInfo.screens[0]) {
            reply->code = CODE(STALE_TARGET); return;
        }
        target->xid = win->drawable.id; target->generation = generation(win);
        reply->target = target; reply->width = win->drawable.width; reply->height = win->drawable.height;
        return;
    }
    switch (request->operation_case) {
    case OP(OPEN): seat = open_seat(client); break;
    case OP(BUTTON): id = request->button->seat; break;
    case OP(KEY): id = request->key->seat; break;
    case OP(MOTION): id = request->motion->seat; break;
    case OP(CANCEL): id = request->cancel->id; break;
    case OP(CLOSE): id = request->close->id; break;
    case OP(STATUS): id = request->status->id; break;
    default: reply->code = CODE(INVALID_ARGUMENT); return;
    }
    if (!seat) for (seat = seats; seat; seat = seat->next) if (seat->id == id && seat->owner == client) break;
    if (!seat) { reply->code = request->operation_case == OP(OPEN) ? CODE(RESOURCE_LIMIT) : CODE(NO_SEAT); return; }
    if (request->operation_case == OP(CLOSE)) { close_seat(seat); return; }
    if (request->operation_case == OP(CANCEL)) cancel(seat);
    if (request->operation_case == OP(BUTTON)) {
        const Spatial__Input__V1__Button *button = request->button;
        WindowPtr win = button->target ? live_window(button->target->xid, button->target->generation) : NULL;
        if (!win) { reply->code = CODE(STALE_TARGET); return; }
        if (!button->button || button->button >= 32 || button->button > seat->pointer_slave->button->numButtons || (!button->keep_capture_position && (button->x >= win->drawable.width || button->y >= win->drawable.height)) ||
            (button->keep_capture_position && !seat->buttons) ||
            !!(seat->buttons & (1u << button->button)) == !!button->pressed) {
            reply->code = CODE(INVALID_ARGUMENT); return;
        }
        if (seat->buttons && (seat->target != button->target->xid || seat->target_generation != button->target->generation)) {
            reply->code = CODE(BUSY); return;
        }
        if (button->pressed) {
            reply->code = bind_application(seat, win);
            if (reply->code != CODE(OK)) return;
        }
        seat->target = button->target->xid; seat->target_generation = button->target->generation;
        if (!button->keep_capture_position) { seat->x = button->x; seat->y = button->y; }
        if (button->focus && button->pressed) {
            if (SetInputFocus(serverClient, seat->keyboard, seat->target, RevertToNone, CurrentTime, FALSE) != Success) {
                reply->code = CODE(STALE_TARGET); return;
            }
            seat->focus = seat->target; seat->focus_generation = seat->target_generation;
        }
        send_input(seat, button->pressed ? ButtonPress : ButtonRelease, button->button);
        if (button->pressed) seat->buttons |= 1u << button->button;
        else seat->buttons &= ~(1u << button->button);
    }
    if (request->operation_case == OP(MOTION)) {
        const Spatial__Input__V1__Motion *motion = request->motion;
        WindowPtr win = motion->target ? live_window(motion->target->xid, motion->target->generation) : NULL;
        if (!win) { reply->code = CODE(STALE_TARGET); return; }
        if (!seat->buttons) { reply->code = CODE(INVALID_ARGUMENT); return; }
        if (seat->target != motion->target->xid || seat->target_generation != motion->target->generation) {
            reply->code = CODE(BUSY); return;
        }
        /* Core X events and sprite coordinates must not wrap signed 16-bit values. */
        if (motion->x < INT16_MIN || motion->x > INT16_MAX || motion->y < INT16_MIN || motion->y > INT16_MAX ||
            (int64_t)win->drawable.x + motion->x < INT16_MIN || (int64_t)win->drawable.x + motion->x > INT16_MAX ||
            (int64_t)win->drawable.y + motion->y < INT16_MIN || (int64_t)win->drawable.y + motion->y > INT16_MAX) {
            reply->code = CODE(INVALID_ARGUMENT); return;
        }
        seat->x = motion->x; seat->y = motion->y;
        send_input(seat, MotionNotify, 0);
    }
    if (request->operation_case == OP(KEY)) {
        const Spatial__Input__V1__Key *key = request->key;
        if (!live_window(seat->focus, seat->focus_generation)) { reply->code = CODE(NO_FOCUS); return; }
        if (key->keycode < 8 || key->keycode >= 256 ||
            !!(seat->keys[key->keycode / 8] & (1u << (key->keycode % 8))) == !!key->pressed) {
            reply->code = CODE(INVALID_ARGUMENT); return;
        }
        send_input(seat, key->pressed ? KeyPress : KeyRelease, key->keycode);
        if (key->pressed) seat->keys[key->keycode / 8] |= 1u << (key->keycode % 8);
        else seat->keys[key->keycode / 8] &= ~(1u << (key->keycode % 8));
    }
    reply->seat = seat->id;
    reply->pointer_device = seat->pointer->id; reply->keyboard_device = seat->keyboard->id;
    reply->held_buttons = seat->buttons;
    reply->held_keys.data = seat->keys; reply->held_keys.len = sizeof(seat->keys);
    if (seat->focus) { focus->xid = seat->focus; focus->generation = seat->focus_generation; reply->focus = focus; }
}

typedef struct { CARD8 reqType, operation; CARD16 length; CARD32 bytes; } SpatialRequest;
typedef struct { BYTE type, pad; CARD16 sequence; CARD32 length, bytes, unused[5]; } SpatialReply;

static int dispatch(ClientPtr client)
{
    X_REQUEST_HEAD_AT_LEAST(SpatialRequest);
    X_REQUEST_FIELD_CARD32(bytes);
    if (stuff->operation != 0 || stuff->bytes > MAX_PAYLOAD) return BadValue;
    REQUEST_FIXED_SIZE(SpatialRequest, stuff->bytes);
    Request *request = spatial__input__v1__request__unpack(NULL, stuff->bytes, (const uint8_t *)(stuff + 1));
    Reply reply = SPATIAL__INPUT__V1__REPLY__INIT;
    Target target = SPATIAL__INPUT__V1__TARGET__INIT, focus = SPATIAL__INPUT__V1__TARGET__INIT;
    reply.protocol_version = 1;
    if (request) { reply.request_id = request->request_id; operate(client, request, &reply, &target, &focus); }
    else reply.code = CODE(INVALID_ARGUMENT);
    uint8_t payload[MAX_PAYLOAD] = {0};
    size_t bytes = spatial__input__v1__reply__get_packed_size(&reply);
    if (bytes > sizeof(payload)) FatalError("Spatial input reply exceeds its bound\n");
    spatial__input__v1__reply__pack(&reply, payload);
    SpatialReply header = {.type = X_Reply, .sequence = client->sequence, .length = (bytes + 3) / 4, .bytes = bytes};
    if (client->swapped) { swaps(&header.sequence); swapl(&header.length); swapl(&header.bytes); }
    WriteToClient(client, sizeof(header), &header);
    WriteToClient(client, (bytes + 3) & ~3u, payload);
    if (request) spatial__input__v1__request__free_unpacked(request, NULL);
    return Success;
}

static void reset(ExtensionEntry *extension)
{
    (void)extension;
    active = FALSE;
    DeleteCallback(&ClientStateCallback, client_state, NULL);
    DeleteCallback(&DeviceAccessCallback, device_access, NULL);
    while (seats) { SeatRec *next = seats->next; FreeEventList(seats->events, GetMaximumEventsNum()); free(seats); seats = next; }
}

void SpatialInputExtensionInit(void)
{
    if (!dixRegisterPrivateKey(&device_key, PRIVATE_DEVICE, 0) ||
        !dixRegisterPrivateKey(&application_key, PRIVATE_CLIENT, 0) ||
        !dixRegisterPrivateKey(&application_device_key, PRIVATE_DEVICE, 0) ||
        !dixRegisterPrivateKey(&client_key, PRIVATE_CLIENT, sizeof(uint64_t)) ||
        !dixRegisterPrivateKey(&window_key, PRIVATE_WINDOW, sizeof(uint64_t)) ||
        !dixRegisterPrivateKey(&screen_key, PRIVATE_SCREEN, sizeof(ScreenState)) ||
        !AddCallback(&ClientStateCallback, client_state, NULL) ||
        !AddCallback(&DeviceAccessCallback, device_access, NULL) ||
        !AddExtension("SPATIAL-INPUT", 0, 0, dispatch, dispatch, reset, StandardMinorOpcode))
        FatalError("Cannot initialize spatial input\n");
    for (int i = 0; i < screenInfo.numScreens; ++i) {
        ScreenPtr screen = screenInfo.screens[i];
        ScreenState *state = dixLookupPrivate(&screen->devPrivates, &screen_key);
        state->resize = screen->ResizeWindow; screen->ResizeWindow = resize_window;
        state->realize = screen->RealizeWindow; screen->RealizeWindow = realize_window;
        state->unrealize = screen->UnrealizeWindow; screen->UnrealizeWindow = unrealize_window;
        state->destroy = screen->DestroyWindow; screen->DestroyWindow = destroy_window;
    }
    active = TRUE;
}
