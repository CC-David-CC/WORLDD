M06 production host-session focused evidence

400 selected checks passed. The full M06 gate remains pending.
Each host passed 200 checks in Debug and ASan/UBSan.
Counts per host and profile: OFF startup 5, production native 24, related callers 71.
The OFF startup checks use the actual OFF executable and all transitive Python bindings.
No packaging check was skipped.

Source checkpoint: ee416fc245943f6d7168341abe47a86d917e96e5
Base: 03242c5c690266da04e1e01a9d7037bb1428d4ba.
All nine committed blob hashes equal the frozen deployed candidate.
Primary reviewed production source, schema, client, CMake, and both fixtures.
Four coding-host ON/OFF compile profiles passed before runtime qualification.

Final ASan native and related runs kept GLX enabled.
Qualified M06 server-launch/Xvfb wrappers supplied the pinned patched Mesa dependency closure.
Leak detection and fatal sanitizer diagnostics stayed enabled.
No CORE_ONLY profile or sanitizer suppression was used in the successful rerun.

Preserved failures
The first ASan launch used raw Xvfb with the unpatched system Mesa dependency closure.
Each host failed 23 server-cleanup cases with a 3176-byte GLX/DRI leak in five allocations.
The allocation stack reaches __glXDRIscreenProbe at glx_dri/glxdriswrast.c:439.
These failures remain in the frozen records. They are not replaced with passed credit.
Focused02 then failed during launcher setup before any test executed.
A fresh explicit launcher emitted and verified its complete dry-run plan before focused03.
Focused03 passed all 27 selected suites on each host.

Evidence layout
Each host archive preserves exact raw logs, command/environment receipts, source/artifact hashes, and failure evidence.
Each archive has a per-file manifest and retained launcher scripts.
The three GLX diagnosis receipts explain the wrapper and dependency mismatch.
No runtime database, credential, or build binary is included in this evidence commit.
Both runtime launchers exited 0. Their tmux sessions ended.

Integration must retain the root resource-wrapped hostcontroller test registration.
It must retain the standalone actor-session-client protobuf generator with one owner per output.
Root integration and combined-source checks remain separate from this task checkpoint.
Remote shared geometry and actor-view presentation remain required for M06.
Physical-input and full Spatial-M1 qualification remain pending.
