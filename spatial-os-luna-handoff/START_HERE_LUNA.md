# Start here, Luna

You are the driver. Build the runtime to satisfy the supplied tests. Keep each step small enough to explain with one invariant and one failing assertion.

## First session

1. Read `AGENTS.md` and `docs/ARCHITECTURE.md`.
2. Inspect the machine without changing the desktop:
   `python3 tools/inspect_machine.py --output evidence/machine.json`.
3. Run `python3 -m unittest discover -s tests/harness -t . -v`.
4. Run `python3 tools/gate.py --through P00`. It must report BLOCKED because there is no production adapter yet. Do not fix that by supplying a mock.
5. Read P00 in `tasks/INDEX.json` and the first contract sections.
6. Create the initial C++ project with a `spatial-test-host` executable and shared production library. For P00, implement line framing, typed validation errors, request IDs and version/build reporting. The initial host can link the production core directly; by P02 world actions go to `worldd`, and by P04 graphics go to `spatialwm`.
7. Make P00 pass. Move to P01 geometry. Do not start with a complete renderer or fork the whole server before the primitive contracts are established.

## One primitive at a time

```sh
python3 tools/gate.py --through P01 --case test_yaw_turns_forward_to_left \
  --sut '["/absolute/path/to/build/spatial-test-host"]'
```

Read the assertion and implement the production function.
Run affected checks during development. Run the cumulative suite at the phase gate:

```sh
python3 tools/gate.py --through P01 \
  --sut '["/absolute/path/to/build/spatial-test-host"]'
```

Do not paste all tests into one helper request. `tasks/INDEX.json` names small tickets. A file under `tasks/primitives/` contains the acceptance test IDs for one primitive. Add only its required source context.

## Driver checkpoint

Use an ordinary JSON file at `local/driver-state.json`:

```json
{
  "phase": "P01",
  "primitive": "geometry.inverse",
  "commit": "actual commit here",
  "last_command": "actual test command here",
  "last_report": "evidence path here",
  "status": "in_progress",
  "next_test": "exact test ID here",
  "failed_attempts_on_same_invariant": 0,
  "helper_review": null,
  "blocker": null
}
```

On resume, verify current source, saved evidence, and live jobs.
Select the next affected check. Do not restart the full gate solely because work resumed.
A checkpoint is a navigational aid, not proof. The root AGENTS.md defines the current test schedule.

## Code layout to start with

```text
src/core/       vectors, poses, rigid-body integration, intersections
src/world/      authoritative state, actor actions, transactions, effect journal
src/render/     scene draw, frame capture, camera projection
src/x11/        WM ownership, surface resources, input/server bridge
src/platform/   processes, SpaceMouse, monotonic clock
src/testing/    thin conformance host and fault/clock adapters
server/        pinned XLibre checkout and a small maintained patch series
```

Do not force every feature into every folder. Small modules with clear ownership beat generic frameworks. The directory suggestions are not public APIs; `docs/CONTRACT.md` defines the observable boundary.

## Stop-and-escalate cases

Use the helper for a concrete analysis when coordinate conventions conflict, an X request cannot express the actor-local routing invariant, a live texture is stale after resize, a concurrent action steals another actor's focus, or a crash leaves an external effect ambiguous. Give the helper the reproducer, not “fix everything.”

An unsupported operation is an explicit failure while its phase is incomplete. It is not a reason to implement a fake success path.
