# Native surface attribution

SPATIAL-INPUT Inspect returns one native window generation, geometry, security mode, and optional application identity.
The server obtains identity from the live connection that owns the actual window resource.
Window properties, names, transient hints, process IDs, and credentials supplied by applications cannot choose this identity.

ApplicationIdentity contains workspace, application, server instance ID, and connection ID.
Workspace and application labels identify the application group.
Several connections can share that group while retaining different connection IDs.
The message contains no authentication cookie or credential identifier.
The server serializes borrowed immutable labels before processing another request.

Only the current root redirect owner can inspect native input targets.
The existing native security policy restricts that role to trusted infrastructure.
Ordinary applications cannot use this endpoint to enumerate foreign authority.
Invalid, destroyed, unmapped, and revoked targets produce no application identity.
Infrastructure-owned windows produce no application identity.

The compositor requires explicit DEVELOPMENT or ENFORCED mode in successful replies.
An omitted or unknown mode fails closed, including replies from an older server.
DEVELOPMENT supports the existing dedicated diagnostic session and assigns no application authority.
ENFORCED excludes mapped infrastructure windows from workspace surfaces.
It requires bounded nonempty labels, a 32-byte server ID, and a nonzero connection ID.
The compositor copies that checked identity into its private NativeSurface snapshot.
Unmapping clears the current identity. Remapping obtains a fresh identity with the new native generation.

Migration 0.15.0 adds these private protobuf fields and explicitly records the locked core-schema change.
See NATIVE_SURFACE_IDENTITY_MIGRATION.md. Locked acceptance assertions remain unchanged.
Generate all bindings from the checked-in schemas.
Deploy the server and compositor together because missing security modes fail closed.

Both hosts passed all 22 focused attribution checks.
Both passed 729 core checks across 38 suites, 51 harness checks, and 151 P10 cases in Debug and runtime ASan/UBSan builds.
Both native-server ASan/UBSan profiles passed 210 focused checks with GLX disabled and leak detection enabled.
Both also rejected the older server in Debug and runtime sanitizer profiles before compositor readiness.
No final executed profile skipped a case.
The independent native witness checks ownership, generations, property spoofing, revocation, and query restrictions.
The compositor witness starts real spatialwm and reads its private protobuf snapshots.
The compositor tests use software GLX; core native-server sanitizer checks run separately without GLX.
These tests do not establish physical GPU-session qualification.

Application observation grants, control grants, leases, and actor-facing metadata filtering remain required in worldd.
Worldd must also compare application labels before accepting transient hints as application-group relationships.
Session credential provisioning also remains required.
M04, M09, and Spatial-M1 remain incomplete.
