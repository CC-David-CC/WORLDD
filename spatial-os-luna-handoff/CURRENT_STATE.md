# Implementation status

## 2026-10-09: Real runtime diagnostic confirms mandatory participation gap

The current world runtime admits actors without an activated issuer service generation.
A real TLS diagnostic combined the barrier issuer draft with the qualified service-session world binary.
The owner read a complete revocation barrier containing zero participants.
While a relay held the genuine event, an independent witness measured another 0.100455821 metres of actor motion.
After event delivery, two position observations matched. Motion expiry did not explain the stop.

This confirms missing runtime participation. The issuer cannot track a world that never activates.
Require world and workspace broker participation before protected admission or execution.
Acknowledgements must also wait for native input, deferred leases, observations, handlers, and required process termination.
The exact worker and integration contract is recorded in docs/AUTHORITY_PARTICIPANT_RUNTIME.txt.
The reproduction and binary hashes are in evidence/revocation-barrier-2026-10-09/runtime-participation-gap-01.
This diagnostic is not a passed cancellation requirement. M02 and Spatial-M1 remain incomplete.
Service-session Debug and runtime ASan/UBSan profiles passed on sb16: 1,152 core, 51 harness, and 151 P10 checks each.
No case failed or skipped. All 238 source hashes match. sb16-2 sanitizer qualification remains live.
Full barrier qualification has started on sb16. No production source or operator state changed.

## 2026-10-09: Durable participation checked client passes focused Debug checks

The isolated barrier client now validates activation, application acknowledgements, release, and owner completion queries.
It rejects mismatched generations, connections, revisions, page counts, challenges, proofs, and replayed query results.
Historical state reads do not authorize another connection to acknowledge or release the generation.

On sb16, candidate 04 passed 33 unique new checks and 187 related regression checks in Debug.
The new total contains 19 server checks and 14 client checks.
All 240 candidate input hashes match. The earlier wrong-outcome baseline remains preserved.
The audit preserves 257 messages, 988 fields, 123 enum values, and all 41 locked files.
All 87 unchanged existing test files and four explicit migration fixture updates remain verified.
Full barrier qualification is prepared. It will not overlap the preceding native qualification processes.
This barrier candidate is not integrated. Actual world and broker effect wiring remains required.

The preceding service-session candidate passed complete Debug on both hosts.
Each passed 1,152 core checks across 66 suites, 51 harness checks, and 151 P10 cases without failure or skips.
Both runtime sanitizer profiles remain live. Integration is prepared but gated on their final evidence.
No production source or operator state changed. M02 and Spatial-M1 remain incomplete.
See evidence/participant-barrier-2026-10-09/client-candidate-04 and evidence/participant-session-2026-10-09/candidate-03.

## 2026-10-09: Durable revocation-obligation draft passes focused Debug checks

An isolated issuer draft records active service generations and their revocation obligations transactionally.
It requires contiguous application acknowledgements from the generation's original authenticated connection.
Disconnects and issuer restarts preserve pending obligations. Another connection cannot replace the active generation.
Owner queries report global completion separately from the existing commit-only revocation reply.
Clean release cannot skip pending cancellation. Explicit schema-6 migration preserves existing authority state.

On sb16, candidate 02 passed 19 new checks and 187 related regression checks in Debug.
The first draft passed 16 of 17 checks. One fixture omitted the required revocation reason.
The corrected fixture reaches and passes its original rollback assertions. The failure remains archived.
The audit preserves 257 messages, 988 fields, 123 enum values, and all 41 locked files.
All 87 other existing test files are unchanged. Four migration fixtures have explicit schema-6 updates.
All 239 candidate source hashes are pinned.

Checked-client support and full qualification remain pending. This draft is not integrated.
The separate service-session candidate passed complete Debug on sb16: 1,152 core, 51 harness, and 151 P10 checks.
No case failed or skipped. Its sb16 sanitizer and sb16-2 qualification remain running.
World/broker effect acknowledgements and supervisor fencing remain required.
These issuer tests do not prove native input release, process cancellation, or full distributed cancellation.
M02 and Spatial-M1 remain incomplete. No operator database or normal session changed.
See evidence/participant-barrier-2026-10-09 and docs/AUTHORITY_PARTICIPANT_BARRIER.txt.

## 2026-10-09: Checked service authentication candidate under full qualification

The isolated service-session candidate now includes strict TLS client response validation.
The client binds each proof to the requested participant, registration revision, process incarnation, issuer, and connection.
It consumes pending proof context before completion, including typed denial responses.
Malformed or substituted replies close the channel. No completion is automatically replayed.

On sb16, candidate 03 passed 32 new checks and 88 related regression checks in Debug.
The new suites contain 20 server checks and 12 checked-client checks.
Both schema audits preserve 253 messages, 966 fields, and 122 enum values.
All 89 existing test files and 41 locked files remain unchanged.
All 238 candidate source hashes are pinned. The failed baselines and review remain archived.
Complete Debug and runtime ASan/UBSan qualification is running on both hosts.
No full profile result is claimed yet. This candidate is not integrated.

Service proof does not activate execution or acknowledge completed cancellation.
Durable activation, fencing, cancellation obligations, and effect acknowledgement remain required.
M02 and complete Spatial-M1 remain unfinished.
See evidence/participant-session-2026-10-09/candidate-03 and docs/AUTHORITY_PARTICIPANT_SESSION.txt.

## 2026-10-09: Authority participant registry qualified and integrated

The issuer now supports owner-signed registration of separate service keys and explicit authority domains.
Registration persists atomically and rejects conflicting identities, keys, and domains.
The checked TLS client validates registry records and pages.
Existing issuer databases require explicit schema-5 migration.

Both hosts passed complete Debug and runtime ASan/UBSan qualification for candidate 02.
Each profile passed 1,120 core checks across 64 suites, 51 harness checks, and 151 P10 cases.
No final case failed or skipped. All 236 integrated source hashes match both host manifests.
The schema audit preserves 248 messages, 946 fields, and 122 enum values.
All 41 locked files and 84 existing test files remain unchanged.
Three migration fixtures have explicit, reviewed schema-5 updates.
Primary review and the failed duplicate-revision counterexample remain archived.

The registry component is complete. Commit 2f86927 is pushed to box-git and adopted on both hosts.
Both adoption audits match all 236 source hashes and report clean worktrees.
Registration alone does not authenticate services, activate execution, or acknowledge cancellation.
The separate service-session candidate now passes focused server and checked-client tests on sb16.
Its full qualification remains pending. M02 and Spatial-M1 remain incomplete.
No operator database or normal login session changed.
See evidence/participant-registry-2026-10-09/candidate-02 and docs/AUTHORITY_PARTICIPANT_REGISTRY.txt.

## 2026-10-09: Service-session server draft passes focused Debug checks

An isolated draft binds registered service-key proof to the actual issuer TLS connection.
It rejects wrong connections, actor/owner/transport keys, replayed proofs, altered challenges, and expired challenges.
Service proof creates no actor credentials, grants, execution activation, or cancellation acknowledgement.
The management pipe cannot supply the required TLS context.

On sb16, 20 new checks and 88 related regression checks passed in Debug.
Three baseline cases first confirmed that the registry candidate lacks these protocol operations.
One first-draft test errored because a helper shadowed an enrollment fixture method.
The corrected test preserved its assertions. All evidence remains archived.
Checked-client support, malformed-response tests, complete schema audit, and full qualification remain pending.
The older registry candidate continues its independent runtime sanitizer qualification on both hosts.
No operator database or normal login session changed. Spatial-M1 remains incomplete.
See evidence/participant-session-2026-10-09 and docs/AUTHORITY_PARTICIPANT_SESSION.txt.

## 2026-10-09: Authority participant registry candidate under qualification

The service registry is implemented in an isolated candidate. Production has not adopted it.
It uses owner-signed administration and separate service keys for explicit world and workspace domains.
Registration persists atomically without creating actors, credentials, grants, or active service sessions.
The proposed protobuf and schema-5 migration contract is in docs/AUTHORITY_PARTICIPANT_REGISTRY.txt.

Candidate 01 passed 28 new checks and 190 existing identity checks on sb16 in each profile.
The profiles were Debug and runtime ASan/UBSan. No focused case failed or skipped.
Review then reproduced a missing duplicate-registration-revision check in the TLS client.
Candidate 02 fixes that check and strengthens rollback, binary-ID pagination, and migration tests.
The failed counterexample and two earlier fixture failures remain preserved.

Candidate 02 passed all 31 new checks and 51 harness checks on both hosts in Debug.
Both hosts completed Debug: 1,120 core checks across 64 suites, 51 harness checks, and 151 P10 cases each.
No Debug case failed or skipped. Both runtime sanitizer profiles are running.
Both schema audits preserve 248 messages, 946 fields, and 122 enum values across all proto files.
Both audits preserve 41 locked files and 84 existing test files.
Three migration fixtures have explicit, reviewed changes for schema 5. Their preservation assertions remain intact.
Do not treat these audits or the earlier focused result as complete candidate-02 qualification.

Distributed cancellation remains incomplete. Registration alone does not enforce a cancellation barrier.
No operator database or normal login session changed. Spatial-M1 remains incomplete.
See evidence/participant-registry-2026-10-09 and tasks/m1/identity.participant_registry.json.

## 2026-10-09: Remote actor transport qualified and integrated

The world daemon now accepts explicitly configured actor connections through mutual TLS 1.3.
Signed actor proof, world admission, and scoped authority remain separate from transport access.
Local and remote actors use the same protobuf messages and production actor handlers.
The trusted management pipe stays private.
Remote observations omit live application pixels and metadata. Remote application input remains outside M1 scope.

Both hosts passed complete Debug and runtime ASan/UBSan qualification for candidate 02.
Each profile passed 1,089 core checks across 62 suites, 51 harness checks, and 151 P10 cases.
No final profile failed or skipped a case.
Each core total includes 46 remote checks across seven suites.
Direct cross-host TLS scenarios passed with each host acting as world authority in turn.
The scenarios used independent actor identities, real motion and inspection, and durable checkpoints.

All 233 integrated source hashes match both qualified host manifests.
The schema audit preserves 227 messages, 849 fields, and 111 enum values.
All 80 existing test files and 41 locked files remain unchanged.
Primary review corrected TLS read-retry deadline handling before qualification.
The original retry counterexamples and fixture failures remain preserved.
Commit 62a617a is pushed to box-git. Both hosts adopted it with clean worktrees.
Both adoption audits match all 233 qualified source hashes.

The remote actor transport component is complete. Full M01, M02, and M05 remain incomplete.
Distributed cancellation acknowledgement is the next required component.
A real delayed-notification diagnostic confirms that issuer commit alone is not cancellation completion.
See tasks/m1/identity.revocation_barrier.json and evidence/revocation-barrier-2026-10-09.
Physical devices, native-server GLX sanitizers, GPU performance, and complete Spatial-M1 remain unqualified.
No operator authority database or normal login session changed.
See evidence/world-remote-2026-10-09/candidate-02 for all four complete profiles and source pins.

## 2026-10-09: Remote actor candidate 02 passes both-host Debug and cross-host checks

Candidate 02 remains outside production while both runtime sanitizer profiles run.
Both hosts passed complete Debug: 1,089 core checks across 62 suites, 51 harness checks, and 151 P10 cases.
No Debug case failed or skipped. Both hosts passed all 46 remote cases across seven suites.
Direct LAN TLS scenarios passed with each host acting as world authority in turn.
Each scenario used distinct authenticated actors, real movement, inspection, and a durable world checkpoint.
Transport certificates alone did not grant actor authority. Entry did not select a workspace.

Primary review found a TLS read-retry deadline defect in draft 01.
Three controlled retry cases reproduced it after a real mutual TLS handshake.
Candidate 02 preserves the original deadline across retries and checks frame deadlines before retrying.
Five retry cases and twelve listener configuration cases pass on both hosts in Debug.
The test wrappers exist only in the dedicated probe executable.
The production world daemon contains no retry-injection wrappers.

Both audits match all 233 candidate source hashes.
The schema audit preserves 227 messages, 849 fields, and 111 enum values.
All 80 existing test files and 41 locked files remain unchanged.
The first retry fixture rejected unsafe certificate permissions before reaching the assertion.
The first cross-host supervisor read an incomplete copied result file.
Both failures remain preserved. Corrected fixtures passed without weakening production checks.

Complete runtime ASan/UBSan qualification remains active on sb16 and sb16-2.
Integration requires both complete profiles, exact source hashes, and the preserved cross-host results.
Distributed revocation acknowledgement remains the next separate component.
These results do not complete M01, M02, M05, physical acceptance, or Spatial-M1.
See evidence/world-remote-2026-10-09/candidate-review-02.txt and candidate-02 for commands and results.

## 2026-10-09: Authenticated enrollment delivery integrated after both-host qualification

The world and workspace adapters now use signed enrollment profiles to create explicitly delegated grants.
The guarded integration applied eighteen reviewed files and preserved the corrected process witness.
All 223 integrated source hashes match both hosts' qualified source manifests.
Commit d4b025e is pushed to box-git. Both hosts adopted it with clean worktrees.
Both host adoption audits matched all 223 qualified source hashes.
The authenticated enrollment-delivery component is complete. This does not complete the M01 identity milestone.

Both hosts passed complete Debug and runtime ASan/UBSan qualification for candidate 04.
Each profile passed 1,037 core checks across 55 suites, 51 harness checks, and 151 P10 cases.
No final profile failed or skipped a case.
Each exact binary set also passed six new process-witness checks and twelve unchanged process tests.
The twelve repeated process tests are not additional unique checks.

World delivery requires valid actor proof and explicit domain delegation.
Workspace delivery requires an explicit authenticated operation and preserves availability, selection, and placement.
Issuer revision changes reject stale operations without terminating independently authorized applications.
Malformed issuer context and failed transport still disable the affected authority channel.
No operator authority database was migrated. No normal login session changed.
M01, M02, and complete Spatial-M1 remain unfinished.
See evidence/bootstrap-workspace-adapters-2026-10-09 for all four final profiles and source pins.

## 2026-10-09: Remote actor draft passes focused Debug checks

The isolated draft adds an explicit TLS 1.3 listener to the existing world actor interface.
Transport certificates do not replace signed actor proof, world admission, or workspace authority.
The private management pipe remains separate. Production source has not adopted this draft.

On sb16, five functional cases, thirteen security cases, and six lifecycle cases passed.
Three native cases passed with real confined application windows and compositor output.
Remote observations retain world geometry and omit live application pixels and metadata.
Local reconnection restores authorized application observation.
A stalled large reply closes its remote channel while a local actor and application remain usable.
Disconnect and revocation stop motion before the configured ten-second motion expiry.
Restart preserves checkpointed actor context and rejects an old authentication challenge.
The draft also passed 68 existing local identity, authority, and bootstrap checks.
These 95 focused Debug checks had no failures or skips.

The same frozen source passed 109 focused runtime ASan/UBSan checks.
This includes the same 95 checks and fourteen existing native pixel regressions.
Two further delayed-issuer cases passed in Debug and runtime sanitizers.
Credential revocation and disconnect prevent grant creation while an independent actor remains usable.
The final focused totals are 97 Debug checks and 111 runtime sanitizer checks, without failures or skips.
Full cumulative qualification, second-host remote qualification, and physical cross-host transport remain pending.
Additional configuration counterexamples remain required.
The primary review also requests an audit of deadlines during TLS read-to-write retries.
No active qualification source was changed.
See evidence/world-remote-2026-10-09 and the root M01_WORLD_REMOTE_DEBUG.txt.

## 2026-10-09: Authenticated bootstrap adapters under qualification

The isolated candidate connects signed enrollment profiles to real world and workspace grants.
World admission applies matching world templates only after valid issuer proof.
Workspace application uses an explicit actor request and the existing private broker channel.
The broker verifies the signed channel credential and current registration before applying templates.
Binding administration requires ordinary parent delegation authority.
The checked broker client rejects substituted domains, actors, profiles, and malformed results.
Workspace application does not create availability, selection, or geometry.

The candidate passed real confined writes, read-only mounts, restart recovery, and receipt retry cases on sb16.
It also passed credential and parent revocation tests during real issuer waits.
Both hosts passed candidate 03 Debug builds, 51 harness checks, and all 63 checks across five adapter suites.
Candidate 03 passed complete sb16-2 Debug: 1,036 core checks, 51 harness checks, and 151 P10 cases.
Its sb16 Debug run failed one process witness on ProcessLookupError during /proc inspection.
That run did not reach P10 or runtime sanitizer checks. The original failure remains preserved.
The corrected witness passed six new checks and all twelve real process tests on both hosts.
Fixture commit b2ab006 preserves every original process-test body and all locked acceptance files.
Candidate 03 also passed complete sb16-2 runtime sanitizer qualification with the same counts and no failures or skips.
That candidate omits the final ordinary workspace access correction. Its results do not qualify candidate 04.
All 223 candidate input hashes matched both hosts.
The additive schema audit preserved 219 messages, 804 fields, and 111 enum values.
All 75 existing test files and 41 locked files remain unchanged.

The first workspace run exposed two incorrect draft test expectations. The corrections preserve real confinement checks.
Two added tests exposed a real process-lifetime defect during bootstrap credential revocation.
The corrected candidate rejects that operation without terminating independently authorized processes.
A further test reproduced the same defect in ordinary workspace access.
Candidate 04 extends that correction. Its sb16 focused Debug run passed 52 workspace/broker checks and ten race checks.
Its sb16 complete Debug and runtime sanitizer profiles each passed 1,037 core checks across 55 suites.
Each profile also passed 51 harness checks and 151 P10 cases. No case failed or skipped.
The corrected process witness then passed six new checks and twelve unchanged process cases against both exact binary sets.
The sb16-2 candidate 04 complete Debug profile passed 1,037 core checks, 51 harness checks, and 151 P10 cases.
No case failed or skipped. Its runtime sanitizer profile and corrected-witness follow-up remain pending.
Candidate 04 still includes the original process witness. Active source trees remain immutable.
Integration must preserve the separately qualified b2ab006 witness correction.
An automatic follow-up waits for each candidate 04 matrix to finish successfully.
It then runs the corrected witness and all twelve real process cases against both exact qualified binary sets.
The sb16 follow-up passed. The sb16-2 follow-up remains queued.
All 223 proposed integration hashes match the sb16 qualified source and corrected witness.
The guarded integration command is prepared. It requires both hosts' complete results before changing production files.
No adapter is integrated into production yet. The source under active tests remains unchanged.

The initial full snapshot omitted four locked documents. The harness rejected it on both hosts.
The corrected snapshot includes those unchanged documents. All original failures remain preserved.
See docs/BOOTSTRAP_DELIVERY.md and evidence/bootstrap-workspace-adapters-2026-10-09.
M01, M02, native-server GLX sanitizer qualification, and full Spatial-M1 remain incomplete.

Five remote actor transport baseline tests now fail against the exact qualified sb16 candidate 04 binary.
Worldd rejects the proposed listener arguments before the later TLS and actor-action assertions execute.
The baseline and draft interface are recorded in evidence/world-remote-2026-10-09.
No remote listener implementation or passing remote-world qualification is claimed.

## 2026-10-09: Domain bootstrap store qualified on both hosts

The domain store is committed as c470a4e and pushed to box-git.
Both hosts adopted that commit. All 214 tested source hashes still matched after integration.
Bindings require parent ownership and explicit delegation rights. They do not create root grants.
Materialization records the delegated grant and durable receipt in one authority-store transaction.
Retries return the original grant. Changed profiles or parents cannot replace an existing receipt.
Parent revocation invalidates dependent grants. Independent grants remain valid.
The explicit spatial-authority-migrate command upgrades authority schema one to two.
Normal startup refuses old schemas. No operator state has been migrated.

Both hosts passed complete Debug and runtime ASan/UBSan qualification.
Each profile passed 973 core checks across 50 suites, 51 harness checks, and 151 P10 cases.
No final profile failed or skipped a case. All 214 source hashes matched.
See evidence/bootstrap-domain-production-2026-10-09 for commands, source pins, and complete reports.
Each profile runs the two authority suites first, then all remaining core suites and P10.
This partition avoids duplicate tests. Both partitions are required for a complete result.
The isolated sb16 candidate already passed 43 existing and 32 new checks in Debug and runtime sanitizers.
The additive schema audit preserved 13 messages, 49 fields, and 23 enum values.
All 43 existing authority test bodies remain unchanged.

Issuer integration is complete at 9e21cdf and pushed to box-git.
Both hosts fast-forwarded to that commit with their original qualified edits preserved in stashes.
The 212 issuer source hashes still matched after integration.
See each host's integration.json in evidence/bootstrap-profiles-2026-10-09.

Authenticated world/workspace bindings, checked credential/profile resolution, and live grant delivery remain required.
Neither the store nor its fixture probe authenticates an actor.
An isolated sb16 world adapter passed 15 authenticated login cases and six additional counterexamples.
The additional cases cover revocation during real issuer waits, independent grants, and partial profile results.
The first six-case attempt had fixture errors. The original failed log remains preserved.
The world adapter remains outside production.
Its focused sb16 Debug and runtime sanitizer profiles each passed 81 regression checks and six additional cases.
The profiles include existing world identity, world authority, and workspace authority checks.
Neither profile failed or skipped a case. Full cumulative and second-host adapter qualification remain pending.
The five workspace adapter baseline cases remain failing because that adapter is unimplemented.
See evidence/bootstrap-adapters-2026-10-09. These focused results do not qualify complete domain delivery.
The next implementation must use the existing authenticated actor path and checked issuer transport.
M01, M02, and full Spatial-M1 remain incomplete.

## 2026-10-09: Versioned issuer bootstrap profiles qualified

Issuer owners can publish immutable enrollment profiles through signed operations.
Manual and automatic issuance pin the exact selected revision in signed credentials.
Automatic issuance rejects administrative templates. Failed issuance rolls back partial records.
Publication uses compare-and-swap revisions. Updating the selected profile invalidates stale manual approvals.
Checked TLS retrieval requires the exact profile identity, revision, issuer, structure, and bounds.
Explicit schema-four migration preserves existing issuer state. Operator state remains unchanged.

Both hosts passed complete corrected Debug and runtime ASan/UBSan qualification.
Each profile passed 941 core checks across 49 suites, 51 harness checks, and 151 P10 cases.
No final profile failed or skipped a case. Both final 212-file source audits passed.
The descriptor audit preserved 37 existing issuer messages, 154 fields, and 17 enum values.
The primary agent reviewed each changed file without local-model review.
See evidence/bootstrap-profiles-2026-10-09, docs/BOOTSTRAP_PROFILES.md, and the root M01_BOOTSTRAP_PROFILES_DEBUG.txt.

The first sb16 Debug attempt failed during native display startup, before its isolation assertion.
The separate fixture correction is committed as 388d500 and pushed to box-git.
It waits for a complete bounded display-number notice. Both hosts passed all 14 native suites.
No XLibre source, locked assertion, or independent witness changed. Original failures remain preserved.
See evidence/native-display-ready-2026-10-09.

Domain bindings, durable materialization, and authenticated world/workspace effects remain required.
An isolated candidate built and linked on sb16. It passed 43 existing and 32 new authority tests.
Both Debug and runtime ASan/UBSan focused profiles passed those checks.
The schema audit preserved 13 old messages, 49 fields, and 23 enum values.
All 43 existing authority test bodies remain unchanged.
The initial candidate test error used FILE_WRITE instead of the existing FILE_EDIT enum.
That failed attempt and the corrected runs remain in evidence/bootstrap-domain-2026-10-09.
The candidate remains outside production under local/bootstrap-domain-draft.
Production integration, sb16-2 candidate qualification, and authenticated adapters remain pending.
Profile storage alone grants no world or workspace authority.
M01, M02, full Spatial-M1, and native-server GLX sanitizer qualification remain incomplete.

## 2026-10-09: Authenticated actor pixels checked

Actors can now request bounded RGB8 images through the authenticated protobuf observation path.
The compositor returns real pixels and metadata from the same filtered frame.
Images use top-left row order and exact packed byte lengths.
Metadata-only requests retain their previous behavior and viewport limits.
The checked compositor client rejects malformed, missing, unsolicited, or mismatched images.
Worldd rechecks authority after rendering and before each queued write.
Revocation stops further queued export without terminating independently authorized applications.
Observation grants no selection, control, or execution authority.

Both hosts passed 14 real native pixel checks and nine adversarial checked-client tests.
The focused run also passed 22 existing rendering and observation checks.
Both hosts passed complete Debug and runtime ASan/UBSan qualification.
Each profile passed 914 core checks across 47 suites, 51 harness checks, and all 151 P10 cases.
No final profile failed or skipped a case.
The 460-file tested source snapshots matched both hosts.
The primary agent reviewed each changed file without local-model review.

The baseline exposed missing images and missing pixel-specific bounds on both hosts.
The first implementation run exposed a missing required label in the occlusion fixture.
The corrected fixture preserves every pixel and authority assertion.
The schema migration remains separate at 71386ba45d9f55368e5b1bb53913bbf30bcdce27.
No locked acceptance assertion, oracle, independent witness, or XLibre source changed in the implementation increment.
See docs/ACTOR_PIXELS.md and evidence/actor-pixels-2026-10-08.
The root M10_ACTOR_PIXELS_DEBUG.txt preserves the failure history and remaining limits.

Native-server GLX sanitizer coverage remains unrun for this component.
Physical devices, native GPU timing, shared-world transport, cabinets, restoration, and model tasks remain incomplete.
M10 and full Spatial-M1 qualification remain incomplete.

Both hosts also gained verified, repository-scoped direct box-git access during the test wait.
That separate change did not modify the runtime source under test.
See evidence/git-transport-2026-10-09 for seven checks per host and exact recovery commands.

## 2026-10-08: Actor pixel schema recorded

Migration 0.16.0 adds explicit bounded RGB8 image fields to actor and compositor observation messages.
It preserves all existing fields, assertions, oracles, and independent witnesses.
Both hosts passed the descriptor audit: 488 fields, 74 enum values, 136 messages, and 151 acceptance identifiers.
Both hosts passed the schema-only Debug build, 35 focused core checks, 51 harness checks, and 151 P10 cases.
No cases failed or skipped in these schema qualification profiles.
See docs/ACTOR_PIXELS_MIGRATION.md and evidence/actor-pixels-schema-2026-10-08.
The schema alone does not implement pixel delivery. M10 and Spatial-M1 remain incomplete.

## 2026-10-08: Authenticated native application control checked

Worldd now acquires production broker reservations before authenticated native application input.
It checks observation, execution authority, native application identity, room, and target generation.
Workspace selection is not required for control.
One actor controls an application group, including its popups.
Different application groups support simultaneous controllers with genuine native input devices.
Read-only actors cannot control writable applications.

Native cancellation clears held input and focus before the world acknowledges broker release.
Issuer revocation and controller socket loss remain serviced during component waits.
An in-flight compositor request finishes before cancellation; it is never replayed.
A close requested during native cancellation completes before reservation release.
Control loss preserves independently authorized application execution.
Explicit diagnostic interaction grants remain available on unlabelled displays.
Labeled applications cannot fall back to those grants after reservation loss.

Both hosts passed 25 new control checks, 20 existing identity checks, and 16 observation checks.
Both hosts then passed complete Debug and runtime ASan/UBSan qualification.
Each profile passed 891 core checks across 45 suites, 51 harness checks, and 151 P10 cases.
No final profile skipped a case.
The 452-file source audit matched Windows and both hosts.
The primary agent reviewed each changed file without local-model review.

The preserved failures exposed delayed cancellation during broker waits and incomplete native device teardown.
The first full Debug run also exposed an overly broad diagnostic-input denial.
The final passing profiles include the corrections and their regression tests.
Fixture setup failures remain separate from production failures in the debug note.
No locked acceptance assertion, schema, independent C witness, or XLibre source changed.
No host system configuration changed.
See docs/WORLD_APPLICATION_CONTROL.md and evidence/world-application-control-2026-10-08.
The root M04_WORLD_APPLICATION_CONTROL_DEBUG.txt records the failure history and remaining limits.

These renderer-dependent world tests use GLX on the normal patched native server.
Native-server ASan/UBSan GLX coverage remains unrun for this component.
The existing instrumented GLX cleanup issue remains unqualified.
Filtered actor pixels, physical input, native GPU timing, restoration, and full M1 qualification remain pending.
M04, M09, M10, and Spatial-M1 remain incomplete.

## 2026-10-08: Broker control client checked

The production broker client now supports checked control acquisition, release, and asynchronous revocation.
It binds leases to the requested actor and native application identity.
Idle polling and request wait callbacks use the same receive buffer.
Partial frames retain a deadline and do not block the caller.
A revocation received before its acquisition reply remains effective.
Repeated acquisition cannot restore a revoked lease.
Unknown notifications, duplicate events, substituted identities, and malformed replies close the authority channel.

Both hosts passed 29 transport checks and 25 real issuer/broker client checks.
Both hosts passed 866 core checks across 44 suites in Debug and runtime ASan/UBSan.
Each profile also passed 51 harness checks and 151 P10 cases.
Both native-server ASan/UBSan profiles passed the 25 live cases with GLX disabled and leak detection enabled.
No final qualification profile skipped a case.

The initial API scaffolding failed all 24 baseline transport cases on both hosts.
The first live run exposed a probe half-close error and missing event draining during a blocked request.
The corrected probe preserves normal broker shutdown and polls from the wait callback.
The original failures remain separate from final passing evidence.
The primary agent reviewed each changed file without local-model review.
The protobuf schemas, locked tests, independent native witness, and XLibre source remain unchanged.
See docs/BROKER_CONTROL_CLIENT.md and evidence/broker-control-client-2026-10-08.
The root M04_BROKER_CONTROL_CLIENT_DEBUG.txt records failures and remaining work.

Worldd does not acquire these leases yet.
Consumers must poll during idle periods and component waits, and defer recursive release requests.
Balanced native cancellation must precede broker release acknowledgement.
Authenticated native input, filtered pixels, physical input, GPU timing, and complete M1 qualification remain pending.
M04, M09, M10, and Spatial-M1 remain incomplete.

## 2026-10-08: Broker application control reservations checked

Workspaced now reserves each native application group for one authorized actor.
Acquisition requires explicit control and observation grants plus sufficient effective execution rights.
Revocation sends an unsolicited protobuf notification, including during idle time and process waits.
The broker retains the reservation until the trusted world acknowledges input cancellation.
Restored permissions do not reactivate a revoked reservation.
Revoking control preserves independently authorized application execution.

Both old brokers failed all 16 initial cases because the new operations were absent.
Both implementations passed the expanded 25-case suite.
Both hosts passed 812 core checks across 42 suites in Debug and runtime ASan/UBSan.
Each profile also passed 51 harness checks and 151 P10 cases.
Both native-server ASan/UBSan profiles passed 25 focused checks with GLX disabled and leak detection enabled.
No final qualification profile skipped a case.

The first sb16-2 Debug run exposed an ESRCH race in the process-exit test helper.
The helper now accepts process disappearance during procfs reads.
Its live-process, revocation, timeout, and side-effect assertions remain unchanged.
Sb16-2 then passed full qualification with the correction.
Sb16 also passed the corrected 19-case authority suite in both profiles after its full runs.
The original failures and corrected execution evidence are preserved.

The additive broker schema preserves 18 existing messages and 70 fields.
The core schema, native witness, acceptance lock, and XLibre source remain unchanged.
The primary agent reviewed each changed file without local-model review.
See docs/APPLICATION_CONTROL_LEASES.md and evidence/application-control-lease-2026-10-08.
The root M04_APPLICATION_CONTROL_LEASE_DEBUG.txt records failures and remaining work.

Worldd does not consume these broker reservations yet.
Authenticated input routing, balanced native cancellation, and filtered pixel delivery remain pending.
These broker tests do not certify native input release.
Physical input, native GPU timing, instrumented GLX lifecycle, and native soak remain unqualified here.
M04, M09, M10, and Spatial-M1 remain incomplete.

## 2026-10-08: Authenticated application metadata checked

Worldd now filters labeled application surfaces using the authenticated actor and native application identity.
The production broker checks explicit workspace and application observation authority.
Aim and visibility metadata use the same filtered scene.
Worldd rechecks access after rendering and before each queued metadata write.
Late revocation discards the observation or closes the actor channel before further export.
Legacy static interaction grants cannot control labeled applications.

Both old worldd binaries failed the eight corrected baseline cases.
Both implementations passed all 16 focused checks with real confined C clients and authenticated actor channels.
Both Debug profiles passed 787 core checks across 41 suites, without skips.
The initial runtime sanitizer profile passed 771 checks and failed 16 confined-witness startup checks.
ASan shadow memory exceeded the production 4 GiB application address-space limit.
A dedicated uninstrumented target now builds the same independent C witness.
Production runtime instrumentation and resource limits remain enforced.
Both profiles then passed the affected 16-case suite, 51 harness checks, and 151 P10 cases on both hosts.
The final qualification combines the full core runs with affected-suite reruns after this fixture-only correction.
All 787 core requirements have passing evidence per host/profile, with no skips or unresolved failures.
No locked tests, schema, C witness, acceptance lock, or XLibre server source changed.
The primary agent reviewed this increment without local-model review.
See docs/APPLICATION_OBSERVATION.md and evidence/application-observation-2026-10-08.

Authenticated pixel delivery, application control, and live input cancellation remain pending.
Native-server sanitizer GLX coverage, physical input, GPU timing, and soak remain unqualified here.
M04, M09, M10, and Spatial-M1 remain incomplete.

## 2026-10-08: Broker application-access authority checked

Workspaced now answers bounded protobuf queries for application observation and control.
It requires a live broker-owned process, matching native server identity, and explicit workspace application grants.
Control also requires the actor's effective execution rights to cover the application's recorded rights.
Read-only actors cannot control writable applications.
Application scopes, parent revocation, and independent grants use the persistent authority store.
Revoking observation or control does not itself terminate valid process execution.

Both old brokers failed all 22 corrected baseline cases because the operation was absent.
Both implementations then passed 28 focused checks using real issuers, grants, confined processes, and native credential issuance.
Both Debug and runtime ASan/UBSan profiles passed 771 core checks across 40 suites.
Each profile also passed 51 harness checks and all 151 P10 cases.
Both native-server ASan/UBSan profiles passed the 28 new checks with GLX disabled and leak detection enabled.
No final profile skipped a case.

The additive broker schema preserves all 14 existing messages and all 59 existing fields.
The core schema, locked tests, and acceptance lock remain unchanged.
The primary agent reviewed this increment without local-model review.
See docs/APPLICATION_ACCESS.md and evidence/application-access-2026-10-08.
The root M04_APPLICATION_ACCESS_DEBUG.txt records failures and remaining work.

The query is a permission snapshot, not a transferable grant or interaction lease.
Worldd consumers, actor-filtered pixels and metadata, and application-control revocation remain required.
Authenticated actors still receive no native application surfaces until that integration is complete.
Physical input, native GPU output, full instrumented GLX lifecycle, and native soak remain unqualified.
M04, M09, and Spatial-M1 remain incomplete.

## 2026-10-08: World application relationships and leases checked

Worldd now checks native server, workspace, and application identity before it follows a transient parent hint.
Invalid relationships have no world placement and do not appear in a foreign application's child list.
Control leases use authenticated application identity across separate connections and top-level windows.
Valid same-application hint changes preserve held input.
Invalid relationships and parent destruction release affected input.

Both hosts reproduced six failures in the corrected ten-case baseline before the production change.
Both then passed fourteen real native integration checks with independent C clients.
Both Debug and runtime ASan/UBSan profiles passed 743 core checks across 39 suites.
Each profile also passed 51 harness checks and all 151 P10 cases, with no skipped cases.
The primary agent reviewed the change without local-model review.
Schemas, locked acceptance tests, and the native XLibre server source remain unchanged.

See docs/WORLD_SURFACE_GROUPS.md and evidence/world-surface-groups-2026-10-08.
The root M04_SURFACE_GROUPS_DEBUG.txt records the baseline failures and remaining limits.
These tests use management-fixture actors with genuine native application credentials.
Authenticated actor sessions still exclude application surfaces until broker-authorized observation and control are integrated.
Application grants, filtered metadata, revocation integration, and session provisioning remain required.
Physical input, native GPU output, full instrumented GLX lifecycle, and native soak remain unqualified.
M04, M09, and Spatial-M1 remain incomplete.

## 2026-10-08: Native surface attribution checked

The native Inspect reply now carries server-owned application attribution with window geometry and generation.
The compositor checks that attribution and includes it in its private NativeSurface snapshot.
Mapped infrastructure windows do not become workspace application surfaces.
Unmapping and failed pixmap acquisition clear current attribution.
Property spoofing cannot change the server-owned labels.
Both hosts passed fifteen native checks and seven real compositor integration checks.
Both also rejected an older server that omits the required security mode.

Migration 0.15.0 explicitly records the additive locked core-schema change and its imported security schema.
The descriptor audit preserves 481 existing fields, 71 enum values, and all 151 acceptance identifiers.
The acceptance assertions, oracles, and native witness remain unchanged.
Both hosts passed 729 core checks across 38 suites in Debug and runtime ASan/UBSan builds.
Each profile also passed 51 harness checks and all 151 P10 cases.
Both native-server ASan/UBSan profiles passed 210 focused checks with GLX disabled and leak detection enabled.
The older-server rejection also passed with runtime sanitizers on both hosts.
No final executed profile skipped a case.
See docs/NATIVE_SURFACE_IDENTITY.md and docs/NATIVE_SURFACE_IDENTITY_MIGRATION.md.
The root M04_SURFACE_IDENTITY_DEBUG.txt records failures and remaining work.
Worldd application grants, leases, filtered actor metadata, and session provisioning remain required.
M04, M09, and Spatial-M1 remain incomplete.

## 2026-10-08: Confined native application launch checked

Workspaced now accepts a sealed NativeLaunchConfig protobuf descriptor from its supervisor.
It authenticates the pinned native server and issues one credential per confined application.
The application identity is the broker-allocated process ID within the authorized workspace.
Bubblewrap mounts only that application's read-only Xauthority record and the pinned X socket.
Existing process, filesystem, namespace, and syscall restrictions remain enforced.
Completed applications now lose native access while the broker is idle.
Authority denial during issuance revokes only the tentative credential.
Both hosts reproduced the two lifecycle defects before their corrections.
Both final Debug and runtime ASan/UBSan profiles passed 707 checks across 36 CTest suites on each host.
Each profile also passed 51 harness checks and all 151 P10 cases.
Both native-server ASan/UBSan profiles passed 195 focused checks, including all 19 new launch checks.
Those native profiles disabled GLX and enabled leak detection with abort/halt on sanitizer errors.
No final executed profile skipped a case. Locked acceptance tests and their lock file remain unchanged.

The primary agent reviewed this component under David's local-model review waiver.
See docs/NATIVE_APPLICATION_LAUNCH.md and the repository-root M04_NATIVE_LAUNCH_DEBUG.txt.
Evidence is in evidence/native-application-launch-2026-10-08.
Automatic session provisioning, authenticated surface identity, and application grants remain required.
Physical devices, native GPU output, full instrumented GLX lifecycle, and native soak remain unqualified.
M04, M09, and Spatial-M1 remain incomplete.

## 2026-10-08: Native application actor binding checked

Application connections now use their controlling actor's genuine native devices.
Connections with the same workspace/application identity share one controller and input state.
Different application groups can have simultaneous controllers.
Cancellation returns the group to private idle devices after releasing held input and active grabs.
It also clears native focus, modifier locks, latches, and previous motion history.
Passive core grabs retain valid device references across actor closure.

Both previous servers failed 24 of the 25 new native checks.
One application-local focus control passed on that baseline.
Both corrected native-server ASan/UBSan profiles passed all 176 focused native checks.
Those profiles disabled GLX, enabled leak detection, and halted on sanitizer errors.
Both Debug and runtime sanitizer profiles passed 688 checks across 35 CTest suites.
Each profile also passed 51 harness checks and 151 P10 cases, with no skipped cases.

Sanitizer validation found two lifecycle defects in the spatial overlay.
Actor teardown now publishes its hierarchy update after removing the complete device group.
Keyboard routing now preserves the shared sprite window and leaves pointer transitions to pointer processing.
The original failures remain in the evidence. No suppression or new upstream patch was added.
The motion-history witness now queries the previous binding's timestamp interval, excluding legitimate fresh samples.
Locked acceptance tests and acceptance.lock.json remain unchanged.

The primary agent reviewed this increment under David's local-model review waiver.
See docs/APPLICATION_ACTOR_BINDING.md and M04_ACTOR_BINDING_DEBUG.txt at the repository root.
Evidence is in evidence/application-actor-binding-2026-10-08.
Broker credential delivery and compositor/worldd application-authority integration remain required.
Physical devices, native GPU output, full instrumented GLX lifecycle, and session soak remain unqualified.
M04, M09, and Spatial-M1 remain incomplete.

## 2026-10-08: Private application input devices checked

Authenticated application connections now share a real device pair within their application identity.
Other applications receive separate pointer, keyboard, focus, and grab state.
The server assigns devices before the connection's first native request.
Allocation failure rejects authentication instead of selecting shared devices.
The last connection releases its application's pair.

Both previous servers failed 12 of the 15 new checks.
Three same-application positive controls passed on that baseline.
Both final hosts passed all 15 new checks and 136 existing focused native checks.
Both native-server ASan/UBSan profiles passed those 151 checks with GLX disabled and leak detection enabled.
Both final Debug and runtime sanitizer profiles passed 663 checks across 34 CTest suites.
Each profile also passed 51 harness checks and 151 P10 cases.
No executed profile reported skipped cases.

The first native sanitizer run exposed a 193-byte XKB doodad-string leak.
Patch 0007 frees all old destination doodad strings before geometry replacement.
The original failure remains in the evidence. No suppression was added.
The InputFocus attack fixture now uses the attacker's actual keyboard.
Its BadAccess assertion remains unchanged, and the fixture correction has its own reviewed commit.

David authorized primary-agent review without the local model.
The primary agent reviewed the implementation, lifecycle paths, tests, and evidence.
The contract is docs/PRIVATE_APPLICATION_INPUT.md.
Evidence is in evidence/private-application-input-2026-10-08.

This component supplies private idle devices. It does not bind applications to actor devices.
The configured policy currently denies application access to unbound actor devices.
Next: bind the whole application group to its authorized controller, then qualify transfer and revocation.
Motion history, asynchronous metadata, broker/compositor integration, and physical sessions remain unqualified.
M04 and Spatial-M1 remain incomplete.

## 2026-10-08: Shared device administration checked

Ordinary applications and brokers cannot configure shared keyboard or pointer devices.
The policy checks keyboard mapping, keyboard controls, bell, pointer controls, pointer mapping, and modifier mapping.
Management or bell access intent is required before the callback checks the core opcode.
A previous opcode does not change authorization for asynchronous reads or event delivery.
The callback preserves existing actor-seat denials.
Public mapping reads and trusted service configuration remain available.

Both pre-patch servers failed nine of the 13 new checks.
All four positive controls passed on that baseline.
Both patched Debug servers passed all 136 focused native checks.
Both instrumented Xvfb servers passed the same checks with GLX disabled.
The suites contain 13 device, 19 global, 14 selection, 32 extension, 36 resource, and 22 identity checks.
Native sanitizer runs enabled leak detection and halted on invalid access or undefined behavior.
Both Debug hosts passed all 151 locked P00-P10 cases and 51 harness checks.
Each host detected six isolated configuration bypass mutations.
No mutation entered production.

The local model reviewed each implementation file and the additive contract.
Incorrect callback signatures, privilege-helper arguments, guard interpretations, and CTest names were rejected.
Corrected proposals received further review before integration.
Model prose does not establish execution evidence.

Strata was repeatedly stopped during documentation review.
The recorded stop override was absent.
The first authorized start restored bounded reviews.
The second start was stopped cleanly during model loading, before port 8080 opened.
No unit files changed.
David subsequently authorized primary-agent review without the local model.
The primary agent reviewed the source, tests, additive contract, and evidence summaries.
The review found no further defect in this bounded administration change.
The evidence records the service-state change and recovery command.

The contract is docs/NATIVE_DEVICE_ADMINISTRATION.md.
The primitive is tasks/m1/application.device_administration.json.
Evidence is in evidence/native-device-administration-2026-10-08.
Only disposable project displays ran the updated server.

Both hosts also passed the complete registered core suite in Debug and runtime ASan/UBSan.
Each profile passed 648 checks across 33 CTest suites, 51 harness checks, and 151 P10 cases.
Those executed profiles report no skipped or unrun cases.
Physical input profiles were not run.
Shared device administration checks do not complete input authority or privacy.
Application input binding, actor-seat reads, focus/pointer queries, grabs, and event metadata remain unqualified.
Broker/compositor integration and GLX provider cleanup remain required.
M04 and Spatial-M1 remain incomplete.
Next: bind application input to its authorized controller and qualify private input observation.
Both hosts now reproduce missing focus, pointer, held-key, and grab isolation.
M04_INPUT_PRIVACY_FINDINGS.txt at the repository root records these diagnostics.


## 2026-10-08: Shared server administration checked

The native policy now checks shared server administration and screen-saver access.
Application and broker connections cannot grab or explicitly release the entire server.
They cannot administer host access controls, inspect font paths, or reap retained service resources.
They cannot inspect, change, or force shared screen-saver state.
Public font names and font information remain available to applications.
Supervisor and compositor connections retain the privileged service boundary.

Both pre-patch servers failed 14 of the 19 new checks.
All five positive controls passed against that baseline.
The first patch build failed because it used a nonexistent DixUngrabAccess constant.
The corrected build exposed three host-access failures.
The -ac flag bypassed the authority callback in AuthorizedClient.
The final patch checks authority before that shortcut.
It uses the verified DixGrabAccess mask for explicit ungrab authorization.
The compile failure and intermediate test failures remain in the evidence.

Both patched Debug servers passed all 123 focused native checks.
Both native-server ASan/UBSan builds passed the same checks with GLX disabled.
The suites contain 19 global, 14 selection, 32 extension, 36 resource, and 22 identity checks.
Native sanitizer runs enabled leak detection and halted on invalid access or undefined behavior.
Both Debug hosts passed all 151 locked P00-P10 cases and 51 harness checks.
Five isolated bypass mutations were detected on each host.
They cover the server callback, screen-saver callback, ungrab guard, temporary-resource guard, and -ac ordering.
No mutation entered production.

The local model reviewed each implementation file and the additive contract.
Its initial wire-layout claims and CTest registration required correction.
The primary implementation also required the recorded compile and host-access fixes.
Model prose does not establish test outcomes.

The contract is docs/NATIVE_GLOBAL_POLICY.md.
The primitive is tasks/m1/application.global_administration.json.
Evidence is in evidence/native-global-administration-2026-10-08.
The updated servers ran only in disposable project displays.
The normal desktop and recovery routes remain available.

This increment did not repeat complete C++ core or runtime-sanitizer profiles.
GLX provider cleanup and physical session qualification remain unverified.
Full global isolation, M04, and Spatial-M1 remain incomplete.
Device authority, focus and pointer queries, atom privacy, private selection namespaces,
asynchronous metadata, and broker/compositor integration remain required.
Next: audit device authority and global focus/pointer observation.


## 2026-10-08: Owned selection access checked

The native policy now checks owned selections through XACE_SELECTION_ACCESS.
Foreign application identities cannot query, replace, clear, or convert an owned selection.
Matching workspace membership or application names alone grant no access.
Connections with the same workspace and application identity retain real selection conversion.
Supervisor and compositor connections retain access. Broker credentials do not grant it.

The rebuilt pre-patch baseline failed nine of the 14 new checks on each host.
Five positive controls passed on that baseline.
An older cached server had a different policy source hash.
It was excluded from the current-source baseline claim.
The rebuilt baseline and patched builds record their source and binary hashes.

Both patched servers passed all 104 focused native checks in Debug and native-server ASan/UBSan.
The suites contain 14 selection, 32 extension, 36 resource, and 22 identity checks.
Focused sanitizer runs disabled GLX and enabled leak detection.
Both Debug hosts also passed all 151 locked P00-P10 cases and 51 harness checks.
An isolated selection-policy bypass mutation failed nine checks on sb16.
The other five selection checks still passed against that mutation.

The local model reviewed each implementation file and the additive contract.
Its initial callback signature, access constants, and extra test helper were incorrect.
Corrected proposals received further review before integration.
Model responses are review evidence, not execution evidence.

The contract addition is docs/NATIVE_RESOURCE_POLICY.md.
The primitive is tasks/m1/application.selection_owner_access.json.
Evidence is in evidence/native-selection-access-2026-10-08.
Disposable project displays preserved the normal desktop and recovery routes.

This increment did not repeat the complete C++ core or runtime-sanitizer profiles.
Focused native-server sanitizers do not qualify GLX cleanup or physical hardware.
Full M04 and Spatial-M1 remain incomplete.
Atom-name privacy, independent selection namespaces, asynchronous metadata, global operations,
device authority, and broker/compositor integration remain required.
Next: audit global core requests and add independent native attacks.


## 2026-10-08: Native extension access component passed

The extension access component passed on sb16 and sb16-2.
All three focused Debug CTest suites passed on both hosts.
ASan/UBSan Xvfb builds passed the same suites on both hosts.
GLX was disabled during these focused sanitizer runs.
One sb16 security-identity attempt hit an Xvfb displayfd startup error.
Its retry passed all 22 tests.
The locked P00-P10 gate passed 151 of 151 cases on both hosts.

The earlier project Xvfb exposed 18 extra extensions to applications.
It also accepted direct XTEST version dispatch.
Current M04 witnesses failed against that build, as expected.
The baseline SHAPE-hole witness stopped at CreatePicture denial.
That result does not prove pixel exposure.
Mutation reports detected dispatch, discovery, window-pixel, resource-access,
root-enumeration, and parent-relative-pixel mutations.
See local/m04-extension-mutation-02/mutation.json,
local/m04-extension-mutation-03/mutation.json, and
local/m04-isolation-mutation-02/mutation.json on sb16.
The earlier project server is local/xlibre-patched/6f3032647e19809e21d4390b8fc18aca0010447755096ab345f381687fbc8aba/source/build/hw/vfb/Xvfb.

No TimerFree-specific mutation was run.
Focused sanitizer runs do not qualify GLX cleanup.
Full M04 and Spatial-M1 remain incomplete.
Global operations, atom and selection privacy, device authority, aliases,
asynchronous metadata, broker credential setup, and compositor authority remain.
Physical acceptance and full Spatial-M1 qualification remain.
Next: enforce global native operations and device authority.

## 2026-10-07: Native application resource access checked

Strict project XLibre servers now enforce immutable application ownership at native resource lookup boundaries.
Native resource lookups deny access across application identities, including applications in the same workspace.
Connections with the same workspace and application identity retain legitimate resource sharing.
The policy checks windows, pixmaps, graphics contexts, properties, and window event targets.
Root enumeration filters foreign children. Root pixels and properties remain private to trusted services.
Applications cannot retain resources after disconnect. Revocation destroys affected resources before acknowledgement.
Native client-slot reuse does not carry the previous application's authority.

Independent attacks also reproduced inherited pixel exposure through ParentRelative backgrounds.
The server now checks background and border inheritance, including reparenting to the root.
Unspecified foreign-parent borders use public black pixels.
SendEvent now preserves access denials, including its input-focus alias.

The final source passed this development matrix:

- sb16 Debug: 151 P00-P10 acceptance cases, 570 core checks, and 51 harness checks.
- sb16-2 runtime ASan/UBSan: the same 151 acceptance cases, 570 core checks, and 51 harness checks.
- Both hosts' Debug native servers: 36 resource checks and all 22 connection-identity checks.
- sb16-2 instrumented native server: all 58 resource and identity checks, with GLX explicitly disabled.
- Three isolated mutations were detected: resource access, root enumeration, and parent-relative pixel checks.

These executed suites had zero failures, errors, skips, or unrun cases.
This increment did not repeat full Debug on sb16-2, full runtime sanitizer checks on sb16, or native server sanitizers on sb16.
Final Spatial-M1 qualification still requires the complete profile on both hosts.
The prior instrumented GLX graphics-provider cleanup failure remains open. This increment does not retest or resolve it.
Earlier missing-feature failures and intermediate regression results remain with the final passing evidence.
All recorded source hashes match local files and both hosts. Locked acceptance files remain unchanged.

The contract is docs/NATIVE_RESOURCE_POLICY.md.
Evidence is in evidence/native-resource-access-2026-10-07.
The updated server ran only in disposable project displays. No host service or operator desktop changed.
Local-model review remains waived under David's hardware-failure instruction.

This is an M04 resource-access component. Full native resource isolation remains incomplete.
Extension dispatch, global operations, atoms, selections, devices, aliases, and asynchronous metadata still require qualification.
Broker provisioning and compositor authority integration remain required.
Strict mode remains explicit. Do not connect untrusted applications before the complete native boundary passes.
All incomplete M01-M16 and H01-H08 work remains active. Full Spatial-M1 is not claimed.
Next: audit extension access and add independent attacks against global native operations.
The full project goal remains active. No external blocker is recorded.

## 2026-10-07: Native X connection identity component qualified

The pinned XLibre server now supports strict authentication from a sealed protobuf bootstrap descriptor.
Every accepted strict-mode connection receives immutable role, workspace, application, credential, connection, and server identities.
Host-access exceptions cannot bypass strict authentication, including `-ac`.
The supervisor and broker issue bounded application credentials through the native protobuf extension.
Ordinary Xlib applications use private Xauthority files without a custom client library.
Revocation closes dependent native connections and prevents cookie reuse.
Issuer disconnect also revokes its credentials. Independent issuers and credentials remain valid.

Both hosts passed Debug and runtime ASan/UBSan qualification with GLX enabled on the project server:

- All 151 P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 534 core checks, including 22 native identity checks.
- All 51 harness checks.

Both instrumented native servers also passed all 22 identity checks with GLX explicitly disabled.
That focused profile enables address, undefined-behavior, and leak checks, including server shutdown.
It qualifies core X authentication. It does not qualify instrumented GLX or native GPU output.
The default identity test and cumulative runs keep GLX enabled.
Two independent mutations bypassed cookie matching and omitted issuer-disconnect revocation.
Each mutation failed its corresponding counterexample. Neither entered production.

Instrumentation exposed upstream GLX server-client and failed-listener leaks, plus repeated Xvfb cleanup.
The maintained server patch fixes those owned allocations and clears freed framebuffer pointers.
The GLX server-client leak also reproduced with strict security disabled.
Installed graphics providers still leak during instrumented GLX shutdown.
That failure remains open in tasks/m1/server.graphics_cleanup.json.
No sanitizer suppression or system graphics change was applied.
Initial build, fixture, and sanitizer failures remain with the passing evidence.

All 168 recorded source hashes match local files and both hosts.
The acceptance lock, locked assertions, and locked native witness remain unchanged.
The additive contract is docs/NATIVE_SECURITY_IDENTITY.md.
Evidence is in evidence/native-security-identity-2026-10-07.
No system service, operator desktop, or actual David identity changed.
Local-model review remains waived under David's hardware-failure instruction.

This is an M04 component. Workspace resource isolation and broker/compositor provisioning remain required.
Strict mode is explicit. Existing trusted diagnostic mode is not an application security boundary.
Authenticated actors still receive no native application observation or control from this increment.
Cross-workspace enumeration, pixels, properties, grabs, input injection, and retained-resource policy remain required.
All incomplete M01-M16 and H01-H08 work remains active. Full Spatial-M1 is not claimed.
Next: enforce native resource isolation and independently attack its boundaries.
The full project goal remains active. No external blocker is recorded.

## 2026-10-07: Persistent workspace execution authority qualified

Authenticated actors now use persistent workspace grants for selection and confined process execution.
The production broker opens explicitly configured workspace grant stores and checks current caller and ancestor identities.
An exclusive seed command installs roots. Actor requests cannot install roots.
World sessions expose subject-filtered listing, delegation, and revocation through additive protobuf fields.
The broker derives access from scoped grants, workspace policy, and the requested process profile.
World admission, world administration, and workspace availability do not grant workspace access.
Static memberships cannot override a configured persistent workspace authority.

Real processes use existing private bare repositories, worktrees, namespaces, and syscall restrictions.
Partial file grants cannot make an entire project writable.
Read-only profiles keep project files and Git administration read-only.
Process records retain the authenticated principal and effective execution capabilities.
Grant or ancestor revocation cancels processes that lose those capabilities, including idle and waiting processes.
Independent grants can preserve execution. Losing selection or a credential does not stop otherwise authorized processes.
Persistent grants, workspace selection, real files, and completed process records survive restart.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 512 core checks, including 19 workspace-authority checks.
- All 51 harness checks.

The tests use real issuer enrollment, authenticated world sessions, workspaced, Git, and confined POSIX processes.
An expanded relay test reproduced stale launch acknowledgement after revocation during an issuer-status refresh.
The broker now reconciles process authority before its retry lookup.
An existing world-authority test also caught a changed denial code when no broker exists.
The production fix preserves that assertion.
Three independent mutations removed process cancellation, permitted excess writes, and restored the stale retry bug.
Each mutation failed its corresponding counterexample. None entered production.
Initial build and fixture errors remain in the evidence alongside final passing runs.

All 154 source hashes match local files and both hosts.
The acceptance lock and locked assertions remain unchanged.
The additive contract is docs/WORKSPACE_AUTHORITY.md. Execution storage remains schema version 4.
Evidence is in evidence/workspace-authority-2026-10-07.
No system service, operator session, or actual David identity changed.
Local-model review remains waived under David's hardware-failure instruction.

Native application observation and control still require workspace and XLibre isolation.
Cabinet actions, nonempty bootstrap profiles, remote actor transport, and distributed revocation acknowledgement remain required.
M01, M02, Spatial-M1, and the other mandatory M01-M16/H01-H08 items remain incomplete.
Next: assign native X connections immutable workspace and application identities, then enforce resource isolation.
The full project goal remains active. No external blocker is recorded.

## 2026-10-07: Persistent world authority qualified

Authenticated world actors now use persistent scoped grants in the production actor engine.
An explicit private seed command installs world roots. Actor requests cannot mint roots.
Grant listing filters records by the authenticated subject and uses bounded pages.
Delegation and revocation share the actor sequence counter and require a matching authority revision.
The adapter checks current caller and ancestor identities through the pinned issuer client.
Private admission requires WORLD_ENTER. Public admission does not create action grants.
Navigation, observation, and object manipulation use separate rights.
Object grants use exact room and object scope components, plus existing visibility and reach checks.

Committed grant changes cancel affected motion and object drafts before the world reply.
Issuer actor-revocation events invalidate dependent grants even when the ancestor has no world session.
Independent grants survive unrelated revocation. Credential revocation does not revoke actor grants.
World grants cannot grant workspace selection, application control, or process execution.
Native application surfaces remain excluded from persistent world actors until workspace authority is integrated.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 493 core checks, including 27 new world-authority checks.
- All 51 harness checks.

Tests used real issuer enrollment, cryptographic proofs, worldd, SQLite, and native X clients.
They cover seed atomicity, scope attenuation, unknown recipients, replay, restart, motion cancellation, and object leases.
The initial 16 checks passed before review added ten configuration and authority counterexamples.
A further relay test reproduced credential revocation during ancestor-status preparation.
Worldd now keeps in-progress credentials visible to the revocation handler until admission finishes.
The final 27-check suite includes that race regression.
An old-binary test first demonstrated missing world authority initialization on both hosts.
Final-source mutations omitted grant-cache invalidation and object-scope enforcement.
Both mutations failed their independent counterexamples. Neither entered production.

All 151 source hashes match reviewed local files and both hosts.
The acceptance lock and locked assertions remain unchanged.
The preliminary sb16-2 sanitizer run exceeded the old 300-second aggregate core limit.
The runner now permits 600 seconds for the expanded complete core suite.
Individual test deadlines remain unchanged. Final qualification uses the revised runner.
The additive protobuf contract is docs/WORLD_AUTHORITY.md.
Evidence is in evidence/world-authority-2026-10-07.
No system service, host configuration, operator session, or actual David identity changed.
Local-model review remains waived under David's hardware-failure instruction.

Actor channels remain local inherited sockets. Remote authenticated sessions remain required.
Workspace grants, application visibility, execution authority, and nonempty bootstrap profiles remain required.
Issuer snapshots and world effects are separate transactions. Distributed revocation acknowledgement remains required.
M01, M02, Spatial-M1, and all other mandatory M01-M16/H01-H08 items remain incomplete.
Next: connect workspace-domain grants to authenticated workspace access and broker execution.
The full project goal remains active. No external blocker is recorded.

## 2026-10-07: Current actor-status service qualified

The production issuer now returns bounded actor-status batches through its existing protected service interface.
Each batch reports the requested actors in order, with known, revoked, and active flags.
The reply identifies the issuer and one committed security revision.
Unknown actors remain inactive. Pending enrollment does not create an actor implicitly.
The query discloses no labels, public keys, credentials, workspace memberships, or grants.
Credential revocation remains separate from actor revocation.
The checked client validates the issuer pin, exact actor list, count, order, flags, and revision range.
Malformed successful replies disable the client. Typed request errors leave it usable.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 466 core checks, including 21 new actor-status checks.
- All 51 harness checks.

Tests used real enrollment, owner-authorized revocation, issuer restart, and production TLS clients.
SQLite witnesses verified read-only queries and rolled-back revocation.
Independent TLS peers supplied malformed responses to the checked client.
Final-source mutations ignored actor revocation and an issuer pin. Both counterexamples detected their defect.
The mutations never entered production.
Initial tests observed the old issuer rejecting the new operation.
The first 19 focused checks passed before review added restart and rollback counterexamples.
The final 21 checks preserve those assertions and add malformed nested-field and inactive-flag cases.

All 148 source hashes match reviewed local files and both hosts.
The acceptance lock and locked assertions remain unchanged.
issuer.proto adds a read-only service operation. Issuer database schema remains version 3.
Evidence is in evidence/actor-status-2026-10-07. The contract is docs/ACTOR_STATUS.md.
No system service, host configuration, operator session, or actual David identity changed.
Local-model review remains waived under David's hardware-failure instruction.

Grant checks now have an issuer API for ancestor status without collecting ancestor credentials or private keys.
Worldd and workspaced still do not use that store for live authorization.
Actor status does not authenticate a caller, grant resource authority, or cancel live control by itself.
Status batches are point-in-time observations. Later external effects are not atomic with them.
Persistent domain provisioning, authenticated action adapters, nonempty profiles, and live cancellation remain required.
M01, M02, Spatial-M1, and all other mandatory M01-M16/H01-H08 items remain incomplete.
Next: connect world-domain grants to authenticated actor actions and revocation handling.
The full project goal remains active. No external blocker is recorded.

## 2026-10-07: Persistent grant-store component qualified

The production authority library now stores scoped world or workspace grants in SQLite.
Each domain retains its own identity. World and workspace scopes remain independent.
Delegation records a durable parent and requires an active chain with narrower scope, rights, and depth.
Held rights and delegable rights are separate. Administration does not imply delegation of administration.
Parent revocation disables descendants on the next store decision. Independent grants remain valid.
The store checks ancestor identities through a required caller-supplied identity-status checker.
Creation and revocation commit with their authority revisions in one database transaction.
Unknown schemas, malformed records, missing parents, cycles, and unsafe storage fail closed.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 445 core checks, including 43 new authority-store checks.
- All 51 harness checks.

The tests exercised real persistence, restart, process death, and transactional storage failure.
Independent SQLite reads verified stored parent links and records.
Generated chains and adversarial cases checked structural scope and transitive narrowing.
A final-source mutation ignored stored revocation. The parent-revocation counterexample detected it.
The mutation never entered production.
Initial startup failed because SQLite NOFOLLOW rejected an intentional descriptor-path symlink.
The corrected implementation retains NOFOLLOW and uses the checked directory's kernel-resolved path.
Initial failure logs and the final qualification logs are retained.

All 147 source hashes match reviewed local files and both hosts.
The acceptance lock and locked assertions remain unchanged.
The new grants.proto defines stored authority records. Existing wire and database schemas remain unchanged.
Evidence is in evidence/authority-store-2026-10-07. The contract is docs/AUTHORITY_STORE.md.
No system service, host configuration, operator session, or actual David identity changed.
Local-model review remains waived under David's hardware-failure instruction.

This qualifies the production library through a thin fixture driver.
The driver is not an authenticated actor endpoint.
Worldd and workspaced do not yet use the store for live authorization.
Static world grants and workspace memberships remain unchanged at this increment.
The store does not yet publish revocation notifications or cancel live input.
Nonempty enrollment profiles, authenticated runtime adapters, and distributed cancellation remain required.
M01, M02, full Spatial-M1, and all other mandatory M01-M16/H01-H08 items remain incomplete.
Next: connect domain grants to authenticated runtime identities and live world and workspace checks.
Then apply bounded enrollment profiles through explicit domain-authorized grants.
The full project goal remains active. No external blocker is recorded.

## 2026-10-07: Authenticated local world sessions qualified

worldd now accepts actor-only Protocol Buffer channels separately from trusted management.
The supervisor supplies private unnamed local sockets. Their descriptors close before child program execution.
The world verifies issuer signatures, current credential status, and fresh proofs bound to the channel and world.
It derives persistent actor identity from issuer and actor identities.
Clients cannot choose actor identities, provide grants, or transfer internal session tokens.
Private admission and server-selected action grants remain separate.

Authenticated channels call the existing production actor engine.
Observation permission checks run inside that engine and consume denied action sequences.
Trusted fixture behavior remains available through the management interface.
The world checks current issuer status before protected actor operations.
Revocation notifications cancel affected pending proofs and active sessions.
Disconnects, oversized frames, and issuer failure also cancel dependent control.
Cancellation releases native keys, buttons, focus, capture, motion, previews, and leases.
Applications remain alive when their controller loses credentials.
A read-only keepalive preserves the checked issuer connection during an idle session.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 402 core checks, including 20 new world-session checks.
- All 51 harness checks.

The first 18 session checks passed before review added idle and excessive-frame counterexamples.
Review also moved observation checks into the shared sequence path and fixed callback cleanup.
Existing assertions were preserved. Initial focused logs and final qualification logs are retained.
Independent native witnesses verified key and button release without another actor request.
The final-source mutation ignored world revocation events. A native witness detected the held input.
The mutation never entered production.

A world on sb16-2 authenticated through identityd on sb16.
An actor key generated on sb16-2 completed real enrollment and fresh world authentication.
Remote credential revocation released real held input while the fixture application stayed alive.
This verifies cross-host issuer integration. It does not qualify a shared world or remote actor transport.
Disposable keys, certificates, databases, native displays, and transfer archives were cleaned up.

All 141 source hashes match reviewed local files and both hosts.
The acceptance lock, locked assertions, core wire schema, oracles, and native witness remain unchanged.
world_session.proto adds a separate versioned interface. World database schema remains unchanged.
Evidence is in evidence/identity-world-2026-10-07. The contract is docs/WORLD_IDENTITY.md.
No host configuration, permanent service, operator session, or actual David credential changed.
Local-model review remains waived under David's hardware-failure instruction.

M01 and full Spatial-M1 remain incomplete.
Nonempty bootstrap profiles and persistent scoped grants remain required.
Static baseline grants do not establish workspace or application authority.
Remote actor transport and shared-world admission remain required.
Issuer acknowledgement does not prove distributed cancellation. A revocation barrier or equivalent protocol remains required.
All remaining M01-M16 and H01-H08 requirements stay mandatory.
Next: scoped authority records and nonempty bootstrap profiles with M02 delegation integration.
The full project goal remains active. No external blocker is recorded.

## 2026-10-07: M01 issuer revocation feed qualified

The issuer now returns committed actor and credential revocations through versioned Protocol Buffers.
An omitted cursor requests a current baseline. An explicit cursor requests contiguous durable history.
Each batch contains at most 128 events. Missing history produces a storage error.
Duplicate revocations and rolled-back writes do not create events.
The TLS listener supports bounded waiting and wakes after a revocation commit.
Other issuer connections remain usable during that wait.

The checked client validates the pinned issuer, requested cursor, batch range, event order, and target identities.
The production IssuerWatcher owns a dedicated client thread and a bounded consumer queue.
Its world-facing poll method does not wait for network I/O.
Queue overflow, invalid responses, issuer errors, and connection failure disable the watcher.
Failure discards queued batches and reports a permanent error. The watcher does not reconnect automatically.
The spatial-issuer-watch adapter provides framed updates, nonblocking output, and supervisor-controlled shutdown.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 382 core checks, including 17 new revocation-feed checks.
- All 51 harness checks.

Independent checks cover real SQLite rollback, restart, pagination, concurrent TLS peers, invalid replies, and queue overflow.
The production watcher on sb16-2 received a revocation committed by identityd on sb16.
An isolated mutation removed the missing-history check. The negative test detected it.
The mutation never entered production.
Two cross-host fixture setup attempts ended before notification testing. Their causes and corrected coordination are recorded.
The final transfer used the coordinator's existing verified SSH connections.
Disposable fixture keys, certificates, databases, and transfer archives were removed.

All 137 source hashes match reviewed local files and both hosts.
The acceptance lock, locked assertions, core transport, oracles, and native witness remain unchanged.
The issuer.proto extension preserves existing fields and database schema 3.
Evidence is in evidence/identity-watch-2026-10-07. The contract is docs/ISSUER_WATCH.md.
No host configuration, permanent service, operator session, or actual David credential changed.
Local-model review remains waived under David's hardware-failure instruction.

The feed is a dependency for authenticated world sessions. World integration remains incomplete.
A successful issuer revoke response confirms its commit, not cancellation in every world.
Current action checks and immediate live input, observation, and control revocation remain required.
World admission, nonempty bootstrap profiles, and scoped grants remain required.
All remaining M01-M16 and H01-H08 requirements stay mandatory. Full Spatial-M1 remains incomplete.
Physical controls, performance, and native-session acceptance remain pending.
Next: identity.world_session through the checked issuer client and revocation watcher.
The full project goal remains active. No external blocker is recorded.

## 2026-10-07: M01 checked issuer client qualified

The production IssuerClient library and spatial-issuer-client adapter now connect through mutually authenticated TLS 1.3.
They verify explicit server trust, DNS subject alternative name, required protocol capabilities, and configured issuer identity and key.
Caller requests are sent only after those checks succeed.
Numeric IPv4 and IPv6 endpoints are supported. Network address and TLS identity remain separate.
Client and server share the checked TLS file loader and context setup.

The client assigns internal request IDs and restores caller IDs only after reply validation.
It checks framing, versions, unknown fields, enum values, and expected reply outcomes.
Valid issuer errors preserve the connection. Invalid replies, transport failure, and timeout disable it.
The client does not reconnect or replay after a lost reply.
Fixed deadlines include partial writes and reads. Tick callbacks run during waits and can cancel the exchange.
A thin C++ probe verifies that cancellation disables the production client without further I/O.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 365 core checks, including 24 new issuer-client checks.
- All 51 harness checks.

Production identityd verifies enrollment, actor authentication, and credential revocation through this client.
Independent TLS fault peers exercise malformed envelopes, truncation, oversized replies, slow progress, and lost responses.
An initial new fixture omitted the required proof while expecting an authorization error.
It correctly received INVALID_ARGUMENT. The corrected fixture signs a fresh challenge with an unauthorized key.
The assertion and issuer behavior were not weakened. Both initial and corrected focused logs are retained.

A production C++ client on sb16-2 connected directly to identityd on sb16.
It completed enrollment and fresh actor authentication with independently verified issuer credential signatures.
An isolated mutation disabled server hostname verification. The negative test detected the unauthorized connection.
The mutation never entered production. Disposable cross-host keys, certificates, databases, and transfer archives were removed.

All 132 source hashes match reviewed local files and both hosts.
The acceptance lock, locked assertions, core transport, oracles, and native witness remain unchanged.
The existing TLS fixture now accepts IPv6 listener addresses. Its assertions remain unchanged.
issuer.proto adds a client configuration message. Existing wire fields and database schema 3 remain unchanged.
Evidence is in evidence/identity-client-2026-10-07. The contract is docs/ISSUER_CLIENT.md.
No host configuration, permanent service, operator session, or actual David credential changed.
Local-model review remains waived under David's hardware-failure instruction.

The checked transport does not create world sessions or grant actor authority.
Consumers must still match authentication subjects and context before admission or execution.
Authenticated world entry, current action checks, and immediate live input and observation revocation remain incomplete.
Nonempty bootstrap profiles and scoped grants remain required.
All remaining M01-M16 and H01-H08 requirements stay mandatory. Full Spatial-M1 remains incomplete.
Next: identity.world_session integration through the production issuer client.
The full project goal remains active.

## 2026-10-07: M01 issuer TLS service listener qualified

The production issuer now accepts framed Protocol Buffers over mutually authenticated TLS 1.3.
Trusted service connections use the same issuer handlers and persistent authority as the management pipe.
Service certificates permit transport access. Owner and actor signatures still control their respective operations.
The supervisor supplies a listening TCP socket and an explicit private configuration.
EOF on its lifetime channel closes the listener and all peers.

Connections have fixed handshake, request, and reply deadlines, plus an idle timeout.
Each connection has a one-MiB frame limit. A configured connection cap bounds concurrent peers.
Malformed protobuf produces typed errors. Invalid TLS, truncation, excessive frames, and expiry close the affected peer.
The nonblocking loop preserves progress for other peers.
TLS 1.2, plaintext, missing certificates, untrusted certificates, and expired certificates are rejected.
Keys and trust files are opened through checked descriptors. Encrypted or unsafe private keys fail without prompting.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 341 core checks, including 23 new TLS transport checks.
- All 51 harness checks.

An independent Python TLS client on sb16-2 connected directly to identityd on sb16.
It verified server trust and name, the expected issuer identity, enrollment, and the issuer credential signature.
It then completed fresh, context-bound actor authentication through the production handlers.
Only disposable fixture identities and certificates were used. Their transfer archives and temporary state were removed.

An isolated mutation allowed peers without a service certificate.
The independent negative test detected the unauthorized application response.
The mutation never entered production.
All 125 source hashes match the reviewed local files and both hosts.
The acceptance lock, existing assertions, core transport, oracles, and native witness remain unchanged.
issuer.proto adds a supervisor TLS configuration message. Existing wire field meanings and database schema 3 remain unchanged.

Evidence is in evidence/identity-tls-2026-10-07. The contract is docs/ISSUER_TLS.md.
No system service, package, firewall, login setting, or permanent certificate installation changed.
Local-model review remains waived under David's hardware-failure instruction.

This increment qualifies the issuer listener. The production checked client remains required.
Authenticated world entry and immediate revocation of live input, observation, and control remain incomplete.
Nonempty bootstrap profiles and scoped grants remain required.
All remaining M01-M16 and H01-H08 requirements stay mandatory. Full Spatial-M1 remains incomplete.
Next: identity.issuer_client, then identity.world_session integration.
The full project goal remains active.

## 2026-10-07: M01 issuer revocation and authentication backend qualified

The production issuer now persists credential and actor revocation with an independent security revision.
Owner-signed decisions use revision checks and real SQLite transactions.
Credential revocation preserves other credentials. Actor revocation disables all credentials for that identity and prevents new attachment.
Committed revocation removes affected pending authentication challenges. Failed writes preserve still-valid proofs.

Authentication verifies the issuer signature, exact registered credential, and current actor and credential status.
A fresh actor proof binds the issuer, incarnation, nonce, credential, intended service, and connection context.
Completion consumes the challenge and rechecks current status.
Results derive identity from issuer records. They do not grant world admission or workspace authority.
Protected service consumers must match the returned context to their own pending authentication.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 existing P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 318 core checks, including 24 new revocation and authentication checks.
- All 51 harness checks.

Independent OpenSSL proofs, direct SQLite reads, real write aborts, and process death verify the backend.
An isolated mutation ignored credential revocation when calculating active status.
The restart counterexample detected fresh authentication with that revoked credential. The mutation never entered production.
All 120 source hashes match reviewed local files and both hosts.
The acceptance lock, locked assertions, core transport, oracles, and native witness remain unchanged.
Enrollment assertions remain unchanged. Their shared helpers moved to a fixture class.
The version-1 migration fixture now removes later security tables when preparing its original schema.

Evidence is in evidence/identity-revocation-2026-10-07. The contract is docs/IDENTITY_REVOCATION.md.
The issuer.proto extension preserves existing fields and adds current status, revocation, and authentication messages.
Schema version 3 adds security_state and revocation tables.
Explicit migration from versions 1 and 2 preserves issuer identity, keys, policy, and existing credential bytes.
Only disposable test issuers were initialized or migrated. No actual David credential changed.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.

This qualification covers the issuer backend. It does not establish revocation across active world sessions.
Authenticated world entry, protected transport, current action checks, and immediate input and observation cancellation remain required.
Nonempty bootstrap profiles, scoped grants, and dependent-grant revocation remain M01 and M02 integration work.
All remaining M01-M16 and H01-H08 requirements stay mandatory. Full Spatial-M1 remains incomplete.
Next: identity.world_session and production integration of current issuer status.
The full project goal remains active.

## 2026-10-07: M01 actor enrollment and signed credentials qualified

The production issuer now persists proven enrollment requests, actor identities, and signed credentials.
Manual approval creates an actor and credential in one SQLite transaction.
The owner can reject a request or explicitly attach a proven key to an existing actor.
Human and agent identities use the same rules. Actor and credential identities remain separate.
Fresh proofs recover the same durable result after restart without duplicate issuance.

Owner approval and approve_always use one credential-issuance implementation.
The current resolver supports only the immutable empty-v1 profile, which grants no authority.
Unknown profiles fail before issuance commits. Nonempty profiles and bootstrap grant records remain required work.
Changing policy does not approve old pending requests or change existing credentials.
Signatures alone do not establish current actor status, admission, or workspace authority.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 existing P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 294 core checks, including 25 new enrollment checks and the existing 18 issuer checks.
- All 51 harness checks.

Independent OpenSSL commands verify issued credentials.
Real SQLite aborts roll back partial issuance. Direct database reads and process restarts verify persistence.
An isolated mutation trusted the proof-supplied key instead of the stored challenge key.
The unrelated-key counterexample detected the unauthorized enrollment. The mutation never entered production.
All 119 source hashes match reviewed local files and both hosts.
The acceptance lock, locked assertions, core transport, oracles, and native witness remain unchanged.
Existing issuer tests only share their fixture helpers. Their assertions remain unchanged.

Evidence is in evidence/identity-enrollment-2026-10-07. The contract is docs/ENROLLMENT.md.
The issuer.proto interface adds enrollment messages without changing existing field numbers or meanings.
Schema version 2 adds actor, enrollment, and credential tables.
Version 1 requires explicit identityd --migrate. Migration preserves the issuer key, owner, identity, and policy.
No actual David credentials or live issuer database were created or migrated.
No package, host setting, or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.

Manual enrollment is qualified with the empty profile. Automatic issuance has the same bounded profile limitation.
M01 still requires nonempty bootstrap grants, credential and actor revocation, protected transport, and authenticated world sessions.
All remaining M01-M16 and H01-H08 requirements stay mandatory. Full Spatial-M1 remains incomplete.
Next: identity.credential_revocation and identity.actor_revocation in the production issuer.
The full project goal remains active.

## 2026-10-07: M01 persistent issuer control qualified

The production identityd service now owns a persistent issuer identity and enrollment-policy configuration.
The new issuer.proto schema defines its versioned protobuf interface.
Explicit local initialization binds the owner's public key and creates a separate issuer key.
Normal startup preserves identity and rejects missing, mismatched, unsafe, or unknown state.
One process holds the issuer directory's authority lock.

Policy reads and changes require a fresh signature from the configured owner.
Challenges bind the issuer, service incarnation, nonce, owner, operation, purpose, and audience.
Completion consumes the challenge before verification or execution.
Replay, expiry, another key, altered content, and pre-restart challenges cannot authorize changes.
SQLite transactions preserve policy and enforce expected revisions.
The bootstrap profile remains a stored reference. Enrollment must resolve it before any grant issuance.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 existing P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 269 core checks, including 18 new issuer checks.
- All 51 harness checks.

Independent OpenSSL signatures and direct SQLite reads check the production service.
Real process tests cover restart, SIGKILL, expiry, authority locks, and revision conflicts.
An isolated mutation bypassed owner verification. The outsider-key counterexample detected the unauthorized policy read.
All 118 source hashes match reviewed local files and both hosts.
The existing acceptance lock, assertions, schemas, oracles, and native witness remain unchanged.

Evidence is in evidence/identity-issuer-2026-10-07. The contract is docs/ISSUER_CONTROL.md.
The service uses supervisor-owned stdin/stdout. It does not expose a network listener.
Only identity.key_material and identity.issuer_control are qualified in M01.
Enrollment, bootstrap grant issuance, revocation, protected network transport, and authenticated world sessions remain unfinished.
The full M01-M16 and H01-H08 qualification requirements remain mandatory.
No package, host setting, operator session, or actual David credential changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: identity.manual_enrollment with actor records and independently verified issued credentials.
The full project goal remains active.

## 2026-10-07: M01 production key material qualified

The spatial-identity library and spatial-key executable provide persistent Ed25519 keys and purpose-bound signatures.
Their production interfaces use the new versioned key.proto schema.
Generation publishes a complete private key without replacing an existing identity.
Loading rejects unsafe file permissions, links, invalid key content, and unsupported key algorithms.
Signature verification requires an independently selected public key and the exact signed purpose and payload.
The key utility does not authenticate an actor or grant authority by itself.

Both hosts passed Debug and ASan/UBSan qualification:

- All 151 existing P00-P10 acceptance cases, with zero failures, errors, skips, or unrun baseline cases.
- All 251 core checks, including 16 new key checks.
- All 51 harness checks.

Independent OpenSSL commands verify signature interoperability and public key extraction.
Real filesystem checks cover private permissions, persistence, malformed keys, and concurrent creation with one winner.
An isolated mutation removed signed purpose binding. The counterexample detected a signature accepted for another purpose.
All 113 source hashes match reviewed local files and both hosts.
The existing acceptance lock, assertions, schemas, oracles, and native witness remain unchanged.
The new identity schema extends the build without changing the locked core transport.

Evidence is in evidence/identity-keys-2026-10-07.
The contract is docs/IDENTITY.md. The mandatory full profile is tasks/spatial-m1.json.
The profile retains all M01-M16 and H01-H08 requirements. It is not an executed completion report.
Only identity.key_material is qualified in M01.
Issuer control, enrollment, bootstrap policy, challenge freshness, revocation, and authenticated world sessions remain unfinished.
The prior fixture actor entry point remains a trusted management interface.
No package, host setting, operator session, or actual David credential changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: identity.issuer_control and persistent issuer enrollment state through the production identity service.
The full project goal remains active.

## 2026-10-07: P10 automated walkthrough qualification complete

All 151 P00-P10 acceptance cases pass on sb16 and sb16-2 in Debug and ASan/UBSan builds.
There are zero failures, errors, skipped cases, or unrun cases in this baseline suite.
Each P10 story also passed its focused Debug check on both hosts.
The existing production components already satisfy these integration stories. No further runtime change was needed.

The stories verify:

- Actor observation, picking, and independently received native input agree on the target.
- Closing an actor releases its modifier and control lease so another actor can type normally.
- An actor crosses rooms, uses a real X client, runs a confined process, and returns.
- Workspace selection survives room travel. Real file output and Git branch identity remain correct.

An isolated mutation omitted input release during actor closure.
The locked walkthrough rejected the resulting BUSY response when another actor tried to control the application.
The mutation never entered production. Acceptance assertions and native witnesses remain unchanged.
All 108 source hashes match both hosts and the reviewed production files.
The preceding 235 core and 51 harness checks remain applicable to these unchanged sources in both build profiles.
The new Debug baselines also reran all 51 harness checks and the full preceding P09 gate.

Evidence and independent native event logs are in evidence/walkthroughs-2026-10-07.
See docs/WALKTHROUGHS.md for exact commands, source pins, limits, and recovery.
P00-P10 are complete for the automated baseline profile.
Mandatory Spatial-M1 requirements M01-M16 and hardware requirements H01-H08 remain unfinished.
Fixture actor identities, software displays, and scripted stories do not establish those requirements.
No host configuration, operator-session, schema, or runtime-source change was required for P10.
Local-model review remains waived under David's hardware-failure instruction.
Next: define the mandatory M01 qualification contract and implement cryptographic enrollment.
The full project goal remains active.

## 2026-10-07: P09 recovery qualification complete

Surface validation now checks the current mapped native identity and resource generation.
Resize, unmap, destruction, replacement, and compositor restart invalidate old handles.
Validation does not grant actor authority, transfer control, or inject input.
World placement remains independent of native resource identity.

Both hosts passed Debug and ASan/UBSan qualification:

- All 148 P00-P09 acceptance cases, including all ten P09 primitives.
- All 235 core checks, including four new native handle checks.
- All 51 harness checks.
- Zero failures, errors, or skipped cases. Three P10 cases remain unrun.

Independent X clients verify real resource changes, received input, and restart with the same surviving XID.
An isolated mutation accepted stale handles. The locked native witness rejected that behavior.
Its initial unused-variable build failure and corrected mutation evidence are retained.
No production assertion was weakened.
All 108 source hashes match reviewed local files and both hosts.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.

Evidence is in evidence/recovery-complete-2026-10-07.
See docs/SURFACE_HANDLES.md and docs/RECOVERY_BOUNDARIES.md for scope, commands, and recovery.
P09 is complete for its automated qualification profile.
P10, mandatory Spatial-M1 requirements M01-M16, and hardware requirements H01-H08 remain unfinished.
These software-display results do not establish full Spatial-M1 or LOCAL_READY.
No database migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: story.observe_act_verify and the P10 walkthrough baseline.
The full project goal remains active.

## 2026-10-07: P09 durable native surface slots

Live native surfaces now attach explicitly to durable world slots.
Each slot supplies one authoritative room, pose, and size.
Surface placement updates the slot through the world transaction. Direct slot edits update the same live placement.
Failed writes preserve both durable and live placement.
Concurrent attachment permits one winner. Native transients follow their parent.
Destroy and restart clear live associations without deleting the slot.
Slot deletion removes presentation without terminating the application.

The new checks exposed an existing native lifecycle race on both hosts.
Native input inspection can reject an unmapped window without proving destruction.
The compositor now preserves identity until actual destruction evidence and invalidates the unavailable resource generation.
The original failing logs and source manifests are retained. No assertion was weakened.

Both hosts passed Debug and ASan/UBSan qualification after the correction:

- All 231 core checks, including nine new slot and lifecycle checks.
- All 51 harness checks.
- 147 acceptance cases, including all 138 preceding P00-P08 cases.

The full P00-P09 gate executed 148 of 151 cases.
It reports one assertion failure, zero errors, zero skipped, and three unrun P10 cases.
Nine P09 primitives pass. Stale-handle validation remains incomplete.
P09, full Spatial-M1, and native hardware qualification remain unfinished.

Independent X clients verify pixels, native geometry, restart identity, deletion, transients, and 64 resize/unmap/remap cycles.
All 108 source hashes match reviewed local files and both hosts.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-slots-2026-10-07.
See docs/DURABLE_SURFACE_SLOTS.md for scope, commands, and recovery.
No database migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: surface.stale_handle using current native resource identity and generation.
The full project goal remains active.

## 2026-10-07: P09 real Git completion crash recovery

Workspaced now exits after real worktree creation and its flushed completion receipt, before the ready acknowledgment.
The world authority detects broker death before sending an operation reply.
Recovery reconciles matching pending effects once and preserves uncommitted files.
Missing or corrupt receipts and changed Git effects remain uncertain. Recovery never replays those creations.
Fault controls require independent fixture enablement in worldd and workspaced.
Normal daemons reject them. Armed state never persists across restart.

Both hosts passed Debug and ASan/UBSan qualification:

- All 222 core checks, including six new Git recovery checks.
- All 51 harness checks.
- 146 acceptance cases, including all 138 preceding P00-P08 cases.

The full P00-P09 gate executed 148 of 151 cases.
It reports one assertion failure, one error, zero skipped, and three unrun P10 cases.
Eight P09 primitives pass. Durable slots and stale-handle validation remain incomplete.
P09, full Spatial-M1, and native hardware qualification remain unfinished.

Independent witnesses verify status 88, pending registry rows, completion receipts, and the real Git branch before restart.
They verify retry, fixture authority, isolation, lost pending arms, and conservative recovery after external changes.
All 108 source hashes match reviewed local files and both hosts.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-git-2026-10-07.
See docs/GIT_CRASH_RECOVERY.md and docs/RECOVERY_BOUNDARIES.md for scope, commands, and recovery.
No database migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: surface.durable_slot using durable world placement and current live native surfaces.
The full project goal remains active.

## 2026-10-07: P09 real SQLite write-failure recovery

Worldd now injects storage.next_write at SQLite's actual xWrite boundary.
The private VFS returns SQLITE_FULL with ENOSPC once.
Failed creates, updates, deletes, and checkpoints preserve the previous durable state.
A later valid write succeeds. Faults remain fixture-only and never persist across restart.
Normal daemons retain SQLite's default VFS.

Both hosts passed Debug and ASan/UBSan qualification:

- All 216 core checks, including six new storage checks.
- All 51 harness checks.
- 145 acceptance cases, including all 138 preceding P00-P08 cases.

The full P00-P09 gate executed 148 of 151 cases.
It reports one assertion failure, two errors, zero skipped, and three unrun P10 cases.
Seven P09 primitives pass. Git-effect faults, durable slots, and stale-handle validation remain incomplete.
P09, full Spatial-M1, and native hardware qualification remain unfinished.

A native component witness verifies sixteen VFS lifecycles with real SQLite files.
It checks isolated faults, retry, WAL forwarding, unchanged default registration, and database reopening.
All 108 source hashes match reviewed local files and both hosts.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-storage-2026-10-07.
See docs/STORAGE_FAULTS.md and docs/RECOVERY_BOUNDARIES.md for scope, commands, and recovery.
No database migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: recovery.git_side_effect_boundary after real Git worktree creation and before the ready acknowledgment.
The full project goal remains active.

## 2026-10-07: P09 committed state survives a lost reply

Worldd now supports a real exit after successful COMMIT and before sending its reply.
The committed data and revisions survive exactly once.
A stale retry cannot apply the edit again.
A failed COMMIT returns STORAGE_ERROR and preserves the armed post-commit fault.
Both commit hooks remain restricted to explicit fixture mode.

Both hosts passed Debug and ASan/UBSan qualification:

- All 210 core checks, including eleven recovery checks.
- All 51 harness checks.
- 144 acceptance cases, including all 138 preceding P00-P08 cases.

The full P00-P09 gate executed 148 of 151 cases.
It reports one assertion failure, three errors, zero skipped, and three unrun P10 cases.
Six P09 primitives pass. Storage-write faults, Git-effect faults, durable slots, and stale-handle validation remain incomplete.
P09, full Spatial-M1, and native hardware qualification remain unfinished.

An isolated mutation moved the post-commit exit before COMMIT.
The locked acceptance witness rejected the unchanged durable label.
All 104 source hashes match reviewed local files and both hosts.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-after-commit-2026-10-07.
See docs/RECOVERY_BOUNDARIES.md for exact commands, fixture controls, and recovery steps.
No database migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: recovery.storage_failure at the actual SQLite write boundary.
The full project goal remains active.

## 2026-10-07: P09 real exit before world commit

Worldd can now exit at its actual SQLite boundary immediately before a write transaction commits.
The conformance supervisor detects the real worker death and closes transport.
Restart preserves the previous durable state.
Fault controls require explicit fixture enablement and remain unavailable through actor actions.
Reads and rejected writes preserve the pending arm. Restart never restores an armed fault.

Both hosts passed Debug and ASan/UBSan qualification for this primitive:

- All 206 core checks, including seven new recovery checks.
- All 51 harness checks.
- 143 acceptance cases, including all 138 preceding P00-P08 cases.

The full P00-P09 gate executed 148 of 151 cases.
It reports one assertion failure, four errors, zero skipped, and three unrun P10 cases.
Five P09 primitives pass: before-commit exit, preview rollback, repeated kills, worker identity, and worker death.
The remaining failures identify unimplemented post-commit, storage-write, Git-effect, surface-attachment, and stale-handle operations.
P09 remains incomplete. Full Spatial-M1 and native hardware qualification remain mandatory.

All 104 source hashes match reviewed local files and both hosts.
The acceptance lock, locked tests, schemas, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-before-commit-2026-10-07.
See docs/RECOVERY_BOUNDARIES.md for fixture controls, recovery, and exact qualification commands.
No schema migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: recovery.after_commit_before_reply.
The full project goal remains active.

## 2026-10-07: P09 recovery transport migration

Migration 0.14.0 adds protobuf requests for the existing recovery contract.
The requests cover fault arming, durable slot attachment, and live surface-handle validation.
Fault controls remain separate from actor actions.
All 151 acceptance identifiers, existing assertions, independent oracles, and native witnesses remain unchanged.
The migration records each changed lock hash explicitly.

Both hosts passed Debug and ASan/UBSan regression qualification:

- All 138 P00-P08 acceptance tests.
- All 199 core checks.
- All 51 harness checks, including eight new transport checks.

Three P09 diagnostics now reach production instead of failing in the codec.
Fault arming and slot attachment return UNIMPLEMENTED errors.
The stale-handle assertion fails because validation still returns UNIMPLEMENTED instead of STALE_TARGET.
Coverage is 138 passed, one assertion failure, two errors, zero skipped, and ten unrun.
P09 remains incomplete. No recovery effect is claimed by this transport migration.

All 103 source hashes match reviewed local files and both hosts.
Evidence is in evidence/recovery-transport-2026-10-07.
See docs/RECOVERY_TRANSPORT_MIGRATION.md for the exact scope and qualification commands.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: implement recovery.before_commit at the actual world transaction boundary.
Full Spatial-M1, native hardware qualification, and the complete project goal remain active.

## 2026-10-07: P08 confined workspace processes complete

Actors now launch real applications through the production workspace broker.
The broker intersects workspace policy, actor membership, and the requested application profile.
Applications see their real worktree at /project in separate namespaces.
Read-only authority applies to both project files and writable Git administration.
Ordinary confined Git changes the actual worktree and its bare backing repository.

The namespace supervisor confirms launch and actual child exit through Protocol Buffers.
Launch records bind actor, operation ID, workspace, arguments, and profile.
Retries cannot duplicate execution or silently change workspace context.
Lost supervisors and crash-interrupted operations remain uncertain and are never replayed automatically.
Revocation stops affected application namespaces, including detached descendants.
Actor disconnect preserves the workspace application. Other actors' applications remain independent.

Both hosts passed final Debug and ASan/UBSan qualification:

- All 138 P00-P08 acceptance tests, including all 25 P08 primitives.
- All 199 core checks, including twelve process counterexamples.
- All 43 harness self-tests.

There are zero assertion failures, errors, or skips. Thirteen later acceptance tests remain unrun.
An isolated writable-mount mutation failed the locked read-only acceptance witness.
All 102 source hashes match reviewed local files and both hosts.
Evidence is in evidence/processes-2026-10-07.
P08 is complete for automated software qualification.

Execution storage advances to schema version 4 through explicit migration.
Both hosts now have a project-specific Bubblewrap copy and an AppArmor user-namespace profile.
Their global namespace restrictions, ordinary login sessions, displays, and SSH recovery remain unchanged.
See docs/WORKSPACE_PROCESSES.md for exact bootstrap, migration, rollback commands, and resource limits.
Installed files and the bootstrap installer have recorded hashes.

Aggregate quotas, confined GUI access, and full M03/M04 qualification remain incomplete.
Full Spatial-M1 and native hardware qualification remain mandatory.
The acceptance lock, all locked files, and public protobuf schemas remain unchanged.
Local-model review remains waived under David's hardware-failure instruction.
Next: P09, starting with recovery.before_commit.
The full project goal remains active.

## 2026-10-07: P08 workspace authority and actor context

Workspaced now owns workspace definitions and membership in its execution database.
Worldd owns explicit workspace availability and durable selection per actor.
Rooms do not own execution context. Room transitions preserve selection.
Creation, membership, availability, selection, and cabinet placement remain separate operations.

Selection requires the intersection of workspace policy and member authority.
Actor reconnect revalidates saved context. Revocation and unavailability clear affected selections.
Broker revocation remains effective if the separate world cleanup transaction fails.
Actor requests recheck authority before using saved context.
Workspace-bound worktrees cannot be retired or shared with another independent workspace.

A new private protobuf envelope carries broker management and authorization queries.
Public core and input schemas remain unchanged.
Explicit migrations advance execution storage to version 3 and world storage to version 2.
See docs/WORKSPACE_CONTEXT.md for migration commands, boundaries, and recovery notes.

Both hosts passed Debug and ASan/UBSan qualification:

- All 113 preceding P00-P07 acceptance tests and seventeen P08 acceptance tests.
- All 187 core checks, including thirteen new workspace checks.
- All 43 harness self-tests.

The full 138-case P00-P08 gate ran in each build on both hosts.
Coverage is 130 passed, zero assertion failures, eight errors, zero skipped, and thirteen unrun later tests.
All eight errors report unimplemented process execution.
P08 remains incomplete. Full Spatial-M1 and native hardware qualification remain pending.
All 98 source hashes match reviewed local files and both hosts.
Evidence is in evidence/workspace-context-2026-10-07.

The first qualification attempt detected an accidental locked-test edit.
That edit was restored exactly. A separate unlocked test covers corruption under the current world schema.
The acceptance lock and all locked files remain unchanged. Evidence preserves the failed integrity run.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: confined process execution, starting with process.workspace_cwd.
The full project goal remains active.

## 2026-10-07: P08 conservative worktree removal

Workspaced now removes clean Git worktrees while preserving their actual files in private retained storage.
It unregisters the worktree from real Git and preserves the branch.
Private Git refs also preserve detached commits.
Dirty files, ignored data, hidden index flags, active locks, and submodule entries prevent removal.

Removal journals bind operation IDs and preserve uncertain effects.
Completion receipts allow recovery after a failed acknowledgment without repeating filesystem changes.
Retries preserve new files created at the original path.
Open file descriptors continue writing to retained files.
Interrupted retirement and contradictory recovery paths remain uncertain and preserved.
Automatic restoration and permanent cleanup of retired data remain unimplemented.

The execution registry advances to schema version 2 through an explicit migration.
Normal startup refuses a version 1 database instead of silently changing it.
See docs/WORKSPACE_REMOVAL.md for the migration command and recovery inspection procedure.
Workspace-bound removal protection remains pending with workspace integration.

Both hosts passed Debug and ASan/UBSan qualification:

- All 113 preceding P00-P07 acceptance tests and thirteen P08 acceptance tests.
- All 174 core checks, including thirty-nine broker checks.
- All 43 harness self-tests.

The full 138-case P00-P08 gate ran in each build on both hosts.
Coverage is 126 passed, zero assertion failures, twelve errors, zero skipped, and thirteen unrun later tests.
All twelve errors report UNIMPLEMENTED for workspace context or process execution.
P08 remains incomplete. Full Spatial-M1 and native hardware qualification remain pending.
All 96 source hashes match reviewed local files and both hosts.
Evidence is in evidence/workspace-removal-2026-10-07.

Locked tests, oracles, native witness sources, and protobuf schemas remain unchanged.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: workspace creation, membership, availability, and actor selection through the production broker.
The full project goal remains active.

## 2026-10-07: P08 live Git status

Workspaced now reads the current commit, branch, and file status from real Git.
External commits, branch changes, detached HEAD, and unborn branches remain observable.
NUL-delimited parsing preserves unusual UTF-8 filenames and both sides of renames.
Tracked changes, merge conflicts, untracked files, and ignored data remain visible.
The broker rejects changed administrative authority links before reading status.
Oversized results return a bounded error without changing files or closing the channel.

Both hosts passed Debug and ASan/UBSan qualification:

- All 113 preceding P00-P07 acceptance tests and eleven P08 acceptance tests.
- All 162 core checks, including twenty-seven broker checks.
- All 43 harness self-tests.

The full 138-case P00-P08 gate ran in each build on both hosts.
Coverage is 124 passed, one failed, thirteen errors, zero skipped, and thirteen unrun later tests.
The failure and errors cover unimplemented removal, workspace context, and process execution.
P08 remains incomplete. Spatial-M1 and native hardware qualification remain pending.

The first implementation rejected valid Git common-directory links with a trailing separator.
The corrected implementation passed all focused checks and full regression qualification.
Evidence preserves both runs in evidence/workspace-status-2026-10-07.
All 96 final source hashes match reviewed local files and both hosts.
See docs/WORKSPACE_STATUS.md for exact commands, evidence, and limitations.

The current status interface rejects filenames containing invalid UTF-8 bytes.
Git status does not lock external writers or reveal changes hidden by every index flag.
Safe removal requires separate protection and remains the next primitive.
Locked tests, oracles, native witness sources, and protobuf schemas remain unchanged.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
The full project goal remains active.

## 2026-10-07: P08 real Git creation through workspaced

The production workspaced process now owns a separate durable execution registry.
The session supervisor starts it independently of worldd and supplies a private protobuf channel.
Repository import creates real bare backing stores with independent Git objects.
Worktree creation follows real branch rules and records durable operation identities.
Matching retries preserve uncommitted files. Changed requests return OPERATION_CONFLICT.

A private completion receipt distinguishes finished checkout from partial Git metadata.
Recovery reconciles witnessed effects against actual Git state.
Uncertain imports and worktrees remain preserved for explicit repair.
Administrative Git uses controlled configuration, a restricted environment, and supervised process groups.
The wrapper does not implement generic application confinement.

Both hosts passed Debug and ASan/UBSan qualification for this increment:

- All 113 preceding P00-P07 acceptance tests and eight P08 acceptance tests.
- All 152 core checks, including seventeen broker checks.
- All 43 harness self-tests.

The full 138-case P00-P08 gate ran in every build on both hosts.
Coverage is 121 passed, one failed, sixteen errors, zero skipped, and thirteen unrun later tests.
Dirty removal fails because its handler still returns UNIMPLEMENTED.
The other errors cover unimplemented Git status, removal, workspace context, and process execution.
P08 remains incomplete.

An isolated mutation removed independent Git object copying.
The external inode witness detected the resulting shared object storage.
All 94 final source hashes match local reviewed files and both hosts.
Evidence is in evidence/workspace-git-2026-10-07.
See docs/WORKSPACE_GIT.md for boundaries, recovery behavior, exact commands, and limitations.
Locked tests, oracles, native witness sources, and the protobuf contract remain unchanged.

No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Full Spatial-M1, shared-world service attachment, and hardware qualification remain pending.
Next: implement git.actual_status and external-change observation from real Git output.
The full project goal remains active.

## 2026-10-07: P08 workspace contract migration

Migration 0.13.0 assigns Git project context and process execution to workspaces.
Rooms no longer determine execution context.
Workspace creation, membership, availability, selection, and cabinet placement remain separate.
The production broker, workspace registry, and confined execution effects are still unimplemented.
New typed protobuf requests return explicit UNIMPLEMENTED errors.

The migration preserves all 141 earlier requirements through two documented test-ID changes.
Ten additional acceptance tests cover workspace authority and confined execution.
Four new harness checks validate typed transport boundaries.
The inventory now contains 151 acceptance tests and 140 primitive tickets.
See docs/WORKSPACE_MIGRATION.md and docs/workspace-migration-0.13.0.json for the correction and exact hash changes.

Both hosts passed Debug and ASan/UBSan runtime regression qualification:

- All 113 cumulative P00-P07 acceptance tests.
- All 135 core checks.
- All 43 harness self-tests.

The targeted P08 real-worktree case returned UNIMPLEMENTED from production on both hosts.
Coverage is 113 passed, one diagnostic error, and 37 unrun.
P08 is not complete. Full Spatial-M1 and physical qualification remain pending.
Evidence is in evidence/workspace-contract-2026-10-07.
Final audits verify 85 source hashes, with a documented formatting-only normalization of the acceptance lock after qualification.
The original qualification lock remains in evidence. Final harness and diagnostic checks use the normalized lock.

Namespace prerequisite checks found working passwordless sudo and privileged mount/PID namespaces on both hosts.
Unprivileged user-namespace creation failed while writing uid_map.
Bubblewrap 0.9.0 is installed. These prerequisites do not prove application confinement.
No system configuration or operator session changed.
Continue without local-model review under David's hardware-failure waiver.
Next: implement git.real_worktree through the production workspace broker.
The full project goal remains active.

## 2026-10-07: P07 object manipulation complete

Actors can acquire visible, reachable objects through typed protobuf actions.
Each object has one editing lease. Each actor can hold one object.
Live previews appear consistently in rendering, picking, and observation for other actors.
Only explicit commit writes the pose through transactional, revision-checked world storage.
Cancellation, stop, close, and portal crossing discard previews and release object leases.
Conflicting edits and replacement objects invalidate old leases.
Storage failures preserve drafts for an explicit retry or cancellation.

Both hosts passed Debug and ASan/UBSan runtime qualification:

- All 113 cumulative P00-P07 acceptance tests.
- All 135 core checks, including 21 new object checks.
- All 39 harness self-tests.

Independent evidence covers native occlusion, preview rendering, reach, competing leases, storage failure, and crash recovery.
A real-time run verifies configurable reach, object size, and material color.
An isolated mutation incorrectly saved live previews.
The crash witness detected that changed durable placement.
All 84 final source hashes match the reviewed source and both hosts.

P07 is complete for automated software qualification.
Legacy coverage is 113 passed, zero current failures, and 28 unrun.
Evidence is in evidence/objects-2026-10-07.
See docs/OBJECTS.md for migration 0.12.0, lease behavior, and qualification limits.
The schema, codec, and fixture configuration hashes change explicitly.
Locked acceptance assertions, oracles, and native witness sources remain unchanged.

Next: workspace-owned Git worktrees and process execution.
The older room-owned P08 contract must first be corrected through an explicit recorded migration.
Fixture grants and software-display tests do not establish full Spatial-M1 authority, confinement, or physical qualification.
All remaining Spatial-M1 and hardware milestones remain mandatory.
No system configuration or operator session changed.
The full project goal remains active.


## 2026-10-07: P07 portal navigation

Actors now cross portal apertures through the production motion and collision path.
Crossing preserves remaining displacement and maps orientation without an extra turn.
Source walls block entry. Destination walls constrain movement and reject obstructed landings.
Portal chains and zero-distance cycles have bounded processing budgets.

Crossing releases native keys, buttons, focus, capture, and application leases.
Other actors retain their input state. Applications and runtime processes remain alive.
Live actor locations prevent room deletion before checkpointing.
Checkpoints restore destination rooms and poses with fresh session tokens.
Portal outlines render from both sides with open apertures.
Rendering, picking, and observation use the same outlines.

Both hosts passed Debug and ASan/UBSan runtime qualification:

- All 98 cumulative P00-P06 acceptance tests.
- All five focused portal acceptance tests and the three previous P07 cases.
- All 114 core checks, including 19 portal counterexamples.
- All 39 harness self-tests.

An isolated mutation discarded remaining movement after crossing.
The rotated-portal witness detected that error.
All 82 final source hashes match the reviewed source and both execution hosts.
Evidence is in evidence/portals-2026-10-07.
See docs/PORTALS.md for migration 0.11.0, numerical boundaries, and qualification limits.
Only the documented protobuf schema hash changes in the acceptance lock.
All locked acceptance assertions, oracles, and native witness sources remain unchanged.

Legacy coverage is 106 passed, zero current failures, and 35 unrun.
P07 remains incomplete. Object grabbing, preview movement, commit, cancellation, and leases are next.
These software-display results do not establish physical-device, native GPU, or full workspace-security acceptance.
All additional Spatial-M1 and hardware milestones remain mandatory.
No system configuration or operator session changed.
The full project goal remains active.


## 2026-10-07: P07 swept collision and visible barriers

Actor motion now uses a continuous sphere sweep and preserves sliding motion along barriers.
The same transformed bounds supply collision, rendering, picking, and observation.
Observation combines visible barrier faces into one entity record.
Turning motion uses bounded chord subdivision to prevent missed collisions along an arc.

Both hosts passed Debug and ASan/UBSan runtime qualification:

- All 98 cumulative P00-P06 acceptance tests.
- The focused P07 sweep, sliding, and observation tests.
- All 95 core counterexamples, including 16 collision checks.
- All 39 harness self-tests.

The new checks cover rounded contacts, tangency, overlap, 180 randomized sweeps, fast motion, and curved paths.
They also cover geometry updates, all six visible faces, and native surface occlusion.
An isolated mutation removed barriers from actor motion while preserving their visible geometry.
The fast-motion witness detected the resulting wall crossing.
All 79 final source hashes matched the reviewed files and both execution hosts.

Legacy coverage is 101 passed, zero current failures, and 40 unrun.
P07 remains incomplete. Portals and object manipulation are next.
Evidence is in evidence/collision-2026-10-07.
See docs/COLLISION.md for migration 0.10.0, numerical limits, contact budgets, and qualification details.
The explicit migration changes schema, codec, and fixture configuration hashes.
All locked acceptance assertions, oracles, and native witness sources remain unchanged.

These software-display results do not qualify physical input, native GPU timing, or full workspace security.
All additional Spatial-M1 milestones remain mandatory.
No system configuration or operator session changed in this increment.
The full project goal remains active.

## 2026-10-07: P07 observation increment

Actors can now request typed protobuf observations from the production worldd action boundary.
The compositor identifies visible representations with the same shader and depth testing used for rendering.
Replies include the sampled camera pose, viewport, world revision, spatial metadata, generations, and visible sample directions.
Observation uses a separate framebuffer and preserves another actor's presented view.

Both hosts passed Debug and ASan/UBSan runtime qualification:

- All 98 cumulative P00-P06 acceptance tests.
- The focused P07 observation acceptance test.
- All 79 core counterexamples, including nine new observation checks.
- All 39 harness self-tests.

An independent native client received input at the observed target's expected pixel.
A deliberate depth-test mutation failed the new occlusion test in an isolated worktree.
All 77 final source hashes matched the reviewed files and both execution hosts.
Only protobuf schema and codec hashes changed in the explicit 0.9.0 acceptance migration.
All locked acceptance assertions, oracles, and native witness sources remain unchanged.

Legacy coverage is 99 passed, zero current failures, and 42 unrun.
P07 remains incomplete. Swept collision, portals, and object manipulation are next.
Observation currently includes rendered panels and mapped native surfaces.
Other spatial representation types remain pending.
The fixture does not yet enforce workspace observation policies.
Full authority, confinement, hardware qualification, and the additional Spatial-M1 milestones remain mandatory.

Evidence is in evidence/observation-2026-10-07.
See docs/OBSERVATION.md for the raster visibility contract, transport migration, limitations, and test coverage.
No operator-session or system configuration changed in this increment.
The full project goal remains active.

## 2026-10-07: P06 captured input complete

Captured native motion now follows the actor's aim and camera pose.
It stays on the original application surface beyond the surface edges.
Additional buttons and releases preserve the last captured pointer position.
The scheduler sends pose-driven motion without nested compositor requests.
A real-time test verifies motion without query polling and expiry at the configured deadline.

Both hosts passed:

- All 98 cumulative P00 through P06 acceptance tests.
- All 70 core counterexamples and 39 harness checks in Debug and sanitizer builds.
- All 13 native input component checks against their private XLibre builds.
- The upstream server unit and modesetting symbol checks.

Independent clients witnessed signed coordinates, transformed capture planes, balanced release, and separate actor devices.
A checked XCB witness verified explicit-grab denial without capture transfer.
An isolated production mutation disabled retained capture coordinates.
The new drag test detected its incorrect release position.
The main runtime retained the correct implementation.

P06 is complete for its automated software qualification.
Legacy coverage remains 98 passed, zero current failures, and 43 unrun.
P07 rooms, portals, collision, observation, and manipulation is next.
Evidence is in evidence/captured-motion-2026-10-07.
See docs/CAPTURED_MOTION.md for protocol migration, coordinate limits, diagnostics, and exact commands.

These results use authenticated disposable Xvfb displays and software rendering.
Sanitizers cover the runtime and compositor, not the XLibre server.
Physical controls, native GPU performance, full workspace security, and the additional Spatial-M1 milestones remain pending.
Interim transient grouping does not replace broker-owned application identity.
The full project goal remains active.


## 2026-10-07: production actor input integration

worldd now routes typed pointer, keyboard, scroll, and cancellation actions through spatialwm to XLibre.
Actor sessions use distinct native input device pairs.
worldd checks grants, sequences, saved aim, current geometry, and surface generations.
Control transfer and cancellation release held native input.
Parent and transient surfaces share an interim control lease.

Both sb16 and sb16-2 passed the Debug and AddressSanitizer/UndefinedBehaviorSanitizer runs:

- All 98 cumulative P00 through P06 acceptance tests.
- All 65 core counterexamples, including nine new actor-input checks.
- All 39 harness self-tests.

Forty actor reconnects reclaimed their native devices and rejected revoked session tokens.
Independent native clients witnessed modifier release, focus transfer, cancellation, and actor isolation.
A new scroll-during-drag counterexample initially failed.
The corrected handler rejects a conflicting scroll without changing either application's input.

Retained evidence is in evidence/actor-input-2026-10-07.
The reports identify source hashes, commands, server binary hashes, and build identities.
See docs/ACTOR_INPUT.md for the explicit protobuf migration and limits.
All locked acceptance assertions and native witness sources remain unchanged.

Legacy coverage is 98 passed, zero current failures, and 43 unrun.
These tests use authenticated disposable Xvfb displays and software rendering.
The sanitizer runs cover the runtime and compositor, not the XLibre server binary.

P06 remains incomplete despite the passing baseline gate.
Captured pointer motion must follow the original surface when aim moves beyond its bounds.
Current input preserves drag release but does not implement that motion.
Full application-group authority must later use broker application identities.
The interim transient relationship is not an authorization boundary.
Native device trials, GPU qualification, and the remaining Spatial-M1 milestones remain pending.
The full project goal remains active.


## 2026-10-07: native input server component

XLibre now supplies compositor-owned actor device pairs and surface-local input routing.
The private interface uses a separate versioned protobuf schema.
The production C++ bridge validates reply framing and correlation.
Server generations invalidate stale window targets, including resize-away-and-back sequences.
Cancellation and controller disconnect release native held input.

Both hosts passed ten component checks with an independent native X client.
Those checks include overlapping windows, distinct device identities, independent modifiers, ordinary X clients, and off-screen coordinates.
Forty seat lifecycle cycles returned native device counts to baseline.
The preceding 80 acceptance tests, 56 core counterexamples, and 39 harness checks also passed on both hosts.
These results use authenticated disposable Xvfb displays and Debug builds.
See docs/NATIVE_INPUT.md and evidence/native-input-2026-10-07.

Public worldd actor actions do not call the input bridge yet.
The P06 pointer diagnostic therefore remains unresolved.
Legacy coverage remains 80 passed, one diagnostic error, and 60 unrun.
P06, full workspace security, native hardware qualification, and Spatial-M1 remain incomplete.

## 2026-10-07: pinned XLibre preparation on both hosts

Both hosts now build XLibre 25.1.9 from the recorded source commit.
The project keeps protocol headers and server binaries in private build directories.
Both builds passed the upstream unit target and modesetting symbol test.
The existing 80 acceptance tests, 56 core counterexamples, and 39 harness checks also passed on both hosts.
Those Debug runs use the pinned XLibre Xvfb binary with authenticated disposable displays.
All 70 recorded project source hashes match the reviewed files.

See docs/SERVER_PREPARATION.md for source pins, commands, limitations, package changes, and recovery.
Retained evidence is in evidence/server-2026-10-07.
The new server does not replace either operator desktop.

P06 native actor input remains unimplemented. The existing pointer diagnostic still has one error.
Legacy coverage remains 80 passed, one diagnostic error, and 60 unrun.
Native hardware and the additional mandatory Spatial-M1 gates remain pending.

The local inference endpoints currently refuse connections on port 8080 from both hosts.
Implementation continues under David's earlier instruction to work without the local agent.

## 2026-10-07: P06 native input baseline

P05 is complete at commit 36d54bad00219839ebc0de56922e0c8fc754229e.
Both hosts have that reviewed production implementation.
The independent native witness also builds with XI2 support on sb16.

The first P06 diagnostic reaches a real application surface and completes actor aim.
The pointer action returns UNKNOWN_METHOD because native actor input is not implemented.
This diagnostic has one error. It does not establish a passing P06 gate.
Current legacy coverage is 80 passed, one diagnostic error, and 60 unrun tests.
Evidence is in evidence/input-2026-10-07/sb16/baseline.

Next, add typed input actions and genuine actor-specific X server input routing.
Preserve native root coordinates, independent focus and modifiers, and balanced cancellation.
Do not substitute XSendEvent, a hidden window atlas, or a shared global input device.
The complete Spatial-M1 plan and its additional mandatory gates remain active.

## 2026-10-07: live application surfaces, P00 through P05

The compositor now imports actual X application backing pixmaps through Composite, Damage, and GLX.
worldd owns their spatial placement. Placement leaves native window coordinates unchanged.
Live identities distinguish compositor sessions and reused XIDs.
Resize, map, unmap, and destruction retire native resources and update generations.
Transients follow the parent room, rotation, translation, and scale.

Both sb16 and sb16-2 passed the normal build and the AddressSanitizer/UndefinedBehaviorSanitizer build:

- All 80 cumulative P00 through P05 acceptance tests.
- 39 harness self-tests.
- All 47 previous protocol, daemon, actor, and rendering counterexamples.
- Nine additional native surface lifecycle counterexamples.

The new checks exposed two production defects before correction.
Cyclic transient metadata could fail unrelated rendering.
Coalesced resize events could preserve an obsolete pixmap when the final dimensions matched.
Both reproductions now pass, together with the complete cumulative gate.
An isolated build with reversed texture orientation failed the independent quadrant assertion as expected.
The reviewed production sources remain unchanged by that mutation.
An initial pre-existing-client fixture also needed orderly compositor shutdown between test cases.

Retained reports, logs, and independent native pixel readbacks are in evidence/surfaces-2026-10-07.
All 69 recorded source hashes match the tested files on both hosts.
All 141 legacy acceptance IDs and assertions remain intact.
The remaining 61 legacy acceptance tests are unrun.
See docs/SURFACES.md for the implementation, protocol migration, and limits.

These results use authenticated disposable Xvfb displays and Mesa llvmpipe.
They do not qualify native GPU scanout, input devices, workspace isolation, or Spatial-M1.
P06 native actor input is next. The full project goal remains active.

Run these commands on either authorized host with new evidence directories:

~~~sh
python3 tools/qualify_core.py --through P05 --evidence-dir local/new-surface-run
python3 tools/qualify_core.py --through P05 --sanitize --build-dir build-sanitize --evidence-dir local/new-surface-sanitize
~~~

Both hosts reported these build identities:

- Debug: sha256:e575bf3eac831ec390086369cce5417f7fd10d821f45c87dd83cd70f694f4213
- Sanitizers: sha256:7061a06d9dc5edd20f3e6f67e99f4a43203679ea1a902c9df40da4325ce94857

The separate user-requested GNOME display changes are recorded in the root HOST_SETTINGS.md.
Both operator desktops have automatic locking, blanking, dimming, idle suspend, and DPMS disabled.
The normal login and SSH recovery paths remain available.

## 2026-10-07: compositor rendering, P00 through P04

worldd now starts a separate spatialwm process on an explicitly selected display.
The production compositor renders actual OpenGL frames from protobuf scene data.
Capture and visible presentation use the same framebuffer.
Actor aim uses the same room, panel poses, sizes, and clipping configuration.
No operator display is adopted automatically.
An existing window manager prevents compositor startup.

Both sb16 and sb16-2 passed:

- All 70 cumulative P00 through P04 acceptance tests.
- 39 harness self-tests.
- 13 protocol, 11 daemon, and 10 actor counterexamples.
- 13 additional rendering, configuration, process, and authorization checks.

The same checks passed with AddressSanitizer and UndefinedBehaviorSanitizer on both hosts.
An independent native X client read pixels identical to the compositor captures.
It checked three output sizes and varied panel colors.
The tests also cover clip boundaries, camera-plane crossings, and half-open panel edges.
A deliberately broken depth comparison failed the coplanar ordering counterexample.
The production depth comparison remains enabled.

Retained evidence is in evidence/render-2026-10-07.
Source hashes match the tested production files.
See docs/RENDERING.md for the implementation and protobuf migration.
All 141 legacy acceptance IDs and assertions remain intact.
The remaining 71 legacy acceptance tests are unrun.

These results use authenticated disposable Xvfb displays and Mesa llvmpipe.
They do not certify native GPU scanout, physical devices, or Spatial-M1.
P05 live application surfaces is the next milestone.
The full project goal remains active.
See docs/MILESTONES.md for the complete plan.

## Commands and build identity

Run on sb16 or sb16-2 from this folder:

~~~sh
python3 tools/qualify_core.py --through P04 --evidence-dir local/new-render-run
python3 tools/qualify_core.py --through P04 --sanitize --build-dir build-sanitize --evidence-dir local/new-render-sanitize
~~~

Use a new evidence directory.
Builds use two jobs and never acquire dependencies.
The qualifier creates authenticated disposable displays for graphics checks.
Reports record production, schema, configuration, test hashes, and binary build identity.

Both hosts reported these build identities:

- Debug: sha256:b9d3eeb9ea2bd3a2f5d47f7289d1ab8c0a2320b0a51ecd6a05486da5e07ea362
- Sanitizers: sha256:640f6bcf3e9d1c6e2b1ade298a5d385f019715c4a812a4f79ae355b71efe919d

The management channel uses trusted inherited pipes.
No public actor listener exists yet.
Use a private state directory.
Stop its authority before copying the complete directory for backup.
Restart from the same directory to resume durable world state.
Do not restore session tokens or held input.

## Previous checkpoints

Commit fd7c781 delivered actor sessions, grants, motion expiry, and atomic pose checkpoints.
Its 60 cumulative acceptance tests passed on both hosts, including sanitizer builds.
See docs/ACTORS.md and evidence/actors-2026-10-07.

Commit f0e1ef3 delivered the persistent world daemon and passing P00-P02 gates.
Commit d31fc59 delivered the protobuf P00/P01 core.
Commit 7283fd9 contains the earlier JSON P00 implementation.
Earlier evidence remains under evidence/worldd-2026-10-07 and evidence/core-2026-10-07.

P04 preparation installed graphics development packages and Xvfb on both hosts.
Its initial diagnostic failed at the missing render.capture operation.
See docs/GRAPHICS_PREPARATION.md for package changes and recovery records.
The later P04 counterexample exposed and corrected a top-edge rasterization mismatch.

David authorized continuation without the local agent after its hardware failure.
No login or desktop configuration changed during P04 implementation.

## Historical remaining-work note (superseded by the dated entries above)

P06 must supply genuine actor input identities and routing.
Later phases add rooms, manipulation, workspace Git storage, process context, recovery, and complete actor workflows.

The additional mandatory milestones include identity, delegation, confinement, cabinets, XLibre security,
shared worlds, model actors, application restoration, native devices, performance, and lifecycle soak.
None is implied by the current rendering result.
No Spatial-M1 or LOCAL_READY qualification is claimed.
