# Architecture and deliberate corrections

## Scope

The deliverable is a native spatial Linux/X11 session: a window manager/compositor, live application surfaces, persistent world objects and rooms, agents with independent input, Git/worktree context, physical SpaceMouse navigation, and operational recovery. It is not a replacement kernel or a promise to support every existing Linux application.

The initial runtime has worldd and spatialwm. Workspace execution requires a separate production workspaced broker. `worldd` owns semantic state and actor action authorization. `spatialwm` owns X resources, the compositor, render submission, input delivery and physical device adaptation. A test host starts/controls them in a test-owned process group and forwards the contract requests. The host is not shipped as the desktop. Workspaced owns workspace identities, bare Git backing stores, process trees, and execution confinement. Its state is independent of world identity and spatial placement. Worldd holds explicit availability and per-world actor selection, then routes authorized operations to the broker. This broker is required work, not an implemented feature; consult CURRENT_STATE.md.

## What changed from the earlier proposal

**No hidden root-coordinate atlas.** The previous design moved native windows into private input slots to accommodate legacy dispatch. Existing-software compatibility is no longer a constraint. The P06 suite instead keeps native root geometry fixed and requires actor-local input routing, distinct XI2 device identity, focus and capture. Use an XLibre server extension or internal server change where stock routing cannot express this naturally. The server-side operation must update genuine device state and normal event delivery; merely sending synthetic events to the witness is not the implementation.

**No universal legacy-client policy.** The reference application contract uses XI2 for input and ordinary X windows for pixels. One actor controls an application instance and its surface group. Current transient grouping is an interim implementation; broker application identities remain mandatory. There is no requirement to make an unmodified legacy toolkit collaborative. Expand the supported application contract deliberately, rather than accumulating per-application exceptions. A lease is a world conflict rule, not a security sandbox.

**No durable identity based on XID.** A live surface has a session-scoped ID and generation. A durable `surface_slot` owns a placement and, eventually, an explicit launch specification. A replacement window can attach to that slot. A directly created test client without a slot is transient; its death does not imply its process memory can be recovered. The slot/surface distinction is a real lifecycle boundary, not two competing copies of placement.

**No SQLite transaction per frame.** The world daemon owns current runtime actor state. Durable entities and explicit pose checkpoints survive restart. Axes, pressed inputs, credentials, leases and uncommitted drags do not. A render frame can reflect transient motion without a database write. Durable edits are acknowledged only after durable commit.

**No fictitious atomic transaction across Git, X and processes.** Record effect intent, perform the external effect, reconcile observations, and record readiness. Git worktrees have inspectable path/ref state, so recovery can recognize an already completed create. Arbitrary child-process side effects do not in general permit exactly-once guarantees after a crash. Live-manager operation retries must not duplicate launches; ambiguous crash recovery must be reported as `uncertain`, not silently relaunched or called successful. SQLite's atomicity applies to the database, not all of these other systems. See `SOURCES.md` [S6].

**No renderer-specific authoritative world.** Production renderer caches are derived from world revisions and live surface generations. Debug/readback methods use the production renderer. The test adapter cannot maintain an alternative scene or hand-return expected pixels.

## Canonical data boundaries

A room is a coordinate chart. A portal maps a frame from one chart into another. Objects and surface slots own room-local poses. An actor has one pose, one room, one independently identified input context, and a capability set. A surface is an external framebuffer attachment. A repository, branch reference, commit, worktree, and workspace are different things. A workspace owns one execution and filesystem authority. Rooms do not own worktrees or execution context. Independent mutable workspaces use separate writable directories and bare Git metadata. Multiple authorized representations of one workspace preserve its single authority.

Application texture coordinates remain two-dimensional. Removing a 2D *desktop* does not remove pixels from application surfaces. World ray intersection produces surface-local UV, then pixel coordinates. Routing must preserve that result through the X server, including focus, held buttons, drag capture and cancellation.

## Suggested X/server boundary

A narrow internal operation can accept an authenticated compositor request with actor input context, target live window/generation, local position, and input transition. It must perform server-side target validation and drive actual input state/event routing. World authorization happens in `worldd`; the server validates the live window/device boundary. Preserve complete input state transitions, not a bag of independent `SendEvent` calls.

Do not pin an extension opcode in source. Discover/allocate it using the actual X extension mechanism. Keep server protocol versioning explicit. Document generation invalidation, bounds, focus changes, capture and actor teardown in the patch series. Test the server boundary with deliberately overlapping native root-coordinate windows and two actors at once.

The existing Composite, Damage and GLX texture-from-pixmap machinery is a reasonable first rendering path [S2, S3]. A different import path is acceptable when required by the measured GPU/backend, but it must satisfy the same real-pixel tests without a parallel fake renderer. Do not add a stack of fallback paths speculatively.

## Security scope

The conformance actor-token tests check routing and API permission discipline. They do not isolate malicious processes sharing an unrestricted X connection or the same Unix user. Luna's full administrative permission is separate from what a world actor may do. Spatial-M1 requires real OS/process confinement and native X workspace isolation. Token routing alone does not qualify those requirements. The reference kernel simulator does not supply production execution authority. Each required boundary needs independent production evidence before M1 acceptance.
