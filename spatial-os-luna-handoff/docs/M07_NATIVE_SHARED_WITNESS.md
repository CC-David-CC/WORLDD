# M07 native shared workspace witness

This regression covers the M07A rule that a placement represents one workspace execution. World placement rows remain independent of workspace execution, availability, and actor selection.

## Selected focused checks

Run `workspace-presentation-shared-representation` for two local worlds. It creates one repository, worktree, workspace, and confined nonce-writing process before either placement. It checks both world rows reference that workspace, then moves and removes one representation. It toggles availability and selection without changing pixels. It restarts the world with the surviving representation and checks its ID, room, transform, generation, nonce inode, and process identity. It also checks locked metadata filtering for an actor without workspace access.

Run `workspace-geometry-native-crosshost` for a shared authoritative world. The workspace broker and authoritative world run on one qualification host. A second host and a second independent actor on the authority host each connect to that world using the production TLS actor protocol. Both native host sessions report geometry pixels and compositor/server PID identities. The test creates and moves one workspace placement, checks pixel changes on both hosts, and checks that the broker, world, confined process, worktree, and nonce file keep their identities. Availability changes must preserve geometry. A viewer with representation visibility but no workspace access must see locked geometry without the workspace ID or path.

The native peer process chain reuses M06: `geometry_native_delegate.py` starts the exact owned unit; `with_adapter_resources.py` provisions the adapter cgroup; `geometry_native_host.py` launches the production host session, compositor, and native X server; `GeometryPixelWitness` reads X pixels independently; the protobuf witness returns native process identities. The authority-side observer uses the same chain locally with its own profile directory, actor key, and native host session. The existing M06 cross-host test keeps its default behavior.

The native actors receive only `WORKSPACE_ACCESS` within workspace `one`. Their world grants allow entry, navigation, observation, and representation visibility. An explicit workspace-scoped grant-list and delegate request assigns access. The fourth actor receives no workspace grant. Its workspace selection fails with `FORBIDDEN`, while its public presentation view remains locked.

Each active native actor observes through its existing production host controller. The witness does not open a second controller for that actor. Public `PresentationView` messages carry only representation ID, room, transform, generation, and lock state. The workspace ID is checked in the authoritative database row.

The witness checks two different generations. Native observations check workspace representation generations 1, 2, and 3. Compositor receipts check a stable presentation-target generation and advancing view sequences with current world revisions. Moving geometry does not replace that target.

A direct return move from the other room must fail with `NOT_VISIBLE` and preserve rows, revisions, and pixels. The owner then walks through a real portal using actor motion, stops, and retries from the target room. The retry must succeed. Process identity, mount namespaces, nonce contents, and nonce inode remain unchanged.

CTest invokes the local test through `with_resource_delegate.py --native`, `with_adapter_resources.py`, and `with_test_display.py`. CTest invokes the cross-host test through the same resource wrappers, then injects the M06 peer connection profile and runs `with_test_display.py`. Each case has a separate selector, so the existing actor suite count stays unchanged.

## Test status

Focused component: **PASS**, recorded on 2026-10-14. Full M07 gate: **UNRUN**.

The passing runtime fixtures use checkpoint `5658b9fc35097df978f3d2504049f7947cb677df`.
Debug reuses all 14 executables from `0bf3cd47ad3b0ed6bbe83d83f4876cc96ff8baad`.
Its package manifest is `2b0a4a71d8677cb2ec622210c62ece6e3125a3d68a34e6fecb85b86f53777a28`.
Its reviewed fixture overlay manifest is `fee266643e2d3dfcccf2caf4668ebb3a4d46d9480e30b114400f98c79cb328ce`.
SAN builds all 14 executables from frozen `5658b9fc35097df978f3d2504049f7947cb677df` through the normal aggregate target.
That build completed 189/189 steps with both outer exits zero.
Its package manifest is `57433d9ff8b82531733ece9ab0f6db801f62d4738c3d3b3cfd4141a5c19bd35e`.

| Profile | Check | Authority / observer | Tests | Seconds | Result |
| --- | --- | --- | ---: | ---: | --- |
| Debug | TLS lifecycle | sb16 | 2 | 6.501 | PASS |
| Debug | Native shared geometry | sb16 / sb16-2 | 1 | 39.102 | PASS |
| Debug | Native shared geometry | sb16-2 / sb16 | 1 | 43.857 | PASS |
| ASan/UBSan | TLS lifecycle | sb16 | 2 | 8.759 | PASS |
| ASan/UBSan | TLS lifecycle | sb16-2 | 2 | 10.522 | PASS |
| ASan/UBSan | Native shared geometry | sb16 / sb16-2 | 1 | 56.355 | PASS |
| ASan/UBSan | Native shared geometry | sb16-2 / sb16 | 1 | 61.090 | PASS |

Each row has raw unittest `OK`, `command.exit=0`, and `wrapper.exit=0`.
Lifecycle tests cover real plain and broker-backed TLS world cleanup twice.
Native tests cover the shared placement, authority filtering, pixels, process identities, nonce inode, and actor navigation.
Cleanup removed every owned scope. Exact units and cgroups were absent afterward.
Sanitizer scans found no reports in retained issuer, world, broker, server, compositor, or outer logs.

The confined independent `spatial-confined-xprobe` intentionally remains uninstrumented under CMake's declared `RLIMIT_AS` exception.
The other 13 SAN executables link ASan and UBSan.
Both profiles use their exact host-specific qualified M06 XACE wrappers and graphics receipts.
The qualified provenance is `/home/dflanag3/spatial-qualification/m06-4f241-final-20261013/deployment/provenance.json`.
Its SHA256 is `9dd01b3f66bffb00ea67bada5bd9492fc998783dad3b0ea2568697630157d0a4`.
These checks cover shared geometry and authority. Remote application streaming remains outside this check.

Evidence root: `/home/dflanag3/spatial-artifacts/m07-crosshost-source-repair-20261014`.
Debug receipts are `cleanup-lifecycle-debug-run01`, `native-retry-debug-run02`, and `native-reverse-debug-run03` on their authority hosts.
SAN receipts are `san-lifecycle-HOST-run01`, `san-forward-native-run01`, and `san-reverse-native-run01` on their authority hosts.
The matching `*-audit01.json` files record raw summaries, sanitizer scans, and cleanup identities.
Debug native audits are `native-retry-debug-audit02.json` and `native-reverse-debug-audit03.json`.
Package metadata records every executable, binding, source hash, mode, launch environment, and wrapper receipt.

The original `first-debug-runtime01` remains **FAILED** and preserved.
Its registered cleanup exposed a missing `broker_channel=None` initialization in the plain TLS fixture constructor.
The reviewed constructor fix and real lifecycle regressions precede every passing row above.
Focused component results do not qualify the cumulative M07 or Spatial-M1 gate.
