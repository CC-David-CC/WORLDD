---
name: spatial-worker-coordination
description: Coordinate Spatial Desktop coding workers through local Git, box-git, isolated worktrees, tmux, and reviewed test evidence. Use for worker assignments, handoffs, checkpoints, retries, and integration in this project.
---

# Spatial worker coordination

Read the current project AGENTS.md and implementation status first.
This skill does not authorize unrelated hosts, GitHub publication, or changing acceptance contracts.
Keep the Spatial-M1 objective intact. A component checkpoint is not a milestone gate.

## Assign ownership before starting

Use middle-child and a5500 for coding and builds under the current host allocation.
Use sb16 and sb16-2 for runtime verification. Do not install Codex there.
Keep box-git limited to repository administration. Keep inference on its designated hosts.

Each worker needs a named branch and separate checkout or worktree.
Record its host, absolute path, branch, base commit, owned files, task, and tmux session.
Branches may overlap in file scope only in separate worktrees. Record the planned integration order.
Do not share one writable checkout between live coding workers.
Do not force one branch into multiple linked writable worktrees.

Give each worker a bounded production outcome and explicit regression cases.
State artifact names, build profiles, permitted execution hosts, and completion requirements.
Include this skill's installed path in the task so the worker reads it.
Start with Luna medium. Escalate a failed implementation with concrete review findings.
Do not escalate merely because a worker is slow.

## Launch and observe

Configure the normal build before a long compile. Record missing dependencies and their installation logs.
Do not fake imported targets or disable required components to conceal missing dependencies.
Use modest build parallelism. The current convention is -j2 per build worker.

Use a persistent tmux session and a unique run directory for each attempt.
Keep the exact task, launch script, model, base commit, stdout, stderr, and final report.
Write the exit code even when the worker fails. Avoid set -e around the worker command.
Transfer multiline scripts as files. Do not embed nested Python and shell scripts in PowerShell command strings.
Use LF line endings for remote scripts.
When editing Linux source from Windows, preserve existing line endings or write new lines explicitly as LF.
Python write_text uses platform newlines unless configured. PowerShell Set-Content can add a BOM or CRLF.
Use explicit encoding and newline handling for hash manifests and transferred files.

Inspect tmux and the specific child process before restarting work.
An observation timeout is not process failure. Recheck the same handle.
A queued correction must wait for the prior writer to exit and its terminal record to exist.
Never edit a live worker's owned files to fix a review finding.
Record that finding outside its source files and hand it to the next bounded attempt.

## Worker completion contract

A zero worker exit code does not mean its task is complete.
Before reporting completion, compare the result with every requested deliverable.
Implement requested tests; a test plan is not a substitute.
If work remains missing, label the handoff INCOMPLETE and list it explicitly.

The final handoff must contain:

- Branch, base commit, changed files, and a short explanation of the resulting behavior.
- Selected tests and the invariants they check, recorded before execution.
- Exact build commands, profiles, binary paths, and build failures with preserved logs.
- Passed, failed, blocked, skipped, and unrun checks as separate counts or named lists.
- Required regressions still missing, including tests requested by the task.
- Known semantic changes and assumptions that require primary review.
- The next safe command or task. Do not say only "continue testing".

Compile-only hosts must label runtime checks UNRUN.
On a compile-only assignment, do not run probes or ctest tests, including fixture-only tests.
Use ctest -N only to inspect registration. Runtime belongs to the designated test hosts.
Do not weaken production checks to fit a worker sandbox's runtime behavior.
Do not claim TLS coverage from a local socket test or native coverage from a fixture peer.
Check production failure paths before asserting that an error proves no side effects.
Do not infer protocol guarantees from test fixtures written with the same assumption.

## Local Git checkpoints

Origin for this project is dflanag3@box-git:/srv/data/git/spacial_desktop.git.
Preserve repository remotes, history, and repository-restricted SSH keys.
Use repository-local identity David <david@code.contractors>.
Verify the current path, branch, remote, status, and staged diff before committing.

Stage explicit files. Never use git add . or git add -A for these large worker checkouts.
Exclude credentials, local/ run directories, databases, environments, and build outputs.
Include selected evidence only after reviewing it for secrets and irrelevant output.
Review git diff --cached and run git diff --cached --check.
Stop if a native command fails. PowerShell does not stop automatically on nonzero native exit codes.
Check $LASTEXITCODE before dependent commits, pushes, or deployment steps.
Do not commit unrelated staged changes.

Use a coherent commit per independently reviewable increment.
Draft checkpoints may be pushed to their task branch for coordination.
Label draft commits and handoffs UNQUALIFIED. Never merge them merely because they compile.
If the worker sandbox denies .git writes, hand exact paths and status to the controller.
The controller performs the authorized commit. Do not bypass the sandbox or retry the same denied Git write.

Push explicit branches to origin without force. Verify the remote branch resolves to the expected commit.
On a rejected push, fetch and inspect divergence. Do not overwrite the other worker's history.
GitHub publication remains a separate authorization decision.

## Primary review and integration

Check source and tests against the production caller, server, and authority contract.
Review all changed files, including generated build registration and schemas.
Inspect source-level error and lifetime paths before running expensive qualification.
Send concrete defects back with the failed invariant and required regression.
Keep evidence from earlier failures.

Freeze source hashes or a commit before distributing binaries.
Verify artifact hashes after transfer to each test host.
Run focused Debug and sanitizer checks on both test hosts when the affected boundary requires them.
Inspect output assertions and sanitizer diagnostics as well as process exit status.
Do not accept a test harness that exits zero after reporting failures.

Integrate reviewed task commits into the milestone branch in the recorded order.
Resolve shared-file conflicts deliberately. Re-run affected checks on the combined source.
Update status with evidence paths and remaining integration work.
Push coherent milestone checkpoints to box-git.
Run the complete required matrix only at the milestone gate, unless shared failures justify broadening earlier.

## Fix recurring process failures

After a process failure, identify the concrete cause before retrying.
Change the assignment, preflight, completion check, or launch mechanism that allowed it.
Do not repeatedly send the same task and hope for a different result.
When tests are omitted, require named implemented cases and verify them before accepting the handoff.
When builds fail on missing packages, fix and record normal dependencies before rebuilding.
After adding CMake targets, run an explicit configure before requesting those targets from an existing Makefile build.
When transport quoting corrupts scripts, transfer script files instead of adding another escaping layer.
When two branches change the same API, merge once and test the combined API before adding more dependent work.
Keep improvements specific to observed failures. Do not create unrelated approval or compliance procedures.
