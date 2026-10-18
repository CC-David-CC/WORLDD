# M08 native gateway three-failure diagnosis and fixture repair

Date: 2026-10-18

Checkout: `/home/dflanag3/src/m08-gateway-three-failure-diagnosis-20261018`
Branch: `m08/gateway-three-failure-diagnosis-20261018`
Base: `a958cd721e0a129856eef7f2f633d25b19462dcb`

No production code or the root-empty test was changed. The two fixture instrumentation repairs are implemented but await primary review. No native runtime test was run from this checkout.

## Original host evidence

The unchanged 24-case actor gateway suite ran on both qualification hosts with the 63-C++ binary package and explicit `a958` fixture overlay. Each host reported 21 passed and three failed. The selected actor-copy case passed. Helpers were restored and child processes retired.

- sb16: `/home/dflanag3/spatial-artifacts/m08-native-gateway-preparation-repair-20261018/debug-preferred-24-a958-run-01/stdout.log` (118.409 seconds)
- sb16-2: `/home/dflanag3/spatial-artifacts/m08-native-gateway-preparation-repair-20261018/debug-preferred-24-a958-run-01/stdout.log` (154.196 seconds)

These runs are preserved. This checkout has not rerun them.

## Root-empty browse remains open

Test: `tests/core/test_actor_cabinet_gateway.py:116-125`.

The inherited `workspace()` fixture creates a registered Git worktree. `GitRepo` commits `README.txt`, and the worktree contains `.git`. The file engine exposes both. The test expects zero entries. Do not change that count or hide either entry.

The independent source review found no honest fixture-only way to make the registered project root empty at this source pin. ExecutionStore validates the in-tree `.git` registration. Production also creates copy-staging state inside the project. A supported control is an actually empty child directory inside the registered project. That proves empty-directory browse/create/retry/read behavior but does not prove an empty root cabinet.

Keep this acceptance unresolved. A root-empty implementation needs a separately reviewed production layout contract for Git administration, root registration, Git operations, copy/restore, restart validation, and staging placement. The existing empty-root oracle remains unchanged.

Independent review note: `/home/dflanag3/src/m08-empty-cabinet-contract-review-20261018/docs/M08_EMPTY_CABINET_CONTRACT_REVIEW_2026-10-18.txt`.

## Wrong registered-workspace binding

Test: `tests/core/test_actor_cabinet_gateway.py::test_actor_cabinet_gateway_rejects_wrong_registered_workspace_binding`.

The old hook corrupted the first `workspace_get`. Cabinet visibility observation performs an earlier lookup through `workspace_file_access`, before the separate dispatch-binding lookup. That did not test the production dispatch guard.

The test now validates the initial projection sequence, including workspace identity, principal, registered worktree, allowed `FILE_BROWSE`, confirmation lookup, rendered-observation workspace checks, and matching observation fence. It then corrupts the next workspace lookup, which is the dispatch binding. The existing oracles remain: top-level `STALE_TARGET`, no file action, and no write.

The same fixture first performs a fresh authorized create with unmodified binding replies. It asserts the dispatch and file action occur and the real file contains the requested bytes. That control distinguishes an effective rejection from a request that never reached dispatch.

## Final post-operation revocation window

Test: `tests/core/test_actor_cabinet_gateway.py::test_actor_cabinet_gateway_revocation_in_final_post_operation_window_is_uncertain`.

The old hook treated the first post-effect lookup, `FILE_CREATE`, and authorization fence as the operation-specific final check. Those requests belonged to cabinet projection during `observe`. A create-only actor's full projection prefix is distinct from final validation:

1. The file action completes.
2. Projection checks `WORKSPACE_ACCESS` with empty components.
3. Projection resolves the workspace binding.
4. Projection denies `FILE_BROWSE` and `FILE_READ`.
5. Projection allows `FILE_CREATE`.
6. Projection confirms the binding and checks rendered-observation workspace authority.
7. Projection reaches its observation fence.
8. The operation-specific path performs another workspace lookup.
9. It performs the exact `FILE_CREATE` check, confirms the binding, and reaches the final fence.

The fixture now recognizes this ordered request/reply sequence. It validates workspace, principal, worktree components, every allow/deny result, binding identity and revision, and fence epochs. It holds only the final authorization fence. The existing successful effect, real revocation, bounded waits, `UNCERTAIN` result, operation id, and no returned metadata assertions remain.

`gateway_trace.py` now classifies real broker protobuf requests and reply oneofs. The scenario hook uses the same classifier for the final operation-specific `FILE_CREATE` check and terminal-state rejection. The separate host-independent controls exercise real protobuf create and edit authority checks, a final-fence request, wrong workspace/principal/scope, attempted file-action counting, missing final create, and duplicate terminal checks.

## Selected static checks and evidence

- Run `python3 -m unittest test_actor_cabinet_gateway_instrumentation -v` from `tests/core`, with generated Python bindings on `PYTHONPATH`, through the installed `scripts/run-command.sh` in a new tmux session. This exercises protobuf request/reply classification and negative controls without launching the native runtime.
- Run Python syntax compilation for the changed test and helper files through the same runner. This checks syntax only.
- Do not run actor gateway integration cases on middle-child. Primary must review these changes before native runtime reruns.

The earlier six-control run at `instrumentation-02` is superseded. It passed generic string-sequence and tuple checks but did not exercise real request classification. The first protobuf-backed run at `instrumentation-06` found a faulty missing-create control and failed. The corrected nine-control run at `instrumentation-08` passed; `command.exit` and `wrapper.exit` are both zero and `terminal-status.txt` is `COMPLETE`. Evidence is in `/home/dflanag3/spatial-artifacts/m08-native-gateway-diagnosis-static-20261018/instrumentation-08`. Static checks do not qualify the fixture changes or M08.

Full M08 remains unrun. The 11 crash cases remain pending.