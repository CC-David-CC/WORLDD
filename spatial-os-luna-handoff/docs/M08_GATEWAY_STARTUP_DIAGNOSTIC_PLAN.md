# M08 Gateway Startup Failure: Diagnostic Plan

Status: investigation note for branch `m08/actor-cabinet-gateway-error-revalidation-20261016`.
Base commit: `43b36a8123926592325c531c7c1d3bec6ebb5122`.

## Finding

The generic Git-worker status-1 log is not the startup failure.

`WorkspaceAuthorityTests.workspace()` imports a fixture repository and creates `branch-one` from `main`.
The new branch does not exist in that repository.
`ExecutionStore::create()` checks for it with `git show-ref --verify --quiet refs/heads/branch-one` in `src/workspace/store.cpp`.
Git returns status 1 with no diagnostic when the ref is absent.
`src/workspace/git_command.cpp` currently logs every nonzero Git status, including this expected negative check.

The focused Debug run still fails before its first world-management reply.
One method reached `world.management("system.hello")` and received EOF.
The captured world log shows authority cancellation, then actor-channel HUPs.
Those HUPs may occur during fixture cleanup after the management failure.
They do not establish why `worldd` closed its management pipe.

The log archive is outside the checkout:
`/home/dflanag3/spatial-artifacts/m08-actor-cabinet-gateway-error-revalidation-wt-20261017/runs/diag-worldd-evidence.tar.gz`
SHA-256: `3a61221528b9a95c1610a95a8509fe5a286510cf366809cf260b270b0390e817`.
The run receipt is in `runs/diag-worldd-one` under the same artifact root.

## Next diagnostic

Run one targeted Debug method through the existing resource, adapter, and display wrappers on an approved test host.
Set `SPATIAL_SECURITY_EVIDENCE` and `SPATIAL_SCOPE_EVIDENCE` to new external artifact directories.
Do not run ASan or the full registration until this diagnosis passes.

Add temporary test-only diagnostics at the `WorldProcess.management()` EOF boundary.
Record the exact `worldd` argv, PID, `/proc/PID/stat`, `poll()` result, and return code before fixture cleanup.
Record `fcntl(F_GETFD)` and peer-socket state for the management pipe and actor sockets.
Copy the raw world, broker, issuer, and adapter logs before cleanup.
Keep the existing assertion and test selection unchanged.

Bound startup diagnostics. Capture at most 128 worldd file-descriptor entries. Copy only this fixture's top-level logs and adapter-provisioning JSON. Limit collection to 48 files, 4 MiB per file, and 16 MiB total. Hash copied files incrementally. Record files that exceed a limit.

Compare these two outcomes:

- If `worldd` exited before EOF, preserve its exit status and raw stderr to identify the exit path.
- If `worldd` remained alive, inspect management-pipe ownership and the writer descriptors before teardown.

Then reproduce only the first failing setup in a focused helper test.
Keep the production code and locked acceptance contract unchanged until evidence proves a production defect.