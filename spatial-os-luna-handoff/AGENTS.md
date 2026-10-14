# Instructions for Luna and delegated workers

## Objective and authority

Build the actual spatial X11 session. Do not substitute a fullscreen launcher, static scene, replayed screenshots, or an in-memory test double for the runtime. Linux and X11 are required. XLibre is a candidate base, not an untouchable dependency. The user allows server modification and does not require existing-software compatibility. Luna is the responsible driver. The stronger local model is a helper, not the owner of integration decisions.

Full machine permission authorizes the project; it does not make uncontrolled destruction useful. Work in a dedicated test session until native-session gates pass. Record changes to system files. Preserve a tested TTY/SSH recovery route. Do not disable the operator's current display while still relying on that display for recovery. Do not kill processes by broad name.

## Authorized hosts and resources

`sb16` and `sb16-2` are the only authorized hosts for this project, including Linux builds, tests, native sessions, and remote helper workloads. Do not use another host unless the user explicitly changes this restriction. Full machine permission applies only within this host scope.

Each host has 6 GB VRAM, 16 GB RAM, and an SSD, as reported by the user. Keep build parallelism, helper model sizes, and runtime resource use within these limits. SSH access as `dflanag3` and passwordless sudo have been verified on both hosts. See `docs/BASE_AND_MACHINE.md` for the verified package baseline and machine-operation rules.

## Read order

Read `START_HERE_LUNA.md`, `docs/CONTRACT.md`, the current task in `tasks/INDEX.json`, and the exact failing test. Read relevant source references for an unfamiliar X/GL/server API. Do not guess API names or semantics.

## Work loop

1. Inspect current source and existing evidence. Verify live jobs before scheduling new work.
2. Select a coherent increment from the current phase. Record changed files and affected test IDs.
3. Reproduce the defect or missing behavior. Preserve its command and failure evidence.
4. State the invariant. Implement it in production code.
5. Run affected tests and related integration checks. Include focused sanitizers for lifetime and resource changes.
6. Review the diff. Record passed, failed, and unrun checks in `local/driver-state.json`.
7. Commit coherent increments that pass their selected checks. Keep the full phase gate pending.
8. Run complete required qualification on both hosts at the phase gate.

The root AGENTS.md defines the current test schedule.
Do not rerun the complete cumulative matrix after every primitive.
Broaden testing when a shared change or failure affects additional components.
Preserve reviewable commits for reproducible regression diagnosis with git bisect.

A partial phase can remain on a work branch. Do not call it complete. Do not mark an unexecuted or skipped test as passed. Do not turn a failing test into a TODO.

## Test integrity

Do not edit locked acceptance tests, their oracles, native witness, gate, or normative contract just to obtain green output. Do not import the independent Python oracles into production. Do not hardcode witness colors, known IDs, test directory names, or expected JSON results. Never implement a second world engine inside the test host.

The test host is only a thin boundary: forward world actions to production handlers, call production pure functions, inject a clock at the actual scheduler boundary, and expose narrowly named fault points in the actual effect/commit path. Native surface tests must use the independently launched C witness.

A genuine test/contract contradiction is possible. Stop that primitive, record the exact contradiction, preserve a reproducer, and have the stronger helper review the smallest correction. Record the corrected invariant, why it is not weakened, and additional counterexamples. Version the test-contract change as a separate reviewed change. Never silently rewrite `acceptance.lock.json`. The lock is tamper-evident, not tamper-proof against a full-access driver.

## Architecture

One authoritative semantic world. One X11/graphics/input boundary. One canonical actor action vocabulary shared by physical controls and agents. Production C++20 is the suggested implementation language; X-server changes remain in the server's native language. No Godot, Electron, Docker, or conventional desktop dependency is needed.

Do not preserve the earlier hidden 2D interaction-atlas proposal. Test world-local routing with overlapping root-coordinate windows. Modify the server at the correct routing boundary when needed. Keep genuine surface-local pixel coordinates: 3D application panels still have pixels.

Do not put every camera tick in SQLite. Runtime poses and input are live state; acknowledged durable edits and explicit checkpoints have defined persistence. Do not claim a SQLite transaction makes X requests, child processes and Git changes one atomic operation. Journal recoverable effects; expose uncertainty when effects cannot be reconciled safely.

IDs are opaque. A branch name is not a worktree path. A live XID is not a durable application identity. A PID is not a durable process identity. A helper's prose is not execution evidence.

## Delegation

After two unsuccessful implementations of the same invariant, stop guessing and use the stronger helper. Delegate a bounded file/function/primitive, with the actual failing assertion and source contract. The helper may propose production code and additional tests. It may not weaken existing acceptance checks or silently redesign adjacent phases.

Use a separate worktree for a tool-enabled helper, or `tools/delegate.py` for a text/patch response. Do not grant two agents write ownership of the same production files simultaneously. Review the patch, check that it applies, run tests locally, then integrate. Never accept a helper's claimed tests in place of Luna executing the gate.

Keep this handoff's model names as roles. Do not invent a Codex model identifier, helper URL, authentication key, Responses support, or tool-calling support. Inspect actual local configuration and supported interfaces.

## Completion report

Report exact commands, commit/source pins, passed/failed/unrun test counts, native-session evidence, physical SpaceMouse evidence, known limitations, and recovery procedure. The OS is not done because the harness passes or because a room renders. Read `docs/HARDWARE_ACCEPTANCE.md` before claiming the native operating environment works.
