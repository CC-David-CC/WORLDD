# M07 broker progress focused checkpoint, 2026-10-14

M07 remains **UNQUALIFIED**. The selected broker and owner-channel checks passed.
The separate actor copy-status control failed and remains unresolved.
This checkpoint does not integrate the task branch into main.

## Source and behavior

Branch: `m07/copy-engine-completion-20261014`.
Base: `02dbb69b7dc26a71ff8742b321b00d68813b102d`.
All seven changed code/test files match the immutable Frozen19 build inputs.
Frozen19 source manifest SHA-256:
`b7485602706fba3d4925163ce284bd58058af279ab01a5d11472c11957bb7f71`.
This summary is the only additional checkpoint file.

Broker issuer maintenance now runs during bounded wait progress.
Polling ends before INFO begins. Exchange and polling guards prevent recursive INFO.
Cached revocation and supervisor polling continue while INFO waits.
Existing issuer idle and I/O budgets remain three and five seconds.
There is no reconnect or request replay.

Administrative progress now checks owner sockets without consuming ordinary requests.
The scan bounds work to 256 owners and uses zero-timeout poll plus one-byte MSG_PEEK.
EOF, half-close and socket failures use existing fencing semantics.
Fencing shuts down borrowed streams and retains unresolved control reservations.
It does not dispatch queued requests or invent recovery receipts.

The issuer witness pins policy from each actual serialized server launch configuration.
Its evidence capture cannot skip daemon shutdown.
The copy harness accepts one exact existing method selector before side effects.
Its default twelve-method selection remains unchanged.
CMake declares the broker fixture closure and strict native sanitizer environment.

## Selected runtime results

Each row finished with command.exit=0 and wrapper.exit=0.

| Host | Profile | Broker checks | Owner-channel checks |
|---|---|---:|---:|
| sb16 | Debug | 5 PASS, 32.139s | 9 PASS, 17.599s |
| sb16 | ASan/UBSan | 5 PASS, 35.725s | 9 PASS, 24.273s |
| sb16-2 | Debug | 5 PASS, 37.337s | 9 PASS, 27.071s |
| sb16-2 | ASan/UBSan | 5 PASS, 41.645s | 9 PASS, 34.807s |

Total: **56 selected testcase executions passed**.
The five broker checks cover delayed INFO with actor revocation, real issuer exit,
owner EOF, queued half-close, and a live owner's queued request.
The live-owner control proves no consumption, dispatch or spurious fence during INFO.
The revoked actor receives exact UNAUTHORIZED with the inactive-identity reason.
Kernel witnesses retain endpoint inode, peek, poll and timing observations.

The nine existing owner checks cover lost replies, retained reservations, other-owner
isolation, supervisor loss, duplicate endpoints, rebinding, process-wait progress,
unfenced recovery refusal, and exact recovery retry.
These are configured supervisor component checks.
They do not replace independent native input or physical-device acceptance.

Both hosts used their qualified XACE wrapper and graphics dependency receipts.
Sanitizer runs used the qualified ASan-UBSan graphics lane and strict sanitizer options.
All eight packaged SAN executables link ASan and UBSan.
Final TLS issuer, broker, process-scope and native-server logs were retained and scanned.
No sanitizer diagnostics appeared in those records.

Shutdown audit: the selected fixtures use TLS Peer connections.
They never call IssuerFixture.start or ClientFixture.client, so stdio connections stay empty.
TlsFixture.stop_daemons waits and copies final issuer logs before temporary-directory removal.
Broker cleanup copies final logs after closure. Native server cleanup waits, copies and scans its log.
One-shot issuer initialization and broker seeding require successful process exit.
The unrelated stdio Connection.close retention gap is not reached by these selections.

Each cleanup receipt proves the exact task unit/scope and captured processes are gone.
Neither selected suite creates storage volumes or changes global copy helpers.
Both runtime lanes were released after the final checks.

## Retained failure

Frozen19 sb16 owner1 ran one testcase in 17.175 seconds and failed; outer exits were 1/1.
`test_copy_broker_wait_keeps_action_issuer_alive_beyond_tls_idle` timed out awaiting actor copy status.
The actor receive helper has a five-second response budget.
The issuer remained alive. The journal reached phase4/error0, beyond the delayed BEGIN.
First-domain sealing completed; second-domain worker output was still pending at cleanup.
The exact delayed call remains unproven.

Source confirms root-request head-of-line risk: copy advancement precedes root request dispatch.
Advancement and status validation still contain synchronous owner exchanges.
Issuer keepalive does not make those exchanges asynchronous.
No timeout increase, copy-status production change or retry followed this failure.
The relay audit was lost when successful cleanup removed its operator directory.
Future bounded timing evidence must distinguish queue delay from status validation time.

Both copy helpers were restored absent. The failed image remains detached and preserved:
`sb16:/var/lib/spatial/volumes/m07-engine-7518e55036f441cc/volume.img`.
SHA-256: `e156bbd361c5783b67f2281654a6179f10840df6caa0724c92d4c598a9a54abe`.
Earlier 18a setup and 18b oracle/owner-EOF failures remain retained externally.

## Provenance and remaining coverage

Builds ran only on middle-child, with normal CMake, -j2 and directory-specific flock.
Canonical object directories are `preflight06/debug` and `preflight06/san`.
The executable owner target is `spatial-storage-owner-service`; its output is `spatial-storage-owner`.
Debug package: 12 rebuilt executable outputs and six explicitly reused unchanged probes.
SAN package: eight rebuilt executable outputs for the selected fixture closure.

Artifact root on middle-child:
`/home/dflanag3/spatial-artifacts/m07-engine-completion-20261014`.
Debug build/package receipts: `preflight19-owner-loss`.
SAN build/package receipts: `preflight19-owner-loss-san`.
Debug archive SHA-256: `fc972cd75a7ee071770729c4943d96c2181377053d953423206adb31b105c665`.
SAN archive SHA-256: `dba20dcbbdf3ff1745f2098b9e1c1d59a226c1636446654e87b9552a8cd47071`.

On each runtime host, raw outer logs and cleanup receipts use:
`/home/dflanag3/spatial-artifacts/runs/m07-engine19-{broker5,owners9}-{debug,san}-v1-{outer,evidence}`.
The separate failed control uses `m07-engine19-owner1-debug-v1-{outer,evidence}` on sb16.
Compact receipt index: `checkpoint19-broker-progress/focused-results.json` on middle-child.
The bounded timeout analysis is `preflight19-owner-loss/owner1-read-only-findings.txt`.

The full engine and M07 gate remain pending.
Unrun requirements include active copy revocation, fragmented absolute300-second deadline,
lost actor acknowledgement after publication, worker death/broker restart uncertainty,
per-class overflows, hostile Git, remaining migration/recovery combinations,
and constructor-specific mixed-mode coverage.
Participant-domain provisioning work remains separate and unintegrated.
No full M07, full engine SAN, independent input, or physical acceptance claim follows here.
