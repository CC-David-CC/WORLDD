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

Source repair is ready for primary review. Runtime verification remains UNRUN. No qualification result is claimed here. Preserve the pre-change M06 receipt at `/home/dflanag3/spatial-qualification/m06-4f241-final-20261013/deployment/provenance.json`; use its exact package closure for the focused overlay after review. Do not run the full cumulative gate for this regression.
