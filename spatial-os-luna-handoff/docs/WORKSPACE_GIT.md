# Workspace Git creation

This increment implements real repository import and worktree creation through workspaced.
Live Git status is now qualified in [WORKSPACE_STATUS.md](WORKSPACE_STATUS.md).
Conservative removal is now qualified in [WORKSPACE_REMOVAL.md](WORKSPACE_REMOVAL.md).
Workspace membership, selection, and bound-worktree protection are qualified in [WORKSPACE_CONTEXT.md](WORKSPACE_CONTEXT.md).
The subsequent [process increment](WORKSPACE_PROCESSES.md) adds confined application launch.
The [Git crash boundary](GIT_CRASH_RECOVERY.md) adds a real broker exit before the ready acknowledgment.
See CURRENT_STATE.md for the current phase qualification.

## Authority and process ownership

Workspaced owns an execution database independent of the world database.
Its registry contains no room or world identity.
The session supervisor starts workspaced and worldd as separate processes.
Worldd receives an inherited private broker channel and the broker's monitored process identity.
Worldd does not own or launch the broker process.
Standalone workspaced accepts an explicit execution-state directory.
Standalone worldd requires an explicitly supplied broker connection for Git operations.

The acceptance supervisor stores execution state under its isolated fixture directory.
That fixture path does not define workspace identity or authorize another world to access it.
Shared host-service attachment and cross-world workspace qualification remain future implementation work.
The broker has no public actor listener.
Inherited management channels supply trusted fixture authority.
Cryptographic enrollment, delegation, and authenticated external transport remain mandatory Spatial-M1 work.

Actor Git creation uses the same broker handler as administrative Git creation.
Worldd checks the actor's Git administration grant before forwarding the operation.
This grant does not confer application execution or workspace membership.

## Real Git effects

Repository import creates a private bare backing store from an authorized local repository.
The import copies committed Git objects without hard links.
It leaves the source repository and uncommitted source files unchanged.
An imported repository does not borrow another repository's alternate object storage.
Sources that use alternates require a separate explicit import procedure.
Git's local clone semantics apply to concurrent source changes.
Provision from a stable source when a reproducible import is required.

Opaque repository and worktree IDs never become directory names.
The registry allocates independent directory names inside its managed execution boundary.
Branch names follow real Git rules.
Start revisions resolve to actual commits before creation.
An already checked-out branch returns BRANCH_IN_USE.
An existing inactive branch returns ALREADY_EXISTS when the request requires a new branch.
The broker never uses forced checkout.

Operation IDs bind the normalized creation request.
A matching retry returns its original result.
A changed request returns OPERATION_CONFLICT.
Retries preserve uncommitted worktree files.
Git remains the authority for refs, objects, indexes, and worktree registration.

These choices use Git's documented [local clone behavior](https://git-scm.com/docs/git-clone)
and [worktree rules](https://git-scm.com/docs/git-worktree).

## Recovery

The broker writes a durable intent before running Git.
Git must finish successfully before the broker creates a private completion receipt.
The broker flushes the worktree, Git metadata, receipt, and containing directories.
Only then does the execution database record ready state.

Recovery requires both the receipt and matching external Git state.
A partial checkout cannot become ready merely because its branch and metadata exist.
Missing receipts, moved paths, and inconsistent effects become uncertain.
The broker preserves uncertain effects for explicit repair.
It does not force-delete data or repeat an uncertain creation.
An interrupted import also remains uncertain because it lacks a reliable clone-completion witness.
SQLite does not make Git and filesystem operations one atomic transaction.

## Administrative Git execution

Git receives a fixed executable path, bounded arguments, a restricted environment, and bounded output.
The wrapper disables hooks, filesystem monitors, automatic maintenance, and unauthorized transports.
Managed configuration keys use an explicit allowlist.
Unknown configuration fails before administrative worktree execution.
Repository filters cannot acquire broker execution authority.
These restrictions follow Git's [configuration controls](https://git-scm.com/docs/git-config).

A separate job supervisor owns each administrative Git process group.
Owner death terminates that job's nested processes.
Deadline handling targets only that owned group.
The parent keeps the supervisor waitable until output closes and group cleanup completes.
This prevents PID reuse during cleanup, using documented [waitid semantics](https://man7.org/linux/man-pages/man2/wait.2.html).
Each Git command has an eight-second deadline. Broker requests have a ten-second communication deadline.
Large repositories may exceed these limits and require explicit recovery.
The world scheduler continues to service motion expiry while waiting for the broker.

This administrative command wrapper is not application confinement.
Generic workspace processes now use the separate confinement path in WORKSPACE_PROCESSES.md.
Complete M03 confinement, X authority, and native application integration remain mandatory.

## Creation qualification

Both hosts passed Debug and ASan/UBSan qualification for this increment.
Each run passed 152 core checks, including seventeen broker counterexamples, and 43 harness checks.
All 113 preceding P00-P07 acceptance tests passed.
Eight additional P08 acceptance tests passed.
The full P08 gate reports 121 passed, one failed, sixteen errors, and no skips.
Thirteen later acceptance tests remain unrun.
The dirty-removal test fails because removal still returns UNIMPLEMENTED.
The other P08 errors also cover unimplemented operations.

The broker checks use real Git, /proc process evidence, SQLite write-failure injection, and actual supervisor death.
They cover completed-effect recovery, uncertain-effect preservation, unsafe configuration, and inherited environment isolation.
An isolated mutation removed the no-hardlinks clone option.
The object-copy witness detected the shared source inode.
The main implementation retained the independent copy.

Evidence is in evidence/workspace-git-2026-10-07.
Each host directory includes source hashes, build identities, exact commands, and complete gate reports.
All 94 final source hashes match the reviewed local files and both execution hosts.
The sb16 mutation directory preserves the failing independent witness and its exact source change.
The first mutation collector expected different unittest wording.
Its follow-up audit verified the actual retained assertion without changing or rerunning the result.

The qualification command uses tools/qualify_core.py --through P07 with each host's explicit private XLibre server.
Sanitizer runs add --sanitize --build-dir build-sanitize.
The subsequent tools/gate.py --through P08 command runs the full current phase and records its remaining failures.
The tests use authenticated disposable software displays.
Sanitizers cover the broker, runtime, and compositor, not the private XLibre server.

Locked acceptance tests, their oracles, the native witness, and the protobuf contract remain unchanged in this increment.
No host configuration or operator session changed.
Full Spatial-M1 and hardware qualification remain pending.
The subsequent status increment observes independent Git changes and unusual paths.
See WORKSPACE_STATUS.md for its qualification evidence and current counts.
The removal increment preserves retired data and reconciles witnessed completion.
Next: implement workspace membership, availability, selection, and confined execution.
