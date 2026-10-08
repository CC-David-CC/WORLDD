/* SPDX-License-Identifier: MIT
 * Resources inherit their allocating connection's immutable application label.
 * DIX retains that connection until its resource table has been destroyed.
 */
#include <dix-config.h>
#include "dix/dix_priv.h"
#include "dix/client_priv.h"
#include "Xext/xacestr.h"
#include "windowstr.h"
#include "spatialsecurity.h"

static Bool subset(Mask requested, Mask allowed)
{
    return requested && !(requested & ~allowed);
}

static Bool root_access(ClientPtr client, Mask access)
{
    /* Only these requests may use the shared root as a parent or format source.
     * A bare access mask is insufficient: DestroySubwindows also asks Remove.
     */
    switch (client->majorOp) {
    case X_CreateWindow: return subset(access, DixAddAccess);
    case X_CreatePixmap:
    case X_CreateGC:
    case X_GetGeometry:
    case X_GetWindowAttributes:
    case X_TranslateCoords: return subset(access, DixGetAttrAccess);
    case X_QueryTree: return subset(access, DixListAccess);
    case X_DestroyWindow: return subset(access, DixRemoveAccess);
    case X_ReparentWindow: return subset(access, DixAddAccess | DixRemoveAccess);
    default: return FALSE;
    }
}

static void resource_access(CallbackListPtr *list, void *unused, void *data)
{
    XaceResourceAccessRec *rec = data;
    (void)list; (void)unused;
    if (SpatialSecurityPrivileged(rec->client)) return;
    ClientPtr owner = dixLookupXIDOwner(rec->id);
    if (SpatialSecuritySameApplication(rec->client, owner)) {
        if ((rec->access_mode & DixCreateAccess) && rec->rtype == X11_RESTYPE_WINDOW)
            ((WindowPtr)rec->res)->forcedBG = TRUE;
        return;
    }
    if (owner == serverClient && SpatialSecuritySameApplication(rec->client, rec->client)) {
        if (rec->rtype == X11_RESTYPE_WINDOW) {
            WindowPtr window = rec->res;
            if (window && window == window->drawable.pScreen->root && root_access(rec->client, rec->access_mode))
                return;
        }
        /* The default visual's colormap contains public color definitions.
         * Neither global installation nor writes to allocated colors are allowed.
         */
        if (rec->rtype == X11_RESTYPE_COLORMAP &&
            subset(rec->access_mode, DixReadAccess | DixGetAttrAccess | DixUseAccess | DixAddAccess)) return;
    }
    rec->status = BadAccess;
}

static void property_access(CallbackListPtr *list, void *unused, void *data)
{
    XacePropertyAccessRec *rec = data;
    (void)list; (void)unused;
    if (!SpatialSecurityVisibleResource(rec->client, rec->pWin->drawable.id)) rec->status = BadAccess;
}

static void send_access(CallbackListPtr *list, void *unused, void *data)
{
    XaceSendAccessRec *rec = data;
    (void)list; (void)unused;
    /* NULL denotes server-generated input, not an unauthenticated X client.
     * Actor-specific device authorization remains a separate boundary.
     */
    if (rec->client && !SpatialSecurityVisibleResource(rec->client, rec->pWin->drawable.id))
        rec->status = BadAccess;
}

static void receive_access(CallbackListPtr *list, void *unused, void *data)
{
    XaceReceiveAccessRec *rec = data;
    (void)list; (void)unused;
    if (!SpatialSecurityVisibleResource(rec->client, rec->pWin->drawable.id)) rec->status = BadAccess;
}

static void client_access(CallbackListPtr *list, void *unused, void *data)
{
    ClientAccessCallbackParam *rec = data;
    (void)list; (void)unused;
    if (SpatialSecurityPrivileged(rec->client)) return;
    if (!SpatialSecuritySameApplication(rec->client, rec->target)) {
        rec->status = BadAccess;
        return;
    }
    /* Do not leave application resources after its last connection is gone.
     * The request's one-byte mode does not depend on native byte order.
     */
    if (rec->client->majorOp == X_SetCloseDownMode &&
        ((xSetCloseDownModeReq *)rec->client->requestBuffer)->mode != DestroyAll)
        rec->status = BadAccess;
}

void SpatialSecurityPolicyInit(void)
{
    if (!XaceRegisterCallback(XACE_RESOURCE_ACCESS, resource_access, NULL) ||
        !XaceRegisterCallback(XACE_PROPERTY_ACCESS, property_access, NULL) ||
        !XaceRegisterCallback(XACE_SEND_ACCESS, send_access, NULL) ||
        !XaceRegisterCallback(XACE_RECEIVE_ACCESS, receive_access, NULL) ||
        !AddCallback(&ClientAccessCallback, client_access, NULL))
        FatalError("Cannot initialize spatial resource policy\n");
}

void SpatialSecurityPolicyReset(void)
{
    XaceDeleteCallback(XACE_RESOURCE_ACCESS, resource_access, NULL);
    XaceDeleteCallback(XACE_PROPERTY_ACCESS, property_access, NULL);
    XaceDeleteCallback(XACE_SEND_ACCESS, send_access, NULL);
    XaceDeleteCallback(XACE_RECEIVE_ACCESS, receive_access, NULL);
    DeleteCallback(&ClientAccessCallback, client_access, NULL);
}
