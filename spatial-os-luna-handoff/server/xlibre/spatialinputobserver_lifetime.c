/* SPDX-License-Identifier: MIT */
#include <dix-config.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>
#include "dix/devices_priv.h"
#include "dix/dix_priv.h"
#include "dix/input_priv.h"
#include "input.h"
#include "dix/resource_priv.h"
#include "dixstruct.h"
#include "privates.h"
#include "windowstr.h"
#include "spatialinputobserver_lifetime.h"

_Static_assert(DOWN_LENGTH == 32, "Review native bitmap width if XLibre changes");
_Static_assert(sizeof(((KeyClassPtr)0)->down) == DOWN_LENGTH, "Key bitmap width");
_Static_assert(sizeof(((ButtonClassPtr)0)->down) == DOWN_LENGTH, "Button bitmap width");

static DevPrivateKeyRec device_epoch_key, client_epoch_key, window_epoch_key;
static uint64_t session_serial, epoch_serial;
static Bool ready, client_callback, resource_callback;

static uint64_t *epoch_at(PrivatePtr *privates, DevPrivateKey key)
{
    return dixLookupPrivate(privates, key);
}

static uint64_t pin_epoch(PrivatePtr *privates, DevPrivateKey key)
{
    uint64_t *value = epoch_at(privates, key);
    if (!*value) {
        if (epoch_serial == UINT64_MAX) {
            ready = FALSE;
            return 0;
        }
        *value = ++epoch_serial;
    }
    return *value;
}

static Bool same_session(uint64_t session)
{
    return ready && session && session == session_serial;
}

static void client_changed(CallbackListPtr *list, void *closure, void *data)
{
    NewClientInfoRec *info = data;
    (void)list; (void)closure;
    if (!ready || !info || !info->client) return;
    if (info->client->clientState == ClientStateRetained ||
        info->client->clientState == ClientStateGone)
        *epoch_at(&info->client->devPrivates, &client_epoch_key) = 0;
}

static void resource_changed(CallbackListPtr *list, void *closure, void *data)
{
    ResourceStateInfoRec *info = data;
    (void)list; (void)closure;
    if (!ready || !info || info->state != ResourceStateFreeing ||
        info->type != X11_RESTYPE_WINDOW || !info->value) return;
    WindowPtr window = info->value;
    *epoch_at(&window->devPrivates, &window_epoch_key) = 0;
}

void SpatialObserverLifetimeReset(void)
{
    ready = FALSE; /* Invalidate every pin before detaching callbacks. */
    if (client_callback)
        DeleteCallback(&ClientStateCallback, client_changed, NULL);
    if (resource_callback)
        DeleteCallback(&ResourceStateCallback, resource_changed, NULL);
    client_callback = resource_callback = FALSE;
    /* Keep serials monotonic across server resets in this process. */
}

Bool SpatialObserverLifetimeInit(void)
{
    /* Startup-only. PRIVATE_WINDOW registration must precede root creation. */
    if (ready || client_callback || resource_callback ||
        session_serial == UINT64_MAX || epoch_serial == UINT64_MAX) return FALSE;
    if (!dixRegisterPrivateKey(&device_epoch_key, PRIVATE_DEVICE, sizeof(uint64_t)) ||
        !dixRegisterPrivateKey(&client_epoch_key, PRIVATE_CLIENT, sizeof(uint64_t)) ||
        !dixRegisterPrivateKey(&window_epoch_key, PRIVATE_WINDOW, sizeof(uint64_t)))
        return FALSE;
    client_callback = AddCallback(&ClientStateCallback, client_changed, NULL);
    if (client_callback)
        resource_callback = AddCallback(&ResourceStateCallback, resource_changed, NULL);
    if (!client_callback || !resource_callback) {
        SpatialObserverLifetimeReset();
        return FALSE;
    }
    ++session_serial;
    ready = TRUE;
    return TRUE;
}

uint64_t SpatialObserverLifetimeSession(void)
{
    return ready ? session_serial : 0;
}

static Bool namespace_valid(const SpatialSelectionNamespace *label)
{
    return label->workspace_size && label->application_size &&
        label->workspace_size <= sizeof(label->workspace) &&
        label->application_size <= sizeof(label->application);
}

static Bool namespace_equal(const SpatialSelectionNamespace *a,
                            const SpatialSelectionNamespace *b)
{
    return namespace_valid(a) && namespace_valid(b) &&
        a->workspace_size == b->workspace_size &&
        a->application_size == b->application_size &&
        !memcmp(a->workspace, b->workspace, a->workspace_size) &&
        !memcmp(a->application, b->application, a->application_size);
}

static ClientPtr live_application(int index, SpatialSelectionNamespace *label)
{
    if (!ready || index <= 0 || index >= currentMaxClients) return NULL;
    ClientPtr client = clients[index];
    if (!client || client->index != index || client->clientGone ||
        client->clientState != ClientStateRunning ||
        !SpatialSecurityEnabled() || !SpatialSecuritySameApplication(client, client) ||
        !SpatialSecuritySelectionNamespace(client, label) || !namespace_valid(label))
        return NULL;
    return client;
}

Bool SpatialObserverPinClient(int index, SpatialObserverClientPin *pin)
{
    SpatialObserverClientPin value = {0};
    if (!pin) return FALSE;
    memset(pin, 0, sizeof(*pin));
    ClientPtr client = live_application(index, &value.label);
    if (!client) return FALSE;
    value.epoch = pin_epoch(&client->devPrivates, &client_epoch_key);
    if (!ready || !value.epoch) return FALSE;
    value.session = session_serial; value.index = index;
    *pin = value;
    return TRUE;
}

Bool SpatialObserverClientMatches(const SpatialObserverClientPin *pin,
                                  ClientPtr recipient)
{
    SpatialSelectionNamespace label;
    if (!pin || !recipient || !same_session(pin->session) || !pin->epoch ||
        !namespace_valid(&pin->label)) return FALSE;
    ClientPtr client = live_application(pin->index, &label);
    return client && client == recipient &&
        *epoch_at(&client->devPrivates, &client_epoch_key) == pin->epoch &&
        namespace_equal(&label, &pin->label);
}

Bool SpatialObserverPinWindow(XID xid, SpatialObserverWindowPin *pin)
{
    SpatialObserverWindowPin value = {0};
    WindowPtr window = NULL;
    if (!pin) return FALSE;
    memset(pin, 0, sizeof(*pin));
    if (!ready || !xid || dixLookupWindow(&window, xid, serverClient, DixReadAccess) != Success ||
        !window || !window->parent || !window->mapped || !window->realized) return FALSE;
    ClientPtr owner = dixLookupXIDOwner(xid);
    if (!owner || !SpatialObserverPinClient(owner->index, &value.owner)) return FALSE;
    value.epoch = pin_epoch(&window->devPrivates, &window_epoch_key);
    if (!ready || !value.epoch) return FALSE;
    value.session = session_serial; value.xid = xid;
    *pin = value;
    return TRUE;
}

Bool SpatialObserverWindowMatches(const SpatialObserverWindowPin *pin,
                                  ClientPtr recipient)
{
    WindowPtr window = NULL;
    if (!pin || !same_session(pin->session) || pin->owner.session != pin->session ||
        !pin->epoch || !pin->xid || !SpatialObserverClientMatches(&pin->owner, recipient) ||
        dixLookupWindow(&window, pin->xid, serverClient, DixReadAccess) != Success ||
        !window || !window->parent || !window->mapped || !window->realized) return FALSE;
    return dixLookupXIDOwner(pin->xid) == recipient &&
        *epoch_at(&window->devPrivates, &window_epoch_key) == pin->epoch;
}

Bool SpatialObserverPinNativeClient(int index, SpatialObserverNativeClientPin *pin)
{
    SpatialObserverNativeClientPin value = {0};
    ClientPtr client;
    if (!pin) return FALSE;
    memset(pin, 0, sizeof(*pin));
    if (!ready || !SpatialSecurityEnabled() || index <= 0 ||
        index >= currentMaxClients || !(client = clients[index]) ||
        client->index != index || client->clientGone ||
        client->clientState != ClientStateRunning || client == serverClient)
        return FALSE;

    if (SpatialSecuritySameApplication(client, client)) {
        if (!SpatialSecuritySelectionNamespace(client, &value.label) ||
            !namespace_valid(&value.label))
            return FALSE;
        value.role = SPATIAL_OBSERVER_NATIVE_CLIENT_APPLICATION;
    } else if (SpatialSecurityPrivileged(client)) {
        value.role = SpatialSecurityInputController(client) ?
            SPATIAL_OBSERVER_NATIVE_CLIENT_COMPOSITOR :
            SPATIAL_OBSERVER_NATIVE_CLIENT_SUPERVISOR;
        if (value.role == SPATIAL_OBSERVER_NATIVE_CLIENT_INVALID)
            return FALSE;
    } else {
        return FALSE;
    }
    value.epoch = pin_epoch(&client->devPrivates, &client_epoch_key);
    if (!ready || !value.epoch) return FALSE;
    value.session = session_serial;
    value.index = index;
    *pin = value;
    return TRUE;
}

Bool SpatialObserverPinMarker(XID xid, SpatialObserverMarkerPin *pin)
{
    SpatialObserverMarkerPin value = {0};
    WindowPtr window = NULL;
    if (!pin) return FALSE;
    memset(pin, 0, sizeof(*pin));
    if (!ready || !xid || dixLookupWindow(&window, xid, serverClient, DixReadAccess) != Success ||
        !window || !window->parent || !window->mapped || !window->realized) return FALSE;
    ClientPtr owner = dixLookupXIDOwner(xid);
    if (!owner || !SpatialObserverPinNativeClient(owner->index, &value.owner) ||
        (value.owner.role != SPATIAL_OBSERVER_NATIVE_CLIENT_SUPERVISOR &&
         value.owner.role != SPATIAL_OBSERVER_NATIVE_CLIENT_COMPOSITOR)) return FALSE;
    value.epoch = pin_epoch(&window->devPrivates, &window_epoch_key);
    if (!ready || !value.epoch) return FALSE;
    value.session = session_serial; value.xid = xid;
    *pin = value;
    return TRUE;
}

static Bool pin_target_window(const SpatialObserverWindowPin *pin,
                              WindowPtr *window)
{
    WindowPtr live = NULL;
    if (!pin || !window || !same_session(pin->session) ||
        pin->owner.session != pin->session || !pin->epoch || !pin->xid ||
        pin->owner.index <= 0 || pin->owner.index >= currentMaxClients ||
        !clients[pin->owner.index] ||
        !SpatialObserverClientMatches(&pin->owner, clients[pin->owner.index]) ||
        dixLookupWindow(&live, pin->xid, serverClient, DixReadAccess) != Success ||
        !live || !live->parent || !live->mapped || !live->realized ||
        dixLookupXIDOwner(pin->xid) != clients[pin->owner.index] ||
        *epoch_at(&live->devPrivates, &window_epoch_key) != pin->epoch)
        return FALSE;
    *window = live;
    return TRUE;
}

static Bool lookup_devices(const int ids[SPATIAL_OBSERVER_DEVICE_COUNT],
                           DeviceIntPtr devices[SPATIAL_OBSERVER_DEVICE_COUNT]);

Bool SpatialObserverSnapshotContextV2(const SpatialObserverDeviceSetPin *device_pin,
                                      const SpatialObserverWindowPin *target_pin,
                                      SpatialObserverContextV2 *context)
{
    DeviceIntPtr devices[SPATIAL_OBSERVER_DEVICE_COUNT];
    SpatialObserverContextV2 value = {0};
    WindowPtr target = NULL;
    int ids[SPATIAL_OBSERVER_DEVICE_COUNT];

    if (!context) return FALSE;
    memset(context, 0xff, sizeof(*context));
    context->valid = FALSE;
    context->session = 0;
    if (in_input_thread() || !device_pin || !target_pin ||
        !same_session(device_pin->session) ||
        target_pin->session != device_pin->session ||
        !pin_target_window(target_pin, &target))
        return FALSE;
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        if (device_pin->devices[i].role != (enum SpatialObserverDeviceRole)i ||
            !device_pin->devices[i].epoch)
            return FALSE;
        ids[i] = device_pin->devices[i].id;
    }
    if (!lookup_devices(ids, devices)) return FALSE;

    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        DeviceIntPtr device = devices[i];
        SpatialObserverDeviceContextV2 *out = &value.devices[i];
        if (*epoch_at(&device->devPrivates, &device_epoch_key) !=
            device_pin->devices[i].epoch)
            return FALSE;
        out->device = device_pin->devices[i];
        out->active_grab = device->deviceGrab.grab != NullGrab;
        out->has_focus_class = device->focus != NULL;
        out->focus_kind = SPATIAL_OBSERVER_FOCUS_NOT_CAPTURED;
        if (device->focus) {
            WindowPtr focus = device->focus->win;
            out->focus_revert = device->focus->revert;
            if (focus == NoneWin)
                out->focus_kind = SPATIAL_OBSERVER_FOCUS_NONE;
            else if (focus == PointerRootWin)
                out->focus_kind = SPATIAL_OBSERVER_FOCUS_POINTER_ROOT;
            else if (focus == FollowKeyboardWin)
                out->focus_kind = SPATIAL_OBSERVER_FOCUS_FOLLOW_KEYBOARD;
            else {
                if (focus != target) return FALSE;
                out->focus_kind = SPATIAL_OBSERVER_FOCUS_OTHER;
                out->focus_is_target = TRUE;
                out->focus_window = *target_pin;
            }
        }
        if (out->active_grab) {
            GrabPtr grab = device->deviceGrab.grab;
            ClientPtr owner;
            SpatialObserverNativeClientPin owner_pin;
            if (!grab || !grab->resource || grab->device != device ||
                grab->window != target)
                return FALSE;
            owner = dixClientForXID(grab->resource);
            if (!owner || owner == serverClient ||
                !SpatialObserverPinNativeClient(owner->index, &owner_pin))
                return FALSE;
            out->grab_resource = grab->resource;
            out->grab_owner = owner_pin;
            out->grab_window = *target_pin;
            if (grab->confineTo) {
                if (grab->confineTo != target) return FALSE;
                out->has_confine_window = TRUE;
                out->confine_window = *target_pin;
            }
        }
        out->complete = TRUE;
    }
    value.session = device_pin->session;
    value.valid = TRUE;
    *context = value;
    return TRUE;
}

static Bool lookup_devices(const int ids[SPATIAL_OBSERVER_DEVICE_COUNT],
                           DeviceIntPtr devices[SPATIAL_OBSERVER_DEVICE_COUNT])
{
    if (!ready) return FALSE;
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        if (ids[i] <= 0 || dixLookupDevice(&devices[i], ids[i], serverClient, DixReadAccess) != Success ||
            !devices[i] || !devices[i]->enabled || !devices[i]->inited) return FALSE;
        for (unsigned j = 0; j < i; ++j) if (ids[i] == ids[j]) return FALSE;
    }
    DeviceIntPtr pm = devices[SPATIAL_OBSERVER_POINTER_MASTER];
    DeviceIntPtr ps = devices[SPATIAL_OBSERVER_POINTER_SLAVE];
    DeviceIntPtr km = devices[SPATIAL_OBSERVER_KEYBOARD_MASTER];
    DeviceIntPtr ks = devices[SPATIAL_OBSERVER_KEYBOARD_SLAVE];
    if (!InputDevIsMaster(pm) || !IsPointerDevice(pm) ||
        !InputDevIsMaster(km) || !IsKeyboardDevice(km) ||
        InputDevIsMaster(ps) || !IsPointerDevice(ps) ||
        InputDevIsMaster(ks) || !IsKeyboardDevice(ks) ||
        GetPairedDevice(pm) != km || GetPairedDevice(km) != pm ||
        GetMaster(ps, MASTER_ATTACHED) != pm || GetMaster(ks, MASTER_ATTACHED) != km ||
        !pm->button || !ps->button || !pm->button->numButtons || !ps->button->numButtons ||
        !km->key || !ks->key || !km->key->xkbInfo || !ks->key->xkbInfo ||
        !km->focus || !ks->focus) return FALSE;
    return TRUE;
}

Bool SpatialObserverPinDevices(const int ids[SPATIAL_OBSERVER_DEVICE_COUNT],
                               SpatialObserverDeviceSetPin *pin)
{
    DeviceIntPtr devices[SPATIAL_OBSERVER_DEVICE_COUNT];
    SpatialObserverDeviceSetPin value = {0};
    if (!pin) return FALSE;
    memset(pin, 0, sizeof(*pin));
    if (!ids || !lookup_devices(ids, devices)) return FALSE;
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        value.devices[i].id = ids[i];
        value.devices[i].role = (enum SpatialObserverDeviceRole)i;
        value.devices[i].epoch = pin_epoch(&devices[i]->devPrivates, &device_epoch_key);
        if (!ready || !value.devices[i].epoch) return FALSE;
    }
    value.session = session_serial;
    *pin = value;
    return TRUE;
}

static void copy_native_context(DeviceIntPtr device,
                                SpatialObserverNativeDeviceState *out)
{
    out->active_grab = device->deviceGrab.grab != NullGrab;
    out->native_frozen = device->deviceGrab.sync.frozen;
    out->frozen_by_other_grab = device->deviceGrab.sync.other != NullGrab;
    out->native_sync_state = device->deviceGrab.sync.state;
    out->replay_device = syncEvents.replayDev == device;
    if (device->focus) {
        /* Focus win may be a sentinel or stale pointer. Never dereference it. */
        WindowPtr focus = device->focus->win;
        out->has_focus_class = TRUE;
        out->focus_revert = device->focus->revert;
        if (focus == NoneWin)
            out->focus_kind = SPATIAL_OBSERVER_FOCUS_NONE;
        else if (focus == PointerRootWin)
            out->focus_kind = SPATIAL_OBSERVER_FOCUS_POINTER_ROOT;
        else if (focus == FollowKeyboardWin)
            out->focus_kind = SPATIAL_OBSERVER_FOCUS_FOLLOW_KEYBOARD;
        else
            out->focus_kind = SPATIAL_OBSERVER_FOCUS_OTHER;
    }
}

Bool SpatialObserverSnapshotDevices(const SpatialObserverDeviceSetPin *pin,
                                    SpatialObserverNativeState *state)
{
    DeviceIntPtr devices[SPATIAL_OBSERVER_DEVICE_COUNT];
    SpatialObserverNativeState value = {0};
    int ids[SPATIAL_OBSERVER_DEVICE_COUNT];
    if (!state) return FALSE;
    /* Failure must not look like four all-clear native bitmap records. */
    memset(state, 0xff, sizeof(*state)); state->valid = FALSE; state->session = 0;
    if (!pin || !same_session(pin->session)) return FALSE;
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        if (pin->devices[i].role != (enum SpatialObserverDeviceRole)i || !pin->devices[i].epoch)
            return FALSE;
        ids[i] = pin->devices[i].id;
    }
    if (!lookup_devices(ids, devices)) return FALSE;
    for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
        DeviceIntPtr device = devices[i];
        if (*epoch_at(&device->devPrivates, &device_epoch_key) != pin->devices[i].epoch)
            return FALSE;
        SpatialObserverNativeDeviceState *out = &value.devices[i];
        out->identity = pin->devices[i];
        copy_native_context(device, out);
        if (i == SPATIAL_OBSERVER_POINTER_MASTER || i == SPATIAL_OBSERVER_POINTER_SLAVE) {
            out->has_button_class = TRUE;
            out->paired_or_attached = i == SPATIAL_OBSERVER_POINTER_MASTER ?
                devices[SPATIAL_OBSERVER_KEYBOARD_MASTER]->id : devices[SPATIAL_OBSERVER_POINTER_MASTER]->id;
            memcpy(out->button_down, device->button->down, sizeof(out->button_down));
            memcpy(out->button_postdown, device->button->postdown, sizeof(out->button_postdown));
            out->num_buttons = device->button->numButtons;
            out->buttons_down = device->button->buttonsDown;
            out->button_state = device->button->state;
        } else {
            out->has_key_class = TRUE;
            out->paired_or_attached = i == SPATIAL_OBSERVER_KEYBOARD_MASTER ?
                devices[SPATIAL_OBSERVER_POINTER_MASTER]->id : devices[SPATIAL_OBSERVER_KEYBOARD_MASTER]->id;
            memcpy(out->key_down, device->key->down, sizeof(out->key_down));
            memcpy(out->key_postdown, device->key->postdown, sizeof(out->key_postdown));
        }
    }
    /* Count every queue record, including unrelated devices, in one bounded pass.
     * On overflow the caller still receives the initial invalid/nonzero sentinel state.
     */
    unsigned visited = 0;
    QdEventPtr pending;
    xorg_list_for_each_entry(pending, &syncEvents.pending, next) {
        if (visited == SPATIAL_OBSERVER_MAX_PENDING_RECORDS) return FALSE;
        ++visited;
        for (unsigned i = 0; i < SPATIAL_OBSERVER_DEVICE_COUNT; ++i) {
            /* Compare only callback-local live identities. Retain neither pointer. */
            if (pending->device == devices[i]) ++value.devices[i].pending_events;
        }
    }
    value.any_pending_events = !xorg_list_is_empty(&syncEvents.pending);
    value.replay_pending = syncEvents.replayDev != NULL;
    value.replay_in_progress = syncEvents.playingEvents;
    value.session = pin->session; value.valid = TRUE;
    *state = value;
    return TRUE;
}
