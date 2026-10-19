# M08 workspace-authority credential callback repair - 2026-10-19

Status: UNQUALIFIED fixture correction. No production protocol, authority rule,
lease behavior, process lifetime, timeout, or assertion changes.

## Failure and selected regression

The sb16 Debug `WorkspaceAuthorityTests.test_unrelated_revocation_refuses_inflight_fence_then_allows_retry_and_preserves_process`
failed in its revocation callback. The callback read `credential_id` directly
from an `EnrollmentRecord`; the actual protobuf stores the signed `ActorCredential`
payload under `EnrollmentRecord.credential.content.payload`. The callback raised
`AttributeError`, so the relay returned an issuer transport failure instead of
the expected credential-only revocation response. The existing retry, authority
fence, and live-process assertions remain unchanged.

The selected runtime regression is that exact test method. A focused protobuf
fixture check verifies that the signed nested `ActorCredential` matches the
claims returned by the existing enrollment verification helper. Primary owns
Debug and ASan-UBSan execution on both test hosts. Runtime results remain UNRUN
on this source worker.

## Scope

Only `tests/core/test_workspace_authority.py` and this note belong to this task.
The correction reads the real parsed protobuf claims. It does not change issuer
semantics or security policy.
