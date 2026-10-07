# Spatial conformance contract, version 1

This document and the acceptance suite specify observable behavior. Units, coordinate conventions, lifecycle semantics and independent evidence are normative. Suggested production file layout and server patch location are not.

## 1. Test-host transport

The executable is launched as `spatial-test-host --state-dir ABSOLUTE_DIRECTORY`. stdin/stdout carry UTF-8 JSON lines. stderr carries diagnostics. The harness creates a unique state directory per test and retains it as evidence. The host must confine managed world databases, worktrees and captures to that directory. Reference source repositories and explicitly requested test-process output files are separate paths inside the same test run directory.

Request: `{"id":1,"method":"world.get","params":{"id":"room-a"}}`.
Success: `{"id":1,"result":{...}}`. Failure: `{"id":1,"error":{"code":"NOT_FOUND","message":"...","details":null}}`.

Exactly one result/error per request. Responses may arrive out of order but must keep the request ID. A request has a deadline; never stall indefinitely. Reject non-finite JSON numbers, invalid shapes, invalid types, unknown fields on action payloads, and missing required fields. Diagnostic output on stdout is a transport error. IDs are integers, not booleans.

`system.hello {}` returns `protocol:"spatial-conformance/1"`, `implementation:"production"`, `build_id` identifying the actual build/commit, and a unique string-array `capabilities`. Do not label a test double production. An unknown method returns `UNKNOWN_METHOD`; a known method with malformed arguments returns `INVALID_ARGUMENT`. During development a well-formed but unimplemented method may return `UNIMPLEMENTED`; that fails its required gate.

By P02 the host forwards authoritative world operations to the actual world daemon. By P04 it forwards rendering to the actual compositor process. Pure geometry requests call the exact library functions used in production. All production child workers remain in the host's test-owned process group. P04+ uses the explicitly selected `SPATIAL_TEST_DISPLAY`; do not adopt an arbitrary operator display or silently replace its WM. Native tests may run on a dedicated software display, but hardware certification requires a native GPU session separately.

## 2. Coordinate and deterministic profile

Right-handed coordinates, meters, seconds, radians. +X right, +Y up, camera forward -Z. Pose is `{"p":[x,y,z],"q":[qx,qy,qz,qw]}`. Quaternions are unit length (tolerance 1e-5), finite and nonzero; reject nonunit inputs rather than silently guessing units. `q` and `-q` represent the same orientation. Local/body rotation is composed on the right of the current orientation.

The conformance fixture uses the normal production configuration loader to set this known profile: linear gain 1 meter/second per normalized axis, angular gain 1 radian/second, independent per-axis deadzone 0.05, axis freshness timeout 0.25 seconds, actor collision sphere radius 0.2 meters, interaction reach 3 meters, vertical FOV 90 degrees, unlit RGB panel colors, background RGB (10,12,16), no smoothing or acceleration. Those are fixture values, not permanently fixed UX requirements.

Deadzone: `f(x)=0` for `abs(x)<=.05`; otherwise `sign(x)*(abs(x)-.05)/.95`. Do not normalize a multi-axis translation vector to unit length. Each axis has its own physical meaning. Input values outside [-1,1] are invalid. SpaceMouse device units/signs must be calibrated into this canonical frame at the hardware boundary.

## 3. Pure geometry and motion methods

| Method | Parameters | Result |
|---|---|---|
| `geometry.transform_point` | `pose, point:[3]` | `{point:[3]}` |
| `geometry.inverse_point` | `pose, point:[3]` | `{point:[3]}` |
| `geometry.uv_pixel` | `uv:[u,v], pixels:[width,height]` | `{pixel:[x,y]}` or `{pixel:null}` |
| `geometry.ray_panel` | `origin:[3], direction:[3], pose, size:[width_m,height_m], pixels:[width_px,height_px]` | `{hit:{uv,pixel,distance}}` or `{hit:null}` |
| `geometry.sweep_sphere_aabb` | `origin:[3], displacement:[3], radius, bounds:{min:[3],max:[3]}` | `{hit:{fraction,position:[3],normal:[3]}}` or `{hit:null}` |
| `motion.integrate` | `pose, linear:[3], angular:[3], seconds` | `{pose}` |

A panel is centered at its pose, in local XY with front normal +Z. UV (0,0) is top-left, (1,1) bottom-right. A panel's right/bottom edges are excluded; left/top included. Pixel conversion is floor(u*width), floor(v*height), never rounding or clamping a miss into a target. Zero direction, zero/negative physical extents, nonpositive pixel dimensions are invalid. A ray direction may have any finite nonzero length and is normalized for a distance in meters. Parallel, behind-origin, back-facing, and out-of-bounds hits return null. Edge behavior must remain consistent between rendering, picking and input.

`motion.integrate` is exact constant body-twist rigid motion over the interval: `T_new = T_old * exp(twist * dt)`. Equivalent stable analytic SE(3) integration is fine. Translation during a simultaneous turn follows the arc; “translate with old rotation, then rotate” is not partition invariant. Use the proper small-angular-rate limit, not an unstable division by zero. Negative time is invalid; zero time is identity. This is a production pure function, not another motion engine for tests.

`geometry.sweep_sphere_aabb` is the same continuous collision primitive used by actor motion, exposed like the other pure geometry functions. Find the first contact along `origin + fraction * displacement`, with fraction in [0,1]. Return the sphere center at contact and an outward unit contact normal. Test the segment, not just its endpoints. Radius must be finite and positive; bounds must have strictly increasing finite components. A zero displacement gives no hit unless already overlapping, in which case fraction is zero. Initially overlapping cases require a deterministic documented separation normal. The baseline tests exercise face contact; extend edge/corner/overlap counterexamples when implementing the production function. Do not replace a sphere with a cube without documenting and testing the changed collision shape.

## 4. World methods and entity forms

`world.create {entity}` returns the created entity. `world.get {id}` returns one entity. `world.list {}` returns `{entities:[...]}`. `world.update {id, expected_revision, patch}` returns the updated entity. `world.delete {id, expected_revision}` returns `{deleted:true}`. `world.snapshot {}` returns a stable durable snapshot containing `world_revision` and durable entities in stable ID order. It must not contain wall-clock time, credentials, process IDs, live XIDs, file descriptors or presentation handles. `world.checkpoint {}` commits current actor poses/rooms and returns `{world_revision}`.

Each durable create begins at `revision:1`. Each accepted update increments that entity revision once and global `world_revision` once. Returned entities include both revisions. A stale expected revision gives `REVISION_CONFLICT`, including under concurrent writers. Validate the whole edit before committing any field. IDs, kinds and revision fields cannot be patched. Rejected requests do not change a durable snapshot. Missing ID: `NOT_FOUND`; duplicate ID: `ALREADY_EXISTS`; deleting a referenced/nonempty room: `IN_USE`.

Entity forms:

- `room`: `id,kind:"room",label`, optional `worktree` ID.
- `object`: `id,kind:"object",room,pose,label` (label optional). Default small visible object pick proxy. Acquiring an object checks visibility and reach. Once grabbed, a permitted transform edit can place its preview farther away; this baseline is an object-editing operation, not a fixed-length physical arm.
- `panel`: `id,kind:"panel",room,pose,size:[2],color:[R,G,B],label`. RGB integers 0–255, front-facing and opaque.
- `barrier`: `id,kind:"barrier",room,pose,bounds:{min:[3],max:[3]}`. Test barriers use identity pose and axis-aligned room-local bounds.
- `portal`: `id,kind:"portal",room,pose,size:[2],destination:{room,pose}`.
- `surface_slot`: `id,kind:"surface_slot",room,pose,size:[2],label`. A durable placement; it has at most one live attachment.
- `actor`: created/reconnected by `actor.open`, not arbitrary untrusted `world.create` requests. Its stable ID/pose/room can be inspected through management methods.

All non-room entities reference an existing room. Portal destinations and room worktree bindings reference existing corresponding resources. A room change changes a reference, not object identity. Human-readable labels are data, including Unicode and shell metacharacters. Runtime actor get/inspect returns current live pose; the durable snapshot/checkpoint boundary determines what survives a crash.

## 5. Actor lifecycle and canonical actions

`actor.open {id,room,pose,grants:[strings]}` is a privileged management/fixture method. It creates a fresh actor or reconnects an existing disconnected durable actor. A reconnect restores the saved room/pose, ignoring the initial defaults in the request, and does not reset it to the origin. It returns `{id,token}`. The new token is session scoped and bound to that actor. It is not a client-chosen actor identifier. `actor.close {token}` cancels motion and held input, rolls back active object preview, drops leases and revokes the token. `actor.inspect {token}` returns `id,room,pose,axes:[6],held_keys:[],held_buttons:[],focus` where focus is null or a current surface handle.

`actor.act {token,seq,op,args}` is the single public action boundary. Each actor has a strictly increasing positive sequence. Replayed/older sequences return `STALE_SEQUENCE`. A syntactically well-formed sequence is consumed even when policy/argument validation denies the action; subsequent commands must use a new sequence. Unknown/revoked token: `UNAUTHORIZED`. A payload cannot select another actor. Missing grant returns `FORBIDDEN` before looking at focus/target state.

| Op | Args | Grant and semantics |
|---|---|---|
| `input` | `axes:[tx,ty,tz,rx,ry,rz]` | `navigate`. Normalized body-frame rate input; updates the axis freshness deadline. |
| `stop` | `{}` | Always permitted for the actor. Immediately zero axes, release held native input, cancel object preview and release interaction leases/focus. Idempotent. |
| `aim` | `direction:[3]` | `navigate` or `interact`. Camera-local ray; return `{hit:null}` or `{hit:{id,generation,uv,pixel,distance}}`. Save the aim handle/geometry generation. Panels have generation 1; real surfaces use their live generation. |
| `pointer` | `button:1/2/3,pressed:bool` | `interact`. Use the saved visible aim and valid generation. First press acquires surface lease and focus. Missing target: `NO_TARGET`; changed geometry/resource: `STALE_TARGET`; another lease holder: `BUSY`. |
| `key` | `keysym:string,pressed:bool` | `interact`. Deliver to the actor's own focus; no focus gives `NO_FOCUS`. Update real X keyboard/modifier state. Fixture keymap is US. |
| `scroll` | `steps_y:integer` | `interact`. Current aimed surface; positive is up. Reference client receives balanced button-4 events for up and button-5 for down. |
| `unfocus` | `{}` | Release this actor's pressed inputs and surface lease; focus becomes null. |
| `object.grab` | `id` | `world.write`. One visible object within 3 meters; elsewhere `NOT_VISIBLE`, too far `OUT_OF_REACH`, already leased `BUSY`. |
| `object.move` | `pose` | `world.write`. Change the held object's runtime preview, not committed placement. |
| `object.release` | `commit:bool` | Commit the preview durably, or cancel back to the original pose; drop the lease. |
| `observe` | `{}` | Return `{actor,room,pose,world_revision,visible:[{id,...}]}` based on this actor's camera and occlusion, not all scene entities. |
| `git.worktree.create` | same as management method below | `git.write`; invokes the same production Git operation handler. |
| `process.launch` | `argv:[strings],operation_id` | `process.launch`; execute argv directly, cwd from current room's bound worktree; return `{process_id}`. No implicit shell. |

Pure panels are world objects, not real application input sinks. Do not claim a successful X click for a panel without an application attachment. Input tests use live surfaces.

Fresh rate input expires at exactly 0.25 seconds in the fixture. Split a large clock advance at that deadline. Unrelated queries do not refresh it. A new sample refreshes it. `clock.advance {seconds}` advances an injected monotonic test clock and waits for all resulting simulation work; it is a fixture-only clock boundary into the actual production scheduler. It is not exposed to ordinary world actors.

Focus/drag capture are per actor. A held-button drag stays on its original surface until release/cancellation even when aim moves elsewhere. Aim may change during capture, but actual captured motion remains routed to the original target. Destroy/unmap/actor disconnect releases or discards held state safely, never retargeting a release into another app. A room transition clears interaction with surfaces left behind. Another actor's focus, modifiers and pointer remain unaffected.

## 6. Rooms, portals and collisions

Rooms are distinct coordinate charts even when positions have equal numeric coordinates. Do not draw/select an unconnected room's contents through a wall or background.

A portal activates when an actor center crosses its rectangle from positive local Z to negative local Z. It does not activate on a reverse crossing or outside the rectangle. The crossing maps the remainder of the movement and orientation by:

`T_destination_pose * inverse(T_portal_pose)`.

There is **no implicit extra 180-degree turn** in this convention. A reciprocal portal explicitly uses the reversed entry/exit orientation. Retain the remaining displacement after crossing; do not drop it or apply a second full time step. Resolve a crossing once. A doorway is not a magic room button in the walkthrough tests.

The actor collision primitive is a swept sphere, radius 0.2 in the fixture. Solid barriers stop the normal component and preserve tangential sliding. A thin wall cannot be crossed merely because both sampled endpoints lie outside it. The portal is an aperture entity, not an overlapping solid barrier. Free-flight motion has no gravity in this baseline. Camera collision and visual walls must share the same world geometry; do not use invisible input-blocking exceptions to compensate for renderer errors.

## 7. Render and X surface methods

`render.capture {token,width,height}` renders that actor's view with the actual compositor renderer, fences its work and writes binary P6 RGB (max 255) in the state directory. Return `{path,frame_id,world_revision}`. Frame IDs monotonically increase within the runtime. Frames reflect all preceding acknowledged scene edits. `render.present {token,width,height}` submits the same view to the actual compositor output drawable, makes it viewable, fences it, and returns `{xid,frame_id,world_revision}`. The C witness independently captures that drawable via XGetImage. This tests the compositor output path, not physical display scanout; that is the hardware gate.

`surface.find {probe_id}` returns `{surface:null}` until a real mapped X window bearing `_SPATIAL_PROBE_ID` exists and is registered. Then it returns `{surface:record}`. This property belongs to the independent C test client; it must not cause a mocked registration. `surface.get {id}` returns the live record or `NOT_FOUND`. `surface.place {id,room,pose,size}` changes world placement without changing native root coordinates. If attached to a slot, update the slot's single authoritative placement. `surface.children {id}` returns `{surfaces:[records]}`.

A live record contains `id,xid,generation,room,pose,size,pixels:[w,h],mapped,parent_id,slot` (last two nullable). The ID includes or otherwise disambiguates session incarnation. Generation increases when its mapped backing resource or pixel geometry changes, including resize/remap. Old aim handles must not silently act on resized or replacement content. A destroyed live surface is no longer returned by get. `surface.validate {handle:{id,generation}}` returns `{valid:true}` for a current handle, otherwise `STALE_TARGET`.

`surface.attach_slot {id,slot}` attaches a live surface to an existing unoccupied durable slot and derives placement from the slot. It does not use a previous XID as durable identity. Another live attachment gives `BUSY`. A destroyed attachment leaves the slot intact. Automatic application state restoration is not inferred from a window title; a later launch supervisor uses explicit launch/slot identity.

Redirect application drawing into backing storage and import it into a GPU texture. Correct RGB channels, front/back orientation and top/bottom orientation are required. Handle asynchronous map/configure/damage/destroy ordering. A resize requires reacquiring the correct backing resource; do not free an in-flight GPU resource before its use is complete. The Composite and GLX protocols are documented in [S2,S3].

Native transients created with WM_TRANSIENT_FOR are attached to their parent surface, carry `parent_id`, and follow its room and world transform. This is a deliberately narrow supported application relation, not a promise about all legacy popup heuristics. A child moves through the parent's transform; a root-coordinate offset is not a second independent world placement.

`runtime.fence {}` waits for queued world commands, X input routing, native X lifecycle/damage handling and the corresponding render synchronization. A single no-op reply, a fixed sleep, or an XSync that leaves the compositor's pending event queue unprocessed is insufficient. It does not wait for arbitrary long-running child jobs.

`runtime.resources {}` returns actual resource bookkeeping including `retired_pixmaps_pending` and `invalid_texture_bindings`. They are secondary diagnostics, not independent proof of absence of leaks. The native soak gate also requires sanitizer/process-resource evidence.

`runtime.identities {}` returns the real `worldd_pid,spatialwm_pid,display` and may include server/build/device information. Workers must be real distinct processes within this test host's process group. Do not report the user's production services as test workers.

## 8. Git and child processes

`git.repo.import {id,path}` registers an existing local repository. Use real Git as the authority for references, commits, index and worktree status. Do not replace it with a branch-name table. A registry stores identity and operational context, not an independently writable imitation of Git history.

`git.worktree.create {repo,id,branch,start,operation_id}` creates a new branch at `start` and a real managed worktree inside the state directory. Return `{id,repo,branch,path,head}` with an absolute path. The branch name is validated with Git ref rules. Never force an already checked-out branch into a second writable worktree. Invalid reference: `INVALID_ARGUMENT`; already active branch: `BRANCH_IN_USE`. The operation ID is an idempotency key over a normalized request: same ID + same request returns the original result; same ID + different request gives `OPERATION_CONFLICT`.

`git.status {worktree}` returns `{head,branch,clean,modified:[paths],untracked:[paths]}` from actual current Git state. It must notice changes made by a separate Git process. Paths with unusual characters must be parsed using machine-readable/NUL-delimited Git output, not whitespace splitting.

`git.worktree.remove {id,operation_id}` refuses dirty/untracked files with `DIRTY_WORKTREE`, removes a clean worktree without deleting its branch, and records completion. Do not use forced deletion as recovery. `git.operation {operation_id}` returns at least `{state}`; successful/reconciled create is `ready`. Pending is not ready.

A room can bind to a managed worktree. `process.launch` runs the provided argv directly in that worktree and returns a stable process-record ID. Reject a missing worktree context explicitly rather than using the user's home directory. Duplicate launch requests within a running manager return the same process record, not another process. `process.wait {id,timeout_seconds}` waits for that actual child and returns `{exit_code}` (nonzero preserved); exceeding the supplied deadline returns `TIMEOUT`. Do not infer completion from an output file merely existing.

For an arbitrary launch interrupted around fork/exec, record/reconcile process identity using a supervisor and PID start time/pidfd where applicable. An unresolved state is `uncertain`, not “exactly once.” Do not automatically repeat arbitrary external side effects after losing their acknowledgment.

## 9. Fault and restart boundaries

`fault.arm {point,action,...}` is a fixture-only hook at a production boundary, disabled from the public actor API. It arms one occurrence, not a parallel simulator.

- `world.before_commit`, action `exit`: terminate before durable commit; old durable state survives.
- `world.after_commit_before_reply`, action `exit`: terminate after durable commit but before success reply; new state survives exactly once.
- `git.after_worktree_before_ready`, action `exit`: the real Git worktree exists, but final ready metadata/reply was not committed. Startup reconciles without creating a second worktree.
- `storage.next_write`, action `error`, `errno:"ENOSPC"`: inject the error at the actual persistence I/O boundary (e.g. SQLite VFS write), report `STORAGE_ERROR`, and retain the prior durable state. This is a fault-injection test, not a physical-full-disk claim.

A fatal production worker exit causes the conformance host to exit/close transport. Tests require an actual subprocess death, not a JSON message claiming it. The test harness kills only its own process group, restarts with the same state directory, and verifies the resulting state. This fatal test-host policy is not a ban on a production supervisor restarting a worker after safe cleanup.

After restart: no held input, old tokens, live-X assumptions, stale leases or uncommitted object previews. Durable room/object/slot state and acknowledged edits remain. An explicit actor checkpoint restores its saved pose; uncheckpointed motion is not promised. Git external effects are reconciled independently of database recovery.

## 10. Acceptance limits

This contract establishes a concrete vertical operating environment and its primitives. It does not certify unrestricted third-party toolkit behavior, malicious same-user isolation, every GPU driver, zero leaks without a soak, or physical SpaceMouse behavior from synthetic samples. P00–P10 and the separate hardware gate must be reported independently. Tests may expose a genuine contract error; use the reviewed correction procedure in AGENTS.md, never an unrecorded weakening.
