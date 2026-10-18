# M08 actor cabinet gateway

This increment routes the existing actor cabinet action through the workspace broker and FileEngine. It does not add public request fields or change schemas. The earlier typed-UNIMPLEMENTED copy placeholder is superseded by the separate copy-runtime increment (fab264) and staging integration. The actor-path copy regression requires that combined source; the d346 cabinet snapshot alone does not provide the completed broker/runtime.

The gateway resolves the presentation from the actor's current room, checks its generation, visible geometry, reach, and separate observation, view, and use rights. It takes workspace identity from that registered presentation and principal identity from the authenticated actor. The requested project-relative filename remains action data and is validated by FileEngine. The gateway never accepts caller workspace, authority scope, root, or host path overrides.
Cabinet placement and projection resolve the broker WorkspaceReply for that registered workspace. They require the returned workspace ID to match and its worktree ID to be nonempty. They check whether any FILE_* right is effective at exactly that worktree-ID component, then re-resolve and require the ID, worktree ID, and policy revision to remain stable. The check retains supplied authorization fences. It does not require WORKSPACE_ACCESS or FILE_BROWSE. Worktree paths and names remain private and never enter public presentation metadata.

The private broker request carries the actor principal, registered workspace ID, exact file action, and operation ID. FileEngine remains responsible for fresh exact file-right and workspace-policy checks. The gateway does not add browse or read prerequisites to create-only operations.

Before broker delivery, the gateway snapshots the actor token, principal, entity, room, and pose. It checks that snapshot after observation and again after the final registered-binding query. It derives the immutable worktree ID from the broker's WorkspaceReply. After a successful file action, it rechecks the same workspace ID, worktree ID, and policy revision. It checks the exact FILE_* right at the single worktree-ID scope component, then compares the result with an authorization fence. Empty-component workspace-wide grants still cover this descendant scope. Presentation IDs and filenames are never authority components.

The production dispatcher serializes actor actions. An in-flight cabinet call cannot concurrently service another actor close/reopen action. The gateway still checks the session-map entry and saved actor identity after all waits. The regressions use the reachable real issuer/world-authority revocation path during observation and after a durable file effect. Any lost post-effect authority produces UNCERTAIN with the original operation ID and no file metadata. Local world-right checks run before the final session, context, and target comparison. They do not pump BrokerClient callbacks.

The focused fixture source covers authorized browse/read/create and replay, create-only authority, an exact worktree-scoped grant, workspace mismatch and policy denial, hidden/use/reach/stale target rejection, revocation during observation, revocation in the final post-operation authorization window, revocation after a filesystem effect, copy's typed unimplemented result, and malformed request/path rejection. The exact-scope placement regression also checks a successful CABINET placement and real create for a FILE_CREATE grant scoped to its registered worktree, plus a wrong-worktree denial with a generic-placement control. These checks are compile-only here. Runtime tests remain unrun pending primary review and qualification-host scheduling.


### Cabinet presentation designation and storage (2026-10-18)

M08 adds an explicit `PresentationKind` to placement requests. Omitted kind remains GENERIC for existing M07 callers. CABINET is persisted in a new SQLite column; existing v3 presentation rows upgrade transactionally with GENERIC as the default only through `worldd --state-dir PATH --migrate-v3` while stopped. Normal startup refuses v3 without this explicit migration. Existing five-field operation receipts remain readable as GENERIC. Cabinet receipts use the versioned `PKD1` kind tail, and unknown versions or kind values fail closed. Generic receipt bytes remain unchanged. A replay whose requested kind differs from its committed receipt conflicts.

The public `PresentationView.locked` field keeps its qualified M07 meaning for GENERIC. Cabinet-specific authorization and appearance use cabinet metadata fields and a taller, shallow production-rendered box. Workspace availability and selection still do not create placements.


### M08 public presentation-view extension (2026-10-18)

M08 appends `PresentationView.kind` at stable tag 6 and `cabinet_locked` at stable tag 7. Tags 1 through 5 keep their M07 meanings and wire encodings. An omitted kind means GENERIC, and false `cabinet_locked` adds no serialized bytes. A compatibility regression compares the resulting bytes with a legacy five-field payload.

The two appended fields carry only representation kind and effective cabinet file-authority status. They do not expose workspace IDs, paths, names, filenames, directory structure, branches, applications, or file-operation receipts. The generic `locked` field remains tied to WORKSPACE_ACCESS. Cabinet lock state uses effective FILE_* authority.

Maintained source-level descriptor assertions now whitelist the exact seven fields while retaining existing unauthorized-metadata checks. `acceptance.lock.json` remains unchanged. It still pins the earlier `core.proto` hash `ad7ab93ff1666259273e3c6a7f2bc3658e84e60c0748a94f67f72717566d50c4`; the current M08 candidate schema hash is `52eda03b3b6658346f4ba6d782270e60b898fe5beea3f7d48d3d940facc2ed5e`. Primary review must resolve this schema-pin discrepancy before checkpointing.


The existing geometry-presentation-native registration still uses a synthetic publisher. The separate workspace-cabinet-native-crosshost registration now uses the production worldd actor path, the native spatialwm observer, and the independent X/geometry pixel witness. It checks no implicit cabinet after availability and selection, FILE_BROWSE-authorized and WORKSPACE_ACCESS-only native actor projections with distinct cabinet lock status, an unauthorized metadata-private locked projection, and pixel changes after a persistent placement move. Both native actors' projected generation and transform are checked after the move. The fixture target is workspace-cabinet-native-crosshost-fixture; the test is compile-only on middle-child and remains UNRUN until qualification hosts replay it.

Runtime selection: ctest --test-dir BUILD_DIR -R ^workspace-cabinet-native-crosshost$ --output-on-failure. The registration reuses the qualified cross-host geometry environment and wrapper inputs. Deploy worldd, spatialwm, identityd, spatial-issuer-client, native fixture helpers, generated Python protobufs, and the checked source test tree. Record the source manifest and artifact hashes before replay on both sb16 and sb16-2.


### Cabinet native witness validation plan

Selected before checks:
- Python syntax compilation of tests/core/test_cabinet_native_crosshost.py checks the new fixture entry point parses.
- CMake configure plus ctest -N checks named registration and generated target wiring without starting fixtures.
- Build workspace-cabinet-native-crosshost-fixture with Debug and -j2 checks every declared native executable/helper dependency compiles.
- Runtime test workspace-cabinet-native-crosshost checks authenticated actor projection, locked metadata privacy, no implicit placement, and independent pixel changes after create and move. This runtime check is UNRUN on middle-child and belongs on sb16 and sb16-2.
