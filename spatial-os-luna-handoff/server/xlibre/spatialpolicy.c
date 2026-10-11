/* SPDX-License-Identifier: MIT
 * Resources inherit their allocating connection's immutable application label.
 * DIX retains that connection until its resource table has been destroyed.
 */
#include <dix-config.h>
#include <string.h>
#include <X11/extensions/render.h>
#include "dix/dix_priv.h"
#include "dix/client_priv.h"
#include "dix/devices_priv.h"
#include "dix/extension_priv.h"
#include "dix/selection_priv.h"
#include "dix/server_priv.h"
#include "dix/screensaver_priv.h"
#include "render/picturestr_priv.h"
#include "Xext/xacestr.h"
#include "windowstr.h"
#include "spatialsecurity.h"

static DevPrivateKeyRec selection_namespace_key;

Bool SpatialSecurityFilterEvent(ClientPtr client, const xEvent *event, xEvent *filtered)
{
    if (SpatialSecurityPrivileged(client) ||
        (event->u.u.type & 0x7f) != ConfigureNotify ||
        event->u.configureNotify.aboveSibling == None ||
        SpatialSecurityVisibleResource(client, event->u.configureNotify.aboveSibling)) return FALSE;
    *filtered = *event;
    filtered->u.configureNotify.aboveSibling = None;
    WindowPtr window;
    if (dixLookupWindow(&window, event->u.configureNotify.window, serverClient, DixReadAccess) == Success) {
        for (WindowPtr sibling = window->nextSib; sibling; sibling = sibling->nextSib) {
            if (SpatialSecurityVisibleResource(client, sibling->drawable.id)) {
                filtered->u.configureNotify.aboveSibling = sibling->drawable.id;
                break;
            }
        }
    }
    return TRUE;
}

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
        /* Window pictures can address a shared framebuffer outside the window.
         * Only pixmap and procedural pictures are untrusted RENDER sources.
         * This also rejects window alpha maps when they are bound.
         */
        if (rec->rtype == PictureType && (rec->access_mode & DixReadAccess)) {
            PicturePtr picture = rec->res;
            if (picture->pDrawable && picture->pDrawable->type == DRAWABLE_WINDOW) {
                rec->status = BadAccess;
                return;
            }
        }
        if ((rec->access_mode & DixCreateAccess) && rec->rtype == X11_RESTYPE_WINDOW)
            ((WindowPtr)rec->res)->forcedBG = TRUE;
        return;
    }
    if (owner == serverClient && SpatialSecuritySameApplication(rec->client, rec->client)) {
        /* CreateCursor converts an already-authorized source into a temporary
         * picture. ID zero cannot be addressed by a client request.
         */
        if (rec->id == 0 && rec->rtype == PictureType &&
            rec->access_mode == (DixCreateAccess | DixSetAttrAccess) &&
            rec->client->majorOp == EXTENSION_MAJOR_RENDER &&
            rec->client->minorOp == X_RenderCreateCursor) return;
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
        /* Server-defined pixel layouts contain no application content. */
        if (rec->rtype == PictFormatType && rec->access_mode == DixReadAccess) return;
    }
    rec->status = BadAccess;
}

static void property_access(CallbackListPtr *list, void *unused, void *data)
{
    XacePropertyAccessRec *rec = data;
    (void)list; (void)unused;
    if (!SpatialSecurityVisibleResource(rec->client, rec->pWin->drawable.id)) rec->status = BadAccess;
}

static void selection_access(CallbackListPtr *list, void *unused, void *data)
{
    XaceSelectionAccessRec *rec = data;
    (void)list; (void)unused;
    Selection *selection = *rec->ppSel;
    SpatialSelectionNamespace wanted;
    if (!selection || !SpatialSecuritySelectionNamespace(rec->client, &wanted)) {
        rec->status = BadAccess;
        return;
    }
    if (!SpatialSecurityPrivileged(rec->client) &&
        !SpatialSecurityAtomVisible(rec->client, selection->selection)) {
        rec->status = BadAtom;
        return;
    }
    /* DIX finds a selection by public atom first. Refine that lookup using
     * immutable application labels before it can observe or change ownership.
     */
    for (Selection *candidate = CurrentSelections; candidate; candidate = candidate->next) {
        if (candidate->selection != selection->selection) continue;
        SpatialSelectionNamespace *label = dixLookupPrivate(&candidate->devPrivates,
                                                            &selection_namespace_key);
        if (label->workspace_size == wanted.workspace_size &&
            label->application_size == wanted.application_size &&
            !memcmp(label->workspace, wanted.workspace, wanted.workspace_size) &&
            !memcmp(label->application, wanted.application, wanted.application_size)) {
            *rec->ppSel = candidate;
            return;
        }
    }
    Selection *created = dixAllocateObjectWithPrivates(Selection, PRIVATE_SELECTION);
    if (!created) {
        rec->status = BadAlloc;
        return;
    }
    created->selection = selection->selection;
    *(SpatialSelectionNamespace *)dixLookupPrivate(&created->devPrivates,
                                                   &selection_namespace_key) = wanted;
    created->next = CurrentSelections;
    CurrentSelections = created;
    *rec->ppSel = created;
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

static void server_access(CallbackListPtr *list, void *unused, void *data)
{
    ServerAccessCallbackParam *rec = data;
    (void)list; (void)unused;
    if (SpatialSecurityPrivileged(rec->client)) return;
    /* Font definitions are public dependencies. Their host paths are not. */
    if (SpatialSecuritySameApplication(rec->client, rec->client) &&
        (rec->client->majorOp == X_ListFonts || rec->client->majorOp == X_ListFontsWithInfo)) return;
    rec->status = BadAccess;
}

static void screensaver_access(CallbackListPtr *list, void *unused, void *data)
{
    ScreenSaverAccessCallbackParam *rec = data;
    (void)list; (void)unused;
    if (!SpatialSecurityPrivileged(rec->client)) rec->status = BadAccess;
}

static void device_administration(CallbackListPtr *list, void *unused, void *data)
{
    DeviceAccessCallbackParam *rec = data;
    (void)list; (void)unused;
    /* NULL is a server-internal call, never a native request connection. */
    if (!rec->client || SpatialSecurityPrivileged(rec->client)) return;
    /* A client's last opcode must not affect asynchronous event access. */
    if (!(rec->access_mode & (DixManageAccess | DixBellAccess))) return;
    switch (rec->client->majorOp) {
    case X_ChangeKeyboardMapping:
    case X_ChangeKeyboardControl:
    case X_Bell:
    case X_ChangePointerControl:
    case X_SetPointerMapping:
    case X_SetModifierMapping:
        rec->status = BadAccess;
    }
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

static Bool extension_allowed(ClientPtr client, ExtensionEntry *extension, int minor)
{
    if (SpatialSecurityPrivileged(client)) return TRUE;
    if (!strcmp(extension->name, "SPATIAL-SECURITY")) return minor < 0 || minor == 0;
    switch (extension->base) {
    case EXTENSION_MAJOR_BIG_REQUESTS: return minor < 0 || minor == 0;
    case EXTENSION_MAJOR_XC_MISC: return minor < 0 || minor <= 2;
    }
    if (!SpatialSecuritySameApplication(client, client)) return FALSE;
    switch (extension->base) {
    case EXTENSION_MAJOR_SHAPE: return minor < 0 || minor <= 8;
    case EXTENSION_MAJOR_RENDER:
        if (minor < 0) return TRUE;
        /* These pinned handlers check every externally supplied resource.
         * Do not admit reserved or future requests from a numeric range alone.
         */
        switch (minor) {
        case X_RenderQueryVersion:
        case X_RenderQueryPictFormats:
        case X_RenderQueryPictIndexValues:
        case X_RenderCreatePicture:
        case X_RenderChangePicture:
        case X_RenderSetPictureClipRectangles:
        case X_RenderFreePicture:
        case X_RenderComposite:
        case X_RenderTrapezoids:
        case X_RenderTriangles:
        case X_RenderTriStrip:
        case X_RenderTriFan:
        case X_RenderCreateGlyphSet:
        case X_RenderReferenceGlyphSet:
        case X_RenderFreeGlyphSet:
        case X_RenderAddGlyphs:
        case X_RenderFreeGlyphs:
        case X_RenderCompositeGlyphs8:
        case X_RenderCompositeGlyphs16:
        case X_RenderCompositeGlyphs32:
        case X_RenderFillRectangles:
        case X_RenderCreateCursor:
        case X_RenderSetPictureTransform:
        case X_RenderQueryFilters:
        case X_RenderSetPictureFilter:
        case X_RenderCreateAnimCursor:
        case X_RenderAddTraps:
        case X_RenderCreateSolidFill:
        case X_RenderCreateLinearGradient:
        case X_RenderCreateRadialGradient:
        case X_RenderCreateConicalGradient:
            return TRUE;
        }
    }
    return FALSE;
}

static void extension_access(CallbackListPtr *list, void *unused, void *data)
{
    ExtensionAccessCallbackParam *rec = data;
    (void)list; (void)unused;
    if (!extension_allowed(rec->client, rec->ext, -1)) rec->status = BadAccess;
}

static void extension_dispatch(CallbackListPtr *list, void *unused, void *data)
{
    ExtensionAccessCallbackParam *rec = data;
    (void)list; (void)unused;
    if (!extension_allowed(rec->client, rec->ext, rec->client->minorOp)) rec->status = BadAccess;
}

void SpatialSecurityPolicyInit(void)
{
    if (!dixRegisterPrivateKey(&selection_namespace_key, PRIVATE_SELECTION,
                               sizeof(SpatialSelectionNamespace)) ||
        !XaceRegisterCallback(XACE_RESOURCE_ACCESS, resource_access, NULL) ||
        !XaceRegisterCallback(XACE_PROPERTY_ACCESS, property_access, NULL) ||
        !XaceRegisterCallback(XACE_SELECTION_ACCESS, selection_access, NULL) ||
        !XaceRegisterCallback(XACE_SEND_ACCESS, send_access, NULL) ||
        !XaceRegisterCallback(XACE_RECEIVE_ACCESS, receive_access, NULL) ||
        !AddCallback(&ClientAccessCallback, client_access, NULL) ||
        !AddCallback(&DeviceAccessCallback, device_administration, NULL) ||
        !AddCallback(&ServerAccessCallback, server_access, NULL) ||
        !AddCallback(&ScreenSaverAccessCallback, screensaver_access, NULL) ||
        !AddCallback(&ExtensionAccessCallback, extension_access, NULL) ||
        !AddCallback(&ExtensionDispatchCallback, extension_dispatch, NULL))
        FatalError("Cannot initialize spatial resource policy\n");
}

void SpatialSecurityPolicyReset(void)
{
    XaceDeleteCallback(XACE_RESOURCE_ACCESS, resource_access, NULL);
    XaceDeleteCallback(XACE_PROPERTY_ACCESS, property_access, NULL);
    XaceDeleteCallback(XACE_SELECTION_ACCESS, selection_access, NULL);
    XaceDeleteCallback(XACE_SEND_ACCESS, send_access, NULL);
    XaceDeleteCallback(XACE_RECEIVE_ACCESS, receive_access, NULL);
    DeleteCallback(&ClientAccessCallback, client_access, NULL);
    DeleteCallback(&DeviceAccessCallback, device_administration, NULL);
    DeleteCallback(&ServerAccessCallback, server_access, NULL);
    DeleteCallback(&ScreenSaverAccessCallback, screensaver_access, NULL);
    DeleteCallback(&ExtensionAccessCallback, extension_access, NULL);
    DeleteCallback(&ExtensionDispatchCallback, extension_dispatch, NULL);
}
