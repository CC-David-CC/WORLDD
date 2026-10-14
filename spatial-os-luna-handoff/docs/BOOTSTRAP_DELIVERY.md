# Authenticated bootstrap delivery

Status: qualified, integrated, and pushed to box-git as d4b025e. Both hosts adopted the qualified source.
This contract extends BOOTSTRAP_PROFILES.md without changing locked acceptance assertions.

## World delivery

World authentication completes the issuer proof before applying any bootstrap template.
The actor cannot choose a grant recipient or supply template terms.
Worldd reads the credential's exact issuer profile through the checked issuer client.
It applies only templates for the current world identity.
Private admission still requires active WORLD_ENTER authority after application.
Public admission does not turn a missing template binding into action authority.

The reply can include bootstrap_attempt alongside an admission failure.
This report is available only after valid actor proof.
Each template reports a stored grant or a typed failure.
A stored grant may already be revoked. The report does not authorize an action.

## Workspace delivery

Workspace application requires an explicit authenticated actor request.
The world forwards that channel's principal and signed credential through the existing private broker channel.
The broker verifies the issuer signature, registered credential, and matching actor identity.
The broker reads the credential's exact profile and applies only matching workspace templates.
Application does not change workspace availability, selection, or cabinet placement.

Binding administration uses the same authenticated actor channel.
The broker reads the selected profile and requires the caller's ordinary parent delegation authority.
The administrator's own enrollment profile does not constrain which profile it may bind.
Bind and revoke require an actor sequence and expected authority revision.
Apply requires an actor sequence. Durable store receipts separately prevent duplicate grants.

All three new broker operations carry the channel's signed credential.
The actor cannot provide a replacement credential through the world request.
BootstrapBindRequest uses credential field six. BootstrapRevokeRequest uses credential field five.
BootstrapApplyRequest uses credential field three.
These additions affect draft broker messages only. Existing field numbers remain unchanged.

## Revocation and process lifetime

Check credential registration before issuer profile retrieval.
Keep the in-flight credential visible to the broker revocation watcher.
Refresh the credential and complete parent ancestry against one observed issuer security revision.
Reject changed snapshots before mutating the domain store.
Drain revocation events before each domain mutation.
Check the world actor channel after broker waits before returning a result.

Credential revocation cancels the pending bootstrap operation.
It does not remove an application's independent actor and workspace authority.
A concurrent issuer revision change invalidates the current preparation attempt.
That change alone must not disable the entire workspace authority cache.
Revocation events still invalidate affected actors and dependent grants.
Malformed issuer identity context and failed issuer transport remain failures of the authority channel.

## Checked replies and durable effects

The checked broker client validates binding identity, workspace, selector, parent, authorizer, and revision.
It validates report issuer, profile revision, workspace, recipient, and delegated grant structure.
It rejects duplicate template results, duplicate grant identities, missing outcomes, and substituted reply context.
An exact stored revoked grant remains valid receipt data. It grants no new authority.

Each template has its own domain transaction.
Do not claim atomicity across templates, the issuer, worlds, or workspaces.
A whole-attempt report error applies only before any template result.
An interrupted request after a commit can leave an uncertain result.
Recover through the existing receipt. Do not create another grant or undo unrelated successful templates.

## Test clarification

The first draft incorrectly expected every read-only actor's development-profile launch to fail.
Existing confinement intersects actor authority, workspace policy, and the requested profile.
A development-profile process can run with a read-only project mount.
The corrected test attempts real unauthorized project writes under both development and read-only profiles.
Both attempts must fail, and the original file must remain unchanged.

Workspace access revocation clears an invalid selection.
The corrected test requires empty selection, FORBIDDEN on reselection, and NO_WORKSPACE on launch.
The original failed tests remain preserved. Existing acceptance tests remain unchanged.

## Qualification limits

Candidate 04 passed complete Debug and runtime ASan/UBSan qualification on both hosts.
Each profile passed 1,037 core checks across 55 suites, 51 harness checks, and 151 P10 cases.
No final case failed or skipped.
The corrected witness passed six new checks and twelve unchanged process cases against each exact binary set.
Those twelve reruns do not add new unique cases.
All 223 integrated source hashes match both hosts' qualified manifests.
Both host adoption audits matched all 223 hashes after the clean fast-forward.
Integration preserves the separately qualified b2ab006 process-witness correction.
M01, M02, native-server GLX sanitizer qualification, and complete Spatial-M1 remain unfinished.

The following paragraphs preserve earlier qualification stages and their failures.

The first complete snapshot omitted four locked contract documents.
The harness refused that snapshot on both hosts. The corrected snapshot includes the original documents.
The schema audit preserves 219 messages, 804 fields, and 111 enum values.
All 75 existing test files and 41 locked files remain unchanged.

Both hosts passed 63 adapter checks and 51 harness checks for candidate 03 in Debug.
Complete Debug and runtime sanitizer profiles remain pending.
A later test exposed the same process-survival defect in ordinary workspace access.
Candidate 04 extends the correction and adds that counterexample.
Candidate 04 requires its own complete qualification before integration.
Neither candidate completes M01, M02, or Spatial-M1 by itself.

Candidate 04 focused sb16 Debug verification passed 52 workspace/broker checks and ten revocation checks.
Its complete sb16 Debug profile passed 1,037 core checks across 55 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. The remaining host and runtime sanitizer results are pending.
Candidate 03 passed complete sb16-2 Debug with 1,036 core checks, 51 harness checks, and 151 P10 cases.
Candidate 03 failed sb16 Debug on a process-witness read race before P10 and sanitizer execution.
The separate fixture correction is b2ab006. It preserves all twelve original process-test bodies.
Both hosts passed six new witness checks and twelve real process tests for that correction.
Active candidate source trees remain unchanged. Integration must retain the corrected witness.

Candidate 03 subsequently passed complete sb16-2 runtime sanitizer qualification with the same counts as Debug.
That result preserves intermediate regression evidence. It does not qualify candidate 04's later correction.
Candidate 04 is now running on both hosts. Its complete second-host and sanitizer results remain pending.

Candidate 04 subsequently completed both Debug and runtime sanitizer profiles on sb16.
Each profile passed 1,037 core checks across 55 suites, 51 harness checks, and 151 P10 cases.
The corrected witness passed six new checks and twelve unchanged process cases against each exact binary set.
All 223 prepared integration hashes match that qualified combination. No production adapter integration is claimed.
Sb16-2 still requires its complete candidate 04 profiles and corrected-witness follow-up.
