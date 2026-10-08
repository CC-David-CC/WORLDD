# Native resource policy

This component extends strict native connection identities with application resource checks.
It contributes to M04. Full native isolation remains incomplete.

## Invariant

Native resource lookups enforce application identity and explicit public-resource exceptions.

Different application identities remain separate within one workspace.
The same application name in another workspace grants no access.
Several connections with the same workspace and application identity may share resources.
The production broker must issue that identity only within one application authority boundary.

## Resource ownership

X resources belong to the connection identified by their native resource ID.
The pinned DIX resource table retains that connection until its resources are freed.
Resource checks use its immutable server-owned security label.
An ordinary client cannot allocate resource IDs in another connection's range.
Client-index reuse occurs only after the old resource table is destroyed.

The policy checks native resource lookups and creation hooks.
The same checks cover windows, pixmaps, graphics contexts, and extension resources that use these hooks.
Unknown owners and unlabelled connections fail closed.
Native denial returns BadAccess.

Application connections cannot select RetainPermanent or RetainTemporary.
Disconnect destroys their resources through normal DIX teardown.
Credential revocation destroys affected live resources before its acknowledgement.
This rule does not restore application state. Restoration requires the application supervisor.

## Shared server resources

The root window supplies a parent and public screen geometry.
Application root queries return only children from the same application identity.
Coordinate translation does not disclose a foreign child ID.
Applications cannot read root pixels or root properties.
Applications cannot select root events or change all root children.

Allow specific root operations for creating and destroying owned windows.
Allow root geometry as a format source for new pixmaps and graphics contexts.
Do not authorize an operation from an access mask alone.
Different native requests can use the same mask for different effects.

Applications can use public default-colormap definitions.
They cannot install a global colormap or modify its allocated colors through this exception.
New application windows use the server's forced-background restriction.
Parent-relative backgrounds require access to the parent application's pixels.
Reparenting cannot turn an owned background into a read of private root pixels.
Implicit border inheritance from a foreign parent uses public black pixels.
Explicit border copying requires access to the parent's pixels.
Full transparency and graphics-extension qualification remain required.

## Service roles

The server, supervisor, and compositor can access native application resources.
These are trusted service connections. They are not actor credentials.
The compositor must enforce current world and workspace authority before exporting pixels or routing control.
Broker credentials grant no general application observation rights.

Property and event hooks also enforce the application boundary.
These hooks do not establish actor-specific device authority.
Server-generated events still require the input routing policy.

## Verification and remaining work

The independent witness sends real X11 requests to a disposable project XLibre server.
It uses generated protobuf bindings only for credential setup and completion fences.
It does not call production resource-policy helpers.
Keep ordinary Xlib authentication tests and the locked P00-P10 baseline.
Record missing-feature failures before production changes.
Use both native byte orders and positive tests for owned resources.

Native isolation also requires extension dispatch, global operations, atoms, selections, and device policy.
Audit aliases, server-created resources, rendering extensions, and asynchronous event metadata.
Test each path with independent attacks before claiming M04.
Broker provisioning, compositor integration, and physical session acceptance remain separate requirements.
The existing graphics-provider sanitizer failure remains open.

## Inspected source

The source pin is recorded in server/base.lock.json.
Ownership and lifetime were inspected in dix/resource.c, dix/dix_priv.h, and dix/dispatch.c.
Hook parameters were inspected in Xext/xacestr.h and dix/client_priv.h.
The maintained patch filters QueryTree and TranslateCoords in dix/dispatch.c.
