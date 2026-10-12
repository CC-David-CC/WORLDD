# M06 deployed gate workflow

Status: tooling draft with local static fixture checks. No build, deployment, or M06 gate has run through this tool.
The root AGENTS.md controls host allocation. The legacy qualify_m04_gate.py remains unchanged.

## Boundaries

Prepare on a5500 (hostname G164) or middle-child. Run deployed tests only on sb16 and sb16-2.
Use a frozen committed candidate. Preparation rejects dirty tracked files and untracked source files.
Run full Debug and ASan/UBSan builds, with SPATIAL_INPUT_TESTS enabled.
A separate BUILD_TESTING=OFF build must provide spatial-host-session and its Python client binding closure.
This branch predates that entrypoint. Preparation intentionally blocks until the reviewed entrypoint is integrated.

The tool preserves development build trees. It stages a separate runtime closure.
That closure includes generated runtime.textproto, generated Python, executable files, runtime libraries/configuration, and CTest metadata.
All archived files receive hashes. The transferred archive receives a separate SHA-256 receipt.
The OFF build has a recorded source/configuration and generated-binding manifest. It is a compile/packaging check, not runtime evidence.

## Required external runtime manifest

Supply one reviewed JSON manifest for the final project candidate.
Its top-level fields are candidate, required_egl_names, required_helper_names, and hosts.
The hosts object must have sb16 and sb16-2. Each contains Debug and ASan-UBSan entries.
Each entry has server_receipt, xace_wrapper, egl_files, and installed_helpers.
Each file entry has an absolute path and SHA-256 in sha256.
Both spatial-bwrap and spatial-storage-owner are mandatory, root-owned, and not group/world writable.
Pin the wrapper's EGL vendor JSON and the library referenced by its ICD.library_path.

Use the existing build_xserver.py receipt format. Do not fabricate a new success receipt.
The tool checks host, sanitizers, successful commands, server/base.lock.json, and every extension_sources hash against the candidate.
An unchanged compatible server may be reused across project commits. A source mismatch requires a reviewed matching build.
The wrapper must match the reviewed Python exec template and execute the exact receipt Xvfb binary.
A wrapper hash alone is insufficient.

The M04 server lacks the M06 scoped input admission change. Do not use it for this gate.
Known compatible receipt locations on each host are:
/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff/local/m06-scoped-input-05/Debug/server-build/run.json
/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff/local/m06-scoped-input-05/ASan-UBSan/server-build/run.json
Matching wrappers are under each profile's server-launch/Xvfb directory.
Read evidence/m06-presentation-bindings-2026-10-12/{host}-audit.json for their prior evidence.
These are candidate inputs, not permission to skip fresh hash verification.
External server/EGL/helper files are not copied into the application archive.
Deployment remains BLOCKED until all required files exist and pass validation on that host.

## Commands

From the reviewed source tree on a coding host:

```sh
python3 tools/m06_deployed_gate.py prepare --candidate FULL_COMMIT --output /absolute/unique-parent --runtime-manifest /private/reviewed-runtime.json
```

Preparation creates an attempt directory containing m06-artifacts.tar.gz, archive-manifest.json, provenance.json, builds, and raw build logs.
Transfer the archive and archive manifest through normal trusted deployment.
Verify the externally recorded archive hash independently on each host.
Use a new destination on each host:

```sh
python3 tools/m06_deployed_gate.py verify-deploy --bundle /staging/m06-artifacts.tar.gz --sha256 EXPECTED_SHA256 --archive-manifest /staging/archive-manifest.json --host sb16 --destination /absolute/new-deployment
```

Use --host sb16-2 on the second host. The actual hostname must match.
The tool rejects links, special files, traversal, duplicate files, and file-manifest mismatches.
Only CTestTestfile.cmake files are relocated. Runtime config and Python bytes remain unchanged.
Every relocated command and property must equal the original inventory after declared path substitutions.
Declared future evidence outputs are exempt from existence checks. Input files remain mandatory.
The deployment keeps the original relocated metadata for unique future attempts.

Supply exact final integrated suite names for every M06 category:

```sh
python3 tools/m06_deployed_gate.py run-local --deployment /absolute/new-deployment --native-controller host-actor-controller-native --real-world actor-transition-real-world --recovery actor-transition-recovery --host-session FINAL_HOST_SESSION_SUITE --held-input FINAL_HELD_INPUT_SUITE
```

Each named category must be present. Record the requirement-to-suite mapping in run.json.
The reviewed held-input requirement shares host-actor-controller-native with native-controller.
The separate host-actor-controller core suite remains mandatory.
Run each registered suite once. Do not invent duplicate registrations for category names.
The complete dynamic CTest inventory runs, including additional registrations.
One gate process owns a user runtime lock on each host. Run the two hosts in parallel when available.
Use persistent tmux and retain its exact exit code.
The tool runs both profiles, all CTest suites, 51 harness tests, all 151 locked P10 cases, and five named storage-owner scenarios.
It checks case identities, pass counts, cleanup receipts, sanitizer logs, and before/after artifact hashes.
CMake's individual resource wrappers remain intact. The held-input native suite requires its registered adapter resources.
Package BUILD_TESTING=OFF closures for Debug and ASan-UBSan, including runtime.textproto and generated Python.
Verify their hashes before deployment, before execution, and after execution.
Run all five host-session startup checks explicitly against each OFF executable and its OFF Python directory.
Use OFF/python:source for that stage's PYTHONPATH. Do not supply imports from testing-enabled bindings.
Append that OFF Python directory to the generated host-session-startup CTest command.
Record this stronger invocation separately from path relocation. Do not change test or CMake source.
Require complete command/property equality after that declared augmentation.
Reject inner unittest skips in verbose output even when outer CTest JUnit reports success.
A local pass is local_passed_crosshost_pending. phase_complete always remains false.
On failure, preserve the attempt and inspect exact remaining systemd scopes. Do not kill unrelated processes.

## Cross-host handoff

The final reviewed cross-host runner is a separate dependency.
The tool's crosshost-handoff command validates a supplied plan and exits BLOCKED; it does not execute or certify cross-host cases.
The plan must name runner, directions [sb16-to-sb16-2, sb16-2-to-sb16], profiles [Debug, ASan-UBSan], cases, and provisioning.
The complete M06 gate still needs both directions/profiles for actual actor TLS transitions.
Reassess M05 cross-host topology, remote-key, and supervised-recovery regressions against final integrated source.
Primary review must reconcile every requirement before marking M06 complete.

## Review and validation

Run01 and run02 worker snapshots and logs remain under local/m06-deployed-gate in the coding worktree.
Run01 failed review despite a zero worker exit. Run02 also had confirmed closure and receipt-schema defects.
The primary subagent corrected the stopped draft directly.
Pure static fixture checks run on the Windows controller, with no server, build, SSH, or runtime invocation:

```sh
python3 tools/test_m06_deployed_gate_static.py tools/m06_deployed_gate.py /path/to/actual/M06/server-build/run.json /path/to/project-source
```

These checks use temporary fixture bytes and the real receipt schema.
They cover config retention, missing-config rejection, unchanged-server reuse, extension tampering,
wrong wrapper targets, full inventory equality, future output paths, and OFF transitive bindings.
They also check pinned OFF closure copies, explicit startup CLI augmentation, and inner-skip rejection.
They do not prove archive deployment, CTest execution, native behavior, or milestone completion.
All those runtime operations remain UNRUN for this tool.
