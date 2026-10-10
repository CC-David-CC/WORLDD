# Implementation status

## 2026-10-10: Corrected M02 candidate and broader timing prepared

The exact-executable tracer passed positive and negative controls on both hosts.
It captures selected systemd-launched services and excludes the unrelated service.
The known 180 ms delay measured 180894 microseconds on sb16 and 180179 on sb16-2.
Three setup failures per host remain preserved. They exceeded the installed string limit or BPF stack limit.
Separate exact-path probes resolved the instrumentation setup failure. No production source changed.
Read evidence/m02-gate-2026-10-10/tracer-validation-01/primary-review.json for all 84 raw attachments.

Candidate02 freezes 351 source files at commit 1f5088e026573d86be8388cf1f41dfce20189db9.
Only the reviewed enrollment fixture differs from the first candidate. All 41 locked files are unchanged.
Archive SHA-256: f39471ca91385d0f2bee702e5beed0383e7e0e8308ddf69b403d379afbc170dd.
Read evidence/m02-gate-2026-10-10/candidate02-preparation.json.
Both hosts will build both profiles before testing. Each build uses two compiler jobs.
The complete Debug core sequence first runs with broader syscall timing as diagnostic evidence.
Untraced Debug and sanitizer qualification remain required. Diagnostic native logs use a separate directory.
Assertions, client deadlines, durability, and failure-stop behavior remain unchanged.
This preparation is not execution evidence. The original startup and historical timeouts remain unresolved.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Five focused mutation witnesses verified

Five isolated Debug mutations on sb16 each caused the intended existing assertion to fail.
Each witness passed before mutation and after restoration: ten positive passes and five expected failures.
The checks cover parent revocation, object scope, partial write authority, premature barrier completion, and world-cache invalidation.
The cache mutation allowed motion after grant revocation. Its witness detected changed position after the revocation.
All 351 source files were restored. All 41 locked files remain unchanged.
No defective mutation entered the project branch or the frozen qualification binaries.

Read evidence/m02-gate-2026-10-10/mutations-04/primary-review.json and cache-mutation-05/primary-review.json under the same parent.
The first four checks retain patches, build logs, exact commands, intended failures, and restored passes.
Their individual mutant executable hashes were not recorded. The fifth check records binary hashes for all three states.
The two setup failures remain preserved. These were an SSH authentication failure and an empty sparse-checkout index.
The reviewed Git bundle avoided credential changes. Explicit index population fixed the isolated checkout preparation.

The required regression-sensitivity witnesses have current focused evidence against frozen commit 54715f9.
Reassess this evidence when affected source changes. It does not qualify the complete M02 phase.
Next: retain broader timing during the original full qualification workflow, with the corrected enrollment fixture.
The startup timeout and historical timeouts remain unresolved. M02 and Spatial-M1 remain incomplete.

## 2026-10-10: Bounded startup traces remain inconclusive

The four-suite prefix passed 39 cases on a quiet sb16-2 host.
It passed another 39 cases after an isolated sanitizer rebuild of the same frozen source.
Both attempts used the original Debug binaries. No production code or deadline changed.
The quiet trace measured maximum issuer fsync and fdatasync durations of 31100 and 31193 microseconds.
The rebuilt attempt measured 32285 and 10433 microseconds. Neither trace recorded a 100 ms issuer stall.

The rebuilt attempt omitted the gate inventory and harness commands before the four-suite prefix.
Its target started 6.48 seconds after the build. Its writeback timing differs from the original failed gate.
These successful repetitions do not explain the original 985 ms event-loop delay.
The first wrapper failed before the target because it omitted SPATIAL_TEST_XSERVER.
Its raw failure_reproduced label is incorrect. The reviewed report classifies that attempt as a setup failure.
Read evidence/m02-gate-2026-10-10/startup-timing-01/primary-review.json.
All 97 raw attachments are preserved. The original failure and historical timeouts remain unresolved.

Independent focused mutation witnesses were prepared on sb16 from the same frozen production source.
The first fetch lacked SSH permission. A verified Git bundle supplied the reviewed history without credential changes.
An empty sparse-checkout index needed explicit population. All 351 source hashes then matched.
These setup failures are distinct from test results. The completed witness results are recorded above.
Next: retain broader timing during the original full qualification workflow.
M02 and Spatial-M1 remain incomplete.

## 2026-10-10: M02 gate found migration-fixture and startup failures

The frozen candidate remains unchanged. sb16 completed all 124 Debug core suites; 123 passed and one failed.
Its 51 Debug harness cases passed. Debug P10 and all sanitizer test segments remain unrun.
sb16-2 also completed all 124 Debug core suites; 122 passed and two failed.
Its 51 Debug harness cases passed. Debug P10 and all sanitizer test segments remain unrun there too.
Both hosts failed the version-one issuer migration fixture.
That fixture retained authority_retirement, a version-ten table, before declaring version one.
The correction removes that table and checks the exact version-one schema.
The diagnostic confirmed the table collision and unchanged database after rejected migration.
All 64 selected cases passed in Debug and 64 in runtime ASan/UBSan on sb16.
The selected suites cover enrollment, issuer database rejection, and retirement migration rollback.
Production code and locked acceptance files remain unchanged. Existing binaries were verified and reused.
Read evidence/m02-gate-2026-10-10/issuer-v1-fixture-01/primary-review.json.
The original failed gate evidence is retained in evidence/m02-gate-2026-10-10/failure01.
The fixture fix and sb16 evidence were pushed to box-git at d39279169fd9670eded679371a96bf4c0042093b.

sb16-2 also failed world-revision-interleave during world startup, before its injected stale reply.
The participant activation timed out with reply_header progress 0/4 after 702 ms.
The retained issuer log records work_ms=985 at monotonic_us=316997784114.
Nearby resource samples show disk writeback and increasing I/O pressure without observed compiler overlap.
These observations do not establish the delayed operation or resolve the historical timeout failures.

The interim progress parser missed CTest failure rows without whitespace before ***Failed.
Earlier no-failure progress summaries were incorrect. Command exits and final XML determine qualification results.
Preserve the original logs and record failed, passed, and unrun segments separately.
Next: diagnose the startup delay with bounded timing checks before freezing another complete gate candidate.
M02 and Spatial-M1 remain incomplete. No normal desktop or operator database changed.

## 2026-10-10: Complete M02 candidate qualification started on both hosts

The reviewed timing diagnostics are committed and pushed to box-git at 54715f9a78aaa92911d2bb0a9c782f316f78902f.
The gate archive freezes all 351 source inputs and preserves all 41 locked files.
Its SHA-256 is 2755d25b62b9acdd2d2b8b6f4a71460a46de0dcf544e9beb63e14b574e16eb51.
Both hosts started isolated Debug and runtime sanitizer builds. Builds precede tests on each host.
The selected matrix includes all registered CTest suites, 51 harness cases, and 151 P00-P10 cases per profile.
The runner records resource pressure, compiler overlap, source pins, binary pins, and command results.
Read docs/M02_QUALIFICATION_AUDIT.txt for the requirement and witness review.
Cross-host and mutation evidence still need reassessment against this candidate.
The historical startup and timeout failures remain open. This launch does not certify a phase.
M02 and Spatial-M1 remain incomplete. No normal desktop or operator database changed.

## 2026-10-10: Client and issuer timing diagnostics pass focused qualification

The client now reports the failed transport stage and bounded timing counters.
The issuer records event-loop delays of at least 100 ms. The fixture retains issuer stderr.
Existing deadlines, transport validation, and permanent failure behavior remain unchanged.
The diagnostic increment passed 88 selected cases in Debug and 88 in runtime ASan/UBSan on sb16.
The five suites cover issuer TLS, the client, participant runtime, approval interruption, and the native supervisor.
The deliberate issuer pause verifies the delay log and subsequent protocol replies through the real daemon.
All 351 frozen source hashes match. All 41 locked files remain unchanged.

Read evidence/control-recovery-2026-10-09/issuer-timeout-diagnostics-02/primary-review.json.
The earlier candidate's actual 705 ms reply-header timeout remains preserved and unexplained.
Read issuer-timeout-diagnostics-failure01 under the same evidence directory.
These focused passes qualify the diagnostic behavior. They do not establish a timeout fix or complete M02.

The bounded TLS trace passed twenty attempts and observed 260 complete participant reply headers.
Its largest request-write to reply-header interval was 117906 microseconds.
The initial tracer failed before target execution because it contained two END blocks. Both attempts are preserved.
Read evidence/control-recovery-2026-10-09/participant-tls-progress-01/primary-review.json.
Dynamic probes can alter timing. No payload buffers or credentials were captured.

Earlier evidence separately confirmed slow durable writes during compiler overlap.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/issuer-sync-review-09.json.
That finding does not prove the cause of the newer failure without a live compiler.
Next: complete the M02 evidence audit and prepare broader both-host qualification with retained issuer timing and resource evidence.
Keep historical failures open. M02 and Spatial-M1 remain incomplete.

## 2026-10-10: Revocation timeout reproduced with transport diagnostics

An unintegrated client patch records the transport stage, transfer progress, elapsed time, and maximum check and callback intervals.
It keeps existing deadlines, validation, and permanent failure behavior.
All 64 selected Debug cases passed on sb16.
The sanitizer client suite passed 30 cases. The participant suite passed 15 cases and failed one.
Its active revocation read timed out before receiving any reply-header bytes.
The diagnostic recorded elapsed_ms=705, gap_ms=10, and tick_ms=0.
The two later sanitizer native suites were not run after this failure.

Read evidence/control-recovery-2026-10-09/issuer-timeout-diagnostics-failure01/primary-review.json.
All 351 candidate source hashes and 41 unchanged locked files are preserved.
The diagnostic patch and its two changed test files remain unintegrated in the worktree.
The missing diagnostic itself was reproduced with three expected failures against the previous binary.

Earlier original-order timing passed three interruption and fourteen supervisor cases without reproducing the historical failure.
After the new failure, three traced participant suites passed 48 case executions.
No issuer syscall or off-CPU wait reached 100 ms during those successful traced attempts.
Read revocation-sequence-01 and participant-reply-timing-01 under the same evidence directory.
These passes do not resolve the failed qualification or establish its root cause.

Next: capture issuer TLS progress and readiness together with kernel timing around the reproduced failure class.
Keep the two historical failures and this failed candidate open. Do not increase deadlines to obtain a pass.
M02 and Spatial-M1 remain incomplete. Full both-host qualification remains pending.

## 2026-10-10: Operator revocation passes connected native checks

All three operator scenarios passed in Debug and runtime ASan/UBSan on sb16: six native case results.
The command stayed pending while an actual world process held native Shift and button input.
Normal delivery completed with released native input and no application lease.
A bounded timeout retained the committed revision. Explicit resume completed without another revocation.
After a world crash, native cleanup alone did not complete the command.
Checked dependent cleanup and authenticated retirement completed the outstanding obligation with applied revision zero.
The independent confined X application remained responsive. Uncommitted project data survived.

Read evidence/operator-native-2026-10-10/candidate-01/primary-review.json.
All 351 source hashes match. All 41 locked files remain unchanged.
Production source and existing test assertions remain unchanged in this increment.
The new M02 coverage map is docs/M02_GATE_COVERAGE.txt. It identifies required evidence without marking the gate passed.

Next: diagnose the two preserved intermittent failures using the original suite context and process timing.
Then finish the M02 audit and freeze the complete both-host phase gate.
M02 and Spatial-M1 remain incomplete. Software native-session checks do not qualify physical input or GPU performance.
No normal desktop session or database changed.


## 2026-10-10: Operator revocation waits for cancellation completion

The new spatial-revoke command combines an owner-signed revocation with checked cancellation queries.
It reports complete only for the exact target and revision with no pending obligations.
Timeouts retain committed status. Lost mutation replies report uncertainty and never trigger automatic replay.
Explicit resume reads the original barrier without sending another mutation.
The raw issuer client retains its explicit commit-only interface.

All 97 selected cases passed in Debug and runtime ASan/UBSan on sb16: 194 case results.
Twelve new cases cover real commits, pending obligations, acknowledgements, issuer restart, resume, and altered or lost replies.
The 85 related cases cover issuer transport, revocation, durable barriers, and checked barrier replies.
Read evidence/revocation-command-2026-10-10/candidate-02/primary-review.json.
All 350 source hashes match. All 41 locked files remain unchanged.

The first runner omitted imported protobuf bindings. The second omitted one existing probe executable.
Both failures remain preserved. Only the affected Debug probe case required a retry after its binary was built.
The first collector rejected its own literal secret patterns. Its original source remains preserved.
No production code or assertion changed to resolve these runner and collection errors.

Next: exercise the operator command with held native input in the retained production session.
Then resolve the two preserved intermittent failures and complete the remaining M02 audit and both-host gate.
Full M02 qualification remains pending. M02 and Spatial-M1 remain incomplete.
No normal desktop session or database changed. Native, physical-input, and performance evidence is unchanged.


## 2026-10-10: Issuer-outage recovery passes focused native checks

The retained coordinator can now finish fencing after issuer loss during failed-supervisor cleanup.
It requires the original checked failure receipt, matching supervisor exit, and original signing configuration.
Original process, native, and dependent cleanup bytes remain unchanged.
A separate deferred fence precedes authenticated retirement. Unapplied revocations retain applied revision zero.
Ordinary unfenced shutdowns remain ineligible. A reopened journal cannot recreate session ownership.

All 49 selected checks passed in Debug and runtime ASan/UBSan on sb16: 98 checks total.
Five new scenarios cover issuer restart, local and remote persistence failure, altered receipts, wrong keys, and exact retries.
The 44 related checks cover coordinator retirement, broker retirement, stopped-broker dependencies, broker cleanup, and launch ownership.
Read evidence/control-recovery-2026-10-09/deferred-fence-01/primary-review.json.
All 348 source hashes match. All 41 locked files remain unchanged.

The original defect remains preserved in issuer-loss-baseline-03.
Earlier baseline assumptions and the first Debug launch-runner argument error also remain preserved.
The corrected launch command reran only its affected 14 cases. No runtime or test assertion changed for that correction.

Next: audit remaining M02 requirements and resolve the two preserved intermittent failures before the complete phase gate.
Full M02 qualification remains pending on both hosts. M02 and Spatial-M1 remain incomplete.
No normal desktop session or database changed. GPU, physical-input, and performance qualification remain pending.

## 2026-10-10: Cross-host world crash recovery passes focused native checks

Eight coordinated scenarios passed in Debug and runtime ASan/UBSan across both hosts.
Four cases crash the delayed world. Four cases check ordinary delayed delivery after the fixture refactor.
The crashed world releases native input, but its issuer obligation remains pending.
Production dependent recovery releases the exact reservation and persists the cleanup receipt.
The issuer obligation remains pending until explicit checked retirement.
Retirement preserves applied revision zero. The other three generations complete through revision-one acknowledgements.
Both applications keep responding. The other host retains its original world and broker launch identities.
Read evidence/control-recovery-2026-10-09/crosshost-recovery-02/primary-review.json.

The first candidate failed during fixture teardown after retirement. It sent a second stop for the stopped world.
Candidate02 uses the checked cleanup receipt. The original failed run remains preserved.
All 347 source hashes match. All 41 locked files remain unchanged.
Production compiled inputs remain unchanged. Both profiles reuse verified binaries and separately generated test bindings.

Next: issuer interruption during native recovery and remaining failure races.
Both earlier intermittent timeout causes remain unresolved. Full M02 qualification remains pending on both hosts.
M02 and Spatial-M1 remain incomplete. No normal desktop session or database changed.


## 2026-10-10: Cross-host credential cancellation passes focused native checks

Both issuer directions passed in Debug and runtime ASan/UBSan. All four coordinated scenarios passed.
Each scenario uses one real issuer and a native production session on each host.
The issuer tracks four exact world and broker generations across two independent worlds.
One world's encrypted replies remain held while the other host releases its native input and control reservation.
Saved replies prove nonzero held bytes and unreleased input on the delayed host before relay release.
Both applications remain responsive. Revoked actor requests fail. All four obligations complete through acknowledgements.
The two affected existing suites passed 14 cases per host per profile: 56 cases total, without failures or skips.
Read evidence/control-recovery-2026-10-09/crosshost-cancellation-03/primary-review.json.

Candidate01 failed at fixture teardown. The fixture tried to stop its broker before its world.
Candidate02 corrected that order. The original failure and two exploratory Debug successes remain preserved.
Candidate03 strengthens the encrypted-delay witness. Compiled inputs remain identical across all three candidates.
All 347 source hashes match. All 41 locked files remain unchanged. No production runtime behavior changed.

These checks qualify delayed credential cancellation. Shared-world geometry and remaining failure races still need qualification.
The two earlier intermittent timeout causes remain unresolved. The complete both-host M02 phase gate remains pending.
M02 and Spatial-M1 remain incomplete. No normal desktop session or database changed.


## 2026-10-10: Four controlled interruption scenarios pass focused checks

All four scenarios passed in Debug and runtime ASan/UBSan on sb16, without failures or skips.
Interrupted approval cannot start a child or reuse an abandoned incarnation.
A revocation-delivery timeout after activation fences the generation and stops its exact owned scopes.
The issuer keeps the revocation obligation pending. Replacement cannot execute before retirement.
No participant reconnect, acknowledgement, release, or replacement marker appears after that injected timeout.
Read evidence/control-recovery-2026-10-09/approval-interruption-03/sb16/primary-review.json.
All compiled inputs match candidate02. Both profiles reused its hash-verified binaries with separate evidence paths.
Only the new test changed after candidate02. All 41 locked files remain unchanged.

Candidate02's related intermittent timeout remains unresolved. Its failed run is preserved separately.
Five isolated and ten kernel-timed repetitions passed without changing code or deadlines.
The ten timed repetitions show no issuer syscall or off-CPU wait reaching 100 ms.
They do not determine the earlier failure's cause. The failed first tracer compilation is also preserved.
This is a development-branch coverage checkpoint, not full M02 qualification.
Next: cross-host cancellation and remaining recovery races. Both intermittent failures remain phase-gate work.
M02 and Spatial-M1 remain incomplete. No normal desktop session or database changed.


## 2026-10-10: Approval interruption candidate has a related sanitizer failure

The three new approval-interruption scenarios passed in both profiles on sb16.
All 52 selected Debug cases passed. Runtime sanitizers passed 51 cases and failed one.
The existing supervisor revocation case exceeded its issuer revocation-read deadline.
The underlying timeout cause remains unconfirmed. This happened after activation, not during approval.
Read evidence/control-recovery-2026-10-09/approval-interruption-failure02/sb16/failure-review.json.
The exact failure and stderr are preserved. The candidate remains unintegrated.
The unchanged case passed five isolated sanitizer repetitions. This does not resolve the original failure.
Read evidence/control-recovery-2026-10-09/approval-timeout-diagnosis-01/primary-review.json.
Next: bounded issuer transport and storage timing diagnosis.
Full M02 qualification remains pending on both hosts. M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Participant failure diagnostics preserve the cause

The participant worker now records the failed operation and a bounded cause on stderr.
Control characters become question marks. Cause text is limited to 240 bytes.
Public errors, deadlines, permanent failure behavior, and authorization rules remain unchanged.
Native supervisor and participant fixtures now preserve stderr before cleanup.
Two baseline tests reproduced missing diagnostics against unchanged production binaries.

All 53 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
The original native startup assertion also passed ten repetitions per profile with complete logs.
All 344 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/participant-diagnostics-02/sb16/primary-review.json.
The baseline failures remain in participant-diagnostics-01/sb16.

The historical participant-approval failure did not recur. Its original cause remains unconfirmed.
Successful repetition does not establish a fix for that failure.
Next: controlled approval interruptions, recovery races, and cross-host cancellation.
Full M02 qualification remains pending on both hosts. M02 and Spatial-M1 remain incomplete.
No normal host session or database changed.


## 2026-10-10: Actual broker replacement passes focused native checks

Fresh production sessions recover the original world and workspace after checked cleanup and issuer retirement.
Saved geometry, actor context, workspace selection, and uncommitted project files survive.
Interrupted process records become uncertain. Replaying the old launch returns UNAVAILABLE without execution.
An explicit new launch creates a confined application with a fresh native identity and control lease.
Replacement applies current revocation before actor admission. Pending broker retirement prevents replacement execution.
The implementation already provided this path. Only the new test and CMake registration changed.

All 42 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include three new native scenarios and four related suites.
All 344 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/broker-replacement-02/sb16/primary-review.json.
The exploratory three-case runs also passed in both profiles using verified unchanged binaries.
The final candidate was rebuilt after CMake registration and additional confinement assertions.

Next: failure races, the earlier participant-approval startup defect, and cross-host cancellation.
Full M02 qualification remains pending on both hosts.
Automatic application placement restoration and shared compositor lifetime remain required for M1.
No normal host session or database changed. M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Actual world replacement passes focused native checks

The existing production coordinator starts a fresh world generation after checked recovery and issuer retirement.
Native tests reuse the original world, workspace, project directory, and authority policy.
World identity, saved geometry, actor context, workspace selection, and uncommitted project data survive.
The application remains alive. Fresh actor authentication and a new control lease are required.
Old-owner routing is rejected. Retrying old retirement does not cancel replacement control.
Replacement is rejected before retirement. A previously revoked credential cannot enter the replacement.
The test driver changed. Production launch code did not change.

All 52 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include three new native scenarios and seven related suites.
All 343 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/session-replacement-03/sb16/primary-review.json.
The first candidate observed asynchronous activation too early. Its two failures remain preserved.
The correction waits for the actual issuer record before checking admission and persistent state.
The second candidate's sanitizer run used an expired test administrator connection. That failure is also preserved.
The final fixture opens a fresh authenticated connection. Its idle timeout and assertions remain unchanged.
Compiled inputs match candidate02. Both profiles reused its verified binaries with separate commands and evidence paths.

Next: actual broker replacement, failure races, the earlier startup defect, and cross-host cancellation.
Full M02 qualification remains pending on both hosts.
Automatic application placement restoration and shared compositor lifetime remain required for M1.
No normal host session or database changed. M02 and Spatial-M1 remain incomplete.


## 2026-10-10: World dependency recovery after broker shutdown passes focused checks

The retained coordinator now recovers a stopped world's dependency after checked broker shutdown.
Receipt version 2 binds both exact checked scope records. It does not invent a control-channel receipt.
The original world launch bytes and revision remain unchanged during the explicit journal migration.
World retirement remains available after broker exit through retained ownership and either durable proof version.
Existing version 1 receipts remain immutable. Broker and world retirement remain separate operations.
Neither receipt authorizes replacement execution or reconstructs lost process ownership.

All 71 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include nine new native cases and eight related suites.
All 342 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/stopped-broker-dependency-01/sb16/primary-review.json.
The actual missing-operation baseline remains preserved. No normal host session or database changed.

Next: actual replacement recovery and failure races.
The earlier participant-approval startup failure, cross-host cancellation, and full two-host qualification remain required work.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Broker retirement after checked cleanup passes focused checks

The retained coordinator now retires a failed broker generation after checked process and native cleanup.
Its original supervisor signer binds the exact saved launch bytes and committed issuer fence.
The issuer retires only that broker generation. Independent generations retain their existing state.
Explicit launch journal schema 4 saves the issuer result without changing the cleanup bytes or revision.
Exact retries preserve the original result, including after an issuer commit followed by local storage failure.
Broker retirement does not replace world cleanup or authorize replacement execution.

All 93 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include nine new native scenarios and nine related suites.
All 341 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/broker-retirement-01/sb16/primary-review.json.
The actual missing-retirement baseline remains preserved. No normal host session or database changed.

Next: dependent world recovery with a stopped broker, then actual replacement recovery and failure races.
The earlier participant-approval startup failure, cross-host cancellation, and full two-host qualification remain required work.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Retained broker crash cleanup passes focused checks

The session now retrieves checked broker cleanup after its original supervisor exits.
Exact retries preserve the receipt. Repeated waits retain the actual supervisor exit status.
A rejected nonzero exit cannot become successful cleanup on retry.
The coordinator can stop its exact owned world scopes when the broker is unavailable.
Broker cleanup does not replace world cleanup, reservation recovery, or issuer retirement.

All 69 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include four new native cases, two exit-validation cases, and nine related suites.
Native cases cover actual broker/application crashes, delayed native cleanup, independent credentials, and supervisor death.
All 340 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/session-broker-cleanup-01/sb16/primary-review.json.
Both original defect baselines remain preserved. No normal host session or database changed.

Next: broker issuer retirement and dependent recovery with a stopped broker.
Replacement recovery, failure races, cross-host cancellation, and full two-host qualification remain required for M02.
The earlier participant-approval startup failure remains unresolved despite subsequent passing checks.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Actual coordinator cleanup-to-retirement passes focused checks

The retained coordinator now signs retirement after verified world crash cleanup and durable broker reservation recovery.
It hashes the exact saved dependent receipt. Callers cannot supply cleanup, keys, digests, or issuer destinations.
The issuer retires only that fenced generation. The broker and independently authorized application remain usable.
The coordinator reports success after saving the issuer result in launch journal schema 3.
Exact retries preserve the original receipt, including after an issuer commit followed by local storage failure.
Reopened journals cannot recreate ownership or start replacement execution.

All 110 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include eight new native scenarios and ten related suites.
All 337 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/coordinator-retirement-02/sb16/primary-review.json.
The missing-step baseline and first integration failure remain preserved.
The first driver omitted required SIGPIPE setup. Its correction leaves the assertions and retirement logic unchanged.
No normal host session or database changed.

Remaining M02 work includes broker retirement, replacement recovery, failure races, cross-host cancellation, and full two-host qualification.
The previously observed participant-approval startup failure remains unresolved.
This path requires retained coordinator ownership. It does not implement automatic coordinator restart recovery.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Coordinator retirement candidate passes selected Debug checks

The uncommitted coordinator candidate connects checked world cleanup to authenticated issuer retirement.
It binds the exact saved dependent receipt and persists the issuer result in launch journal schema 3.
All eight new native scenarios and ten related suites passed in Debug on sb16.
The selected total is 110 cases, without failures or skips.
The runtime sanitizer build is running. Sanitizer results remain pending.
The frozen source is local/m02-coordinator-retirement-02 on sb16.
Read its checks/review.json and Debug logs for exact commands and source hashes.
All 337 source files are frozen. All 41 locked files remain unchanged.

The initial driver omitted the issuer transport's required SIGPIPE setup.
That run passed one case and failed seven. Its assertions remain unchanged in the correction.
Read evidence/control-recovery-2026-10-09/coordinator-retirement-failure01/sb16/failure-review.json.
The actual missing-step baseline is preserved in coordinator-retirement-baseline01/sb16.

Next: finish sanitizer checks, review collected evidence, and commit this coherent increment.
Broker retirement, replacement recovery, failure races, and cross-host cancellation remain required for M02.
The earlier participant-approval startup failure remains unresolved.
Full qualification remains pending on both hosts. No normal session or database changed.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Authenticated issuer retirement passes focused checks

The issuer now retires an exact fenced generation after a fresh proof from its registered supervisor.
The proof binds the cleanup digest, fence, launch, generation, issuer incarnation, and TLS connection.
One transaction saves retirement and deactivates that generation. Applied revision history remains unchanged.
Barrier replies distinguish retirement from service acknowledgement. Other participants keep their obligations.
Old service connections remain fenced. Replacement needs fresh authorization and current synchronization.

All 230 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include 21 issuer retirement scenarios, six client-validation scenarios, and twelve related suites.
All 336 tested source files match. All 41 locked files remain unchanged.
The explicit issuer migration is version 9 to 10. No normal host database was migrated.
Read evidence/control-recovery-2026-10-09/issuer-retirement-01/sb16/primary-review.json.

These protocol tests supply fixture cleanup digests. They do not prove actual external cleanup.
Next: connect the trusted coordinator signer to its checked, durable dependent receipt.
Then resolve the earlier participant-approval startup failure and run the complete M02 phase gate on both hosts.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Issuer retirement candidate passes selected Debug checks

The uncommitted candidate adds authenticated retirement for one exact fenced generation.
Retirement preserves applied revision history and records how each barrier obligation was completed.
The issuer requires a fresh supervisor proof bound to its TLS connection, fence, and cleanup digest.
The explicit issuer database migration advances version 9 to 10.
All 27 new cases and twelve related Debug suites passed on sb16.
The runtime sanitizer build is running. Its test results remain pending.
The frozen candidate is local/m02-issuer-retirement-01 on sb16.
Read its checks/review.json and Debug logs for commands, source pins, and results.
All 41 locked files remain unchanged. No normal host service or database changed.
The missing-operation baseline is preserved in evidence/control-recovery-2026-10-09/retirement-baseline01/sb16.
These protocol tests supply fixture cleanup digests. Actual coordinator cleanup-to-retirement remains pending.
Next: complete selected sanitizer checks, review and commit this increment, then connect the trusted coordinator signer.
M02 and Spatial-M1 remain incomplete. Full qualification remains pending on both hosts.


## 2026-10-10: Durable dependent cleanup after checked broker recovery

SessionCoordinator now saves checked cleanup before reporting successful reservation recovery.
The protobuf receipt binds the stopped world, retained broker, and exact released reservations.
An explicit SQLite migration adds the receipt without changing the original launch row or revision.
Exact retries preserve the saved bytes. Failed storage remains an error after broker release.
Reopening the journal cannot recreate process ownership or authorize replacement execution.

All 113 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
Five new native cases cover persistence, retries, journal failures, changed records, and denied replacement launches.
The initial baseline fixture error and corrected missing-durability failure remain preserved.
Read evidence/control-recovery-2026-10-09/dependent-cleanup-01/sb16/primary-review.json.
All 334 tested source files match. All 41 locked files remain unchanged.
No normal host session adopted this branch. Full qualification remains pending on both hosts.

Next: authenticate issuer retirement for the exact fenced generation and durable cleanup result.
Then resolve the earlier participant-approval startup failure and run the complete M02 phase gate.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Selective reservation recovery through retained world ownership

SessionCoordinator now releases a stopped world's reservations after checked process and native cleanup.
The broker requires the exact fenced owner. It preserves unrelated owners and independently authorized applications.
Exact retries and inspection return the original cleanup receipt. Changed or incomplete receipts are rejected.
World and actor channels cannot invoke this private recovery operation.
The receipt remains in broker memory. Durable dependent evidence and issuer retirement remain unimplemented.

All 154 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
Fifteen new cases cover broker recovery, reply validation, and an actual coordinator-managed world crash.
The native application remains responsive after its failed world's reservation is released.
Read evidence/control-recovery-2026-10-09/selective-recovery-01/sb16/primary-review.json.
All 41 locked files remain unchanged. One explanatory comment changed after testing; executable statements did not.
No normal host session adopted this branch. Complete qualification remains pending on both hosts.

Next: persist the checked dependent receipt, bound to the exact retained broker and world launches.
Then implement authenticated issuer retirement and resolve the earlier participant-approval startup failure.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Selective recovery candidate passes focused Debug checks

The uncommitted selective-recovery candidate releases only a checked, fenced world's control reservations.
It preserves unrelated owners and application execution. Exact retries return the cached original receipt.
The coordinator derives both cleanup receipts from its retained supervisor. Actors cannot invoke private recovery.
All 154 selected Debug cases passed on sb16, including 15 new cases and three native session scenarios.
The runtime sanitizer build is running. Its selected checks remain unrun until that build succeeds.
The source is frozen at local/m02-selective-recovery-01 on sb16.
Read its checks/review.json and checks/debug logs for commands, hashes, and results.
No normal host session adopted this candidate. Full two-host qualification remains pending.
The broker receipt is still in memory. Durable dependent receipt and issuer retirement remain unimplemented.
M02 and Spatial-M1 remain incomplete.

## 2026-10-10: Local actors and confined application crash recovery

SessionCoordinator now creates local actor channels and transfers each endpoint once to an exact retained world owner.
The actor authenticates through the existing signed proof and uses persistent world and workspace authority.
The real actor launches a confined native application through the supervised broker.
Actor cancellation releases held native input and its control reservation.
A world crash also releases native input. The application remains responsive through the surviving broker.
Checked process and native cleanup match the durable journal. The failed world's reservation remains fenced and pending.

All 100 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These comprise two new native scenarios and 98 related cases across seven suites.
Read evidence/control-recovery-2026-10-09/connected-applications-03/sb16/primary-review.json.
The initial remote-input rejection and fixture evidence-directory error remain preserved in connected-application-baseline01.
The corrected path keeps remote application input disabled. All 41 locked files remain unchanged.
One stale source comment was corrected after testing. Executable source is unchanged.
No normal host session adopted this branch. Full qualification on both hosts remains pending.

Next: release only the checked, fenced world's reservations while preserving unrelated owners and application execution.
Then persist the dependent cleanup receipt and implement issuer retirement.
Resolve the earlier participant-approval startup failure before the M02 phase gate.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Authenticated actor transport through the session coordinator

SessionCoordinator now admits an explicitly supplied TCP listener for the existing TLS actor interface.
It rejects private sockets, unlistening sockets, missing TLS configuration, and caller-selected actor descriptor arguments.
The real supervised world authenticates an issuer-signed actor and accepts its granted navigation actions.
Anonymous access, management commands, and ungranted observation remain denied.
Independent descriptor checks found no private coordinator socket in worldd or spatialwm.

All 117 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
The unchanged connected04 baseline confirmed the missing listener handoff before this candidate ran.
All 41 locked files remain unchanged. No native host session adopted this branch.
Read evidence/control-recovery-2026-10-09/connected-actors-01/sb16/primary-review.json.

Next: connect real workspace authority and a confined application to this actor path.
Verify held-input release, application survival, and exact reservation retention before implementing selective recovery.
Dependent receipts, issuer retirement, the earlier startup failure, and complete two-host M02 qualification remain pending.
Shared compositor lifetime across world transitions also remains required for M1.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: World registration and final cleanup delivery candidate

SessionCoordinator now retains a world supervisor and registers its exact owner with the independently retained broker.
The coordinator creates the world endpoint. Caller-supplied owner substitutions and broker endpoints are rejected.
The world and compositor inherit no private coordinator sockets in the live descriptor checks.
SessionLaunch now shares the actual supervisor lifecycle between broker and world ownership.

The first candidate passed 75 selected checks per profile on sb16.
Four additional journal and descriptor observations passed in each profile.
A real SIGKILL test then exposed missing cleanup delivery after supervisor exit.
The saved journal contained both checked receipts, and the broker still answered.
The coordinator returned Session supervisor ownership lost instead of the receipt.
Read evidence/control-recovery-2026-10-09/world-crash-baseline01/sb16/primary-review.json.

The corrected protocol adds optional cleanup_record field 2 to AuthorityLaunchError.
The supervisor still reports the launch failure and exits with status 2.
The coordinator drains only its retained private channel and validates the original launch and exit outcome.
An exited supervisor without a delivered receipt cannot establish cleanup.
The correction passed all 77 selected checks in Debug and runtime sanitizers on sb16.
Read evidence/control-recovery-2026-10-09/connected-04/sb16/primary-review.json and its raw logs.
All 41 locked files remain unchanged. No host session adopted this branch.

Remaining M02 work includes actor transport admission, native application survival, selective reservation release, and issuer retirement.
A checked process/native receipt does not yet establish complete dependent cleanup.
The earlier participant-approval startup failure remains unexplained.
Shared compositor lifetime across world transitions also remains an M1 requirement.
The current world process starts its own compositor. These one-world checks do not qualify multi-world presentation.
M02 and Spatial-M1 remain incomplete. Full phase qualification remains pending on both hosts.

## 2026-10-10: Session broker ownership and receiving interface pass focused checks

The M02 development branch now contains ChildHandleClient, ProcessHandle, and SessionBroker.
The session starts a real authorized supervisor and retains the broker root and administrative channels.
The receiver authenticates the supervisor and validates the exact launch and transferred live process handle.
Explicit shutdown requires matching process and native cleanup receipts.
World registration and selective recovery remain incomplete. No running host session adopted this branch.

All 65 selected cases passed in Debug and runtime sanitizers on sb16.
These comprise 15 new receiver cases, four new session-broker cases, and 46 related cases.
The original Debug run passed 64 cases and failed one incorrect test expectation.
That fixture expected a cleanup journal after native-role rejection before ownership creation.
The correction requires the exact rejection diagnostic and no journal, child configuration, or activation.
Production source and Debug binaries remained unchanged. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/connected-02/sb16/primary-review.json and its raw logs.

Administrative transport also completed full Debug and sanitizer qualification on sb16-2.
Both hosts passed 1,509 core checks, 51 harness checks, 151 P10 cases, and three native-world checks per profile.
Read evidence/control-recovery-2026-10-09/admin-final-review-01-sb16-2.json.
These component results do not qualify the complete M02 recovery path.
The earlier connected01 participant-approval startup failure remains unexplained.

Next: retain each world supervisor and register its dedicated broker endpoint against the verified launch owner.
Then implement selective release, durable dependent receipts, and issuer retirement.
Run complete qualification on both hosts at the M02 phase gate.
M02 and Spatial-M1 remain incomplete.

## 2026-10-10: Private broker handle server on the M02 development branch

Branch m02/control-recovery contains the private supervisor handle server and its protobuf messages.
The server duplicates the retained broker pidfd after checking the exact authorized launch snapshot.
It checks parent credentials, bounds replies, and prevents child mappings of the private endpoint.
The coordinator receiver remains incomplete. No host adopted this branch into its running session.

All eight new transfer checks passed in Debug and runtime sanitizers on sb16.
All 39 related Debug checks passed.
Related sanitizer checks passed 38 cases and failed one existing participant-approval startup case.
The unchanged failing case then passed five targeted repetitions. Its cause remains unresolved.
Do not treat these results as a clean integration gate.
Read evidence/control-recovery-2026-10-09/connected-01/sb16/primary-review.json and the preserved raw logs.

The first alias fixture collided with another descriptor target before reaching the intended guard.
Its corrected target passed both profiles with unchanged production binaries.
All 41 locked files remain unchanged. The original baseline and both failures remain preserved.

The obsolete sb16-2 handle run was canceled while waiting, before any build or test.
Its inputs remain unchanged. The existing administrative transport run continues on sb16-2.
Next: implement the coordinator receiver and retain broker management independently of world launches.
Selective cleanup, durable dependent receipts, issuer retirement, and complete M02 qualification remain required.

## 2026-10-10: Focused development checks and retained-handle correction

Use affected tests during implementation and complete qualification at phase gates.
The root AGENTS.md records this user-directed schedule.
Existing qualification runs continue. Do not queue a full matrix for each small increment.

All five retained-handle scenarios passed on sb16 in Debug and runtime sanitizers.
The original test incorrectly counted normal cgroup control files as leftover scopes.
The corrected helper checks child directories, root processes, and the populated state.
Both profiles used unchanged handle01 source and prebuilt binaries.
Read evidence/control-recovery-2026-10-09/handle-focused-02/sb16/review.json and its raw logs.
The five original failures remain preserved. No locked acceptance file changed.
This focused result does not qualify the full candidate or its second-host behavior.

The existing sb16-2 administrative transport run remains active.
Its original retained-handle candidate remains queued with immutable test inputs.
Secure transfer, production coordination, selective cleanup, durable receipt, and issuer retirement remain incomplete.
Build these as one connected recovery candidate, with focused checks as components change.
Run complete qualification on both hosts at the M02 phase gate.
M02 and Spatial-M1 remain incomplete.

## 2026-10-10: Qualified supervisor handoff fix integrated and adopted

Both hosts passed complete Debug and runtime sanitizer qualification for handoff01.
Each profile passed 1,498 core checks across 103 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed per profile. No final case failed or skipped.
Both hosts reproduced the two baseline defects before corrected qualification.
The exact three-file fix is integrated and adopted at 0ed35eb3e21268fcaed38b190b93619a17260088.
All 304 qualified source hashes match. Both checkouts are clean.
No runtime restarted or operator state migrated. Active helper bytes remain unchanged.
Read evidence/control-recovery-2026-10-09/integration-primary-review-handoff-01.json and the handoff-adoption host receipts.

Administrative transport passed full Debug and sanitizers on sb16.
Each profile passed 1,509 core checks, 51 harness checks, 151 P10 cases, and three native-world checks.
Second-host transport qualification remains required before integration.
Retained-handle qualification has started building on sb16.
Secure transfer, the production coordinator, selective cleanup, and issuer retirement remain incomplete.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Qualified reply validation integrated and adopted

Both hosts passed complete Debug and runtime ASan, LeakSanitizer, and UBSan qualification for reply01.
Each profile passed 1,496 core checks across 102 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed per profile. No final case failed or skipped.
The integration guard verified raw evidence, exact source, unchanged locks, and primary review.
All 303 Git index hashes matched the qualified candidate.
Both hosts adopted commit 14a889396f22708cebbf0b968ed518a70f540607 with clean checkouts and matching source hashes.
No runtime restarted or operator state migrated. Active qualification helper bytes remain unchanged.
Read evidence/control-recovery-2026-10-09/integration-primary-review-reply-01.json and the reply-adoption host receipts.

Handoff qualification continues. Administrative transport and retained-handle checks remain queued.
Secure child-handle transfer, session coordination, selective cleanup, and issuer retirement remain incomplete.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Control recovery qualification queue

Reply01 passed full Debug and runtime sanitizers on sb16.
Reply01 passed full Debug on sb16-2. Its sanitizer core checks are running.
Each completed profile passed 1,496 core checks, 51 harness checks, 151 P10 cases, and three native-world checks.

Handoff01 passed its two focused Debug checks on sb16 after reproducing both baseline failures.
Its full Debug core checks are running. Sanitizer and second-host qualification remain required.
Read evidence/control-recovery-2026-10-09/handoff-focused-01/sb16/debug/review.json.

Administrative transport has 11 new checks, queued behind handoff on each host.
Retained child-handle duplication has five new checks, queued behind transport on each host.
These 16 new checks remain unrun. Neither candidate implements the complete session coordinator.
Only binding01 is integrated and adopted production source.
Secure child-handle transfer, coordinator integration, selective cleanup, and issuer retirement remain incomplete.
M02 and Spatial-M1 remain incomplete.


## 2026-10-10: Reply validation passes both profiles on sb16

sb16 passed full Debug and runtime sanitizer qualification for reply01.
Each profile passed 1,496 core checks across 102 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed per profile. No case failed or skipped.
All evidence attachment hashes and raw core counts were checked independently.
Read evidence/control-recovery-2026-10-09/reply-final-review-01-sb16.json.
Second-host full qualification remains required before integration.

sb16 reproduced both expected retained-descriptor failures against the preceding supervisor.
Its handoff01 build has started. Corrected handoff checks remain unrun.
Administrative transport qualification waits behind handoff01 on both hosts.
No reply, handoff, or administrative transport source is integrated.
M02 and Spatial-M1 remain incomplete.

## 2026-10-10: Qualified retained-launch binding integrated

The exact seven-file binding01 source change is integrated.
Both hosts passed complete Debug and runtime ASan, LeakSanitizer, and UBSan qualification.
Each profile passed 1,480 core checks across 101 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed per profile. No final case failed or skipped. No compiler overlap was recorded.
All 41 locked files remain unchanged. The five expected baseline failures remain preserved in each host/profile.
Read evidence/control-recovery-2026-10-09/integration-primary-review-binding-01.json and integration-applied-binding-01.json.

The private supervisor query derives the control owner from retained authorized compositor launch ownership.
It grants no activation or cleanup authority and cannot reconstruct live ownership from a reopened journal.
Both hosts adopted commit 0fb875454df3cc582c35e076c96043035312fdff. All 298 qualified source hashes match. Both checkouts are clean. No runtime restarted or operator state migrated. Read evidence/control-recovery-2026-10-09/binding-adoption-review-01.json.
Reply qualification continues; handoff lifetime qualification remains queued.
The production session coordinator, administrative transport, selective cleanup, and issuer retirement remain incomplete.
M02 and Spatial-M1 remain incomplete.

## 2026-10-10: Reply validation passes full Debug on sb16

sb16 passed 1,496 core checks across 102 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed. No case failed or skipped.
Read evidence/control-recovery-2026-10-09/reply-debug-review-01-sb16.json.
The exact source, binary hashes, compiler monitor, and raw logs are preserved.
The collector's reply01 path passed these checks after adding handoff01 support.

Reply sanitizer qualification remains required. sb16-2 still runs binding sanitizers.
The handoff tests remain queued and unrun. No reply or handoff source is integrated.
Complete production control recovery and M02 remain incomplete.

## 2026-10-10: Binding full profiles pass on sb16; reply checks pass in Debug

sb16 passed binding01 Debug and runtime sanitizers.
Each profile passed 1,480 core checks across 101 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed per profile. No case failed or skipped.
sb16-2 passed the same complete Debug profile and continues sanitizer qualification.
Read evidence/control-recovery-2026-10-09/binding-final-review-01-sb16.json and binding-debug-review-01-sb16-2.json.
Binding source remains isolated until both hosts finish full qualification and final review.

Reply01 passed 16 focused Debug checks, 51 harness checks, and three native-world checks on sb16.
Read evidence/control-recovery-2026-10-09/reply-focused-01/sb16/debug/focused-review.json.
Its full qualification remains active. The second host still waits.
The handoff correction remains queued and untested. Complete control recovery and M02 remain incomplete.

## 2026-10-10: Supervisor descriptor handoff correction prepared

Source review found retained supervisor copies of selected descriptors and child stdin/stdout after launch.
Those copies can delay EOF after the child exits, including broker world-channel fencing.
The isolated handoff01 draft releases the copies and reserves standard descriptor slots with /dev/null.
It preserves management, stderr, process ownership, and native ownership.
Two independent kernel EOF checks remain unrun. They stop only disposable test supervisors and kill their pinned children.

Both frozen drivers wait for reply01 qualification before reproducing the defect or building the correction.
The draft contains 304 source files and 41 unchanged locked files.
Read docs/SUPERVISOR_HANDOFF_LIFETIME.txt and evidence/control-recovery-2026-10-09/supervisor-handoff-review-01.json.
No handoff source is integrated. Binding qualification remains active; reply validation remains queued.
Complete production control recovery and M02 remain incomplete.

## 2026-10-10: Qualified owner source adopted on both hosts

Both hosts adopted commit 4779f731 with all 297 qualified source hashes.
Both checkouts remained clean. No operator session restarted or runtime state migrated.
Active and queued qualification helper inputs remained unchanged.
Read evidence/control-recovery-2026-10-09/adoption-review-04.json.
The initial adoption attempt stopped before checkout changes because its manifest path was incorrect.
The corrected attempt uses the deployed frozen manifest. Its failure evidence remains preserved.

Binding01 passed full Debug on sb16: 1,480 core checks across 101 suites, 51 harness, and 151 P10 cases.
Three native-world checks also passed. No Debug case failed or skipped.
Read evidence/control-recovery-2026-10-09/qualified-binding01/sb16/summary-debug.json.
sb16 runs binding sanitizers. sb16-2 runs binding Debug after passing both baseline and focused checks.
Reply validation remains queued. Complete production control recovery and M02 remain unfinished.

## 2026-10-09: Qualified owner channels integrated

The exact eight-file owner04 change is integrated into project source.
Both hosts passed full Debug and runtime ASan, LeakSanitizer, and UBSan qualification.
Each profile passed 1,474 core checks across 100 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed per profile. No final check failed or skipped. No compiler overlap was recorded.
Both protocol-validation baseline failures remain preserved. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/integration-primary-review-04.json and integration-applied-04.json.

The broker now binds control reservations to immutable admitted world channels.
Fencing stops new owner requests and preserves reservations and independently authorized applications.
The native-world fixtures still supply owner bindings. Complete production recovery remains unimplemented.
Retained-launch binding qualification continues on both hosts. Reply validation remains queued.
Both hosts adopted commit 0fb875454df3cc582c35e076c96043035312fdff. All 298 qualified source hashes match. Both checkouts are clean. No runtime restarted or operator state migrated. Read evidence/control-recovery-2026-10-09/binding-adoption-review-01.json. No operator session restart is required.
M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Corrected owner04 passes both profiles on sb16

sb16 passed complete Debug and runtime ASan, LeakSanitizer, and UBSan qualification.
Each profile passed 1,474 core checks across 100 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed per profile. No check failed or skipped. No compiler overlap was recorded.
Read evidence/control-recovery-2026-10-09/full-review-04-sb16.json.
The native fixtures still supply owner bindings. Complete production recovery remains unimplemented.

sb16-2 continues owner04 sanitizer qualification. Integration still requires that result and final review.
Binding01 reproduced five expected baseline failures across six checks in both profiles on sb16.
Both corrected builds passed. Debug passed six binding checks, three native-world checks, and 51 harness checks.
Full Debug regression now runs. The full sanitizer profile remains required.
Read evidence/control-recovery-2026-10-09/binding-baseline-01/sb16/baseline-review.json.
Read evidence/control-recovery-2026-10-09/binding-focused-01/sb16/debug/focused-review.json.
The second host's binding driver and both reply-validation drivers still wait.
Production source remains a3a926d. M02 remains incomplete.

## 2026-10-09: Retained-launch binding integration guard prepared

The primary agent reviewed the seven-file binding change against its retained-ownership contract.
The guard requires exact owner04 production integration and both hosts' full Debug and sanitizer binding results.
It also requires preserved baseline failures, raw native evidence, unchanged locks, and final primary review.
An apply attempt correctly refused the unintegrated base. All 294 production source hashes remained unchanged.
Read evidence/control-recovery-2026-10-09/integration-guard-rejection-binding-01.json.

Owner04 sanitizer qualification remains active on both hosts. Binding01 and reply01 still wait.
No binding test result or production integration is claimed.

## 2026-10-09: Corrected owner04 passes full Debug on both hosts

Each host passed 1,474 core checks across 100 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed on each host. No check failed or skipped.
Read evidence/control-recovery-2026-10-09/debug-matrix-review-04.json.
Both raw evidence sets match the same source manifest and unchanged acceptance locks.

Both sanitizer profiles passed the harness, 21 owner checks, and three native-world checks.
Their full core runs remain active. Binding01 and reply01 remain queued on each host.
The collector now supports reply01 and successfully collected the second owner's complete Debug profile.
Its reply01 collection path remains unrun until that candidate qualifies.
No production integration occurred. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Control reply validation prepared in isolation

The reply01 draft adds a production validator for private broker control replies.
It checks correlation, exact ownership, fencing acknowledgements, reservation identity, and bounded snapshots.
The shared owner validator preserves the preceding broker's checks.
Sixteen new checks cover valid replies, typed errors, substitutions, malformed fields, duplicates, and limits.
All sixteen checks remain unrun. No production administrative transport or coordinator is claimed.

Both frozen drivers wait for binding01 qualification before building or testing.
The draft contains 303 source files. All 41 locked files remain unchanged.
Read docs/CONTROL_REPLY_VALIDATION.txt and evidence/control-recovery-2026-10-09/control-reply-review-01.json.
Owner04 still runs full sanitizers on sb16 and full Debug on sb16-2.
Production remains a3a926d. M02 remains incomplete.

## 2026-10-09: Corrected owner04 passes full Debug on sb16

sb16 passed 1,474 core checks across 100 suites, 51 harness checks, and 151 P10 cases.
Three native-world checks also passed. No check failed or skipped.
Read evidence/control-recovery-2026-10-09/debug-full-review-04-sb16.json.
The raw evidence, source hashes, binary hashes, and compiler monitor are preserved.

sb16 also passed the sanitizer harness, 21 owner checks, and three native-world checks.
Its full sanitizer core run remains active. sb16-2 continues full Debug qualification.
Both retained-launch binding drivers still wait for successful owner04 qualification on their host.
No production integration occurred. M02 remains incomplete.

The next coordinator boundary is specified in docs/SESSION_CONTROL_COORDINATOR.txt.
That contract requires independent broker retention and an exact world-endpoint-to-supervisor mapping.
It is an implementation contract, not a claim that the coordinator exists.

## 2026-10-09: Corrected focused Debug checks pass on both hosts

Candidate04 passed 21 owner checks, three native-world checks, and 51 harness checks on each host.
Both protocol-validation regressions now pass in Debug on sb16 and sb16-2.
Read evidence/control-recovery-2026-10-09/owner-focused-04.
Both full qualification drivers remain active. Runtime sanitizer qualification follows Debug on each host.
The retained-launch binding candidate still waits. No production integration occurred.

## 2026-10-09: Both-host owner03 matrix and native-world evidence collected

Both hosts passed candidate03 Debug and runtime sanitizer qualification.
Each profile passed 1,472 core checks across 100 suites, 51 harness checks, and 151 P10 cases.
Each profile also passed three independent native-world checks. No qualifier failed or skipped a check.
No compiler overlap was recorded.
Read evidence/control-recovery-2026-10-09/matrix-review-03.json.

Two additional validation-order checks reproduced the known defect on both hosts in both profiles.
These failures remain separate from the passing existing matrix. Candidate03 remains ineligible for integration.
Candidate04 passed focused Debug checks on sb16 and runs full qualification there.
sb16-2 builds candidate04. Binding01 still waits for its predecessor.
The native fixtures supply owner bindings. Production control recovery remains incomplete.

## 2026-10-09: Corrected owner-channel checks pass on sb16 Debug

Candidate04 passed 21 owner checks, three native-world checks, and 51 harness checks on sb16.
Both validation-order regressions now pass after their baseline failures were preserved.
The full Debug regression run remains active. The sanitizer profile follows it.
Read evidence/control-recovery-2026-10-09/owner-focused-04/sb16/debug/focused-review.json.
This focused result does not authorize integration. Both full profiles are still required on both hosts.

## 2026-10-09: sb16 passes full owner qualification and native-world checks

Candidate03 passed Debug and runtime sanitizers on sb16.
Each profile passed 1,472 core checks across 100 suites, 51 harness checks, and 151 P10 cases.
Each profile also passed three native-world checks for revocation, world crash, and explicit channel fencing.
Native input released, applications survived, and unrelated owner reservations remained independent.
Read evidence/control-recovery-2026-10-09/full-review-03-sb16.json.

The two validation-order defects reproduced exactly in both profiles.
Their failure logs remain in evidence/control-recovery-2026-10-09/validation-baseline-04/sb16.
Candidate04 is now building and qualifying on sb16. sb16-2 still runs candidate03 sanitizers.
Candidate03 remains ineligible for integration. No production recovery completion is claimed.

## 2026-10-09: Retained-launch control binding prepared in isolation

The binding01 draft adds control owner derivation to AuthorityLaunch and its private supervisor interface.
The owner uses the retained issuer, authorization, generation, process launch, and compositor scope identities.
The query cannot create activation or cleanup evidence. A reopened journal cannot reconstruct live ownership.
Six new checks cover exact binding, failed local commits, reopened journals, native role, and the production supervisor.
All six checks remain unrun. Both drivers wait for successful owner04 full qualification before building.
Read docs/CONTROL_OWNER_BINDING.txt and evidence/control-recovery-2026-10-09/control-binding-review-01.json.
The draft contains 298 source files. All 41 locked files remain unchanged.
Production remains a3a926d. The surviving broker coordinator and selective recovery release remain required.

## 2026-10-09: Candidate04 static review and integration guard prepared

The primary agent reviewed all eight changed files against the ownership contract.
The prepared guard requires exact source hashes, unchanged acceptance locks, and both hosts' full Debug and sanitizer results.
It also requires the two baseline failures, three native-world checks per profile, and final primary review.
A rejection check confirmed missing qualification cannot change production source.
Read evidence/control-recovery-2026-10-09/owner-static-review-04.json.
Read evidence/control-recovery-2026-10-09/integration-guard-rejection-04.json.
The correction remains unintegrated. Full qualification continues.

## 2026-10-09: Candidate03 full Debug passes on both hosts

Both hosts passed 1,472 core checks across 100 suites, 51 harness checks, and 151 P10 cases.
The collected raw logs contain no failures or skips. No compiler overlap was recorded.
Read evidence/control-recovery-2026-10-09/debug-full-review-03-sb16-2.json.
Both sanitizer profiles passed the harness and 19 owner-channel checks. Their full core runs remain active.
Candidate03 remains ineligible for integration. Candidate04 must correct protocol-validation ordering.
Native-world checks and production recovery integration remain pending.

## 2026-10-09: Candidate03 full Debug passes on sb16

The raw results confirm1472 core checks across100 suites,51 harness checks,and151 P10 cases.
No Debug check failed or skipped. The compiler monitor recorded no overlap.
Read evidence/control-recovery-2026-10-09/debug-full-review-03-sb16.json.
sb16 now runs runtime sanitizer qualification. sb16-2 still runs Debug.
A read-only live sample confirmed ASan, UBSan, leak detection, and no ptrace in an sb16 issuer process.
This sample applies to that observed process. It does not replace the full sanitizer run.

Candidate03 remains blocked from integration by the strict-validation ordering defect.
The three native-world checks and candidate04 correction remain queued.
Production remains a3a926d. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Native owner checks queued; strict-validation correction prepared

Candidate03 full qualification remains active on both hosts.
Three extra checks will exercise owner channels through real world actors and native application input.
They cover ordinary revocation, world crash, and explicit channel fencing.
They run after successful full qualification on the same host.

Static review found that native attribution precedes strict message validation on managed channels.
A wrong native server ID can hide an invalid message or unsupported version behind FORBIDDEN.
Candidate04 moves attribution into the existing validated dispatch and adds two regression checks.
The exact runtime reproducer remains unrun. It must fail as expected before candidate04 builds.
Candidate03 must not be integrated, even if its existing checks pass.
Read evidence/control-recovery-2026-10-09/control-owner-validation-review-04.json.

No active source inputs changed. Production remains a3a926d.

## 2026-10-09: Owner-channel Debug checks pass on both hosts

Candidate03 passed all19 owner-channel checks and51 harness checks on sb16 and sb16-2.
Both runtime sanitizer builds completed. Full cumulative qualification now runs on both hosts.
Read evidence/control-recovery-2026-10-09/owner-debug-03.
The candidate remains unintegrated. Production source remains a3a926d.
Selective recovery release and checked dependent receipts remain required.

## 2026-10-09: Control owner channels enter full qualification

The isolated candidate binds broker reservations to immutable world-channel owners.
It adds exact-owner inspection and fencing without terminating independently authorized applications.
Production source remains a3a926d. No operator session changed.

Candidate01 passed103 existing checks and51 harness checks on each host.
Its12 new checks hit a fixture helper-name collision. All failures remain preserved.
Candidate02 passed18 new checks and51 harness checks on each host.
One descriptor-count witness could not inspect the hardened broker's procfs directory.
The corrected witness uses read-only sudo. Its assertion and broker protections remain unchanged.
That corrected check passed on sb16 against the unchanged candidate02 Debug broker.

Candidate03 contains297 files and19 new checks. All41 locked files remain unchanged.
Full Debug and runtime sanitizer qualification is launched on both hosts.
Debug reuses identical production binaries and runs the corrected owner suite explicitly.
Read evidence/control-recovery-2026-10-09/control-owner-review-03.json.

Supervisor binding production, selective recovery release, durable dependent receipts, and issuer retirement remain pending.
M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Qualified native handoff and crash fencing adopted on both hosts

Commit a3a926d integrates the exact 294-file qualified source.
Both host checkouts fast-forwarded cleanly. All 294 source hashes match the qualification manifest.
No runtime restarted. No operator database migrated.
Each host/profile passed 1,453 core checks, 51 harness checks, and 151 P10 cases.
Four additional supervised-world integration checks passed in each host/profile combination.
Read evidence/supervisor-crash-fencing-2026-10-09/adoption-review-04.json.

The native world handoff, issuer fencing, explicit supervisor fencing, and automatic crash-fencing primitives are complete.
Supervised-world participation is verified on the integrated source.
The next task is tasks/m1/identity.control_recovery.json.
Both hosts reproduced its dependent reservation gap. The production fix is not implemented.
M02, Spatial-M1, physical-device acceptance, and LOCAL_READY remain incomplete.

## 2026-10-09: Combined294 passes both full profiles and enters source integration

Both hosts passed full Debug and runtime ASan, LeakSanitizer, and UBSan qualification.
Each profile passed 1,453 core checks across 99 suites, 51 harness checks, and 151 P10 cases.
No final check failed or skipped. No compiler overlap was recorded.
Four unchanged supervised-world integration checks also passed in every host/profile combination.
Read evidence/supervisor-crash-fencing-2026-10-09/integration-primary-review-04.json.

The primary agent approved all 37 changed files and applied the exact frozen294 source.
All 41 locked files remain unchanged. Host adoption follows the reviewed commit.
This increment adds native world/compositor handoff and issuer, supervisor, and automatic crash fencing.
Fencing preserves unresolved obligations. It does not complete recovery or authorize replacement execution.

Both hosts reproduced the dependent control-reservation gap with real native input and a surviving application.
The world and compositor exited. Native input released. The broker reservation still required explicit diagnostic release.
The first diagnostic launcher omitted a required argument and failed before test execution.
The corrected launcher preserved all assertions. Both corrected Debug diagnostics passed one check each.
Read evidence/control-recovery-2026-10-09/baseline-results-review-02.json.

Next: bind control reservations to exact recovery ownership and verify their cleanup before issuer retirement.
No operator database migration or session restart is part of source integration.
M02, Spatial-M1, native hardware qualification, and LOCAL_READY remain incomplete.

## 2026-10-09: Combined294 full Debug passes on both hosts

Both hosts passed 1,453 core checks across 99 suites, 51 harness checks, and 151 P10 acceptance cases.
No final Debug check failed or skipped. Both runs used the same frozen294 source.
No compiler overlap was recorded. Assertions, deadlines, and durability settings remain unchanged.
Read evidence/supervisor-crash-fencing-2026-10-09/serial-debug-results-review-02.json.
Full runtime ASan, LeakSanitizer, and UBSan qualification now runs on both hosts.
Production remains broker283 until both runtime profiles and final integration review pass.

The next recovery diagnostic is queued behind each host's successful full qualifier.
It checks native input release, surviving applications, and retained broker reservations after a world crash.
It has not run. A direct diagnostic release cannot establish production recovery.
Read docs/CONTROL_RECOVERY_BASELINE.txt and evidence/control-recovery-2026-10-09/baseline-preparation-review-01.json.
M02 and Spatial-M1 remain incomplete. Historical failures remain preserved.

## 2026-10-09: Combined source review and guarded integration prepared

The primary agent reviewed all 37 changed files between production283 and frozen294.
The review found no unresolved static defect. This does not replace runtime qualification.
The 41 locked files remain unchanged. Existing migration fixtures only account for schema9.
Read evidence/supervisor-crash-fencing-2026-10-09/combined-static-review-04.json.

The integration guard requires all four complete host/profile results and the exact tested source hashes.
It refused missing qualification evidence and left all production files unchanged.
The separate staging audit checks Git's index, raw test logs, and compiler-monitor evidence.
Host adoption also verifies the qualified binaries and source before a fast-forward update.
No runtime restart or operator-state migration is part of source adoption.

Both serial02 qualifiers remain active. Debug harness, crash-fencing, and focused authority checks passed on both hosts.
Full Debug core checks continue. Full runtime sanitizers remain pending in these fresh runs.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Durable sync latency identified; full qualification restarted without compiler overlap

The issuer trace reproduced participant timeouts during a two-job build on sb16-2.
Five consecutive fdatasync calls took 1.021265 seconds. The matching event-loop gap was 1035 ms.
A second sequence took 0.783983 seconds, matching an 800 ms gap.
Both exceeded the fixture client's unchanged 700 ms deadline. The participants failed closed.
The same full workspace-authority suite passed after compilation finished.
The trace affects timing. Diagnostic08 already reproduced an 881 ms gap without ptrace.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/issuer-sync-review-09.json.

Both hosts now run full qualification of the unchanged frozen294 candidate.
Each host runs one qualifier after its diagnostic build and tests finish.
The qualifier checks for compiler overlap and preserves new results separately.
Debug and runtime ASan/LeakSanitizer/UBSan assertions, deadlines, and durability settings are unchanged.
Read docs/QUALIFICATION_SCHEDULING.txt. These runs are pending, not passing evidence.

Original failed runs remain preserved. Production remains broker283 until full qualification and integration review succeed.
M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Authority timeout reproduced with an issuer event-loop stall

The instrumented workspace-authority suite failed on sb16-2 during a two-job build.
The issuer recorded an 881 ms event-loop gap. The client deadline was 700 ms.
The client disabled the participant channel after that deadline. IO pressure increased near the stall.
The exact blocking syscall remains unknown. A scoped fsync/fdatasync trace is running on both hosts.
The trace disables LeakSanitizer only in the traced issuer because ptrace prevents that detector.
This trace is diagnostic evidence. It cannot qualify sanitizer behavior.

Supervisor293 full sanitizers finished with two failed test methods on sb16 and three on sb16-2.
Crash294 full Debug finished with one failed test method on each host.
Crash294 full runtime qualification and both candidates' affected P10 profiles remain unrun.
Their focused results remain passing. Original failure logs remain preserved.

Both hosts passed three diagnostic controls, 96 bootstrap checks, and 24 pixel checks.
Both also passed 24 single-method checks during compilation and six after compilation.
The full workspace-authority suite reproduced the stall despite those individual passes.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/diagnostic-results-review-08.json.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/full-failure-review-04.json.

Production remains broker283. No candidate is integrated. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Transport diagnostics now record the missing failure context

Both hosts completed 20 owner-idle repetitions with no failure. The original failures remain unresolved.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/owner-idle-results-review-04.json.

A separate diagnostic build now records client operation, I/O stage, deadline lag, and TLS errors.
It also records server peer state, close timing, connection-limit events, and event-loop delay.
No payloads or credentials are logged. Production source, assertions, deadlines, and retries remain unchanged.
Three controlled faults will verify the diagnostics before the original failing scenarios run.
These controls do not establish the cause of the original failures.
Both hosts started the diagnostic build with two compiler jobs each.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/transport-diagnostic-review-05.json.

Supervisor and automatic crash-fencing full qualification continue on both hosts.
Integration remains on hold. Production remains broker283. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Automatic crash fencing passes both focused profiles

Both hosts passed ten new checks and 51 harness checks in Debug and runtime ASan/UBSan.
No focused check failed or skipped. Full cumulative qualification continues.
Read evidence/supervisor-crash-fencing-2026-10-09/focused-results-review-01.json.
The candidate remains unintegrated. Bootstrap authority-channel failures still require diagnosis.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Issuer fencing finishes with a second bootstrap failure

sb16-2 passed full issuer fencing in Debug and runtime ASan/UBSan.
Each profile passed 1433 core checks, 51 harness checks, and 151 P10 cases.
sb16 passed Debug. Its runtime core passed 1432 checks and failed one owner-idle bootstrap check.
Runtime P10 is unrun on sb16 because the driver stopped after that failure.
The failing world reported an unavailable participant channel. Its broker reported the same error class.
Both omitted the underlying exception. The cause remains unresolved.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/failure-review-02.json.

Both hosts now exercise this distinct idle scenario with the existing exception-logging diagnostic binaries.
These runs retain the original assertions and deadlines. They do not qualify the fencing candidate.
Automatic crash fencing passed ten new Debug checks per host; runtime builds continue.
Supervisor fencing full sanitizer runs continue. No new source is integrated.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Automatic crash fencing passes focused Debug on both hosts

Both hosts passed ten new automatic crash-fencing checks and 51 harness checks in Debug.
The tests cover child death, management loss, termination, issuer loss, signing keys, and uncertain cleanup.
They preserve pending revocation obligations and distinguish committed fences from completed recovery.
Read evidence/supervisor-crash-fencing-2026-10-09/debug-results-review-01.json.
Related regressions, runtime sanitizers, and full cumulative qualification continue.
The 294-file candidate remains unintegrated.

Both hosts completed the original 30-suite bootstrap diagnostic prefix without failures.
The original startup failure remains unresolved. These diagnostic passes do not replace its failed qualification.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/prefix-results-review-03.json.
Further diagnosis needs transport and server-expiry context. Do not rely on more blind repetitions.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Full supervisor Debug passes; automatic crash fencing enters qualification

Both hosts passed supervisor fencing Debug: 1443 core checks, 51 harness checks, and 151 P10 cases.
All 98 core suites passed. No check failed or skipped. Full runtime sanitizers continue.
Read evidence/supervisor-fencing-2026-10-09/full-debug-review-01.json.

Automatic crash fencing candidate01 now contains 294 source files and ten new checks.
It requests the exact issuer fence on fatal service failure before existing checked cleanup.
It preserves the original fatal error and skips checked graceful release and explicit shutdown paths.
All existing tests, protobuf schemas, and 41 locked files remain unchanged.
Both hosts started qualification with two compiler jobs each.
This is unqualified candidate behavior. No production source is integrated.

The original bootstrap failure remains unresolved. Both 30-suite prefix diagnostics continue.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Bootstrap failure remains unresolved; automatic crash fencing gap reproduced

Both hosts passed 40 isolated repetitions of the failing bootstrap method.
Both hosts also passed eight complete bootstrap-suite repetitions, with 15 checks per repetition.
These diagnostic passes do not replace the original failed qualification.
The original startup failure remains unresolved. Production integration remains on hold.
Both hosts now run the original 30-suite sequence through world-bootstrap with diagnostic-only binaries.
No assertions, deadlines, retries, or authority behavior changed.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/diagnostic-results-review-03.json.

Two new crash witnesses fail as expected on each host in Debug and runtime ASan/UBSan.
The production supervisor performs checked cleanup after child death and management loss.
It preserves pending issuer obligations but does not request the required automatic fence.
Read docs/SUPERVISOR_CRASH_FENCING.txt and evidence/supervisor-crash-fencing-2026-10-09.
Automatic crash fencing implementation remains pending.

Issuer fencing full sanitizer runs and supervisor fencing cumulative runs remain active.
Production remains broker283. No new candidate is integrated. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Supervisor fencing sanitizers pass; world qualification has one unresolved failure

Both hosts passed 10 new supervisor fencing checks and 51 harness checks in both profiles.
Both hosts passed full issuer fencing Debug: 1433 core checks, 51 harness checks, and 151 P10 cases.
Full issuer fencing sanitizers and supervisor cumulative qualification remain active.

sb16 passed full world qualification in Debug and runtime ASan/UBSan.
sb16-2 passed full Debug. Its runtime sanitizer core recorded 1406 passes and one bootstrap failure.
Runtime P10 is unrun on sb16-2. The original world driver has stopped.
Authentication failed because the world's authority participant channel became unavailable.
The service log identifies IdentitySessions line 192 but omits the underlying transport exception.
The cause is unresolved. No sanitizer memory-error marker appeared in this failing core log.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/failure-review-01.json.

Both hosts have isolated diagnostic jobs that expose the swallowed exception.
They preserve the original test, deadlines, retries, authority checks, and frozen candidates.
Passing repetitions alone will not resolve the original qualification failure.
World integration remains disallowed until the failure is understood and applicable qualification passes.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Supervisor fence operation implemented in isolated candidate

Both hosts reproduced the missing supervisor operation in Debug and runtime ASan/UBSan.
Each profile rejected protobuf field 20 after its real owned child activated.
These four expected failures are diagnostic evidence, not acceptance passes.

Candidate01 adds private supervisor fencing and a checked durable fence field.
It derives the request from retained launch authorization and requires issuer confirmation of the consumed generation.
It stops private participant forwarding before fencing and performs checked cleanup before reporting success.
Ten new tests cover normal cleanup, pending obligations, signing keys, journal faults, retries, and uncertain native cleanup.
All existing tests and 41 locked files remain unchanged. Both hosts passed 10 new Debug checks and 51 harness checks.
Read docs/SUPERVISOR_AUTHORITY_FENCING.txt and evidence/supervisor-fencing-2026-10-09.

sb16 completed full world qualification in both profiles:1407 core checks,51 harness checks,151 P10 cases each.
sb16-2 full world sanitizers remain active. World integration still requires both-host completion.
Issuer fencing cumulative qualification also remains active on both hosts.
All new candidates remain unintegrated. Production remains broker283.
Automatic crash fencing, dependent control cleanup, issuer retirement, and cross-host cancellation remain required.

## 2026-10-09: Fencing sanitizers and real-world crash diagnostics pass

Both hosts passed all 26 fencing checks, the baseline regression, and 51 harness checks in both profiles.
Two additional real-world crash checks passed in Debug and runtime ASan/UBSan on each host.
They verify exact supervisor fencing after checked process/native cleanup, rejection of service keys, and preserved pending obligations.
Read evidence/authority-fencing-2026-10-09/focused-results-review-01.json.
Read evidence/supervised-world-fencing-2026-10-09/results-review-01.json.

These diagnostics sign the fence request outside the production supervisor.
They do not prove automatic supervisor recovery or issuer retirement.
The production launch journal still records its original unconsumed authorization, with generation zero.
It does not yet persist the actual activation or a checked fence result.
Production coordination must bind the exact consumed activation before accepting a cleanup receipt.
Dependent application-control cleanup and issuer retirement remain required.

Full world and fencing cumulative qualification remains active on both hosts.
Both candidates remain unintegrated. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Fencing Debug passes; world integration guard prepared

Both hosts passed 26 new fencing checks, the missing-feature regression, and 51 harness checks in Debug.
The frozen candidate contains 292 files. All 41 locked files remain unchanged.
Runtime sanitizer qualification and full cumulative fencing qualification remain active or pending.
Read evidence/authority-fencing-2026-10-09/debug-results-review-01.json.

Both hosts passed full world Debug: 1407 core checks, 95 suites, 51 harness checks, and 151 P10 cases.
Full world runtime sanitizer qualification remains active.
The prepared integration requires passing raw evidence from both hosts and both profiles.
Its missing-evidence refusal preserved all 283 production source files.
Both hosts verified identical protobuf descriptors for the reviewed source-comment correction.
Read evidence/supervisor-world-handoff-2026-10-09/integration-prepared-03.json and comment-equivalence-03.

Production remains broker283 at f4c4148. World and fencing candidates remain unintegrated.
Verified cleanup receipts, issuer retirement, and cross-host cancellation remain required.
M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Missing authority fencing confirmed on both hosts

Both hosts rejected the proposed fence request through the real issuer TLS endpoint.
Debug and runtime ASan/UBSan each recorded one expected missing-feature failure.
The response was INVALID_ARGUMENT with Unknown protobuf fields. Durable issuer state remained unchanged.
Read evidence/authority-fencing-2026-10-09/baseline-review-01.json.

The additive fencing contract is recorded in docs/AUTHORITY_FENCING.txt.
It requires a distinct supervisor proof, exact active launch binding, explicit schema migration, and preserved obligations.
Fencing implementation, checked cleanup receipts, issuer retirement, and cross-host cancellation remain required.
World full qualification continues on both hosts. Integrated production remains broker283 at f4c4148.
M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Broker source adopted; supervised world participation passes

Both hosts adopted broker commit f4c4148 with all 283 source hashes matching and clean worktrees.
No runtime restarted and no operator state migrated.
World candidate02 passed 126 focused core checks and 51 harness checks per host and profile.
Both Debug and runtime ASan/UBSan passed with zero failures or skips.
Read evidence/supervisor-world-handoff-2026-10-09/candidate-02.

Four new issuer-backed world checks also passed on both hosts in both profiles.
They verify persistent world authority, authenticated movement, anonymous denial, revocation cancellation, and graceful issuer release.
The crash witness verifies exact native/process cleanup while preserving an independent connection and unresolved issuer obligations.
Read evidence/supervised-world-participation-2026-10-09/results-review-01.json.
These diagnostics used unchanged candidate02 source and binaries.

World full cumulative qualification remains active on both hosts. World source remains unintegrated.
Trusted fencing, issuer retirement, dependent lease cleanup, and cross-host cancellation remain required.
M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Broker handoff integrated after both-host full qualification

Both hosts passed broker candidate02 in Debug and runtime ASan/UBSan.
Each profile passed 1393 core checks across 93 suites, 51 harness checks, and 151 P10 cases.
No check failed or skipped. All 41 locked files remain unchanged.
The four issuer-backed broker participation checks also passed in all four profiles.
The five reviewed files now form the integrated 283-file production source.
Host source adoption follows the commit. This does not restart sessions or migrate operator state.
Read evidence/supervisor-broker-handoff-2026-10-09/integration-applied-02.json.

World candidate02 passed focused Debug on both hosts. Runtime sanitizer builds remain active.
The world candidate remains isolated and unintegrated. Full cumulative qualification remains pending.
Trusted crash recovery, issuer retirement, and cross-host cancellation remain required.
M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Owned compositor attribution passes; candidate02 full qualification starts

An independent supervisor revoked the recorded native credential on both hosts.
The actual compositor disconnected and the owned process scope stopped.
The first new test incorrectly expected a native cleanup receipt after external revocation.
That contradicts AUTHORITY_NATIVE_SCOPE.txt, which explicitly rejects NOT_FOUND as a receipt.
The corrected check requires CLEANUP_PENDING, the checked process receipt, no native receipt, and unchanged issuer obligations.
It also requires held-input release and the same independently authorized application connection to survive.
All thirteen focused checks pass on both unchanged Debug binaries. Both original failures remain preserved.

Candidate02 changes only the new world test file from candidate01. Production code remains identical between candidates.
Both hosts now run focused Debug and runtime sanitizers, followed by full cumulative gates.
The expected full profile is 1407 core checks across 95 suites, 51 harness checks, and 151 P10 cases.
Both schema audits preserve all twelve existing protobuf descriptors, except the explicit additive field12.
Read evidence/supervisor-world-handoff-2026-10-09/candidate-prepared-review-02.json.

sb16 broker full qualification and integration diagnostics passed in both profiles.
sb16-2 broker full sanitizers remain active. Neither candidate is integrated.
Production remains the supervisor increment at 534a94c. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: World Debug witness corrected; sb16 broker sanitizer and integration pass

World handoff candidate01 built on both hosts. Both passed 51 harness checks and the real startup test.
Nine regression methods passed. Two failed when the independent identity reader encountered an asynchronous MappingNotify event.
Both failures occurred after real pixel or held-input cleanup assertions succeeded. The original failures remain preserved.
The new reader handles bounded MappingNotify packets and still rejects invalid replies and unrelated events.
Its post-cleanup assertion compares the complete original application connection identity.
The corrected witness passed all twelve checks against unchanged Debug binaries on both hosts.
Read evidence/supervisor-world-handoff-2026-10-09/reader-correction-review-02.json.
World runtime sanitizer and full cumulative qualification remain unrun. Candidate01 remains frozen and unintegrated.

sb16 completed full broker Debug and runtime sanitizer qualification:1393 core checks,93 suites,51 harness checks,151 P10 cases each.
It also passed all four supervised broker integration checks in both profiles.
Its queued world baseline confirmed the missing field in unchanged broker binaries in both profiles.
sb16-2 full broker sanitizers remain active. Its dependent diagnostics remain queued.
Production remains the supervisor increment at 534a94c. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: World native handoff candidate01 enters Debug qualification

The isolated world/compositor correction now contains 290 files, with 16 changed or added files.
It preserves all existing tests and 41 locked files.
The supervisor issues a sealed COMPOSITOR configuration. Worldd forwards it to spatialwm.
The new XCB adapter checks the exact socket peer before sending authentication data.
The compositor then checks native role and server ID before rendering.
These implementation claims describe unqualified candidate source, not integrated production behavior.

Both-host Debug drivers are active. Each builds with two jobs, then runs harness and new handoff tests.
Candidate01 contains one startup test and eleven regression methods.
The additional fake-server witness requires zero received authentication bytes.
Read evidence/supervisor-world-handoff-2026-10-09/candidate-prepared-review-01.json and candidate-01.patch.
The earlier partial draft and missing-feature failures remain preserved.

Both full broker sanitizer runs continue independently. Broker and world candidates remain unintegrated.
Production remains the supervisor increment at 534a94c. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Both hosts reproduce the missing world native handoff

The unchanged, qualified Debug binaries fail the new startup baseline on both hosts.
Each reports one failure: AuthoritySupervisorConfig has no compositor_native_target_fd field.
The diagnostic verified all 283 source hashes and the recorded Debug binary hashes.
Read evidence/supervisor-world-handoff-2026-10-09/debug-baseline-01.
These are expected missing-feature failures, not acceptance passes.

A separate six-file draft adds the protobuf schema, sealed configuration reader, and supervisor handoff.
It remains partial, unbuilt, and outside production. World/compositor forwarding and authenticated connection work remain required.
Read partial-draft-01.json and partial-draft-01.patch in the same evidence directory.
Ten prepared correction tests remain unrun.

Both full broker sanitizer drivers remain active. Their queued integration diagnostics remain unchanged.
Production remains the supervisor increment at 534a94c. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Both hosts pass full broker Debug; world correction witnesses prepared

Broker candidate02 passed full Debug on both hosts.
Each host passed 1393 core checks across 93 suites, 51 harness checks, and 151 P10 cases.
Raw logs confirm zero failures or skips. All 283 source hashes and 41 locked files match.
Read evidence/supervisor-broker-handoff-2026-10-09/full-candidate-02 for both Debug reports.
Full runtime sanitizer qualification remains active on both hosts. Broker integration remains unrun.

Ten world/compositor correction tests are prepared and statically reviewed. All ten remain runtime unrun.
They cover real pixels, held-input cleanup, independent application survival, sealed descriptor lifetime, and configuration rejection.
The descriptor witness tracks device and inode, with a retained parent descriptor as its positive control.
Each direct denial test first requires successful compositor startup with a valid configuration.
Read evidence/supervisor-world-handoff-2026-10-09/correction-prepared-review-01.json.
The queued missing-field baseline and frozen broker source remain unchanged.
Production remains the supervisor increment at 534a94c. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Full broker candidate Debug qualification passes on sb16

Broker handoff candidate02 passed full Debug on sb16.
The result contains 1393 core checks across 93 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. Raw logs confirm the reported counts and distinct suite coverage.
The collector verified all 283 candidate source hashes and 41 locked files.
Read evidence/supervisor-broker-handoff-2026-10-09/full-candidate-02/sb16/summary-debug.json.

sb16 full runtime sanitizer qualification is active.
sb16-2 full Debug remains active, followed by its sanitizer continuation.
Both hosts already passed both focused profiles.
Supervised broker participation tests and the world/compositor handoff baseline remain queued.
The five-file broker integration remains prepared and unrun until all full profiles pass.
Production remains the supervisor increment at 534a94c. M02 and Spatial-M1 remain incomplete.


## 2026-10-09: World/compositor native handoff baseline is prepared

The source audit confirms that worldd and RenderWorker pass only a display argument to spatialwm.
Existing native compositor fixtures provide XAUTHORITY through the environment.
The production supervisor restricts its child's environment to PATH and LANG.
These interfaces do not yet provide a sealed native credential handoff to the world and compositor.

A new startup baseline requests compositor_native_target_fd through the actual supervisor parser.
It requires real worldd and spatialwm under the owned cgroup, then checked process cleanup.
Its runtime result remains unrun. Both-host queues wait for the preceding integration diagnostics.
The proposed additive protobuf contract is explicit in docs/SUPERVISOR_WORLD_NATIVE_HANDOFF.txt.
Production files and locked acceptance tests remain unchanged.

Broker candidate02 full Debug and sanitizer qualification continues through exact durable drivers.
Both hosts already passed focused Debug and sanitizer checks: 112 core checks and 51 harness checks per profile.
Four issuer-backed broker participation tests remain queued ahead of the world startup baseline.
Do not treat these prepared tests or source findings as runtime qualification.
Production remains the 281-file supervisor increment at 534a94c. M02 and Spatial-M1 remain incomplete.


## 2026-10-09: Broker focused qualification passes on both hosts

Broker handoff candidate02 passed focused Debug and runtime ASan/UBSan on both hosts.
Each profile passed 112 checks across 11 suites and 51 harness checks, without failures or skips.
Both full cumulative qualification drivers remain active on the same frozen source and binaries.
Primary review covers the exact five changed files. The guarded integration patch is prepared, not applied.
Production remains the 281-file supervisor increment at 534a94c.
The broker candidate contains 283 files and preserves all 41 locks and all existing test files.
Read evidence/supervisor-broker-handoff-2026-10-09/integration-primary-review-02.json.

Four issuer-backed supervised broker integration checks are prepared and primary-reviewed.
They cover real activation, authorized native application launch, missing-principal denial, and graceful release.
They also cover actor revocation and broker death with a visible unretired issuer obligation.
The revocation witness requires native EOF when the barrier completes, without an additional grace period.
Both-host queues wait for exact full broker qualification drivers before these new tests execute.
All four integration checks remain unrun.
Read docs/SUPERVISED_BROKER_PARTICIPATION.txt and tasks/m1/identity.supervised_broker_participation.json.
M02 and all broader Spatial-M1 qualification requirements remain incomplete.


## 2026-10-09: Supervisor adopted on both hosts; broker focused sanitizers pass on sb16

Commit 534a94c integrates the fully qualified supervisor increment.
Both hosts adopted that commit through the guarded fast-forward procedure.
Their 281 source hashes match, and both worktrees are clean.
No session restarted. No operator database migrated.
The adoption reports are in evidence/authority-supervisor-2026-10-09.

Broker candidate02 passed focused Debug and runtime ASan/UBSan on sb16.
Each profile passed 112 checks across 11 suites and 51 harness checks, without failures or skips.
The full cumulative broker Debug run is active on sb16.
sb16-2 passed the startup and nine regression methods in Debug; remaining focused qualification is active.
Its full cumulative continuation waits for the exact driver and checks final focused evidence before proceeding.
Broker changes remain isolated. Supervisor recovery, fencing, and full Spatial-M1 qualification remain incomplete.


## 2026-10-09: Reviewed supervisor increment is integrated after all four profiles pass

Supervisor candidate02 passed full Debug and runtime ASan/UBSan on both hosts.
Each profile passed 1383 core checks across 91 suites, 51 harness checks, and 151 P10 cases.
No final case failed or skipped. The integration guard verified raw reports and all candidate hashes.
The primary-reviewed 77-file increment is now applied to production source.
All 281 production source hashes match the qualified candidate. All 41 locked files remain unchanged.
Read evidence/authority-supervisor-2026-10-09/integration-applied-02.json and full-candidate-02.
Commit, stage audit, and host adoption complete the integration checkpoint.

Both hosts confirmed the absent broker handoff against unchanged supervisor binaries in both profiles.
The separate broker candidate02 passed sb16 focused Debug: 112 checks across 11 suites plus 51 harness checks.
The corrected observer control and both real confined native application tests passed.
sb16 runtime sanitizer qualification and sb16-2 Debug qualification remain active.
Full cumulative broker qualification is queued behind each exact focused driver.
The broker handoff changes are not part of this integrated supervisor source.

Two qualification drivers exited between recorded successful commands and their next commands.
Remote process checks confirmed their original PIDs were absent before continuation.
The cause of driver exit remains unproven. No test failure was converted to success.
Continuations preserved completed build/core results and ran only unrun steps.
They now use detached processes with persistent log files.

M02 remains incomplete. Broker/world credential handoff, fencing, and checked recovery remain required.
Physical hardware, shared-world workflows, real model tasks, and full M1 qualification remain unfinished.


## 2026-10-09: sb16 full supervisor passes; broker observer correction starts

Supervisor candidate02 passed full Debug and runtime ASan/UBSan on sb16.
Each profile passed 1383 core checks across 91 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. Both-host full Debug already passed.
The sb16-2 full sanitizer worker remains active. No supervisor production files are integrated yet.

The unchanged-source broker baseline confirmed the missing handoff on sb16 in both profiles.
Broker handoff candidate01 then passed Debug build, 51 harness checks, and real broker startup.
Six regression methods passed. Two stopped on an observer PermissionError before application actions.
NativeLaunch disables unprivileged process inspection with PR_SET_DUMPABLE=0.
The existing 102 focused checks and runtime sanitizer profile remained unrun for candidate01.
Read evidence/supervisor-broker-handoff-2026-10-09/observer-failure-01.txt and preserved raw logs.

Broker candidate02 retains identical production code and all 41 locked files.
One test file adds a privileged read-only descriptor observer and an open/closed descriptor control.
The observer requires a live pidfd across inspection and treats inspection errors as failures.
Candidate02 qualification is active on sb16. Its nine regression methods remain unverified until execution finishes.
The sb16-2 baseline and original candidate01 remain queued behind its exact supervisor worker.
M02 and Spatial-M1 remain incomplete. Fencing, recovery, and full broker participation remain required.


## 2026-10-09: Primary cumulative supervisor review is complete

The primary agent reviewed all 77 changed files in supervisor candidate02.
All 281 frozen candidate hashes still match. The review found no new unresolved candidate defect.
Read evidence/authority-supervisor-2026-10-09/integration-primary-review-02.json and its partial review records.
This approves the bounded increment only after all four full qualification profiles pass.
It does not complete M02, recovery, or Spatial-M1.

Full Debug passed on both hosts: 1383 core checks across 91 suites, 51 harness checks, and 151 P10 cases each.
No case failed or skipped. Full runtime sanitizer qualification remains active on both hosts.
Read evidence/authority-supervisor-2026-10-09/full-candidate-02 for the collected Debug reports.
The stage audit and host-adoption scripts are prepared and syntax checked, but unrun.
Host adoption refuses active qualification or broker baseline workers.
Adoption checks exact source and binary hashes, then fast-forwards Git without restarting sessions or migrating databases.
No production source is integrated.

## 2026-10-09: Supervisor full Debug qualification passes on sb16

Supervisor candidate02 passed full Debug qualification on sb16.
The result contains 1383 core checks across 91 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. The collector verified 281 source hashes and 41 locked files.
Raw logs and the report independently confirm the counts.
Read evidence/authority-supervisor-2026-10-09/full-candidate-02/sb16/summary-debug.json.

sb16 full runtime sanitizer qualification remains active.
sb16-2 full Debug qualification remains active, with its sanitizer continuation queued.
Both hosts already passed focused Debug and sanitizer qualification.
The broker handoff baselines remain queued behind the exact full drivers.
No additional production source is integrated. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Supervisor focused qualification passes all four host profiles

Supervisor candidate02 passed focused Debug and runtime ASan/UBSan on sb16 and sb16-2.
Each profile passed 383 checks across 25 suites and 51 harness checks.
No case failed or skipped. All 281 source hashes and 41 locked hashes matched.
Both hosts are running full cumulative qualification on those same binaries.
The broker handoff baseline remains queued behind each exact full driver.

A static integration audit compared all 1378 existing test methods with the integrated 242-file source.
All existing methods remain present. Assertion lists are identical in 1372 methods.
Four methods update the expected issuer schema from 6 to 8.
Two methods retain their three-second denial deadline through a shared helper.
That helper requires explicit protocol denial and has a live-credential counterexample.
These are static method counts, not additional executed test results.
The audit does not prove fixture equivalence or replace production review and runtime qualification.
Read evidence/authority-supervisor-2026-10-09/integration-test-audit-02.json.

The cumulative integration patch now reconstructs all 281 candidate hashes from the integrated 242-file source.
It changes 77 files and preserves all 41 locked files.
The prepared integration driver requires complete both-host qualification and an exact primary review record before applying.
Only preparation and disposable reconstruction have run. No integration review completion is claimed.
Read evidence/authority-supervisor-2026-10-09/integration-prepared-02.json and integration-reconstruction-02.json.

No new production source is integrated. M02 and full Spatial-M1 remain incomplete.


## 2026-10-09: Supervisor focused Debug passes both hosts; sb16 sanitizer focus passes

Corrected supervisor candidate02 passed focused Debug on both hosts.
sb16 also passed focused runtime ASan/UBSan.
Each completed profile passed 383 checks across 25 suites and 51 harness checks.
No case failed or skipped. All 281 source hashes and 41 locked files matched.
Existing protobuf descriptors remained unchanged.
Read evidence/authority-supervisor-2026-10-09/candidate-02.

sb16 full cumulative qualification is active on the same binaries.
sb16-2 runtime sanitizer qualification remains active. Its full continuation stays queued.
No full supervisor pass or integration is claimed.

The next bounded step is native broker credential handoff.
The supervisor currently discards the credential returned by AuthorityLaunch::prepare.
Workspaced already accepts sealed native configuration, but the supervisor does not supply it.
A new independent startup test requests the proposed configuration field and starts real workspaced.
Its diagnostic is queued behind each exact full-qualification driver.
It requires completed qualification and unchanged source and binary hashes before testing.
The baseline has not run. An expected missing-field failure will not count as acceptance success.
Read docs/SUPERVISOR_BROKER_NATIVE_HANDOFF.txt and evidence/supervisor-broker-handoff-2026-10-09.

The current integrated production source remains 94c6cb6 with 242 files.
M01, M02, and complete Spatial-M1 qualification remain incomplete.


## 2026-10-09: Both-host relay qualification passes; corrected supervisor passes sb16 Debug

Relay candidate01 passed full Debug and runtime ASan/UBSan on both hosts.
Each profile passed 1366 core checks across 89 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. All 277 source hashes and 41 locked files matched.
The candidate remains isolated. Older failed qualification evidence remains preserved.

Both hosts reproduced native configuration ownership loss against unchanged relay objects in both profiles.
Each profile recorded two expected ownership failures and one passing consuming-interface control.
Read evidence/native-scope-config-2026-10-09/baseline-01.

Corrected supervisor candidate02 passed focused Debug on sb16.
Its 383 checks include three ownership checks, fourteen supervisor checks, and 366 existing regression checks.
All 25 suites and 51 harness checks passed without failures or skips.
The schema audit confirms existing descriptors remain unchanged.
sb16 is building the sanitizer profile. sb16-2 is building the same candidate's Debug profile.
Read evidence/authority-supervisor-2026-10-09/candidate-02/sb16/summary-debug.json.

Cumulative continuation is queued behind each exact qualifier process through a pidfd.
It first verifies both focused profiles, source hashes, and schema preservation.
It stops on failure. It neither changes source nor retries a failed test.
The full target is 1383 core checks across 91 suites, 51 harness checks, and 151 P10 cases per profile.
These are expected counts, not completed full supervisor results.

Integrated production remains the 242-file increment at 94c6cb6.
World and broker wiring, native credential handoff, fencing, and checked recovery remain required.
M01, M02, and complete Spatial-M1 qualification remain unfinished.


## 2026-10-09: sb16 relay qualification passes; supervisor correction starts testing

Relay candidate01 passed full Debug and runtime ASan/UBSan on sb16.
Each profile passed 1366 core checks across 89 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. This candidate remains isolated.
sb16-2 passed full Debug. Its runtime sanitizer run remains active.

The independent ownership witness confirmed NativeScope descriptor ownership loss on sb16 in both profiles.
Each profile produced two expected failures and one passing NativeLaunch control.
This does not establish the cause of earlier world startup failures.
The same witness remains queued behind the unchanged sb16-2 relay run.

Supervisor candidate02 includes the correction, three ownership checks, and fourteen supervisor checks.
Its sb16 configure step passed. Its build is active.
No candidate02 runtime result is claimed. Both hosts must pass qualification before integration.
Integrated production remains the 242-file increment at 94c6cb6.
M01, M02, and full Spatial-M1 qualification remain incomplete.

Read evidence/participant-relay-2026-10-09/full-candidate-01/sb16/summary-final.json.
Read evidence/native-scope-config-2026-10-09/baseline-01/sb16/summary.json.
Read evidence/authority-supervisor-2026-10-09/source-review-02.json.


## 2026-10-09: Relay full Debug passes; native configuration ownership witness prepared

Relay candidate01 completed full Debug on both sb16 and sb16-2.
Each host passed 1366 core checks across 89 suites, 51 harness checks, and 151 P10 cases.
No Debug case failed or skipped. All 277 source hashes and 41 locked hashes match.
Full runtime sanitizer qualification remains active on the same binaries.
Read evidence/participant-relay-2026-10-09/full-candidate-01.

Native handoff review identified a descriptor ownership mismatch in the existing isolated coordinator.
AuthorityLaunch retains a local configuration descriptor that NativeScope already consumes.
The later caller close can affect a reused descriptor number.
The prepared witness checks retained configuration ownership and unrelated descriptor survival.
It will link unchanged frozen native objects after the current timed qualification finishes.
No witness build or runtime reproduction is claimed yet.
Read docs/NATIVE_SCOPE_DESCRIPTOR_OWNERSHIP.txt and evidence/native-scope-config-2026-10-09.

Supervisor candidate01 qualification is held for this focused ownership diagnosis and correction.
Its copied build driver has not run. No supervisor code is integrated.
Do not attribute the earlier world startup failures to this ownership mismatch without evidence.
M02, native handoff, checked recovery, and full Spatial-M1 remain incomplete.

## 2026-10-09: Relay focused matrix passes; broader startup failures remain unresolved

Relay candidate01 passed focused Debug and runtime ASan/UBSan on both hosts.
Each profile passed 366 checks across 23 suites and 51 harness checks.
No case failed or skipped. All 277 source hashes and 41 locked hashes match.
Full cumulative continuation now runs on the same binaries, without overlapping project builds.
No cumulative relay result is claimed yet.

Owned participant candidate01 passed full Debug on both hosts.
Its full sanitizer run failed one startup check on sb16 and two on sb16-2.
The 1339-check core totals were 1338 passed and one failed, then 1337 passed and two failed.
Runtime P10 was unrun after these failures. No overall sanitizer pass is claimed.
Retained service logs show startup transport failures, including an issuer deadline timeout.
The affected logs contain no ASan or UBSan memory report.
Each of the three affected methods then passed five unchanged repetitions on each host.
These 30 diagnostic passes do not establish a cause or replace the original failed qualification.
Production code, fixture deadlines, and assertions remained unchanged.
Read evidence/owned-participant-2026-10-09/full-candidate-01 and startup-diagnostic-01 there.

Both hosts confirmed the missing production supervisor executable.
The baseline builds and 51 harness checks passed; its one new test failed as expected.
The baseline proves a missing executable only. Supervisor behavior remains unqualified.
Isolated supervisor candidate01 now implements startup, automatic relay pumping, and checked shutdown.
Its fourteen new tests cover real issuer transitions, revocation acknowledgement, shutdown, and restart refusal.
The candidate also rejects privileged descriptor aliases and nonzero child exit after release.
All 279 hashes reconstruct from the reviewed patch. Existing tests and 41 locked files remain unchanged.
Linux build and runtime tests have not started. Their driver requires completed relay qualification first.
Read docs/AUTHORITY_SUPERVISOR_RUNTIME.txt and evidence/authority-supervisor-2026-10-09.
No new candidate source is integrated. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Private child channel passes both profiles; supervisor relay enters qualification

Private child-channel candidate02 passed focused Debug and runtime ASan/UBSan on both hosts.
Each profile passed 356 checks across 22 suites and 51 harness checks.
No final case failed or skipped. All 276 source hashes and 41 locked hashes match.
The original fixture failure and corrected diagnostic remain preserved.

Launch authorization candidate03 completed full Debug and runtime ASan/UBSan on both hosts.
Each profile passed 1328 core checks across 86 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. All 273 source hashes and 41 locked hashes match.

Both hosts reproduced the supervisor's unsupported private-channel preparation operation.
The isolated relay now reserves the child endpoint, passes it through the owned process launch, and forwards exact commands.
It retains durable approval, authenticated issuer ownership, bounded transport, and checked child lifetime.
External participant polling and command injection cannot compete with the relay.
A forwarded stale revision remains unchanged for the real issuer to reject.
Parent receipt and child delivery do not acknowledge completed revocation effects.

Relay candidate01 contains 277 files and ten new tests.
Both hosts passed 366 focused Debug checks across 23 suites and 51 harness checks.
No Debug case failed or skipped. Runtime sanitizer qualification is active.
Descriptor review confirms additive protobuf changes. Full cumulative continuation is prepared but has not started.
All previous test files and 41 locked files remain unchanged.
Read docs/AUTHORITY_PARTICIPANT_RELAY.txt and evidence/participant-relay-2026-10-09.

Owned participant full Debug passed both hosts; its runtime sanitizer matrix continues.
No new source is integrated. World/broker wiring, fencing, checked recovery, M02, and Spatial-M1 remain incomplete.

## 2026-10-09: Child-channel fixture corrected; owned participant full Debug passes

Private channel candidate01 ran 356 Debug checks across 22 suites on each host.
One new fixture test retained too many issuer connections and errored before its final assertion.
The other 355 checks passed. Both harness runs passed all 51 checks.
The original failures, commands, source, and additive protobuf audits remain preserved.

Candidate02 closes completed subcase connections before starting the next subcase.
All production files, previous tests, connection limits, and assertions remain unchanged.
Both hosts passed all seventeen corrected channel tests using the original production binaries.
Fresh candidate02 Debug and runtime ASan/UBSan qualification is active on both hosts.
Read evidence/participant-channel-2026-10-09/fixture-review-02.txt.

Owned participant candidate01 passed full Debug qualification on both hosts.
Each host passed 1339 core checks across 87 suites, 51 harness checks, and 151 P10 cases.
No Debug case failed or skipped. Full runtime sanitizer qualification continues.
The launch-authorization full sanitizer matrix also remains active.

No new source is integrated. Production relay, fencing, checked recovery, M02, and Spatial-M1 remain incomplete.

## 2026-10-09: Owned participant sanitizer checks pass; private child channel enters qualification

Owned participant candidate01 passed both focused profiles on both hosts.
Each profile passed 339 checks across 21 suites and 51 harness checks.
No case failed or skipped. All 274 source hashes and 41 locked hashes match.
Full qualification continues on the same frozen binaries. Completed focused tests are not repeated.

Launch authorization candidate03 passed full Debug qualification on both hosts.
Each host passed 1328 core checks across 86 suites, 51 harness checks, and 151 P10 cases.
No Debug case failed or skipped. Full runtime sanitizer qualification remains active.

Both hosts reproduced rejection of inherited participant configuration in the old runtime.
The original broken-pipe test failures remain preserved with independent child exit diagnostics.
Private child-channel candidate01 adds bounded Protobuf transport without child signing keys or direct TLS.
Seventeen new checks cover real issuer transitions, revocation, malformed peers, descriptor identity, and exec inheritance.
Both-host Debug builds have started. No candidate qualification result is claimed yet.
The candidate preserves all prior test files and 41 locked files.

No new candidate source is integrated.
The production supervisor relay must distinguish child consumption from parent receipt.
World and broker integration, fencing, checked recovery, M02, and Spatial-M1 remain incomplete.

## 2026-10-09: Supervisor owns the launch participant; registry full matrix passes

The isolated coordinator now owns the real participant worker and its issuer connection.
It builds supervisor approval from that retained identity and the durable process launch identity.
It persists the exact approval before releasing supervised child execution.
Failed exec cannot permit activation. Commands require a live direct child in the owned scope.
Restart cannot reconstruct this live ownership from the journal.
Cleanup still preserves unresolved issuer generations and revocation obligations.

Both hosts reproduced the missing start gate and unsupported attachment before implementation.
A separate real child witness confirmed execution without approval inside the exact baseline cgroup.
Candidate01 then passed all eleven new Debug checks and 51 harness checks on both hosts.
The new child witness reads durable approval before execution continues and the real issuer accepts the retained connection.
Rollback, wrong-key, identity mismatch, worker failure, cleanup, and restart checks also passed.

Output01 passed 328 checks across twenty suites on each host.
One existing native suite could not import input_pb2 because the build command omitted native-input dependencies.
Its eleven checks remained unrun. The original command, failure, and source remain preserved.
Corrected output02 builds every target and supplies the independent native input witness.
Both corrected Debug runs passed 339 checks across 21 suites and 51 harness checks.
No corrected Debug case failed or skipped. Runtime sanitizer qualification continues.
They use the same 274 source files and unchanged test assertions.
All 41 locked hashes remain unchanged. Descriptor review confirms additive protobuf changes only.
Read docs/AUTHORITY_OWNED_PARTICIPANT.txt and evidence/owned-participant-2026-10-09.

Registry candidate01 completed full Debug and runtime ASan/UBSan on both hosts.
Each profile passed 1305 core checks across 85 suites, 51 harness checks, and 151 P10 cases.
No final case failed or skipped. All 272 source hashes and 41 locked hashes match.
Full launch-authorization qualification remains active on both hosts.

No new candidate source is integrated.
Private child-channel integration, fencing, recovery, M02, and Spatial-M1 remain incomplete.

## 2026-10-09: Exact launch authorization passes both focused profiles

Candidate03 passed Debug and runtime ASan/UBSan on both hosts.
Each profile passed 285 focused checks across sixteen suites and 51 harness checks.
No final case failed or skipped. All 273 source hashes and 41 locked hashes match.
Descriptor comparison preserves every existing protobuf declaration.
The unique configuration-file correction resolves the reproduced fixture race.
All earlier assertions and production source remain unchanged by candidate03.
Original failures and diagnostics remain preserved beside the final reports.

Full authorization qualification has started on both hosts in output03.
Its expected matrix is 1328 core checks across 86 suites, 51 harness checks, and 151 P10 cases per profile.
Expected totals are not completed results.
The supervisor must still connect approval to retained launch ownership and checked recovery.
No authorization source is integrated. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Client fixture correction enters qualification; incarnation full matrix passes

Service incarnation candidate01 completed full Debug and runtime ASan/UBSan on both hosts.
Each profile passed 1287 core checks across 84 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. The collector verified all 271 source hashes and 41 locked hashes.
The final reports are preserved in evidence/participant-incarnation-2026-10-09/full-candidate-01.

Launch authorization candidate02 passed 284 focused checks and 51 harness checks in both profiles on sb16.
On sb16-2, Debug passed. One sanitizer fixture failed; 283 of 284 focused checks passed.
The unsolicited-reply fixture sometimes sent too few or too many requests to its test peer.
A ten-run diagnostic reproduced two failures and recorded the extra connection and early timeout.
Both hosts then reproduced the shared configuration overwrite directly.
The fixture reused one configuration pathname before an earlier client necessarily read it.

Candidate03 gives each fixture client a separate private configuration file.
One additional check verifies independent contents, retained configuration bytes, and private permissions.
All prior test bodies and production source match candidate02 exactly.
The patch reconstructs all 273 files. All 41 locked hashes remain unchanged.
Both hosts passed 285 focused Debug checks across sixteen suites and 51 harness checks.
No Debug case failed or skipped. Runtime sanitizer qualification continues.
Full authorization qualification has not started.
Read evidence/launch-authorization-2026-10-09 for the original failures and corrective patch.

Registry full Debug passed on both hosts: 1305 core checks, 51 harness checks, and 151 P10 cases each.
Its full sanitizer runs remain active. No new candidate source is integrated.
Supervisor launch ownership integration, fencing, recovery, M02, and Spatial-M1 remain incomplete.

## 2026-10-09: Exact supervisor launch authorization passes focused Debug

The isolated issuer records supervisor approval for an exact launch and service connection.
Activation consumes that approval in the same transaction that creates the service generation.
Stale connections, changed supervisor bindings, and superseded generation approvals cannot activate.
Authorization retrieval preserves pending revocation obligations.
The production client validates supervisor challenges, proofs, and correlated replies.

Both hosts passed 284 focused Debug checks across sixteen suites and 51 harness checks.
The twenty-two new checks include storage rollback, migration, restart, concurrency, replay, and independent TLS fault peers.
No corrected Debug case failed or skipped.
An earlier new fixture raced through shared signing files and failed on both hosts.
Candidate02 prepares proofs before issuing concurrent requests. Its production code matches candidate01 exactly.
The original failure and both source patches remain preserved.

All 273 candidate source hashes and 41 locked hashes match.
Six historical migration fixtures changed only schema construction and current-version assertions.
Existing protobuf descriptors remain unchanged after removing the documented additions.
Runtime sanitizer qualification is running. Full qualification has not started.
Read docs/AUTHORITY_LAUNCH_AUTHORIZATION.txt and evidence/launch-authorization-2026-10-09.
No authorization source is integrated. The coordinator signing bridge and checked recovery remain required.
M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Registry focused qualification and coordinator full qualification pass

Supervisor registry candidate01 passed Debug and runtime ASan/UBSan on both hosts.
Each focused profile passed 246 checks across fourteen suites and 51 harness checks.
No final case failed or skipped. Source hashes, locked files, and additive protobuf descriptors match.
Fresh full registry qualification is running separately in output03.
Both full-build Debug runs passed eighteen registry checks and 51 harness checks.
The remaining full matrix is pending. No registry source is integrated.

Launch coordinator candidate03 completed both full profiles on both hosts.
Each profile passed 1282 core checks across 83 suites, 51 harness checks, and 151 P10 cases.
No final case failed or skipped. All 270 source hashes and 41 locked hashes match.
The coordinator remains isolated while authenticated launch and recovery work continues.

The next launch-authorization baseline built on both hosts.
Its positive case failed with UNKNOWN_METHOD through the production issuer client.
The additive contract binds supervisor approval to an exact launch, service connection, and previous generation.
The authorization implementation is not written. Seven additional drafted checks remain unrun.
Read docs/AUTHORITY_LAUNCH_AUTHORIZATION.txt and evidence/launch-authorization-2026-10-09.
M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Owner-controlled supervisor registry enters qualification

The isolated issuer now binds a separate supervisor key through an owner-signed challenge.
The binding names an exact participant registration and persists with a checked revision.
Active generations cannot be adopted. Binding does not clear revocation obligations.
Bound participants cannot activate through a service key alone.
Explicit supervisor launch authorization remains unimplemented.

Both hosts reproduced the missing operation before implementation.
Both hosts passed all eighteen new Debug checks and 51 harness checks.
One existing client test could not run because the first build command omitted its probe executable.
The corrected command builds that probe. Source and assertions remain unchanged.
The corrected command passed all 246 focused Debug checks across fourteen suites on both hosts.
Both hosts also passed 51 harness checks. No corrected Debug case failed or skipped.
Runtime sanitizer qualification continues.

All 272 candidate hashes reconstruct from the saved patch. All 41 locked hashes remain unchanged.
Five historical migration fixtures explicitly remove the schema7 table when constructing older databases.
Their data-preservation and rollback assertions remain intact.
Read docs/AUTHORITY_SUPERVISOR_REGISTRY.txt and evidence/supervisor-registry-2026-10-09.
This source is isolated. Supervisor recovery, M02, and Spatial-M1 remain incomplete.

## 2026-10-09: Preassigned service incarnation passes both focused profiles

The isolated runtime now accepts an explicit 32-byte service incarnation in private configuration.
The exact value reaches issuer authentication and durable activation.
An empty or malformed explicit value fails before network access.
Existing service-key, domain, and used-incarnation checks remain active.
This identity field does not authorize launch or supervisor recovery.

Both hosts passed 84 focused checks across six suites in Debug and runtime ASan/UBSan.
Each profile also passed 51 harness checks. No final case failed or skipped.
All 271 source hashes and 41 locked hashes match.
Descriptor comparison confirms that all existing protobuf fields remain unchanged.
The source remains isolated. Read docs/AUTHORITY_SERVICE_INCARNATION.txt.
Fresh output03 builds completed full Debug qualification on both hosts.
Each host passed 1287 core checks across 84 suites, 51 harness checks, and 151 P10 cases.
No full Debug case failed or skipped. Runtime sanitizer qualification continues.

The first focused command omitted generated Python bindings and failed harness imports on both hosts.
The corrected command generated the bindings without changing production code or test assertions.
The original missing-feature baseline and setup failures remain preserved in evidence/participant-incarnation-2026-10-09.

Authenticated supervisor launch authorization, recovery reconciliation, and M02 remain incomplete.

## 2026-10-09: Participant corrections complete both full profiles

Combined participant candidate09 passed full Debug and runtime ASan/UBSan on both hosts.
Each profile passed 1239 core checks across 79 suites, 51 harness checks, and 151 P10 cases.
No final case failed or skipped. All 255 source hashes and 41 locked hashes match.
Final reports are in evidence/m02-corrections-2026-10-09/candidate-09.
The source remains isolated. These passes do not complete supervisor recovery or M02.

The new authority launch coordinator records ownership before native issuance and execution.
Candidate01 passed 28 focused checks and 51 harness checks per profile on both hosts.
An additional partial-commit regression then found a retained native identity gap on both hosts.
Candidate02 records that identity from its retained live scope before checked cleanup.
Candidate02 passed the partial-commit regression but failed one new cgroup inventory assertion on both hosts.
The assertion counted standard cgroup control files as child scopes. Candidate03 checks child directories and member processes.
All fourteen coordinator checks passed against unchanged candidate02 production on both hosts.
Candidate03 passed 43 focused Debug checks across four suites and 51 harness checks on both hosts.
All 270 source hashes and 41 locked hashes match. No focused case failed or skipped.
It subsequently passed full Debug on both hosts: 1282 core checks, 51 harness checks, and 151 P10 cases each.
No full Debug case failed or skipped. Runtime sanitizer qualification continues.
The expected full matrix is 1282 core checks, 51 harness checks, and 151 P10 cases per profile.
Expected totals are not completed results.
Read docs/AUTHORITY_LAUNCH_COORDINATOR.txt for scope, state transitions, and restart limitations.
No coordinator source is integrated. Full supervisor recovery remains pending.

The older combined 264-file scopes candidate also completed both full profiles on both hosts.
Each profile passed 1260 core checks, 51 harness checks, and 151 P10 cases with no failures or skips.
All 264 source hashes and 41 locked hashes match. The source remains isolated.

## 2026-10-09: Prepared authority launch passes both focused profiles

The isolated prepared-launch candidate01 passed eighteen focused checks on both hosts in Debug and runtime ASan/UBSan.
Eight checks cover the new preparation and descriptor boundary. Ten existing process cleanup checks remain unchanged.
Each profile also passed 51 harness checks. No case failed or skipped.
All 265 source hashes and 41 locked hashes match.

The supervisor can identify an empty process scope before releasing authority execution.
The start operation pins and migrates its child before exec and permits only explicitly selected inherited descriptors.
Independent child witnesses verify the actual cgroup and descriptor contents.
Stopped scopes, second starts, invalid descriptor tables, and execution retries after a failed exec are rejected.
The existing stop and destructor implementations remain unchanged.

Two new boundary checks first failed on both hosts against the previous production launcher.
The patch and complete focused evidence are in evidence/authority-prepared-launch-2026-10-09.
Read docs/AUTHORITY_PREPARED_LAUNCH.txt for the additive private protocol and its limits.
The started flag records an attempt. It does not prove service readiness or issuer activation.

Durable coordinated launch records and verified supervisor recovery remain unimplemented.
The older 264-file combined candidate passed full Debug on both hosts.
Each host passed 1260 core checks, 51 harness checks, and 151 P10 cases with no failures or skips.
Its full sanitizer qualification continues.
No prepared-launch source is integrated. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Supervisor native ownership passes both focused profiles

The isolated native-scope candidate02 passed eleven new checks and 43 existing native regressions on both hosts.
Both hosts also passed 51 harness checks per profile. No final case failed or skipped.
All 259 source hashes and 41 locked hashes match. Debug and runtime ASan/UBSan both passed.

The production class issues a native authority credential through its retained supervisor connection.
It reports cleanup only after a checked revocation reply from the same native server.
Independent witnesses verify descendant revocation, preservation of unrelated credentials, and real key and button release.
Paused-server, timeout, missing-credential, and server-exit cases cannot report successful cleanup.
The existing broker uses the same checked connection initializer and passes its unchanged native regression checks.

Candidate01's new input fixture requested XI2 under an ordinary application credential and failed before receiving input.
Both hosts reproduced that fixture mismatch. The same core-event release assertions passed in controlled diagnostics.
Candidate02 uses permitted application events and independently checks the actual routed device through the supervisor.
No existing test, native witness source, or server permission changed.
See evidence/authority-native-scope-2026-10-09 and docs/AUTHORITY_NATIVE_SCOPE.txt.

This component does not implement durable supervisor ownership, broker reservation recovery, or issuer retirement.
Combined participant candidate09 passed full Debug on both hosts in output10.
Each host passed 1239 core checks, 51 harness checks, and 151 P10 cases. Sanitizer qualification continues.
The combined 264-file process and native ownership candidate01 has started full qualification on both hosts.
Its expected matrix is 1260 core checks, 51 harness checks, and 151 P10 cases per profile.
No native-scope source is integrated. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: World revision ordering and native witness corrections pass both focused profiles

An older status reply can arrive after a newer participant revocation event.
Two counterexamples proved that candidate06 then disabled unrelated world actors.
Both failed on both hosts in Debug and runtime ASan/UBSan.
The isolated correction rejects the superseded read without applying it or disabling unaffected sessions.
Replies older than the revision known before the query still disable the invalid context.
No action is replayed automatically. Existing revocation cancellation remains active.

Candidate08 passed 111 focused checks across ten suites and 51 harness checks in both profiles on each host.
Those checks include both interleavings, two invalid-replay cases, and the corrected bootstrap owner-idle regression.
Debug and runtime ASan/UBSan completed with no failures or skips.

Candidate06 finished full qualification on sb16: 1232 core, 51 harness, and 151 P10 cases per profile passed.
On sb16-2, one sanitizer native-launch method failed during X extension discovery after its connection closed.
A controlled real-server witness reproduced that ordering on both hosts and both profiles.
The corrected polling helper still requires explicit protocol denial after any transient connection closure.
Candidate09 passed 21 native-launch and 22 native security-identity checks in both profiles on each host.
Its production inputs match candidate08. Its complete combined matrix is running on both hosts in output10.
The expected matrix contains 1239 core checks, 51 harness checks, and 151 P10 cases per profile.
Those expected totals are not completed results.

The maintained bootstrap fixture now opens a fresh owner connection before requesting a mutation challenge.
Historical candidate07 completed both full profiles on both hosts: 1233 core checks, 51 harness checks, and 151 P10 cases each.
It lacks the newer corrections and remains historical evidence.
The historical native-control failure remains unexplained; the new ordering defect is not claimed as its proven cause.
See evidence/m02-corrections-2026-10-09 for reviewed patches, explicit fixture changes, original failures, and exact manifests.
No candidate07-09 source is integrated. M02 and Spatial-M1 remain incomplete.

## 2026-10-09: Process scope focused checks pass; broader sanitizer failures retained

The isolated supervisor process-scope candidate03 passed ten checks per profile on both hosts.
Both Debug and runtime ASan/UBSan passed. All 258 source hashes matched.
The production class owns a cgroup before execution and checks subtree termination before its cleanup receipt.
It does not yet provide complete supervisor recovery or issuer fencing.
See evidence/authority-process-scope-2026-10-09 and docs/AUTHORITY_PROCESS_SCOPE.txt.
No process-scope source is integrated into main.

Combined participant candidate06 passed full Debug on both hosts.
Each host passed 1232 core checks, 51 harness checks, and 151 P10 cases with no failures or skips.
Its sanitizer build, harness, and 81 focused checks passed on both hosts. Remaining core testing continues.

Historical candidate05 failed sanitizer core qualification on both hosts.
sb16 failed one credential-revocation native-control method, including cleanup assertions.
sb16-2 failed thirteen bootstrap methods while using a closed owner TLS connection.
Runtime P10 remains unrun for both failed candidate05 runs.
See evidence/participant-sanitizer-failures-2026-10-09 for all raw failures and diagnostics.

A controlled idle diagnostic reproduced the bootstrap connection failure on both hosts.
Opening a fresh owner connection before the mutation challenge passed the unchanged file-write test on both hosts.
That fixture correction still needs incorporation and qualification.
Twenty exact credential-control repetitions passed on candidate06. The historical native-control cause remains unresolved.
M02 and Spatial-M1 remain incomplete. No operator display or database changed.

## 2026-10-09: Native cleanup acknowledgement gap corrected in isolated candidate06

A diagnostic on candidate05 observed issuer completion while the private X server was stopped and native cleanup was unconfirmed.
The timing-sensitive failure occurred with GDB attached only to the test-owned broker.
The intended sustained-pause marker was not reached. Later relay-only and boundary attempts passed.
The original failure and those diagnostic limits remain preserved in evidence/participant-native-settlement-2026-10-09.

Candidate06 rechecks the native channel after process reconciliation, reaping, and control updates.
A cleanup failure now stops that iteration before it can enqueue a revocation acknowledgement.
Both hosts passed 81 focused Debug checks across 11 suites and 51 harness checks.
The two added core cases cover direct revocation and revocation during a delayed keepalive reply.
The corrected debugger diagnostics also passed on both hosts.
All 253 source hashes and 41 locked hashes match. Existing tests remain unchanged.
Complete Debug, P10, and runtime sanitizer qualification continues in output07.
No candidate06 production source is integrated yet. Supervisor recovery and remote participation remain pending.

Candidate05 separately passed 1230 Debug core checks across 76 suites, 51 harness checks, and 151 P10 cases on each host.
Its sanitizer runs continue as historical evidence. Those results cannot qualify candidate06 or override its recorded native failure.
M02 and Spatial-M1 remain incomplete. No operator display or database changed.


## 2026-10-09: Self-revocation receipt corrected; native crash boundary witnessed

Candidate04 completed Debug core testing on both hosts.
Each executed 1227 core checks: 1226 passed, one failed, zero skipped. The 51 harness checks passed.
Both failures affected the same existing world-bootstrap self-revocation receipt assertion.
P10 and runtime sanitizer were unrun after the Debug failure.

Candidate05 preserves the successful receipt when a local grant revocation removes the caller's own access.
It does not restore access or exempt protected reads and delayed operations from cancellation.
Three added counterexamples cover private self-revocation, public admission, and revocation before the local commit.
Both hosts passed 79 focused Debug checks across ten suites and 51 harness checks.
All 252 candidate source hashes and 41 locked hashes match. Existing test methods remain unchanged.
Full qualification continues in output06. No candidate05 production source is integrated yet.
See evidence/participant-fixtures-2026-10-09 for both failed inventories and the reviewed correction.

Both hosts also passed two crash witnesses against candidate04 Debug binaries.
World and compositor exit preceded native key/button release while the owned X server was stopped.
Broker death terminated five pinned descendants. Both cases retained obligations and rejected replacement.
See docs/AUTHORITY_SUPERVISOR_RECOVERY.txt and evidence/participant-recovery-2026-10-09.
Supervisor recovery remains unimplemented. The witnesses do not qualify a physical or GPU session.

Before combined integration, resolve the recorded native-settlement review question.
Native cleanup can fail after the broker's early channel check. Review acknowledgement ordering at that boundary.
The stopped-server diagnostic passed on sb16; a late acknowledgement race is not yet a confirmed runtime defect.
M02, Spatial-M1, cross-host participation qualification, and physical acceptance remain incomplete.


## 2026-10-09: Registered world and broker fixtures enter combined qualification

The broker/world candidate02 passed 36 focused checks and 51 harness checks in both profiles on both hosts.
Broader migrated fixtures then exposed a typed-error regression. The guard now preserves existing failure replies.
All 20 workspace-authority tests pass on that corrected draft, including process retry and clean restart cases.
One earlier setup failure remains unexplained. Eight exact diagnostic repetitions passed; future runs now retain service logs.

The new tests check exact workspace domains and revocation during real delayed replies.
The multi-workspace fixture now registers both domains once and gives each workspace a separate repository.
All existing main test methods remain unchanged. The AST audit and original failed runs are preserved.
The current candidate04 contains 251 frozen files. Full Debug and sanitizer qualification is running on both hosts.
Output05 selects this candidate. The mistaken output04 launches are preserved and excluded from qualification.
Both hosts passed 61 focused Debug checks across eight suites and 51 harness checks on candidate04.
The remaining core suites are running. Runtime sanitizer and full P10 results remain pending.
See evidence/participant-fixtures-2026-10-09 for the patch, review, diagnostics, and source manifests.

Checked process termination is separately integrated and adopted as 94c6cb6.
Remote participation and supervisor fencing remain pending. M02 and Spatial-M1 remain incomplete.


## 2026-10-09: Checked application termination qualified and integrated

Application cancellation now pins the namespace leader before application startup.
Successful cleanup requires namespace exit and a checked reap of the owned outer child.
Kernel-denied signals, failed waits, and unresolved destructor cleanup cannot report successful cancellation.

Both hosts passed complete Debug and runtime ASan/UBSan qualification.
Each profile passed 1192 core checks across 69 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. All 242 source hashes and 41 locked hashes match.
Primary review covered all five changed source and test files. Existing tests and schemas remain unchanged.
The three original cleanup failures remain preserved as regression evidence.
Commit 94c6cb6 is pushed to box-git and adopted on both hosts. All 242 source hashes match and both worktrees are clean.
See evidence/process-termination-2026-10-09/integration-applied-02.json.

The separate broker/world draft still requires broad qualification and supervisor fencing.
Its initial focused matrix passed on both hosts, but broader testing found a typed-error regression.
A corrected isolated draft preserves existing error replies. All 20 workspace-authority checks now pass on sb16.
The current combined qualification also corrects fixture setup and preserves existing test assertions.
No operator database was migrated and no native session was restarted. M02 and Spatial-M1 remain incomplete.


## 2026-10-09: Broker participation passes first process and native reservation checks

The unchanged broker reproduced the zero-participant revocation gap in three tests.
The issuer reported complete while a real application kept writing and its descendant pidfds remained live.
Both baseline failures and the earlier fixture setup error remain preserved.

An isolated broker now activates before protected work and acknowledges only from the outer dispatch loop.
Acknowledgement waits for checked process cleanup, reconciled authority, and revoked control-reservation release.
Clean shutdown stops jobs and releases its generation. Unresolved cleanup or control retains issuer obligations.
Credential revocation preserves independently authorized actor-owned execution.

Candidate01 passed six broker, six world, fourteen worker, and six process checks in sb16 Debug.
Candidate02 adds native reservation tests without changing production code.
Its two native checks and repeated six broker checks passed on sb16 using the candidate01 binaries.
Fresh Debug and runtime ASan/UBSan focused qualification is running on both hosts.
All 250 candidate hashes match. The existing 41 locked files remain unchanged.
See evidence/workspace-participant-2026-10-09 and tasks/m1/identity.workspace_participant.json.

The separate process correction passed its full Debug matrix on both hosts.
Each host passed 1192 core checks, 51 harness checks, and 151 P10 cases.
Its full runtime sanitizer matrices remain live. No correction is integrated yet.
Broad world/broker fixture migration, delayed-reply witnesses, supervisor fencing, and remote participation remain pending.
M02 and Spatial-M1 remain incomplete. No operator database or native session changed.


## 2026-10-09: Process cancellation failures reproduced; correction under qualification

The unchanged ApplicationProcess cleanup failed three independent checks on sb16.
It returned success while two of five pinned descendant pidfds had not reported exit.
It ignored a kernel-denied wait call. It also hung after a kernel-denied signal call.
These failures prevent using the old cleanup result as proof of completed broker revocation.

The isolated correction holds application startup until it pins the namespace leader.
It waits for that leader and checks the outer child reap before returning success.
Unconfirmed destructor cleanup stops the broker with an error, preserving uncertainty.
Both hosts passed four new checks and 51 related regressions in each Debug and runtime ASan/UBSan profile.
Candidate02 retains the same production code and adds two destructor failure tests.
Its full Debug and sanitizer matrix is running on both hosts.
On sb16, all six cleanup tests and 51 harness checks have passed; the existing core suites are running.
All existing tests, protobuf schemas, and 41 locked files remain unchanged.
No production source or operator session changed.
See evidence/process-termination-2026-10-09 and docs/PROCESS_TERMINATION.txt.


## 2026-10-09: Durable issuer barrier integrated after both-host qualification

The issuer records active-service obligations in the same transaction as revocation.
Only the original authenticated service connection can acknowledge contiguous applied revisions or release its generation.
Disconnect and restart preserve unresolved obligations. Owner queries distinguish committed revocation from completed cancellation.
The checked client rejects inconsistent identity, revision, pagination, proof, and outcome data.

Primary review covered all twelve changed source and test files.
The integrated source retains the corrected workspace fixture.
Both hosts qualify 1186 core checks, 51 harness checks, and 151 P10 cases in each Debug and runtime sanitizer profile.
This count combines the preserved full barrier run with the complete corrected workspace suite.
All 240 source hashes and 41 locked files match. Schema6 migration was tested only on disposable databases.
Commit 1d129f7 is pushed to box-git and adopted on both hosts with all 240 source hashes verified.
Both worktrees are clean. No operator database was migrated and no native session was restarted.
See evidence/participant-barrier-2026-10-09/integration-applied-05.json.

The isolated world draft also passed focused checks on both hosts in both profiles.
Each profile passed six world, two native cancellation, and fourteen worker checks.
Broker integration, broad world regression, remote participation witnesses, and supervisor fencing remain pending.
M02 and Spatial-M1 remain incomplete.


## 2026-10-09: World participation and native cancellation pass focused checks

The isolated world activates its registered service identity before admitting actor actions.
Revocation completion waits for motion cancellation and native input release.
A real delayed event keeps the issuer obligation pending. A stopped compositor also keeps it pending.
The in-flight renderer test rejects a late result and prevents stale input replay.
Clean world shutdown releases its generation. A crash retains it and prevents an unfenced replacement.

On sb16, Debug and runtime ASan/UBSan each pass six world, two native, and fourteen worker checks.
The sb16-2 Debug profile has passed the same checks. Its sanitizer profile is running.
All 246 candidate03 source hashes and 41 locked files match on sb16.
These checks use real worldd, spatialwm, and independent X clients on a diagnostic software display.
They do not qualify physical input, native GPU timing, or the full M02 requirement group.
Broker integration, broader fixture migration and regression, and supervisor fencing remain pending.
See evidence/participant-world-2026-10-09.

The isolated worker also passes 14 new and 96 related checks per profile on both hosts.
The issuer barrier passes its full matrix on both hosts in Debug and runtime sanitizers.
Each profile passes 1185 core checks, 51 harness checks, and 151 P10 cases.
The complete corrected workspace suite adds one case, giving 1186 core checks for the prepared integration.
All original logs remain preserved. Barrier integration is prepared and awaits final source review.
No operator databases or desktop sessions changed.


## 2026-10-09: Participation worker passes focused Debug and sanitizer checks

The isolated worker authenticates its service key and exact domains on one checked TLS connection.
Its consumer must explicitly activate, acknowledge each consumed revision, and release a settled generation.
A real activation race requires explicit resynchronization. Queue overflow permanently disables the worker.
Destruction and connection loss preserve pending issuer obligations.

On sb16-2, both Debug and runtime ASan/UBSan pass 14 new checks and 96 related regression checks.
All 244 source hashes and 41 locked files match. Existing 265 protobuf messages retain their fields and enum values.
Equivalent sb16 focused verification is running.
The worker remains isolated. Actual world/broker effect settlement and supervisor fencing are still required.
See evidence/participant-runtime-2026-10-09/candidate-04.

## 2026-10-09: Service participation proof qualified and integrated

The issuer now binds registered service-key proof to its actual TLS connection.
The checked client validates the complete challenge and result context.
The integrated source also includes the reviewed owner-idle fixture correction.
Both hosts qualify 1153 core checks, 51 harness checks, and 151 P10 cases in each Debug and runtime sanitizer profile.
The final core count uses the full corrected 20-case workspace suite. The original failed run remains archived.
All 238 source hashes and 41 locked files are verified. No operator state or running session changes.
Commit 69d6618 is pushed to box-git and adopted on both hosts. Both worktrees are clean.
The barrier and participation worker remain isolated. Service proof alone does not satisfy M02 cancellation.
See evidence/participant-session-2026-10-09/candidate-04.

## 2026-10-09: Workspace revocation fixture uses an expired owner connection

The sb16-2 service-session sanitizer run finished with one workspace-authority test failure.
The expected denial occurred, but the relay did not observe a genuine revocation event.
Five immediate diagnostic repetitions passed. A deliberate idle delay reproduced the exact failure.
The trace shows the owner connection closed before the test could request revocation.
The fixture opens that connection before setup, which can exceed its 3000 ms idle timeout.

The proposed correction opens a fresh owner connection immediately before arming the race.
It preserves every existing assertion and adds a delayed setup regression.
The delayed baseline fails. All 20 corrected workspace checks pass on sb16-2 in Debug and runtime ASan/UBSan.
All 20 corrected checks also pass on sb16 in both profiles. The correction is not integrated.
Frozen sources and the failed full-run evidence remain unchanged.
See evidence/workspace-owner-idle-2026-10-09.

An isolated participant runtime worker draft now exists. Its first Debug build passed on sb16-2.
Its real-issuer Debug tests now pass 13 new checks and 96 related regression checks on sb16-2.
It remains isolated and unconnected to worlds and brokers. M02 remains incomplete.
The barrier candidate passed full Debug on sb16: 1185 core, 51 harness, and 151 P10 checks, without failures or skips.
Both sb16 barrier profiles passed: 1185 core, 51 harness, and 151 P10 checks each.
The sb16-2 Debug barrier profile also passed. Its sanitizer profile is running.
The corrected workspace fixture must remain in the final barrier integration. Its barrier-binary verification is running on sb16.
The remaining sb16-2 service-session sanitizer P10 run passed all 151 cases.
A reviewed composite qualification receipt is still needed before integrating the corrected fixture and service candidate.

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
Service-session Debug and runtime ASan/UBSan profiles passed on sb16: 1, 152 core, 51 harness, and 151 P10 checks each.
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
Each passed 1, 152 core checks across 66 suites, 51 harness checks, and 151 P10 cases without failure or skips.
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
The separate service-session candidate passed complete Debug on sb16: 1, 152 core, 51 harness, and 151 P10 checks.
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
Each profile passed 1, 120 core checks across 64 suites, 51 harness checks, and 151 P10 cases.
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
Both hosts completed Debug: 1, 120 core checks across 64 suites, 51 harness checks, and 151 P10 cases each.
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
Each profile passed 1, 089 core checks across 62 suites, 51 harness checks, and 151 P10 cases.
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
Both hosts passed complete Debug: 1, 089 core checks across 62 suites, 51 harness checks, and 151 P10 cases.
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
Each profile passed 1, 037 core checks across 55 suites, 51 harness checks, and 151 P10 cases.
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
Candidate 03 passed complete sb16-2 Debug: 1, 036 core checks, 51 harness checks, and 151 P10 cases.
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
Its sb16 complete Debug and runtime sanitizer profiles each passed 1, 037 core checks across 55 suites.
Each profile also passed 51 harness checks and 151 P10 cases. No case failed or skipped.
The corrected process witness then passed six new checks and twelve unchanged process cases against both exact binary sets.
The sb16-2 candidate 04 complete Debug profile passed 1, 037 core checks, 51 harness checks, and 151 P10 cases.
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
python 3 tools/qualify_core.py --through P05 --evidence-dir local/new-surface-run
python 3 tools/qualify_core.py --through P05 --sanitize --build-dir build-sanitize --evidence-dir local/new-surface-sanitize
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
python 3 tools/qualify_core.py --through P04 --evidence-dir local/new-render-run
python 3 tools/qualify_core.py --through P04 --sanitize --build-dir build-sanitize --evidence-dir local/new-render-sanitize
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

Commit f0e1ef 3 delivered the persistent world daemon and passing P00-P02 gates.
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

