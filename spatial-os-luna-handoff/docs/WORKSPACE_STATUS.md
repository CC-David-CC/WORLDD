# Live workspace Git status

The production workspaced process reads status from the registered real Git worktree.
Worldd forwards the existing typed protobuf request through its private broker channel.
This increment does not change the acceptance contract or execution database schema.

## Live state

Status reads the current commit and branch from Git, including independent commits and branch changes.
A detached HEAD returns an empty branch string.
An unborn branch returns an empty head string.
Stored creation results remain unchanged. They describe the original operation, not current checkout state.

Tracked modifications include staged changes, deletions, and unresolved merge conflicts.
A rename includes both its source and destination paths.
Untracked paths remain separate from tracked modifications.
Ignored files and directories also appear in the untracked list.
This conservative behavior prevents a clean result from concealing ignored data.
An ignored directory can appear as one directory entry.

The parser uses Git porcelain version 2 with NUL delimiters.
It preserves spaces, tabs, newlines, quotes, backslashes, and valid UTF-8 names.
It does not split paths on whitespace or interpret rename sources as records.
Both path lists use stable ordering and remove duplicates.
The command disables optional Git locks. Status does not refresh the index.

## Boundaries and limits

Status requires a registered worktree with a completed creation effect.
The broker verifies its administrative links against the registered bare repository.
Changed authority paths and symlink replacements fail before status runs.
The existing administrative Git configuration restrictions still apply.
These checks do not replace the required workspace process confinement.

The current protobuf schema uses UTF-8 strings for paths.
A filename with invalid UTF-8 returns INVALID_ARGUMENT without changing files or closing the channel.
Supporting arbitrary POSIX filename bytes requires an explicit interface migration.
Do not claim that this increment supports every possible POSIX filename.

A reply exceeding the transport budget returns RESOURCE_EXHAUSTED.
The broker preserves files and keeps the channel usable after this error.
The administrative command also has its existing output and execution-time limits.

Git status is an observation, not a filesystem lock or an atomic snapshot across external writers.
Git index flags can hide working-file changes from ordinary status.
Worktree removal must independently protect such files and handle concurrent writes.
Worktree removal remains unimplemented in this increment.

## Qualification

Both hosts passed the 27 focused broker checks after correcting a path-normalization defect.
The first run rejected valid common-directory links because normalization retained a trailing separator.
The corrected implementation removes that empty trailing component before comparing authority paths.
The original failing logs remain available with the evidence.

Ten new checks use real Git and filesystem operations.
They cover unusual paths, staged changes, renames, merge conflicts, and external branch changes.
They also cover ignored data, administrative-link replacement, invalid UTF-8, reply limits, and pending operations.
The reply-limit test creates 4,500 real files and verifies their preservation after rejection.

Both hosts passed Debug and ASan/UBSan qualification for this increment.
Each build passed 162 core checks, 43 harness checks, and 124 acceptance tests.
The full P00-P08 gate ran all 138 current cases.
It reports one failure, thirteen errors, and no skips for the remaining unimplemented P08 operations.
Thirteen later acceptance tests remain unrun. P08 remains incomplete.

Evidence is in evidence/workspace-status-2026-10-07.
It contains baseline failures, the initial implementation failure, corrected focused runs, and complete qualification reports.
All 96 final source hashes match reviewed local files and both execution hosts.
Each run records its exact commands, build identity, and private XLibre server hash.
The commands use tools/qualify_core.py --through P07 and then tools/gate.py --through P08.
Sanitizer qualification adds --sanitize --build-dir build-sanitize.
Sanitizers cover the broker, runtime, and compositor. The private XLibre server remains a Debug build.
Tests use authenticated disposable software displays.
No host configuration or operator session changed.

Full Spatial-M1 and native hardware qualification remain pending.
