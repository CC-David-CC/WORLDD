# World surface relationships and application leases

Worldd uses native application identity for relationships and control groups.
The identity comes from spatialwm's private NativeSurface snapshot.
The native server supplies that identity from the actual window owner.
Application properties do not choose it.

A transient parent must share the child's server, workspace, and application identity.
Connection IDs may differ within one application.
An invalid relationship has no room, parent ID, or mapped world representation.
It does not appear in the foreign application's child list.
Other applications remain usable.
Removing the hint restores the surface's previous independent placement, if one exists.
Existing cycle and depth checks remain active.

An application control group uses server, workspace, and application identity.
The group key uses protobuf field encoding to preserve opaque label boundaries.
Connection IDs and transient hints do not split that group.
Separate top-level windows can therefore share one lease.
Different applications can retain simultaneous controllers.
Equal application labels in different workspaces remain separate.

Worldd checks the current surface generation before it obtains a control group.
Its existing focus refresh releases input when the surface becomes invalid or leaves the actor's room.
Valid same-application hint changes preserve held input and the application lease.
Cross-application hints and destroyed parents release affected descendant input.

Explicit native development sessions retain their unlabelled transient grouping.
An attributed surface cannot form a relationship with an unlabelled surface.
The compositor rejects missing native security mode and validates attribution before forwarding snapshots.
Development sessions do not establish production application authority.

The tests launch independent compiled C clients, worldd, spatialwm, and a disposable native server.
Each application receives a real native credential.
The test actors use the management fixture's static grants.
The tests check real key events as well as worldd state.
They do not certify actor workspace grants or an actor-facing exploit.

No protobuf schema, acceptance lock, or locked acceptance test changes are required.
No upstream XLibre patch or installed display change is required.
Workspace-authorized application observation, control, metadata filtering, and revocation remain required.
This component does not complete M04, M09, or Spatial-M1.
