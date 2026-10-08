# Native extension policy

This M04 component extends the strict connection and resource policies.
It does not complete M04 or authorize an untrusted native session.

## Discovery and dispatch

Check extension discovery and request dispatch against the immutable connection role.
Check direct opcodes even when QueryExtension and ListExtensions hide the extension.
Use the same checks for native and swapped clients.
Reject unknown extension families and unapproved minor opcodes with BadAccess.
Do not reset another callback's denial.

| Connection role | Available extensions |
|---|---|
| Application | BIG-REQUESTS, XC-MISC, SPATIAL-SECURITY, RENDER, SHAPE |
| Broker | BIG-REQUESTS, XC-MISC, SPATIAL-SECURITY |
| Supervisor or compositor | Installed service extensions |

The existing trusted diagnostic mode remains unrestricted.
It is not an application security boundary.

BIG-REQUESTS permits Enable. XC-MISC permits version, client-local ID range, and client-local ID list requests.
SPATIAL-SECURITY permits its protobuf envelope. Its handler still authorizes each credential operation.
SHAPE permits the pinned version, shape operations, queries, and event-selection requests, numbered zero through eight.
Every supplied window or pixmap must pass the resource policy.

RENDER permits its pinned implemented requests through CreateConicalGradient.
An explicit opcode list excludes reserved requests and future additions.
Public server-owned picture formats permit read access. Private pictures and glyph sets retain their application identity.
Foreign sources, masks, destinations, clip masks, glyph aliases, cursors, and animated cursors remain inaccessible.

Ordinary applications cannot use XTEST, X-Resource, SPATIAL-INPUT, Composite, MIT-SHM, XFIXES, DAMAGE, or other unqualified families.
This restriction also covers application GLX, DRI, XInput, and XKEYBOARD extension requests.
Required editor and terminal integration must qualify the actual application profiles before milestone acceptance.
Adding an extension requires a handler audit, independent attacks, and positive application evidence.

## Window picture reads

A window picture can address the shared framebuffer outside that window's visible region.
An independent attack copied underlying foreign pixels through a shaped window into an owned pixmap.
Resource ownership alone does not prevent this exposure.

Ordinary applications may use window pictures as drawing destinations.
They may read owned pixmap pictures and procedural sources.
Reject window-picture read access, including cursor sources and alpha-map binding.
This check runs before native RENDER code reads the backing pixels.
It applies even when the requesting application owns the window picture.
Use authorized core image reads or trusted observation for window pixels.
Do not enable window-picture source reads without a qualified, backend-independent pixel boundary.

CreateCursor converts an authorized source through an internal temporary picture with ID zero.
Permit only that internal creation hook, with its exact resource type, access mask, and request opcode.
Client requests cannot allocate resource ID zero.
This exception does not authorize other server resources or skip the source-picture check.

## Animated cursor lifetime

Release the animation timer when the last cursor reference is destroyed.
Free it once, alongside the retained frame cursors on the first screen.
Dropping the resource ID must not destroy a cursor still referenced by a window or pointer.
The maintained 0005 patch fixes a timer allocation that previously survived server shutdown.
The native witness exercises both undisplayed cursors and repeated displayed animation replacement.

## Qualification

The independent native witness is tests/native/test_extension_isolation.py.
Its credential envelope uses generated Protocol Buffers. Native X requests use the standard X11 wire format.
The witness checks real rendering, native errors, and unchanged destination pixels after denied operations.
It also checks broker restrictions, trusted service access, byte order, malformed requests, and reserved opcodes.

Run all identity, resource, and extension witnesses on the production project server.
Run the existing P00-P10 gate, core suite, and harness checks.
Instrument the native server separately from runtime instrumentation.
Record the explicit GLX exclusion in focused core-X sanitizer runs.
The recorded graphics-provider GLX cleanup failure remains unresolved.
Final qualification still requires the full Spatial-M1 profile on both hosts.

Global core operations, atoms, selections, device authority, aliases, and asynchronous metadata remain separate required work.
Broker credential provisioning and compositor authority integration also remain required.

The server runs in disposable project displays during development.
This component changes no host service, normal login session, or recovery route.
