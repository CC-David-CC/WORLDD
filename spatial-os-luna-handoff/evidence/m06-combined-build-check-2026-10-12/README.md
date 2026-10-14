# Combined M06 coding-host build validation - 2026-10-12

This evidence records compile and registration checks only. It contains no runtime acceptance result.

## Source pin and worktree

- Host: a5500 (Linux hostname G164).
- Checkout: `/home/dflanag3/src/spacial-m06-combined-build-check-20261012`.
- Branch: `m06/combined-build-check-20261012`.
- Base and HEAD: `38f6605f0aba6d0073ad18bc65630543539a4b44`.
- Upstream at fetch time: `origin/m06/world-transitions` resolved to the same commit.
- Before build: clean worktree.
- After build: no tracked source changes; `git status --short` reports only untracked `local/` build outputs.

The commands and their purpose are recorded in `selected-checks.txt` before configuration.
Build outputs remain on a5500 under `local/m06-combined-build-check-20261012/`.

## Results

| Profile | Configure | Three target build | Registration listing |
|---|---:|---:|---:|
| Debug | 0 | 0 | 0 |
| ASan/UBSan | 0 | 0 | 0 |

Both builds compiled `spatial-actor-transition-crosshost-probe`, `spatial-actor-transition-integration-driver`, and `spatial-host-actor-native-runner`, with normal dependencies and `-j2`.

Both `ctest -N` listings contained `actor-transition-real-world`, `actor-transition-crosshost`, and `host-actor-controller-native`. Each listing reported three tests.

CTest did not execute tests. The placeholder plan path was `/tmp/m06-combined-build-check-20261012/registration-only-plan.pb`; it was for registration only. No probe or runtime command ran. The tmux session and build processes ended.

`binary-sha256.txt` records hashes for the six generated target binaries. The binary files remain on a5500.

## Launcher receipt defect

The tmux wrapper exit file contains the literal bytes `$?`, not a numeric status. It is preserved as `logs/driver.exit.literal-marker.txt` and is untrusted. The task-level exit is therefore UNKNOWN.

The six per-command exit files each contain `0`. Their configure, build, and registration logs are preserved. All three requested targets exist in each build directory, and both registration listings are complete. No build was restarted to repair the wrapper receipt.

`evidence-files.sha256` lists the copied files. It excludes itself.
Raw evidence whitespace is preserved in binary-sha256.txt and both configure logs.
Primary verified all 17 file hashes, six per-command zero exits, and both three-test inventories.
The aggregate launcher exit remains unknown; runtime remains unrun.
