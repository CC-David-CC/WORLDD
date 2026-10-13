# M06 deployed gate workflow

Status: unqualified gate-wiring implementation. No tests, deployment, or M06 gate have run from this branch.
The root AGENTS.md controls host allocation. The legacy qualify_m04_gate.py remains unchanged.

## Selected wiring checks recorded before implementation

Primary must run `tools/test_m06_deployed_gate_static.py` after this change. Its positive controls must exercise the exact deployed semantic fixture invocation and a terminal receipt. Negative controls must reject stale candidate pins, wrong profiles or artifact roots, absent mandatory geometry suites, missing native cases, failed or incomplete cleanup, and running or partial native results. Primary must run the semantic fixture once per profile and the native nine-case matrix in both directions per profile on sb16 and sb16-2. This coding-host assignment does not run those checks. The coding host may record `py_compile` receipts and exact diffs. It must not execute these static fixtures.

## Boundaries

Prepare on a5500 (hostname G164) or middle-child. Run deployed tests only on sb16 and sb16-2.
Use a frozen committed candidate. Preparation rejects dirty tracked files and untracked source files.
Run full Debug and ASan/UBSan builds, with SPATIAL_INPUT_TESTS enabled.
A separate BUILD_TESTING=OFF build must provide spatial-host-session and its Python client binding closure.
The frozen candidate must contain the reviewed production host-session entrypoint and native geometry sources. Preparation pins the complete candidate before building.

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
python3 tools/m06_deployed_gate.py run-local --deployment /absolute/new-deployment --native-controller host-actor-controller-native --real-world actor-transition-real-world --recovery actor-transition-recovery --host-session host-session-production-native --held-input host-actor-controller-native
```

Each named category must be present. Record the requirement-to-suite mapping in run.json.
The reviewed held-input requirement shares host-actor-controller-native with native-controller.
The separate host-actor-controller core suite remains mandatory.
Run each registered suite once. Do not invent duplicate registrations for category names.
The complete dynamic CTest inventory is checked in each profile. Local CTest runs every registration except `geometry-native-crosshost`, whose environment requires both hosts. The gate runs that registration through its dedicated native stage after both local host gates finish. `geometry-presentation-native` remains in local CTest.
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

## Cross-host semantic and native geometry stages

Run `crosshost-handoff` on sb16 after both hosts finish `run-local` and release their runtime slots. Keep the plan and evidence outside the deployed source tree. Pin one full candidate commit, both verified deployment receipts and provenance hashes, both local `run.json` hashes, both directions, both profiles, and all six semantic cases.
Launch this stage inside a persistent tmux session with the installed run-command helper:

```sh
tmux new-session -d -s m06-crosshost-gate \
  bash /home/dflanag3/.codex/skills/spatial-worker-coordination/scripts/run-command.sh \
  "$DEPLOYMENT/source" "$EVIDENCE/crosshost-run" \
  env -i PATH=/usr/bin:/bin python3 tools/m06_deployed_gate.py crosshost-handoff --plan "$PLAN"
```

Read `command.exit`, `wrapper.exit`, and `terminal-status.txt` before treating the adapter as returned.

The semantic stage calls the existing fixture with the deployed layout:

```sh
python3 "$DEPLOYMENT/source/tests/core/actor_transition_crosshost_fixture.py" \
  --bundle "$DEPLOYMENT" --profile debug --output "$EVIDENCE/semantic/Debug"
```

Use `asan` for ASan/UBSan. The fixture resolves executables and generated Python from `build/Debug` or `build/ASan-UBSan`. It resolves scripts from `source/tests/core`. It preserves the existing private issuer, two authorities, grants, six cases, deadlines, and cleanup.

Accept a semantic result only when the fixture exits zero, `runtime-status.txt` is `PASS`, `harness.exit` is zero, the harness reports its successful protobuf plan, `plan-summary.json` contains six cases and both hosts, and `cleanup.json` reports complete cleanup with no errors. Scan text logs for sanitizer output and skips. A partial run or cleanup failure remains failed.

The native stage uses the deployed `tools/run_geometry_native_crosshost.py` runner. It starts one fresh tmux job for each profile and authority direction. It wraps each command with `timeout --signal=TERM --kill-after=10 1800`, then checks `command.exit`, `wrapper.exit`, and `terminal-status.txt`. A pass requires `COMPLETE`, both exits zero, exactly nine passing tests, all nine local case evidence directories, checked peer-unit cleanup evidence, and remote host-session witness logs.

On timeout or a nonzero terminal result, the adapter checks only exact geometry service units recorded by the fixture. It stops each recorded unit and verifies that systemd reports it inactive and not found, with no cgroup. It never signals a guessed process. Missing unit evidence is an incomplete recovery. A timeout remains failed even when recovery succeeds.

The reconciliation receipt records semantic and native results per profile and direction. It verifies both local gate receipts and the mandatory geometry suite registrations. It always records `phase_complete: false` and requires primary review. Passing reconciliation is component evidence, not M06 completion.

The plan uses schema 1 with these fields:

- `candidate`: the full deployed commit.
- `deployments`: both hosts, same absolute deployment path, provenance hash, and deployment receipt hash.
- `local_runs`: both host run receipt paths and SHA-256 hashes.
- `directions`: `sb16-to-sb16-2` and `sb16-2-to-sb16`.
- `profiles`: `Debug` and `ASan-UBSan`.
- `semantic_cases`: three case names for each direction, exactly once.
- `provisioning`: `actor_transition_crosshost_fixture.py:production-private-two-world-issuer-mtls`.
- `native_geometry`: a runner manifest per profile, with pinned `source`, `bin`, and `python` roots plus each host's Xvfb wrapper, evidence root, command runner, and reachable address.
- `evidence.output`: a new absolute evidence directory outside the source tree.

Do not place credentials or private keys in the plan. The fixture creates private issuer state and removes it after checked cleanup. Never reuse evidence paths or run IDs.

The native geometry registrations are unconditional under `BUILD_TESTING`. Local CTest excludes only `geometry-native-crosshost`; the dedicated stage executes its real test source. The semantic `actor-transition-crosshost` registration remains conditional on `SPATIAL_ACTOR_TRANSITION_PLAN`. Do not configure a private-plan placeholder into local CTest.

## Review and validation

The primary reviewer must inspect every changed file and run `tools/test_m06_deployed_gate_static.py` on the qualification lane. Run the semantic fixture once per profile. Run the native nine-case test in both directions per profile. Preserve each invocation, log, terminal receipt, case evidence, and cleanup result.

The static fixtures prove plan and receipt guards. They do not prove deployment, native pixels, transport, cleanup on both hosts, or M06 completion. Any source change after freezing invalidates the affected build and runtime evidence.
