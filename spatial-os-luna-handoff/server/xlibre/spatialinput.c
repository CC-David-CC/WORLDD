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
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
#include "spatialinputobserver_lifetime.h"
#include "spatialinputobserver_capture.h"
#include "spatialinputobserver_endpoint.h"
#include "spatialinputobserver_serialize_v2.h"
#include "spatialinputobserver_transition_v2.h"
#endif

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
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
static void observer_transition(SeatRec *seat, unsigned phase);
#endif

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
    /* Each supervised world owns a revocable compositor input connection.
     * Its seats remain connection-owned; it need not own persistent graphics. */
    if (SpatialSecurityInputController(client)) return TRUE;
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
    /* Key events share the pointer sprite but do not send pointer crossings.
     * Changing its window here would leave DIX's PointerWindows entry stale.
     */
    if (IsKeyboardDevice(device)) return sprite->win;
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
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
    observer_transition(seat, 1);
#endif
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
    /* The attached keyboard owns focus state independently of its master. */
    SetInputFocus(serverClient, seat->keyboard_slave, None, RevertToNone, CurrentTime, FALSE);
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
    observer_transition(seat, 2);
#endif
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
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
    observer_transition(seat, 3);
#endif
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
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
    observer_transition(seat, 4);
#endif
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

static void operate(ClientPtr client, const Request *request, Reply *reply, Target *target, Target *focus,
                    Spatial__Security__V1__ApplicationIdentity *identity)
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
        reply->security_mode = SpatialSecurityEnabled() ? SPATIAL__INPUT__V1__SECURITY_MODE__ENFORCED :
                                                         SPATIAL__INPUT__V1__SECURITY_MODE__DEVELOPMENT;
        if (SpatialSecurityApplicationIdentity(client, win->drawable.id, identity)) reply->application_identity = identity;
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
    Spatial__Security__V1__ApplicationIdentity identity = SPATIAL__SECURITY__V1__APPLICATION_IDENTITY__INIT;
    reply.protocol_version = 1;
    if (request) { reply.request_id = request->request_id; operate(client, request, &reply, &target, &focus, &identity); }
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


#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
typedef Spatial__Testing__V2__ApplicationIdentity ObserverApplication;
typedef Spatial__Testing__V2__ClientIdentity ObserverClient;
typedef Spatial__Testing__V2__DeviceIdentity ObserverDevice;

static void observer_application(ObserverApplication *wire, const SpatialSelectionNamespace *label)
{
    spatial__testing__v2__application_identity__init(wire);
    wire->workspace_id.data = (uint8_t *)label->workspace;
    wire->workspace_id.len = label->workspace_size;
    wire->application_id.data = (uint8_t *)label->application;
    wire->application_id.len = label->application_size;
}

static Bool observer_client(ObserverClient *wire, ObserverApplication *label,
                            const SpatialObserverNativeClientPin *pin)
{
    spatial__testing__v2__client_identity__init(wire);
    if (!SpatialObserverMapNativeRoleV2((int)pin->role, &wire->role)) return FALSE;
    wire->client_id = (uint32_t)pin->index;
    wire->native_object_epoch = pin->epoch;
    if (pin->role == SPATIAL_OBSERVER_NATIVE_CLIENT_APPLICATION) {
        observer_application(label, &pin->label);
        wire->application = label;
    }
    return TRUE;
}

static Bool observer_devices(SeatRec *seat, ObserverDevice wire[4], ObserverDevice *pointers[4])
{
    int ids[4] = {seat->pointer->id, seat->pointer_slave->id,
                  seat->keyboard->id, seat->keyboard_slave->id};
    SpatialObserverDeviceSetPin pin;
    SpatialObserverNativeState state;
    if (!SpatialObserverPinDevices(ids, &pin) || !SpatialObserverSnapshotDevices(&pin, &state))
        return FALSE;
    for (unsigned i = 0; i < 4; ++i) {
        spatial__testing__v2__device_identity__init(&wire[i]);
        wire[i].device_id = (uint32_t)pin.devices[i].id;
        wire[i].native_object_epoch = pin.devices[i].epoch;
        wire[i].kind = i < 2 ? SPATIAL__TESTING__V2__DEVICE_IDENTITY__KIND__POINTER :
                              SPATIAL__TESTING__V2__DEVICE_IDENTITY__KIND__KEYBOARD;
        wire[i].role = (i == 0 || i == 2) ? SPATIAL__TESTING__V2__DEVICE_IDENTITY__ROLE__MASTER :
                                          SPATIAL__TESTING__V2__DEVICE_IDENTITY__ROLE__SLAVE;
        wire[i].paired_or_attached = (uint32_t)state.devices[i].paired_or_attached;
        pointers[i] = &wire[i];
    }
    return TRUE;
}

static Bool observer_seat(SeatRec *seat, ApplicationInput *application, SpatialObserverSeatStateV2 *state)
{
    if (!seat || !state || (seat->application && seat->application != application)) return FALSE;
    memset(state, 0, sizeof(*state));
    const int ids[4] = {seat->pointer->id, seat->pointer_slave->id, seat->keyboard->id, seat->keyboard_slave->id};
    if (!SpatialObserverPinNativeClient(seat->owner->index, &state->owner) ||
        !SpatialObserverPinDevices(ids, &state->devices)) return FALSE;
    state->id = seat->id; state->target = seat->target; state->focus = seat->focus;
    state->target_generation = seat->target_generation; state->focus_generation = seat->focus_generation;
    memcpy(state->keys, seat->keys, sizeof(state->keys)); state->buttons = seat->buttons;
    state->application = seat->application != NULL;
    state->controller = seat->application && seat->application->controller ? seat->application->controller->id : 0;
    return TRUE;
}

static void observer_transition(SeatRec *seat, unsigned phase)
{
    const Spatial__Testing__V2__ObserverArmV2 *arm = SpatialObserverEndpointArmV2();
    if (!arm || !seat || (phase < 4 ? seat->id != arm->old_native_seat_id : seat->id != arm->peer_native_seat_id)) return;
    SeatRec *old = NULL, *peer = NULL;
    for (SeatRec *item = seats; item; item = item->next) {
        if (item->id == arm->old_native_seat_id) old = item;
        if (item->id == arm->peer_native_seat_id) peer = item;
    }
    WindowPtr window = live_window(arm->expected_window->xid, arm->expected_window->spatial_generation);
    ClientPtr owner = window ? dixLookupXIDOwner(window->drawable.id) : NULL;
    ApplicationInput *application = owner ? dixLookupPrivate(&owner->devPrivates, &application_key) : NULL;
    SpatialObserverSeatStateV2 old_state, peer_state; SpatialObserverWindowPin target;
    if (!application || !observer_seat(old, application, &old_state) ||
        !observer_seat(peer, application, &peer_state) || !SpatialObserverPinWindow(window->drawable.id, &target)) {
        SpatialObserverEndpointFailV2("native transition live identity missing"); return;
    }
    SpatialObserverTransitionCaptureV2(phase, &old_state, &peer_state, &target, generation(window),
        application->controller ? application->controller->id : 0);
}

Bool SpatialInputObserverArm(const Spatial__Testing__V2__ObserverArmV2 *arm)
{
    SeatRec *old = NULL, *peer = NULL;
    uint8_t keys[32] = {0};
    uint32_t buttons = 0;
    if (!arm || !arm->expected_window || arm->n_expected_held_keys > 31 ||
        arm->n_expected_held_buttons > 29) return FALSE;
    for (SeatRec *seat = seats; seat; seat = seat->next) {
        if (seat->id == arm->old_native_seat_id) old = seat;
        if (seat->id == arm->peer_native_seat_id) peer = seat;
    }
    if (!old || !peer || old == peer || old->owner != peer->owner ||
        !old->application || old->application->controller != old ||
        old->target != arm->expected_window->xid || old->focus != old->target ||
        old->target_generation != arm->expected_window->spatial_generation ||
        old->focus_generation != old->target_generation ||
        !live_window(old->target, old->target_generation) ||
        peer->application || peer->target || peer->focus || peer->buttons) return FALSE;
    WindowPtr window = live_window(old->target, old->target_generation);
    ClientPtr owner = dixLookupXIDOwner(window->drawable.id);
    if (!owner || dixLookupPrivate(&owner->devPrivates, &application_key) != old->application)
        return FALSE;
    for (size_t i = 0; i < arm->n_expected_held_keys; ++i) {
        unsigned code = arm->expected_held_keys[i];
        if (code < 8 || code >= 256) return FALSE;
        keys[code / 8] |= 1u << (code % 8);
    }
    for (size_t i = 0; i < arm->n_expected_held_buttons; ++i) {
        unsigned button = arm->expected_held_buttons[i];
        if (!button || button >= 32) return FALSE;
        buttons |= 1u << button;
    }
    for (unsigned i = 0; i < sizeof(peer->keys); ++i)
        if (peer->keys[i]) return FALSE;
    return old->buttons == buttons && !memcmp(old->keys, keys, sizeof(keys));
}

Bool SpatialInputObserverSetup(const Spatial__Testing__V2__ObserverSetupRequestV2 *request,
                              uint8_t *output, size_t capacity, size_t *length)
{
    SpatialObserverNativeClientPin seat_owner, app_owner;
    SpatialObserverWindowPin target_pin;
    SpatialObserverMarkerPin marker_pin;
    SeatRec *old = NULL, *peer = NULL;
    if (length) *length = 0;
    if (!request || !output || !length || !active || !SpatialSecurityEnabled() ||
        !request->old_native_seat_id || !request->peer_native_seat_id ||
        request->old_native_seat_id == request->peer_native_seat_id ||
        !request->presentation_generation || !request->expected_application ||
        !request->target_xid || !request->probe_marker_xid ||
        request->target_xid == request->probe_marker_xid ||
        (request->expected_probe_role != SPATIAL__TESTING__V2__CLIENT_IDENTITY__ROLE__SUPERVISOR &&
         request->expected_probe_role != SPATIAL__TESTING__V2__CLIENT_IDENTITY__ROLE__COMPOSITOR)) return FALSE;
    for (SeatRec *seat = seats; seat; seat = seat->next) {
        if (seat->id == request->old_native_seat_id) old = seat;
        if (seat->id == request->peer_native_seat_id) peer = seat;
    }
    WindowPtr window = live_window(request->target_xid, request->target_spatial_generation);
    if (!old || !peer || old->owner != peer->owner || !window ||
        !SpatialObserverPinNativeClient(old->owner->index, &seat_owner) ||
        seat_owner.role != SPATIAL_OBSERVER_NATIVE_CLIENT_COMPOSITOR ||
        !SpatialObserverPinWindow(request->target_xid, &target_pin) ||
        !SpatialObserverPinNativeClient(target_pin.owner.index, &app_owner) ||
        !SpatialObserverPinMarker(request->probe_marker_xid, &marker_pin) ||
        seat_owner.index == app_owner.index || seat_owner.index == marker_pin.owner.index ||
        app_owner.index == marker_pin.owner.index) return FALSE;
    const SpatialSelectionNamespace *actual = &target_pin.owner.label;
    const ObserverApplication *expected = request->expected_application;
    if (expected->workspace_id.len != actual->workspace_size ||
        expected->application_id.len != actual->application_size ||
        memcmp(expected->workspace_id.data, actual->workspace, actual->workspace_size) ||
        memcmp(expected->application_id.data, actual->application, actual->application_size)) return FALSE;

    ObserverApplication application = SPATIAL__TESTING__V2__APPLICATION_IDENTITY__INIT;
    ObserverApplication seat_label, probe_label, app_label;
    ObserverClient seat_client, probe_client, app_client;
    if (!observer_client(&seat_client, &seat_label, &seat_owner) ||
        !observer_client(&probe_client, &probe_label, &marker_pin.owner) ||
        !observer_client(&app_client, &app_label, &app_owner) ||
        probe_client.role != request->expected_probe_role) return FALSE;
    observer_application(&application, actual);
    ObserverDevice old_devices[4], peer_devices[4], *old_ptrs[4], *peer_ptrs[4];
    if (!observer_devices(old, old_devices, old_ptrs) || !observer_devices(peer, peer_devices, peer_ptrs))
        return FALSE;
    for (unsigned i = 0; i < 4; ++i)
        for (unsigned j = 0; j < 4; ++j)
            if (old_devices[i].device_id == peer_devices[j].device_id) return FALSE;

    Spatial__Testing__V2__WindowIdentity target = SPATIAL__TESTING__V2__WINDOW_IDENTITY__INIT;
    Spatial__Testing__V2__ProbeMarkerPinV2 marker = SPATIAL__TESTING__V2__PROBE_MARKER_PIN_V2__INIT;
    Spatial__Testing__V2__ObserverSetupReplyV2 setup = SPATIAL__TESTING__V2__OBSERVER_SETUP_REPLY_V2__INIT;
    Spatial__Testing__V2__ObserverEndpointReplyV2 reply = SPATIAL__TESTING__V2__OBSERVER_ENDPOINT_REPLY_V2__INIT;
    target.xid = request->target_xid;
    target.native_object_epoch = target_pin.epoch;
    target.spatial_generation = generation(window);
    target.application = &application;
    target.owner = &app_client;
    marker.xid = marker_pin.xid;
    marker.window_epoch = marker_pin.epoch;
    marker.privileged_owner = &probe_client;
    setup.session_id = request->session_id;
    setup.arm_id = request->arm_id;
    setup.native_session_epoch = SpatialObserverLifetimeSession();
    setup.old_native_seat_id = old->id;
    setup.peer_native_seat_id = peer->id;
    setup.n_old_actor_devices = setup.n_peer_actor_devices = 4;
    setup.old_actor_devices = old_ptrs;
    setup.peer_actor_devices = peer_ptrs;
    setup.compositor_seat_owner = &seat_client;
    setup.app_target = &target;
    setup.app_target_owner = &app_client;
    setup.probe_marker = &marker;
    setup.accepted_request = (Spatial__Testing__V2__ObserverSetupRequestV2 *)request;
    reply.message_case = SPATIAL__TESTING__V2__OBSERVER_ENDPOINT_REPLY_V2__MESSAGE_SETUP_REPLY;
    reply.setup_reply = &setup;
    size_t bytes = spatial__testing__v2__observer_endpoint_reply_v2__get_packed_size(&reply);
    if (!bytes || bytes > capacity ||
        spatial__testing__v2__observer_endpoint_reply_v2__pack(&reply, output) != bytes) return FALSE;
    *length = bytes;
    return TRUE;
}
Bool SpatialInputObserverLifecycleSetup(const Spatial__Testing__V3__LifecycleSetupRequestV3 *request,
                                       uint8_t *output, size_t capacity, size_t *length)
{
    if (length) *length = 0;
    if (!request || !request->common || !output || !length ||
        request->cause < SPATIAL__TESTING__V3__LIFECYCLE_CAUSE_V3__TARGET_DESTRUCTION ||
        request->cause > SPATIAL__TESTING__V3__LIFECYCLE_CAUSE_V3__ACTOR_REVOCATION ||
        (request->cause == SPATIAL__TESTING__V3__LIFECYCLE_CAUSE_V3__TARGET_DESTRUCTION &&
         request->peer_target_xid == request->common->target_xid)) return FALSE;
    uint8_t common_bytes[MAX_PAYLOAD]; size_t common_length = 0;
    if (!SpatialInputObserverSetup(request->common, common_bytes, sizeof(common_bytes), &common_length))
        return FALSE;
    Spatial__Testing__V2__ObserverEndpointReplyV2 *common =
        spatial__testing__v2__observer_endpoint_reply_v2__unpack(NULL, common_length, common_bytes);
    if (!common) return FALSE;
    Bool valid = FALSE;
    WindowPtr window = live_window(request->peer_target_xid, request->peer_target_spatial_generation);
    SpatialObserverWindowPin pin;
    SpatialObserverNativeClientPin owner;
    ObserverClient client; ObserverApplication label;
    if (!common->setup_reply || !window ||
        !SpatialObserverPinWindow(request->peer_target_xid, &pin) ||
        !SpatialObserverPinNativeClient(pin.owner.index, &owner) ||
        !SpatialSecuritySameApplication(clients[common->setup_reply->app_target_owner->client_id],
                                        clients[pin.owner.index]) ||
        !observer_client(&client, &label, &owner)) goto done;
    Spatial__Testing__V2__WindowIdentity peer = SPATIAL__TESTING__V2__WINDOW_IDENTITY__INIT;
    peer.xid = pin.xid; peer.native_object_epoch = pin.epoch;
    peer.spatial_generation = generation(window); peer.owner = &client; peer.application = &label;
    Spatial__Testing__V3__LifecycleSetupReplyV3 setup = SPATIAL__TESTING__V3__LIFECYCLE_SETUP_REPLY_V3__INIT;
    setup.old_pins = common->setup_reply; setup.peer_window = &peer;
    setup.accepted_request = (Spatial__Testing__V3__LifecycleSetupRequestV3 *)request;
    Spatial__Testing__V3__LifecycleEndpointReplyV3 reply = SPATIAL__TESTING__V3__LIFECYCLE_ENDPOINT_REPLY_V3__INIT;
    reply.message_case = SPATIAL__TESTING__V3__LIFECYCLE_ENDPOINT_REPLY_V3__MESSAGE_LIFECYCLE_SETUP_REPLY;
    reply.lifecycle_setup_reply = &setup;
    size_t bytes = spatial__testing__v3__lifecycle_endpoint_reply_v3__get_packed_size(&reply);
    valid = bytes && bytes <= capacity &&
        spatial__testing__v3__lifecycle_endpoint_reply_v3__pack(&reply, output) == bytes;
    if (valid) *length = bytes;
done:
    spatial__testing__v2__observer_endpoint_reply_v2__free_unpacked(common, NULL);
    return valid;
}
#endif

#ifndef SPATIAL_NATIVE_INPUT_OBSERVER
_X_NORETURN
#endif
void SpatialInputObserverConfigure(const char *descriptor)
{
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
    SpatialObserverEndpointConfigure(descriptor);
#else
    (void)descriptor;
    FatalError("Spatial observer endpoint requires an enabled qualification build\n");
#endif
}

#ifndef SPATIAL_NATIVE_INPUT_OBSERVER
_X_NORETURN
#endif
void SpatialInputObserverOutputConfigure(const char *descriptor)
{
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
    SpatialObserverEndpointOutputConfigureV2(descriptor);
#else
    (void)descriptor;
    FatalError("Spatial observer output requires an enabled qualification build\n");
#endif
}

static void reset(ExtensionEntry *extension)
{
    (void)extension;
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
    SpatialObserverEndpointReset();
    SpatialObserverTransitionShutdownV2();
    SpatialObserverCaptureShutdown();
    SpatialObserverLifetimeReset();
#endif
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
#ifdef SPATIAL_NATIVE_INPUT_OBSERVER
    if (!SpatialObserverLifetimeInit()) {
        SpatialObserverCaptureShutdown();
        SpatialObserverLifetimeReset();
        FatalError("Cannot initialize native input observer lifetime\n");
    }
    if (!SpatialObserverCaptureInit()) {
        SpatialObserverCaptureShutdown();
        SpatialObserverLifetimeReset();
        FatalError("Cannot initialize native input observer capture\n");
    }
    if (!SpatialObserverTransitionInitV2()) {
        SpatialObserverTransitionShutdownV2();
        SpatialObserverCaptureShutdown();
        SpatialObserverLifetimeReset();
        FatalError("Cannot initialize native observer transitions\n");
    }
    if (!SpatialObserverEndpointInit()) {
        SpatialObserverEndpointReset();
        FatalError("Cannot initialize native observer endpoint\n");
    }
#endif
    active = TRUE;
}
