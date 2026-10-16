# M07 stage-copy focused qualification

Status: FOCUSED-PASS on sb16 and sb16-2 in Debug and ASan/UBSan.
This checkpoint does not qualify full M07, Spatial-M1, or the asynchronous copy engine.

## Frozen inputs

Branch: m07/copy-readonly-flags-20261014.
Checkout: a5500:/home/dflanag3/src/spacial-clean-m07-copy-readonly-flags.
Production source: 7ee84fd3001d963e99b8e241bc015eac34812edd.
sb16 canonical fixture source: the same 7ee pin.
sb16-2 corrected fixture source: 08a5c34ef342d655c00d26941763bd60567a88c1.
The 08a increment changes only the cgroup sampler; every ELF byte is reused unchanged.
Base: f2cf41d2e69db6347dd6004368aa6ea053eaae30.
The qualification commit changes documentation only.
It does not change packaged sources, production inputs, ELF bytes, or immutable manifests.

The implemented binding regression is:
copy_stage_begin_receipt_destination_binding_fences_before_worker.
Its authenticated root relay changes only BEGIN destination_subvolume_id to another valid value.
The actual copy_registered_snapshot_to_staging call rejects exact UNAVAILABLE:
Storage owner copy receipt does not match pinned stage intent.
The test verifies channel fencing, no worker launch, no destination changes, and no later stage request.
The paired authorized relay reaches the real helper, seals 24MiB, and verifies retry and inode independence.
This is authenticated root-negative private-protocol coverage.
It does not model an ordinary actor forging owner credentials.
SCM_CREDENTIALS always comes from actual UID/PID; production authority checks remain intact.

Additional reproduced production defects have narrow fixes and regressions:

- Existing destination regular files now produce DESTINATION_CONFLICT instead of INTERNAL.
- GET_SUBVOL_INFO uses ROOT read-only flags and accepts every nonnegative ioctl return.
- Sealed request bytes use --ro-bind-data; real directory and executable descriptor mounts remain unchanged.
- Worker stderr is drained without blocking, capped 64KiB, scanned, and reports child exit status.
- Reused BEGIN bindings preserve all eight comparisons and return typed CONFLICT.

See M07_COPY_READONLY_FLAGS_REGRESSION_2026-10-14.md for diagnosis and retained failures.
No acceptance lock, schema, or authority contract was weakened.

## Selected checks and results

The named test plan was recorded before execution in the diagnosis document and reviewed drivers.
Each production profile checks six witnesses:

1. Physical read-only destination rejects before worker launch; channel remains usable.
2. Physical writable source rejects before worker launch; separate false-readonly client state fences.
3. Authorized receipt relay reaches the real worker and seals copied bytes.
4. Mutated BEGIN destination binding rejects at its exact guard and fences before worker launch.
5. Direct production copy seals copied bytes with independent inodes and identical retry.
6. Symlink input rejects and records UNCERTAIN.

The FD32 constructor check is one separate descriptor regression.
It does not cover receipt binding.
Recovery is one separate COPYING restart unittest with the unchanged typed CONFLICT assertion.
Both actual recovery owner-generation stderr files are bounded, hashed, and scanned even on exit 0.
The production suite requires all six witnesses, FD32, recovery, and scoped cleanup.

| Host | Profile | Core helper | Native witnesses | FD check | Recovery |
| --- | --- | --- | --- | --- | --- |
| sb16 | Debug | 8/8 PASS | 6/6 PASS | 1/1 PASS (32 iterations) | 1/1 PASS |
| sb16 | ASan/UBSan | 8/8 PASS | 6/6 PASS | 1/1 PASS (32 iterations) | 1/1 PASS |
| sb16-2 | Debug | 8/8 PASS | 6/6 PASS | 1/1 PASS (32 iterations) | 1/1 PASS |
| sb16-2 | ASan/UBSan | 8/8 PASS | 6/6 PASS | 1/1 PASS (32 iterations) | 1/1 PASS |

Final selected focused checks: 64 passes across four host/profile pairs.
Each pair has eight core methods, six native witnesses, one FD check, and one recovery test.
Current completed profile suites have zero failures.
Earlier full-suite failures remain preserved and are listed below.
Skipped focused checks: 0.
Both required hosts ran the selected checks; no compile-host runtime result substitutes for either host.
OFF receives build and target dependency/import checks, not a test run.
Full cumulative gates, asynchronous-engine integration, and M1 acceptance remain pending.

All evidence below lives under /home/dflanag3/spatial-artifacts on its named host.
sb16 core evidence uses unchanged f2 helper bytes:

- Inner runs/m07-copy-debug-core-v3/core; outer runs/m07-copy-debug-core-v3-outer.
- Inner runs/m07-copy-san-core-v3/core; outer runs/m07-copy-san-core-v3-outer.

sb16 native Debug:

- Outer: runs/m07-copy-debug-production-7ee-v1-outer.
- Inner: runs/m07-copy-debug-production-7ee-v1/production.
- Canonical launcher: runs/copy-worker-production-d_mamlby.

sb16 native sanitizer:

- Outer: runs/m07-copy-san-production-7ee-v2-outer.
- Inner: runs/m07-copy-san-production-7ee-v2/production.
- Canonical launcher: runs/copy-worker-production-ejiryo8x.

sb16-2 core evidence uses the same unchanged f2 helper bytes:

- Inner runs/m07-copy-debug-core-7ee-sb16-2-v2/core; outer adds -outer to its parent.
- Inner runs/m07-copy-san-core-7ee-sb16-2-v2/core; outer adds -outer to its parent.

sb16-2 native Debug:

- Outer: runs/m07-copy-debug-production-08a-sb16-2-v3-outer.
- Inner: runs/m07-copy-debug-production-08a-sb16-2-v3/production.
- Canonical launcher: runs/copy-worker-production-a3609zhu.
- Authorized relay 588 and direct-copy 598 valid worker samples; recovery Ran 1 OK in 2.116s.

sb16-2 native sanitizer:

- Outer: runs/m07-copy-san-production-08a-sb16-2-v3-outer.
- Inner: runs/m07-copy-san-production-08a-sb16-2-v3/production.
- Canonical launcher: runs/copy-worker-production-g6f_zcwd.
- Authorized relay 604 and direct-copy 603 valid worker samples; recovery Ran 1 OK in 3.303s.

Each host2 launcher directory contains independent-verification.json.
It records actual witness results, nested owner exits, diagnostics scans, and independent cleanup commands.

Every listed suite has command.exit 0 and wrapper.exit 0.
Native outer and inner stderr are empty.
All four canonical summaries report validated:true and helper_restored:true.
Each native run removed all six case roots and sockets, stopped its unique unit,
applied the native-volume unmount, detached its loop, and cleaned the separate recovery volume.
Each installed helper returned to its pre-run absent state.
Independent process and closed-package-inventory checks also passed.
Primary independently accepted the completed sb16 results as FOCUSED-PASS.

Core sandbox sanitizer runs cover the real tree helper with configured options and captured stderr.
Old pre-diagnostic production attempts do not prove inner-helper sanitizer diagnostic coverage.
The current production worker sets trusted ASan/UBSan options after clearenv and scans bounded helper stderr.
These focused checks do not prove broader graphics, native-session, or performance acceptance.

## Artifact closure

Packages on a5500 and both targets:
packages/m07-copy-readonly-PROFILE-7ee84fd-review-v1.
Archives add .tar.gz.
Each immutable PACKAGE_PROVENANCE.json records per-file hashes, source pins, build receipts,
generated modules, ELF dependency hashes, and reused binary origins.
Its historical UNQUALIFIED/UNRUN status describes package creation.
This external qualification record supplies later runtime results without rewriting the archive.

| Profile | Archive SHA256 | Manifest SHA256 | Files |
| --- | --- | --- | --- |
| Debug | 8a43e8a31a5bac4aaafa04de970b8053283075e73d2fb2305c15f2ff338dd6fb | b4656659b9db260556c0cc7d0fc71ec91a373170c283e78a294618298f138c4b | 82 |
| SAN | 6b13e8d773f7ee12f58bc75febfe866243fc0641b1c61b506808ebc6755d7c3d | c0f9b4fdce541ed3d1ea9510287b1325d96468564c58dc476941c04b22ab8aa7 | 82 |
| OFF | 7f2a97452d08563d9e38fc7b064e56d23f53302070d3cfd7f3a73b8936d4d589 | 711552dbe2597f499b7dde5ab2de17a2e609c5c0e5b2639c3e16d81e28dc63df | 49 |

All three profiles passed strict target inventory, ldd, and four transitive protobuf imports on both hosts.
Required imports: broker_pb2, storage_pb2, process_scope_pb2, spatial.workspace.v1.copy_worker_pb2.
OFF includes the production provision tool and generated Python closure.
No source overlay is stacked over these packages.
The corrected host2 packages are packages/m07-copy-readonly-PROFILE-08a5c34-review-v1.
They replace only canonical fixture bytes and manifest metadata, with unchanged ELF hashes.
Their source comes from the tracked 08a commit; the dirty qualification draft was never packaged.
The original 7ee archives remain immutable, including accepted OFF static closure on both hosts.

| Host2 corrected profile | Archive SHA256 | Manifest SHA256 |
| --- | --- | --- |
| Debug | 804d9658d90f6a932822fb862c4e893a2fc5fa79337e31d77b0d17511db3e259 | c7874c4b1055eea39943ef46558f9cd7536a20004a6f447b0317dd8acac61013 |
| SAN | dff87380ed1f6a5a7870b2b864fa2a1208968f2538f33d2672a7577413a5d4eb | ca4dcd84becf1823ff3d25acdea52e499048ff615b7649723b4df0dc486f9745 |

Exact ELF build-input mapping:

- f2 means f2cf41d2e69db6347dd6004368aa6ea053eaae30.
- 44c means 44c950b109d76996a64f55724125815bde112f59.
- 7ee means 7ee84fd3001d963e99b8e241bc015eac34812edd.
- Debug helper/fixture reuse f2; broker/production probe reuse 44c; owner/probe rebuilt 7ee.
- SAN broker, production probe, owner and owner probe rebuilt 7ee; helper/fixture reuse f2.
- OFF broker/owner rebuilt 7ee; production helper reuses f2.
- Reused helper source is unchanged at 7ee; manifests pin actual historical build commits.

| Profile | ELF | Build pin | SHA256 |
| --- | --- | --- | --- |
| debug | spatial-copy-tree-worker | f2 | 34641ce94f339ef135583946f575d031e91cfcb48897e5200ffae2de5a367988 |
| debug | spatial-copy-tree-worker-fixture | f2 | 9daba447f377fbf6e7c4efb232ebc63cf948c3e885addccf666766052c78c00a |
| debug | spatial-copy-worker-production-probe | 44c | 6cd2d7250488903f08dea511d885029fbf61adba8365fd32bf741c44de9b01c7 |
| debug | spatial-storage-owner | 7ee | 04bef23ce96fa7eb6c265faab0af386ece8061f24df40f51aefeb5d4ba338b56 |
| debug | spatial-storage-owner-probe | 7ee | b8344e18129304503271c0253b52e0fd159474cbfe20ef04f25b5e0bd241eefc |
| debug | workspaced | 44c | 273a5fa7cd34b8991df91a2ef5ca14abc3358911df287873e4fd20d00aaf6072 |
| san | spatial-copy-tree-worker | f2 | dcc0b14acd1eeddc00ecdfb16f48aafa3f40e4dfab8d100b15689a3eb7b73c93 |
| san | spatial-copy-tree-worker-fixture | f2 | 680e9b88aac6041abf56e11684348173e400434f8cb264752ad144f4ef94d775 |
| san | spatial-copy-worker-production-probe | 7ee | b8727ec0926c72a40c1982e7e8bdd7b561e5b3c066d011ecb6e28d6609f4ccfd |
| san | spatial-storage-owner | 7ee | 20bb45daa01af835064975d0bcf6889cd75deeedb57cad19f6615d67099287ac |
| san | spatial-storage-owner-probe | 7ee | ccf8c3c5a50b797c40ca1f1f599837808bfda3630abdf8799b7564e19e991ebf |
| san | workspaced | 7ee | 0b5a927d1a30b239ce56ffa51d4ed21c784e1ca01b41ed68e7ca3b303fe56c33 |
| off | spatial-copy-tree-worker | f2 | ec183f031d1c852577fcd073ebf0221dd8ed7dfc549089acc9c1d8e2d074a11c |
| off | spatial-storage-owner | 7ee | 253e647adc42c51931dbf603d5aa5f122220c46e4a60a07cf486b386c7a297d0 |
| off | workspaced | 7ee | e6ed78888f277351342f14efe40cb455839c382c5b4acb897141a80c9de40d9e |

Build logs on a5500, each final command/wrapper 0/0 with empty stderr:

- Debug broker/probe 44c: runs/m07-copy-jobdata-debug-v1.
- Debug owner/probe 7ee: runs/m07-copy-owner-conflict-debug-7ee84fd.
- SAN four affected targets 7ee: runs/m07-copy-final-san-7ee84fd.
- OFF broker/owner 7ee: runs/m07-copy-final-off-7ee84fd.
- OFF generated Python: runs/m07-copy-final-off-python-7ee84fd.

Build directories retain historical 4a15d85 names; receipts and manifests provide actual input pins.
Exact affected-target commands, each serialized by directory-specific flock:

    cmake --build /home/dflanag3/spatial-artifacts/builds/m07-copy-readonly-debug-4a15d85 --target workspaced spatial-copy-worker-production-probe -j2
    cmake --build /home/dflanag3/spatial-artifacts/builds/m07-copy-readonly-debug-4a15d85 --target spatial-storage-owner-service spatial-storage-owner-probe -j2
    cmake --build /home/dflanag3/spatial-artifacts/builds/m07-copy-readonly-san-4a15d85 --target workspaced spatial-copy-worker-production-probe spatial-storage-owner-service spatial-storage-owner-probe -j2
    cmake --build /home/dflanag3/spatial-artifacts/builds/m07-copy-readonly-off-4a15d85 --target workspaced spatial-storage-owner-service -j2
    cmake --build /home/dflanag3/spatial-artifacts/builds/m07-copy-readonly-off-4a15d85 --target provision-storage-python-bindings -j2

Helper/fixture builds remain external under builds/m07-copy-binding-PROFILE-f2cf41d.
Their original canonical receipts are runs/m07-copy-binding-{debug,san,off}-f2cf41d.
No builds or development worktrees exist on the qualification targets.

Canonical transfer/static receipts on a5500:

- runs/m07-copy-readonly-debug-transfer-7ee84fd.
- runs/m07-copy-transfer-san-sb16-7ee-v2.
- runs/m07-copy-transfer-off-sb16-7ee-v2.
- runs/m07-copy-transfer-debug-sb16-2-7ee-v2.
- runs/m07-copy-transfer-san-sb16-2-7ee-v2.
- runs/m07-copy-transfer-off-sb16-2-7ee-v2.

Corrected fixture packaging logs: runs/m07-copy-sampler-package-{debug,san}-08a5c34, each 0/0.
Corrected host2 transfer/static logs: runs/m07-copy-transfer-{debug,san}-sb16-2-08a-v3, each 0/0.

## Launch and recovery

CTest registers copy-worker-production, TIMEOUT 900, RUN_SERIAL, RESOURCE_LOCK spatial-copy-worker.
The canonical nonroot launcher owns root escalation, unique delegation, evidence, and helper restoration.
Its budget is 600s execution plus 240s cleanup grace and bounded reap.
It validates framed root receipts and exact PID/start-time/argv cancellation.
Root Python and nested Python receive -B, with explicit environment after sudo.
The private outer run root is dflanag3:dflanag3 mode0700.

Reviewed v2 sidecar directory on both hosts:
m07-copy-readonly-sidecars-7ee84fd-v2.
SHA256:

- driver: ee2867f046cf605c3c204fe1a9877087c4bd97c70b71cb62c75e253d82da3336.
- verifier: 97784d721115c80975fe45e84e95d4a40d8b9dda0f31f4de680e981e494d0604.
- installer: ef11e08c5c3fc42bfc3c8deec665631cd1884ae862754489787c621284748da2.
- transfer: 7c48ff5c0b6da35cd057e13bc61f50b863395ba6dea37ff43e1f47a0a07125a3.
- canonical runner: 7053d5ffe05e7050b035d6b715b794dd742b68ee29b9198a703979391323f537.

Host2 corrected v3 sidecars use m07-copy-readonly-sidecars-08a5c34-v3.
The driver and canonical runner hashes remain identical to v2.
Verifier: f0835baee9d759670ec71a7f674427959894b82775cea7c7ab7742694c564926.
Installer: 8f32c35f9e5475bb5b6f943ab6fd00b245f2e8ee5375edac30b14de2ed45cc62.
Transfer: 141134bc968a1e04046be2d1d315c54924fcd35a5f6298e0c78bd7ee00789d01.
The v3 difference changes source/hash/path constants and limits profiles to Debug/SAN.
OFF continues using its accepted 7ee/v2 package and static receipts.
Core helper methods and production assertions remain unchanged.

Actual core driver command, with package and fixture paths supplied as absolute arguments:

    python3 -B SOURCE/tests/core/test_copy_tree_worker.py PACKAGE/spatial-copy-tree-worker-fixture -v
      CopyTreeWorkerTests.test_positive_copy_preserves_bytes_without_sharing_inodes
      CopyTreeWorkerTests.test_tree_digest_is_independent_of_directory_iteration_order
      CopyTreeWorkerTests.test_symlink_special_file_cross_root_link_and_quota_are_denied
      CopyTreeWorkerTests.test_wide_tree_is_bounded_before_name_queue_growth
      CopyTreeWorkerTests.test_depth_128_succeeds_and_depth_129_has_typed_limit
      CopyTreeWorkerTests.test_low_descriptor_limit_caps_supported_depth
      CopyTreeWorkerTests.test_readdir_error_is_reported_as_io_failure
      CopyTreeWorkerTests.test_entry_limit_and_existing_destination_entry_are_denied

The actual driver supplies all eight identifiers in one argv, under the canonical runner.
Its production command is python3 -B SOURCE/tools/run_copy_worker_production.py PACKAGE SOURCE.
ASAN_OPTIONS is detect_leaks=1:abort_on_error=1.
UBSAN_OPTIONS is halt_on_error=1:print_stacktrace=1.
Inherited LSAN_OPTIONS is cleared.
Both runtime lanes were explicitly returned after independent cleanup verification.

Next safe task: primary review and deliberate integration with the separate asynchronous engine branch.
Keep the compiled-input mapping above when importing fixes or reusing artifacts.
Reassess affected tests on the combined source; full M07 gates remain pending.
No further standalone rerun is needed for unchanged inputs.
If repeating qualification, use fresh external directories and coordinate the exclusive helper lane.
The driver form is bash RUNNER PACKAGE FRESH_OUTER bash DRIVER PACKAGE RUNNER FRESH_INNER PROFILE core|production.
Run as dflanag3; the canonical launcher supplies its own sudo.
Do not wrap the whole driver in sudo or a second resource delegate.
On interrupted cleanup, use only the receipt's exact PID/start-time recovery command and owned volume/unit paths.
Do not broad-kill processes or change unrelated cgroups, helpers, sessions, or displays.

The first host2 Debug run failed in the sampler after four successful witnesses and FD32.
Reading a listed cgroup.procs returned ENODEV during descendant teardown.
Direct success had no completed witness; symlink and recovery were unrun.
Evidence: sb16-2:runs/copy-worker-production-1zr75q7t.
Its outer and inner runs/m07-copy-debug-production-7ee-sb16-2-v2 returned 1/1.
All five created roots and the owned volume, socket, unit, and helper were cleaned.
The approved 08a fixture handles ENODEV only for enumerated descendants, never the operation root.
It records each exact path/errno, caps 64 events, and leaves other I/O errors fatal.
It still requires valid helper limits/private-netns samples and terminal SEALED results.
The corrected Debug run exercised one bounded ENODEV skip during symlink teardown.
The sanitizer run needed no skip.
The failed full suite remains separate from the later six-witness passes.

Earlier failures remain preserved, separately from current PASS:
old 805 helper collision on both hosts; f2 relay setup; read-only flag diagnosis;
f4 shared cgroup; b293 discarded helper diagnostics; 348 job bind; 44c capacity; fb21 typed conflict.
The failed 94 diagnostic compilation and original closedir warning builds also remain preserved.
The diagnosis document records exact evidence and corrections.
No failing attempt was relabeled as passing.
