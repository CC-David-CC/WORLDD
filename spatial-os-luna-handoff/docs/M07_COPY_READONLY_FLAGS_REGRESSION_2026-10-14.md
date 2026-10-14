# Btrfs copy read-only flag correction

Status: UNQUALIFIED. New candidate runtime checks remain UNRUN.
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
