# P10 production walkthroughs

P10 qualifies three scripted actor workflows through the existing production runtime.
The source commit is b94ec312d56e344b47378709da79b26619e1088c.
No runtime, protobuf schema, acceptance assertion, or native witness changed for this qualification.

## Workflows

An actor observes a live surface, aims at it, and clicks through the canonical actor action boundary.
The independent XI2 client records the actual target pixel.
Its received input agrees with observation, picking, and focus.

A second story closes an actor while it holds Control.
Another actor acquires that application and types an unmodified letter.
The native client verifies the text received through the X server.

The complete walkthrough creates a real Git worktree and an explicitly authorized workspace.
The actor selects that workspace before crossing a portal.
It uses a real application surface and launches a confined process through the production broker.
The process writes its result inside the workspace project directory.
The original repository remains unchanged.
The actor returns through the reciprocal portal with its workspace selection intact and stale application focus cleared.
Independent filesystem and Git checks verify the result and branch.

## Results and source evidence

Both sb16 and sb16-2 passed all 151 P00-P10 acceptance cases in Debug and ASan/UBSan builds.
Each run reports zero failures, errors, skipped cases, and unrun baseline cases.
All three focused Debug stories also pass on both hosts.
The new Debug baselines reran 51 harness checks and all 148 preceding P00-P09 cases.

All 108 source hashes match both hosts and the reviewed production files.
Core sources did not change after P09 qualification.
Its 235 core checks and 51 harness checks passed in both build profiles on both hosts.
The source audits link those retained results and their exact report hashes.
They distinguish retained qualification from newly executed P10 acceptance runs.

Evidence is in evidence/walkthroughs-2026-10-07.
Each host directory contains commands, reports, source hashes, build identities, and independent native event logs.
The preceding core evidence remains in evidence/recovery-complete-2026-10-07.
Credentials, runtime databases, and RPC session-token traces are excluded from committed evidence.

## Mutation witness

An isolated sb16 worktree omitted release_input from Actors::close.
The mutation left the application's lease with the closed actor.
The locked cancellation story failed with BUSY when the next actor attempted control.
The witness exited with status 1 and executed exactly one case.
Its build, test commands, source hashes, report, and native events are retained under sb16/mutation and sb16/native/mutation.
The mutation never entered the production checkout.

## Commands and recovery

Use the project-built private XLibre server recorded in each run.json.
Set SPATIAL_TEST_XSERVER to that executable.
Set SPATIAL_XPROBE to the matching build's spatial-xprobe.
Set PYTHONPATH to the build's generated Python bindings and this project directory.
Set SPATIAL_EVIDENCE_DIR to a new evidence directory.
Run this command from the project directory:

~~~sh
python3 tools/with_test_display.py -- python3 tools/gate.py --through P10 \
  --sut '["/absolute/path/to/build/spatial-test-host"]' \
  --report /absolute/path/to/new-report.json
~~~

Use build-sanitize paths for the existing ASan/UBSan build.
The retained command records contain the exact paths and arguments used on each host.
The wrapper starts an authenticated private software display and cleans up that test display.
Normal login, GNOME, SSH, and TTY recovery remain available.
No system setting or persistent user world changed during these runs.
Use RECOVERY_BOUNDARIES.md for production world recovery.

## Qualification limits

These stories use fixture actor identities and scripted controls.
They do not qualify cryptographic enrollment, provider-driven actors, cabinet operations, or complete workspace X isolation.
They do not qualify GPU output, physical keyboard/mouse/SpaceMouse behavior, or the native soak.
Mandatory M01-M16 and H01-H08 remain unfinished.
The complete baseline is necessary for Spatial-M1. It is not full Spatial-M1 or LOCAL_READY.
Local-model review remains waived under David's hardware-failure instruction.
Next: the mandatory M01 identity qualification contract and cryptographic enrollment.
