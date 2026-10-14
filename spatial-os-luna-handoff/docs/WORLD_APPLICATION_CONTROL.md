# Authenticated application control

Worldd uses the production broker to authorize native application input.
Each reservation binds an actor to an immutable workspace, application, and native server identity.
Workspace selection is not required for control.
World entry, placement, and observation do not grant control.
The broker also checks the actor's effective execution rights against the application profile.
A read-only actor cannot control a writable application.
Unlabelled diagnostic displays retain their explicit interaction-grant checks.
That diagnostic path does not bypass broker authority for labeled applications.

The reservation covers the complete application group, including its popups.
Each group has one controller.
Different groups can have independent controllers.
The native server routes real X11 events through genuine actor devices.
The world does not synthesize application events or emulate files.

## Input and cancellation order

Worldd checks the aimed or captured surface and its generation.
It acquires or validates the broker reservation before input.
It checks the surface again after the broker wait.
The compositor client checks authority again before sending the first request byte.
A partially submitted request completes before cancellation. The world never replays it.

Broker notifications and issuer revocations remain serviced during component waits.
The same wait callbacks detect controller socket loss without dispatching another actor action.
They stop further input routing when authority becomes invalid.
Compositor exchanges cannot recurse.
An in-flight compositor exchange defers native teardown until that exchange returns.
The broker reservation remains held during that interval.

Cancellation releases native keys, buttons, modifiers, capture, and focus.
Worldd checks the compositor's cancellation result before acknowledging release to the broker.
It preserves the reservation if cancellation cannot be confirmed.
Broker release requests wait until any current broker exchange finishes.
The queued release keeps its original principal after the actor session closes.
If revocation arrives during cancellation, worldd completes pending native device teardown before releasing the reservation.

Grant restoration does not restore focus or held input.
The actor must acquire control again through an explicit action.
Revocation of control does not terminate independently authorized application execution.

## Tests and limits

tests/core/test_world_application_control.py uses real signed actor sessions and confined native C clients.
The C witness observes core X11 events.
An independent supervisor query checks the application's native device binding.
Ordinary confined clients cannot enumerate XI devices.

All 25 focused control checks passed on sb16 and sb16-2.
The final focused run also passed 20 existing identity checks and 16 observation checks.
The 25th control check covers credential revocation during an in-flight native cancellation.
They cover grants, read-only profiles, native typing, scrolling, popups, stale pixels, controller transfer, and independent applications.
They also cover actor and credential revocation, disconnects, blocked broker requests, and a paused compositor.
The paused-compositor test requires cancellation and forbids replay of queued input after revocation.

Both hosts passed full Debug and runtime ASan/UBSan qualification.
Each profile passed 891 core checks across 45 suites, 51 harness checks, and 151 P10 cases.
No final profile skipped a case.
See evidence/world-application-control-2026-10-08 for commands, logs, binary hashes, and the primary review.
Native-server sanitizer testing requires a GLX-enabled profile for these world tests.
The existing instrumented GLX cleanup issue remains outside this component's qualification.
Physical input, native GPU timing, filtered pixels, session restoration, and full Spatial-M1 acceptance remain incomplete.
