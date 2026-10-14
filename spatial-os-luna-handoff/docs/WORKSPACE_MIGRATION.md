# Workspace contract migration 0.13.0

This migration corrects the P08 execution contract before its implementation.
The consolidated project instructions assign execution authority to workspaces.
The older contract assigned process working directories to rooms.
Room entry must not grant authority or select a workspace.

The predecessor is commit d305fd86c4d9c6f9494ea450389dc5749038e1c8 and acceptance lock 0.12.0.
The companion workspace-migration-0.13.0.json records exact hashes, changed files, test renames, and added tests.

## Preserved requirements

All 141 previous acceptance requirements remain represented.
Two test identifiers change to describe explicit workspace selection.
The process-directory test now requires the real workspace directory mounted at /project.
Its literal argument and real child-output checks remain.
The walkthrough selects a workspace explicitly before crossing a portal.
Its native input, file output, Git branch, return pose, and focus checks remain.

Git imports now create managed local bare backing repositories.
Independent Git commands inspect those backing stores instead of the unchanged import source.
The recovery witness still checks the actual worktree registration across a crash.
Dirty-file protection, branch exclusion, external commits, and effect reconciliation remain required.

P00 through P07 assertions remain unchanged.
Independent Python oracles and native witness sources remain unchanged.
No failed requirement becomes a skip, TODO, or simulated success.

## Added counterexamples

Ten P08 tests require these behaviors:

- Separate workspace creation, membership, availability, selection, and geometry.
- Deny workspace access from world presence or global fixture grants alone.
- Preserve selection through portal crossing without restarting runtime processes.
- Clear selection after unavailability or membership revocation.
- Prevent independently writable workspaces from sharing writable Git administration data.
- Run concurrent workspaces at /project in distinct mount namespaces.
- Confine child processes and enforce no_new_privs.
- Intersect application profiles with actor authority before allowing writes.
- Run ordinary Git commands against the actual confined worktree.
- Reject a launch retry that changes the resolved workspace.

Four transport self-tests check explicit false values, structured rights, argv boundaries, and exit-status presence.
The inventory contains 151 acceptance tests and 140 primitive tickets.
It preserves all twelve generated signed-axis tests.

## Production boundary

The production workspaced broker owns execution identities, Git backing stores, and application process trees.
Worldd owns world availability and each actor's workspace selection.
The broker registry remains independent of rooms and world identity.
The fixture requires a real broker process within its owned process group.
Fixture membership methods do not complete cryptographic identity or delegation acceptance.

Each independently writable workspace needs private writable Git metadata.
Separate bare repositories may import the same authorized upstream.
Low-level administrative tests can create linked worktrees within one backing repository.
Those tests do not authorize binding independent workspaces to that shared writable backing store.

Acceptance uses temporary local import sources.
Production workspaces still require box-git as their shared remote.
Acceptance does not publish fixture repositories to box-git.

## Review and evidence

The primary coding agent reviewed this correction under David's hardware-failure waiver.
The configured local model was unavailable. No local-model review is claimed.
The correction follows the consolidated workspace instructions and adds restrictions to execution.
It does not weaken the earlier Git or native-input guarantees.

The predecessor baseline on sb16 passed 39 harness checks and all 113 P00-P07 acceptance tests.
Its old process-directory diagnostic stopped at git.repo.import because no protobuf schema existed.
That diagnostic did not dynamically exercise room-based execution.
The contradiction is in the old normative contract and test source.
Baseline records remain in local/p08-workspace-contract-baseline.

Namespace prerequisite probes ran on both authorized hosts.
Passwordless sudo and privileged mount/PID namespace creation succeeded.
Unprivileged user-namespace creation failed while writing uid_map.
Both hosts have bubblewrap 0.9.0.
These observations do not qualify confinement. No host configuration changed.

The new protobuf interfaces return explicit UNIMPLEMENTED errors until production effects exist.
Both hosts passed all 113 P00-P07 acceptance tests in Debug and ASan/UBSan builds.
Each run also passed 135 core checks and 43 harness checks.
The migrated real-worktree diagnostic reached production and returned UNIMPLEMENTED at git.repo.import on both hosts.
Current acceptance coverage is 113 passed, one error, and 37 unrun.
P08 remains incomplete. No workspace execution or confinement success is claimed.

Evidence is in evidence/workspace-contract-2026-10-07.
Each host directory includes commands, build identities, source hashes, logs, acceptance reports, and namespace prerequisites.
The sb16 baseline directory preserves the predecessor diagnostic.
Final source audits verify all 151 collected IDs and 140 primitive tickets.
They also verify all twelve generated signed-axis tests.

Qualification pinned 85 files per build.
After qualification, generated JSON files received LF line endings.
Only acceptance.lock.json belongs to the pinned source set.
The other 84 pinned files remain byte-identical to their qualified inputs.
Final audits prove the lock changed only from CRLF to LF, with identical JSON contents.
The original qualification lock is retained. Harness checks and the P08 diagnostic ran again after normalization.
All 85 final source hashes match local files and both hosts.

The commands are recorded in each run.json and final/source-audit.json.
The cumulative command used tools/qualify_core.py --through P07 with each host's explicit private XLibre server.
Sanitizer runs used --sanitize --build-dir build-sanitize.
The targeted gate selected test_create_is_a_real_git_worktree through P08.

Tests used authenticated disposable software displays.
Sanitizers covered the runtime and compositor, not the XLibre server.
Workspace execution, full Spatial-M1, native GPU timing, and physical controls remain incomplete.
No operator-session or system configuration changed.
Next: implement git.real_worktree through the production workspace broker.
