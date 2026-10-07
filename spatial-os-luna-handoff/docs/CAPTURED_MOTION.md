# Captured pointer motion

The invariant is native motion and release on the original surface while a button is held.
Motion coordinates may extend beyond that surface's bounds.
The implementation passes the automated P06 qualification on both authorized hosts.

## Protobuf migration 0.8.0

The predecessor acceptance lock SHA-256 is f7eecbbcc5c88956b0a2e29fa70b594edf7aeed309aede0eb9d19e3ceb8a662d.
Only the private compositor command schema changes in the acceptance lock.
Public actor action fields, locked assertions, and the independent native witness remain unchanged.
Add signed captured-motion coordinates and a flag to retain the native capture position.
Extend the separate private XLibre schema with the corresponding motion operation.
Both peers continue to validate protocol version, request correlation, and unknown fields.

## Coordinate policy

Intersect the actor's new aim ray with the original capture plane.
Use the actor camera orientation and the current surface transform.
Convert that intersection to signed surface pixels.
Do not clamp outside coordinates into the window.
An aim parallel to the plane, or pointing away from it, keeps the last native position.
Reject coordinates outside the native signed 16-bit local or root range.
A release uses the last accepted native capture position.
Additional buttons do not move capture back to the initial press position.

XLibre accepts motion only for the current captured target and generation.
Generate native motion with the actor's own device through the normal DIX event path.
Cancellation releases buttons at the last capture position.

## Scheduler and cancellation

Body motion also changes the captured ray.
The scheduler defers native I/O until the outer world loop.
The compositor wait callback only advances actor state.
It never starts a nested compositor request.
Manual clock advancement completes its pending native motion before replying.
The real-time regression requires native motion without query polling.
Unrefreshed navigation stops at the configured deadline.

If body motion produces an unrepresentable captured coordinate, cancel held input and motion.
Record the cancellation in the worldd error log.
A stale or unauthorized capture also cancels input.
Do not replay an uncertain native action.

## Explicit grabs and independent evidence

Ordinary X clients cannot explicitly grab the compositor-owned actor devices.
A checked XCB request verifies the actual BadAccess response.
The independent native client then verifies that motion and cancellation still reach the original target.
This is software evidence for the unsupported-grab policy.
It does not replace physical H04 acceptance or full workspace security.

The first grab witness used libXi's return status and an Xlib error callback.
That interface cannot establish this denial.
[libXi XIGrabDevice](https://raw.githubusercontent.com/mirror/libXi/master/src/XIGrabDevice.c) returns GrabSuccess when _XReply fails.
[Xlib _XReply](https://raw.githubusercontent.com/mirror/libX11/master/src/xcb_io.c) suppresses BadAccess before the application error callback.
The checked XCB witness preserves the requirement and inspects the real protocol error.
A discarded direct-Xlib witness also deadlocked while mixing public and internal locking.
That isolated witness process was inspected and terminated.
The production input bridge was not changed for that witness issue.

The checked witness uses the installed libxcb-xinput.so.0 library.
A libxcb-xinput-dev package was downloaded and extracted under local/p06-xcb-headers on sb16 to inspect declarations.
No package was installed. No system display or login setting changed.


## Final qualification

Both hosts passed 98 cumulative acceptance tests, 70 core counterexamples, and 39 harness checks.
Debug and AddressSanitizer/UndefinedBehaviorSanitizer builds passed.
Both private XLibre builds passed the upstream unit target and modesetting symbol check.
Both hosts passed 13 native input component checks.
The sanitizer claim covers the runtime and compositor, not the server.

An isolated mutation forced button delivery to use the initial press position.
The drag test rejected its release at (80, 60), where (200, 180) was required.
The main worktrees were not mutated.
The retained mutation source is a diagnostic worktree under local on sb16.

Retained evidence is in evidence/captured-motion-2026-10-07.
Reports identify actual commands, server paths and hashes, source inputs, and binary build identities.
Initial missing-motion diagnostics and discarded witness failures remain separately identified.
No locked acceptance assertion or native witness C source changed.

Run on sb16 or sb16-2:

~~~sh
python3 tools/build_xserver.py --evidence-dir local/new-capture-server
python3 tools/qualify_native_input.py --server-report local/new-capture-server/run.json --evidence-dir local/new-capture-native
python3 tools/qualify_core.py --through P06 --xserver /absolute/path/from/server/report/Xvfb --evidence-dir local/new-capture-debug
python3 tools/qualify_core.py --through P06 --xserver /absolute/path/from/server/report/Xvfb --sanitize --build-dir build-sanitize --evidence-dir local/new-capture-sanitize
~~~

Use new evidence directories.
Physical controls, native GPU presentation, application-group authority, and full workspace isolation remain mandatory later qualifications.
