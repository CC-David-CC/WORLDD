# Hardware and native-session acceptance — mandatory, initially UNRUN

These are target-machine procedures, not claims that hardware was tested while authoring the archive. P00–P10 passing is necessary but insufficient. Run this gate on the selected native X11 server with real display output and the user's actual SpaceMouse. Keep recovery access working.

Use `examples/hardware-report.example.json` as a report template. Each item is `not_run`, `blocked`, `failed` or `passed`; attach actual evidence paths, source/base commit and device identifiers. Missing evidence never counts as passed. A target-specific script may automate these procedures; it must exercise the production input/rendering paths and retain measurements.

## H01 — Native ownership and visible live surfaces

Start the selected X server and spatial session without a second window manager/compositor. Record the actual executable paths, full source commits, X extensions and GL vendor/renderer. Launch the independent XI2 witness. Physically observe its live quadrants on a 3D surface, then change unpredictable paint values, resize and unmap/remap it. Compare capture pixels, independent X drawable readback and the visible display. Move behind it and verify the documented backface policy. No ordinary 2D desktop or launcher may substitute for the world.

Reject a claimed GPU result obtained only through Xvfb, a software renderer or pre-rendered frames. Software rendering may be a diagnostic result, clearly labeled.

## H02 — Physical SpaceMouse calibration and complete axes

Record USB/device identification, driver path and libspnav configuration. Record raw samples before choosing the device-to-canonical mapping. For each of tx, ty, tz, rx, ry and rz, physically deflect positive and negative: twelve trials. Record raw sign/magnitude, canonical sign, actor displacement/rotation and a short capture. Test combinations as well as isolated axes. Translate in the rotated actor's local frame. [S7]

Do not infer the raw axis ordering from names alone. The canonical test convention is +X right, +Y up, -Z forward, quaternion xyzw. A device calibration boundary is a real domain boundary; conversions scattered through movement code are not.

Verify button press/release, selection, cancellation and no accidental mapping of controller buttons to permanent held mouse buttons. Report actual noise/deadzone behavior and chosen production gains. Fixture gains are for reproducibility, not a requirement to keep an uncomfortable user setting.

## H03 — Zero input, deadlines and unplug recovery

At rest, observe pose and raw input for at least one minute. Record drift rather than asserting it by inspection. With motion active, stop the input stream, disconnect the client, unplug the device and kill the input bridge in separate trials. The canonical motion command expires at its configured TTL; camera movement stops and held interaction is released. Verify again with a real monotonic wall clock, not only `clock.advance`.

Reconnect the device. It must require fresh input; no stale velocity, old button state or reset to an unrelated room is allowed. Press the emergency cancel input during simultaneous translation, rotation and a held GUI modifier. Verify a subsequent key on the real native witness is unmodified.

## H04 — Multiple actors with real independent input

Run a human and two agents on separate native witness surfaces. Interleave motion, text, drag, scroll and room changes. Native logs must identify distinct actor devices and correct targets. Keep one actor's Shift held while another types. Cancel or disconnect the first and verify the other is unaffected. A shared invisible pointer temporarily moved between surfaces fails.

Exercise a device-specific explicit grab in a small native client or an extended independent witness; verify capture/release and cancellation for the chosen supported XI2 contract. This explicit-grab scenario is an additional target requirement, not a claim that the baseline probe already creates XIGrabDevice requests. Reject unsupported grabs explicitly rather than delivering events to a random surface. One live surface has one interactive lease unless a later collaboratively aware client contract deliberately extends it. [S4]

## H05 — Git world walkthrough

Create two branch rooms backed by different real worktrees. Enter them by moving through doors, not by a test-only room setter. Launch a real child from each room and inspect its cwd/files externally. Let two agents change the same relative filename in the two worktrees; verify isolation. Show the actual Git status/commit in the world. Return to the original room, and verify stable object placement and cleared stale focus. Interrupt a create after its Git side effect and verify one reconciled worktree, not a duplicated branch or force deletion. [S5]

## H06 — Worker, server and login recovery

Kill the renderer, then world daemon, in separate trials. Check no held key/button or motion survives, the supervisor reports the actual failure, and acknowledged durable state can be reopened. Restart the X server: old live surfaces/XIDs/actor tokens must be invalid. Restore configured applications by explicit launch/slot identity, never by treating a reused XID as the old application.

Perform a controlled native-session logout/login and a cold reboot. Demonstrate the intended spatial session is the actual login environment and a configured replacement live window reattaches to its durable slot. P09 checks stable slots but does **not** implement or certify this launch supervisor; Luna must implement it for this native-session gate.

Record the recovery commands and any genuinely uncertain external process effect. Do not promise arbitrary process execution is exactly-once across a crash. Database transactions alone cannot make external Git/process/X effects atomic. [S6]

## H07 — Display change, resource soak and real timing

Change the actual display mode/resolution and resize the output. Rendering and ray-to-pixel targeting must remain aligned. Run repeated actual surface create/resize/unmap/destroy cycles while two actors operate. Use at least 1,000 lifecycle cycles and a 30-minute native session for this initial acceptance budget; increase it when failures or usage require. These are minimum test workloads, not proof of indefinite reliability.

Retain cycle count, RSS/resource trend, GPU-memory observations where available, relevant sanitizer output, frame-time samples and input-to-visible-response samples. Establish an actual machine-specific performance target with the user; until one exists report measured distributions, not an invented pass threshold. Any growing unreclaimed resource pattern, invalid binding, crash or input corruption fails correctness regardless of average FPS. Distinguish driver allocations/caches from a reproduced application leak.

## H08 — Driver/helper operation and final evidence

Demonstrate Luna running a bounded helper ticket, reviewing the patch, executing the selected test and cumulative gate, and preserving evidence. Simulate helper unavailability: Luna must retain the current known state and avoid inventing an answer or accepting an unreviewed patch. No throughput benchmark is implied by helper availability.

Final status includes the exact test suite result, native server build pin, device mapping, hardware trial results, unresolved limits and recovery instructions. The delivery is incomplete until this report shows the target-native gate passing; do not hide unrun items behind the automated test count.
