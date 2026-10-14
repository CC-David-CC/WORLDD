# Btrfs copy read-only flag correction

Status: FOCUSED-PASS on sb16 and sb16-2 in Debug and ASan/UBSan.
See M07_COPY_STAGE_FOCUSED_QUALIFICATION_2026-10-14.md for frozen inputs and current results.
Base: f2cf41d2e69db6347dd6004368aa6ea053eaae30.
Branch: m07/copy-readonly-flags-20261014.
Checkout: /home/dflanag3/src/spacial-clean-m07-copy-readonly-flags on a5500.

GET_SUBVOL_INFO returns root-item flags.
Its read-only bit is BTRFS_ROOT_SUBVOL_RDONLY, with value1.
SUBVOL_GETFLAGS uses BTRFS_SUBVOL_RDONLY, with value2.
The storage client and copy worker previously tested value2 against root-item flags.
They rejected a real immutable snapshot before BEGIN.
Both production sites now test the root-item bit.
The lower worker also accepts nonnegative GET_SUBVOL_INFO returns.
It previously required exactly0 and would reject the observed successful return1.
Tree ID, filesystem identity, directory, and destination-writability checks remain enforced.
The Linux implementation assigns subvol_info->flags from btrfs_root_flags(root_item):
https://raw.githubusercontent.com/torvalds/linux/master/fs/btrfs/ioctl.c

The retained sb16 task image proved this mismatch without mutation.
Snapshot258 returned root_flags1 and GETFLAGS2.
Project257 and staging259 returned flags0 in both interfaces.
Root and the driver's sudo -u dflanag3 invocation observed identical values.
The diagnostic uid field reports file ownership, not its caller identity.
Every mount ancestor was searchable by UID1000.
GET_SUBVOL_INFO returned positive1 with errno0 on this kernel.
The diagnostic records that nonnegative return as an int, not a Boolean.
Its own failure counter still treated positive1 as failure and returned1.
The outer driver returned0 after preserving data and cleanup.
Outer exit0 does not establish diagnostic-binary exit0.
No repeated kernel run was needed to change that diagnostic oracle.
StorageClient already checks failures with less-than-zero.
The lower worker now applies the same success rule.
The first diagnostic incorrectly required return0. Its generic error did not prove an ioctl failure.
The first mount also used an obsolete bare nologreplay option.
The corrected mount used ro,rescue=nologreplay on a read-only loop device.
It unmounted and detached the exact owned loop.
The image SHA256 remained 697ed6e0d80b6bcf210768766bce6990b44753c7b73be43b53f1c0a42dd3db33.
Evidence: sb16:/home/dflanag3/spatial-artifacts/runs/m07-btrfs-flags-readonly-inspection-v3.
The sibling -v3-evidence directory preserves syscall results and cleanup.

Selected checks, recorded before execution:
- Build the production broker and production copy probe in Debug and ASan/UBSan.
- Build the production broker with BUILD_TESTING=OFF.
- Run copy_stage_authorized_receipt_relay_reaches_worker on sb16.
  Its real immutable snapshot must pass both physical read-only checks and seal copied bytes.
- Run copy_worker_rejects_readonly_destination_before_launch on sb16.
  It uses two distinct immutable snapshot identities in one independently authenticated lifecycle.
  The lower worker must reject the exact physical-root cause before resource preparation.
  Destination bytes, inode metadata, and cgroup directory sets must remain unchanged.
  Its storage channel must remain alive.
- Run copy_worker_rejects_writable_source_before_launch on sb16.
  A real writable PROJECT uses otherwise valid roots and subvolume IDs.
  The lower production worker must reject the exact physical-root cause before resource preparation.
  Destination entries, inode metadata, and cgroup directory sets must remain unchanged.
  A false read-only client state for that writable project must fence its separate owner lifecycle.
- Run copy_stage_begin_receipt_destination_binding_fences_before_worker on sb16.
  It must reach BEGIN and the exact binding guard after the source opens successfully.
- Run the direct success, symlink, descriptor, and restart checks in the production harness.
- Run the same affected cases in sanitizer builds after Debug passes.

The writable-source case has its own client and owner lifecycle.
It never fences the positive control's channel.
The previously reviewed relay and harness repairs now live in tracked canonical test paths.
The relay enables kernel credentials before its first queued frame.
It preserves owner PID, exit status, stderr, forwarding status, and original exceptions.
The harness creates its cgroup as the delegated nonroot owner and verifies control-file writability.
Its cleanup requires an applied volume unmount and detached loop.
No production authority checks or acceptance assertions are weakened.
The original f2cf source, binaries, failed v3/v4 runs, and core8 passes remain preserved.
The old Debug and sanitizer core suites passed8 cases each on sb16.
Those passes cover unchanged tree-helper code, not this new native boundary correction.
M07 and Spatial-M1 remain incomplete.

The destination negative was added after all frozen4a15 profile builds finished.
It changes only the probe, harness, and this test plan.
The production fix remains byte-identical to reviewed4a15 source.

Every Python subprocess in the native harness now receives -B.
The nested sudo Python invocations in the restart fixture also receive -B.
This prevents bytecode writes even when sudo strips the outer environment.
The root launch driver must still set PYTHONDONTWRITEBYTECODE after sudo.

CTest now registers copy-worker-production with TIMEOUT900 and RUN_SERIAL.
Its RESOURCE_LOCK is spatial-copy-worker.
The nonroot launcher creates a private UUID evidence parent under spatial-artifacts/runs.
It invokes the existing root harness with explicit environment after sudo and Python -B.
The harness owns its systemd delegation and root helper lifecycle.
The launcher checks six native witnesses, one FD check, one recovery check, and scoped cleanup.
The authenticated relay remains root private-protocol coverage.
All reachable helper targets and generated Python bindings are target dependencies.
CTest registration inspection was compile-host-only.
Subsequent results from both hosts appear in the focused qualification summary.

The root fixture emits a framed JSON receipt from finally.
The nonroot launcher reads that captured receipt instead of traversing root-private setup evidence.
The root fixture publishes its exact PID, start time, and argv before privileged effects.
The launcher allows600 seconds for execution and240 seconds for graceful cleanup.
Cancellation validates that exact live root process identity before sending SIGTERM.
The root SIGTERM handler raises into finally and ignores repeated cancellation during cleanup.
A grace overrun kills only that verified fixture PID and reports cleanup incomplete.

The first f4 Debug attempt stopped at the second case.
The readonly-destination case returned its expected marker; its paired positive remained unrun.
The writable-source case failed before its guard: Broker envelope must not replace existing root limits.
The fixture reused one root across independent WorkspaceResources lifecycles.
Production intentionally leaves its bound root limits in place.
The fixture now creates a fresh operator-owned root and ResourceConfig for every case.
Each root must begin with untouched limits and writable delegated controls.
Each case records its unique root, configuration hash, empty state, and explicit removal.
Cleanup checks all created roots, including the interrupted case.
The failed f4 fixture, exact framed receipt, and full successful scoped cleanup remain preserved.
Evidence: sb16:/home/dflanag3/spatial-artifacts/runs/copy-worker-production-a9u1rjm4.
No production guard or compiled input changes for this fixture correction.

The b293 Debug retry passed both physical-root negative markers and FD32.
The authorized relay reached BEGIN and START, then marked the stage uncertain.
Its worker exited before any helper sample; stderr was discarded by the production launcher.
Its owner and authenticated forwarder exited0 without diagnostics.
All three case roots, volume, socket, unit, and installed helper were cleaned.
Evidence: sb16:/home/dflanag3/spatial-artifacts/runs/copy-worker-production-25hoiplm.
Binding, direct copy, symlink, and recovery remain unrun for that attempt.

The synchronous production worker now captures stderr through a nonblocking pipe.
It drains stderr while waiting, caps retained diagnostics at64KiB, and rejects overflow.
It emits bounded diagnostics and includes the actual child exit status in its private failure.
Sanitizer markers reject even an exit0 worker.
The confined environment sets fixed ASAN_OPTIONS and UBSAN_OPTIONS after clearenv.
No caller credentials or arbitrary sanitizer environment are inherited.
The change follows the reviewed engine preflight06 diagnostic mechanism without importing its async API.
The earlier inner-helper diagnostic limitation applies to retained pre-diagnostic evidence.
The new diagnostic increment requires new affected probe and broker builds before runtime.

The first diagnostic Debug build at94d3b20 failed at compilation.
WorldError accepts const char*, so its dynamic exit-status message now uses a scoped string and c_str().
No WorldError API changes were needed.
Failure logs remain at a5500:/home/dflanag3/spatial-artifacts/runs/m07-copy-diagnostics-debug-94d3b20.

The348 Debug diagnostic attempt reported child exit_status1 and:
bwrap: Can't find source path /proc/self/fd/7: No such file or directory
Its retained evidence is sb16:/home/dflanag3/spatial-artifacts/runs/copy-worker-production-nfm4h8to.
Both physical negative markers and FD32 passed; the authorized control failed.
All three roots were explicitly empty and removed, with applied volume/helper/socket/unit cleanup.
The installed package reports bubblewrap0.9.0-1ubuntu0.3.
Bubblewrap's path-bind source canonicalization explains the anonymous memfd failure.
This is not evidence that ProcessScope closed FD7.
The inherited descriptor mapping and dup2 clearing CLOEXEC are correct.

The sealed job now uses --ro-bind-data7 /job.pb.
Source, destination, helper, and executable descriptor mounts remain unchanged.
The descriptor contains at most4096 bytes of validated versioned WorkerRequest protobuf.
Its offset is rewound and its seals remain enforced before launch.
Bubblewrap reads those fixed bytes into a private read-only bind mount.
The existing ApplicationProcess sealed Xauthority handoff uses the same data-mount mechanism.
The tree helper accepts the resulting regular file without a project-file hardlink rule.
The real authorized relay control is the regression for this launch defect.
Official source references:
https://raw.githubusercontent.com/containers/bubblewrap/main/bubblewrap.c
https://raw.githubusercontent.com/containers/bubblewrap/v0.9.0/bubblewrap.c
No broader file authority or production validation changes were made.

The44c Debug attempt proved the sealed job-data correction through the real helper.
Its authorized relay copied24MiB, sealed, and returned an identical retry with an independent inode.
The sampled helper had256MiB memory,16 PIDs,200000/100000 CPU, and a private network namespace.
Both physical-root negative markers and FD32 also passed.
The fourth repository allocation stopped before binding BEGIN: Volume storage capacity is reserved.
The fixture's512MiB volume left384MiB after64MiB control and64MiB filesystem reserves.
Repository preparation reserves128MiB for each independent domain.
Three domains consumed that384MiB pool; the fourth correctly failed admission.
The fixture now provisions1024MiB: six128MiB domains, two64MiB reserves, and128MiB headroom.
One shared six-mode tuple drives both the capacity plan and case execution.
The capacity plan is recorded before provisioning; production checks remain unchanged.
This adds512MiB logical image capacity, with about49GiB free observed before the prior run.
The earlier image, failure, positive control, and full scoped cleanup remain preserved.
Evidence: sb16:/home/dflanag3/spatial-artifacts/runs/copy-worker-production-edwt8jux.
No production or compiled input changes for this fixture correction.

The recovery method creates a separate512MiB m07-snap volume with a new UUID.
Its own pool is384MiB and its sole repository reserves8MiB.
Restart reuses that same domain, so native-volume headroom is not consumed.
The two recovery owner generations write separate stderr files.
The native fixture now scans both retained files, including zero-exit diagnostics.
Each scan rejects sanitizer markers and files larger than8MiB.
It records path, byte count, SHA256, and successful scan status.
The launcher requires both owner-generation scan receipts before accepting recovery.
All previously captured client, relay, direct-owner, and command diagnostics remain checked.

The fb21 Debug attempt passed all six native witnesses and FD32.
The authenticated binding negative changed only destination_subvolume_id272 to273.
It reached the exact UNAVAILABLE binding guard, fenced the channel, and launched no worker.
The authorized relay and direct copies sealed24MiB with independent inodes and identical retries.
Recovery then failed before restart on its unchanged conflict assertion.
A reused operation with changed maximum_bytes returned INVALID_ARGUMENT1 instead of CONFLICT6.
The owner reused BEGIN branch incorrectly called the generic INVALID_ARGUMENT require helper.
It now throws typed CONFLICT for the same unchanged eight-field binding mismatch.
Malformed request checks, physical object checks, and identical retries remain unchanged.
Other stage-method error branches are outside this narrow correction.
StorageClient already maps owner CONFLICT to wire OPERATION_CONFLICT.
The existing recovery method remains the regression; its oracle is unchanged.
Evidence: sb16:/home/dflanag3/spatial-artifacts/runs/copy-worker-production-pcszud0s.
All six scopes, the native volume, socket directory, unit, and helper were restored.
The separate recovery volume cleanup receipt also remains in its retained test evidence.
The owner service and owner probe require fresh affected-target builds before another run.
