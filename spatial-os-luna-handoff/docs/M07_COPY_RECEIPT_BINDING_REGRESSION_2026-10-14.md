# Copy-stage receipt binding regression

Status: UNQUALIFIED. Runtime checks remain UNRUN.
Base: 5ac265f805adbbcf8d012e78255c97033015eaee.
Production binding code remains unchanged from 805713a420ab9e5df27201f0b641a6d59dc5ce28.
The binding increment changes the compiled test probe and Python harness.
The candidate also incorporates reviewed helper fix de3d73a230225eb948dee5f2b0b84c5d2b60488b.
The acceptance contract and schemas remain unchanged.

The missing case now has an implemented test ID:
`copy_stage_begin_receipt_destination_binding_fences_before_worker`.
The control ID is `copy_stage_authorized_receipt_relay_reaches_worker`.
Both run through `tests/native/test_copy_worker_production.py`.

The relay forwards production protobuf to the real storage owner.
Its forwarding child drops to dflanag3 before sending owner requests.
The owner configuration pins that child's actual nonroot UID and PID.
The root relay supplies its actual PID in the private owner hello.
Linux supplies SCM_CREDENTIALS on the sockets. The fixture never forges credentials.
The negative changes only BEGIN receipt destination_subvolume_id.
The replacement remains positive and differs from the source subvolume.
The fixture checks that restoring this field restores the complete original reply.
This is authenticated root-negative private protocol coverage.
It does not prove an ordinary actor can forge an owner response.

The production function must report UNAVAILABLE with this exact message:
`Storage owner copy receipt does not match pinned stage intent`.
A later inspect must fail through the fenced channel.
The destination must remain empty with unchanged inode, size, mtime, and ctime.
The operation's cgroup directory set must remain unchanged.
Destination enumeration rejects readdir errors and checks closedir.
The harness rejects sanitizer diagnostics even when a process returns zero.
It scans nested production-owner stderr from the relay audit.
Owner fixture processes run in dedicated sessions for exact process-group cleanup.
The restart fixture runs as dflanag3 with an owned evidence directory.
Its environment retains sanitizer and Python configuration.
The relay must observe EOF directly after BEGIN.
It must observe no START, SEAL, UNCERTAIN, or later INSPECT.
These checks establish that worker preparation and launch were not reached.

The authorized relay control must copy real bytes and produce a SEALED receipt.
It must preserve identical retry behavior and use independent destination inodes.
The harness must witness the actual helper inside its bounded descendant cgroup.
It must witness a private network namespace.
The existing direct success and symlink scenarios remain selected.
The existing malformed-owner descriptor check remains separate coverage.

Selected checks, recorded before execution:
- Python AST compilation: syntax of both changed Python files. Compile only.
- Debug target build: spatial-copy-worker-production-probe. Compile only.
- ASan/UBSan target build: spatial-copy-worker-production-probe. Compile only.
- BUILD_TESTING=OFF configure: confirms the test-only probe remains excluded.
- Root native harness on sb16 and sb16-2: both relay cases and existing regressions. UNRUN.

Builds use separate external directories and -j2.
Build logs use the installed canonical run-command.sh wrapper in tmux.
No probe, ctest, storage owner, or world runtime runs on a5500.
No runtime starts before primary review of source and artifact closure.

Package overlays must contain the rebuilt probe and both changed Python files.
They must name this source pin separately from reused production binary pins.
Primary reviewed the helper conflict-mapping fix from a separate worker.
This branch cherry-picks that fix before freezing the candidate.
The helper and fixture rebuild from the combined source pin in Debug and sanitizer profiles.
The production helper rebuilds with BUILD_TESTING=OFF.
Unchanged owner binaries remain explicitly pinned to 805.

After primary review, deploy one frozen package to each required host.
Verify every package hash after transfer.
Run as root with BUILD pointing to the package root and SOURCE to package/source:
`python3 SOURCE/tests/native/test_copy_worker_production.py BUILD SOURCE NEW_EXTERNAL_EVIDENCE_DIRECTORY`.
Each run needs the root-owned spatial-bwrap and delegated cgroup prerequisites.
The harness installs and restores the package helper with verified ownership and hashes.
Preserve failed evidence and evaluate cleanup records even after scenario failure.
M07 and Spatial-M1 remain incomplete.
