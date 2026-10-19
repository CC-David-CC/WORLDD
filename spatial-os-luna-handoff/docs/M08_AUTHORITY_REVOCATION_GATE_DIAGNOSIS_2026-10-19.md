# M08 authority revocation gate diagnosis

Status: source and retained-log diagnosis only. Branch `m08/auth-failure-diagnosis-20261019` is based on frozen source `07991d839bf839c4e95022dedbceb5535f19ddaa`. No build, test, or runtime command ran during this diagnosis. The fault-discovery checkpoint remains on its separate branch.

## Evidence and source identity

The retained logs are under `/home/dflanag3/spatial-artifacts/deployments/m08-07991d8-gate/runtime/evidence/` on each test host:

- `m08-07991d8-sb16-full-local-01/Debug/core.log`
- `m08-07991d8-sb16-2-full-local-01/Debug/core.log`

Both hosts record CTest #123 failing `test_credential_revocation_during_fence_returns_checked_refusal_and_preserves_process` and `test_scoped_authority_denies_after_issuer_actor_revocation`. On sb16 these appear at `core.log` lines 4311-4331; on sb16-2 they appear at the same line numbers. Both retained #125 runs also fail the scoped-authority case at lines 4394-4400. The traces show two different symptoms of the same observation-cancellation policy.

SHA-256 checks match the deployed source on both hosts to the frozen 079 worktree for `tests/core/test_workspace_authority.py`, `tests/core/test_world_authority.py`, `src/workspace/main.cpp`, `src/workspace/authority.cpp`, and `src/workspace/store.cpp`. The checked-out production and fixture sources therefore match the sources named in the CTest trace.

## #123 credential-revocation race

The exact failure in both #123 logs is at `test_workspace_authority.py:967`: the test expected a deterministic in-flight refusal, but received a successful authorization fence (`request_id: 2`, `epoch: 3`). `fence_with_checked_credential_retry()` therefore returned no retry.

This is not an untriggered or mistimed fixture. The callback revokes the credential and verifies `credential_revoked=true` while `actor_revoked=false` (`test_workspace_authority.py:921-943`). The barrier waits for this broker to consume the corresponding watcher revision before the held issuer status response can continue (`:519-536`, `:938`). The operation starts before arming the relay (`:954-965`).

The request carries only `ActorRef`; `AuthorizationFenceRequest` has no credential or session identity. The watcher increments `cancellation_sequence` for each revocation event (`src/workspace/authority.cpp:171-181`). A credential event does not mark its actor revoked. `WorkspaceAuthority::authorization_fence()` refreshes actor subjects and returns a fence while the actor remains active (`:445-455`).

`workspaced` snapshots the sequence before dispatch and checks it after enforcement (`src/workspace/main.cpp:406-422`). When it changed, `revocation_result()` treats authorization fences as observations. It rechecks the actor and redispatches the observation when the actor remains active (`:139-163`). That designed retry produces the successful epoch-3 reply. It conflicts with the test's required first refusal followed by an explicit fresh request.

## #125 actor-revocation response

The scoped-authority test's relay confirms that issuer revocation was delivered. Its in-flight reply has `UNAUTHORIZED` and no scoped-authority payload, but the exact message is `Requester identity was revoked during broker observation`. The oracle requires `Issuer revocation invalidated the in-flight broker result` (`test_workspace_authority.py:790-807`).

The message comes from `revocation_result()` after its post-dispatch actor fence detects revocation (`src/workspace/main.cpp:148-159`). The generic message is currently used only for non-observation requests (`:164-167`). The denial itself is fail-closed. The mismatch is the production error contract, not a weaker authority result or fixture setup failure.

## Proposed fix and regression

Keep existing assertions and errors. `revocation_result()` now preserves an existing error and the management, control-release, and file-action exclusions. Otherwise, a changed captured cancellation sequence returns a cleared `UNAUTHORIZED` reply with the canonical message and original request ID. The broker no longer rechecks or internally redispatches an observation after cancellation. All three call sites use the reduced helper signature.

This makes both tests deterministic. The credential case receives the required first refusal, retries as a new request, gets the current fence, and proves the actor-owned process continues. The actor-revocation case receives the same typed refusal; its subsequent settled scoped check still proves access is denied. No application process is stopped by refusing an observation. No oracle or acceptance lock change is needed.

This policy cancels an in-flight observation on any consumed issuer revocation, including an unrelated actor's event. The current request schema cannot bind a request to the particular credential that authenticated its caller. A narrower credential-specific rule would require an explicit credential or authenticated-session field and a separate protocol review. A caller must retry. A fresh request rechecks current authority and succeeds when its actor remains authorized. This is a conservative global watcher invalidation policy.

The existing credential and actor-revocation assertions remain unchanged. A new deterministic regression revokes a second actor credential while the first actor's fence is pending. It checks the canonical in-flight refusal, successful explicit retry, and unchanged process PID, start time, argv, and completion. Its CTest registration is `workspace-authorization-fence-unrelated-revocation`.

Source changes are unqualified. No build or test ran. Static review used `git diff --check` only. After primary releases the lane, rebuild `workspaced` and run Debug and SAN `workspace-authority`, `workspace-authorization-fence`, `workspace-authorization-fence-scoped`, `workspace-authorization-fence-unrelated-revocation`, and the existing credential and actor-revocation cases on both hosts. The target-only source build target is `workspaced`; its test bundle also needs the updated Python fixture and CTest metadata. The build/package must be refreshed before gate reruns.
