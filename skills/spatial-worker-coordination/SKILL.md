---
name: spatial-worker-coordination
description: Coordinate Spatial Desktop coding workers through local Git, box-git, isolated worktrees, tmux, and reviewed test evidence. Use for worker assignments, handoffs, checkpoints, retries, and integration in this project.
---

# Spatial worker coordination

Read the current project AGENTS.md and implementation status first.
This skill does not authorize unrelated hosts, GitHub publication, or changing acceptance contracts.
Keep the Spatial-M1 objective intact. A component checkpoint is not a milestone gate.

## Assign ownership before starting

Use middle-child and rog-ally-x for coding and builds under the current host allocation.
Do not use a5500. Follow root AGENTS.md if the host allocation changes.
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
Copy required review findings into the worker's task inputs before launch.
Use absolute input paths and verify they exist in that checkout. An older base may lack newer review files.
Keep this repository copy canonical. Install matching copies on the controller and coding hosts.
Verify installed file hashes after updates. Existing workers receive changed instructions at their next bounded attempt.
Start with Luna medium. Escalate a failed implementation with concrete review findings.
Do not escalate merely because a worker is slow.

## ROG to test-host SSH

Use these explicit SSH options for ROG-to-test-host control and SCP transfers:

```text
-i /home/dflanag3/.ssh/spatial_testboxes_ed25519
-o UserKnownHostsFile=/home/dflanag3/.ssh/spatial_testboxes_known_hosts
-o BatchMode=yes
```

Use `dflanag3@10.0.7.49` for sb16 and `dflanag3@10.0.7.94` for sb16-2.
Pass each option as a separate subprocess argument.
Default SSH identities have repeatedly failed while this configured key worked.
A default-key rejection does not prove that access is unavailable.
Retry with the recorded options before reporting an access blocker.
Preserve the failing command and its stderr without printing private key contents.
Transfer artifacts directly between authorized LAN hosts. Do not relay them through the controller.

SSH passes its remote command through a shell. A local subprocess argument list does not quote remote shell syntax.
For remote checks with multiple commands, send a Python script to `ssh HOST python3 -` through stdin.
Run target programs with subprocess argument lists inside that remote script.
Do not concatenate unquoted pipelines, regular expressions, or shell separators into SSH arguments.
Measure hostname, machine identity, filesystem UUID, and disk usage in the same remote process.
Reject results that disagree with the assigned host or filesystem before using them for cleanup.
Preserve the control script and stderr when an audit contradicts the recorded host inventory.

## Launch and observe

Configure the normal build before a long compile. Record missing dependencies and their installation logs.
Do not fake imported targets or disable required components to conceal missing dependencies.
Use modest build parallelism. The current convention is -j2 per build worker.
Allow only one build process per build directory. Use flock with a directory-specific lock on Linux.
For a long build, poll the original tool session. Do not submit the same build again while it runs.
Use an output-specific flock for bundle packaging too. Two archive writers can corrupt the same output.
Write archives to a unique temporary path. Validate them before atomically replacing the final artifact.
Check packaging scripts are nonempty and invoke their intended entrypoint before launch.
Check paths and hashes of final validation helpers before starting expensive packaging stages.
After packaging, compare archived source hashes with the corrected checkout. Exit zero alone cannot detect a no-op packager.
Write payload checksum paths relative to the payload root. Exclude only the root inventory file, not every matching basename.
Extract the package under a different path and verify complete file coverage before handoff. Include runtime configuration reached through inherited fixture setup.
Record each executable's CMake target and output path separately. A library target can share the executable's output name.
Build the executable target after its linked library changes. Verify claimed rebuilt ELF hashes against the previous package.
Freeze packages by removing write permission. Preserve execute permission on binaries and directories.
Target preflight must check executable permission and expected modes. A successful ldd check does not prove executability.
Keep CTest bookkeeping outside frozen payloads.
Verify the runtime user can create and write Testing/Temporary under the selected CTest directory.
Preserve a setup refusal separately from an executed test failure.

Use a persistent tmux session and a unique run directory for each attempt.
Launch terminating jobs directly through tmux with exec. An idle interactive shell prevents pane-process retirement after command completion.
Pass the tmux shell command as one argument when launching a saved script through Python.
Use `subprocess.run(['tmux', 'new-session', '-d', '-s', session, 'exec bash ' + shlex.quote(str(launcher))], check=True)`.
Do not pass `exec`, `bash`, and the launcher path as separate tmux arguments.
Check that the canonical runner created its receipts. A successful tmux call alone does not prove job startup.
For builds and test commands, use `bash scripts/run-command.sh CHECKOUT NEW_RUN_DIR COMMAND ARG...` inside tmux.
Resolve that script from the installed skill. Pass arguments directly; do not construct shell code around exit-status variables.
Invoke the runner through bash, including when flock launches it. Do not rely on executable permission.
Test hosts have no installed Codex skills. Give them a verified external runner copy and record its absolute path.
Keep NEW_RUN_DIR outside CHECKOUT. For artifact-only jobs, use a dedicated staging directory as CHECKOUT, not its artifact parent.
Check those resolved paths and the exact runner invocation before starting tmux. Preserve any prelaunch refusal without claiming a test ran.
Read command.exit for the command result and wrapper.exit for the launcher result. COMPLETE means returned, not passed.
While CTest runs, its parent gate can report running with no blockers after individual tests fail.
Scan the complete current core log for completed CTest failures, timeouts, and skips before reporting status.
A recent tail can hide earlier failures. Keep known failures separate from current progress and later passing tests.
An empty blocker list does not prove that the running stage has no failures.
An interrupted wrapper may leave its child running. A missing command.exit remains unknown; inspect the exact child before retrying.
Use [scripts/run-worker.sh](scripts/run-worker.sh) for remote Codex launches in tmux.
Invoke the installed script with bash. File transfer may not preserve its executable bit.
Pass checkout, expected branch, model, task file, and a new checkout-local run directory as arguments.
Transfer this literal script; do not generate its body through an interpolated PowerShell string.
The script preserves the worker exit status and refuses an existing run directory.
The launcher owns launch.txt, exit.txt, command.exit, wrapper.exit, and terminal-status.txt.
Workers must not create or modify these receipts. Write findings in the task handoff instead.
An exit receipt cannot establish completion while the recorded worker process is still alive.
Keep the exact task, launch script, model, base commit, stdout, stderr, and final report.
Write the exit code even when the worker fails. Avoid set -e around the worker command.
Transfer multiline scripts as files. Do not embed nested Python and shell scripts in PowerShell command strings.
Use LF line endings for remote scripts.
Check transferred shell scripts for CR bytes and run bash -n before launching tmux.
When editing Linux source from Windows, preserve existing line endings or write new lines explicitly as LF.
Fail scripted replacements unless their required match counts agree with the inspected source.
Verify the resulting code before building or formatting.
Check authored notes for unintended control bytes before handoff.
Allow tabs, LF, and CRLF. Preserve raw evidence bytes separately.
Python write_text uses platform newlines unless configured. PowerShell Set-Content can add a BOM or CRLF.
Use explicit encoding and newline handling for hash manifests and transferred files.

Inspect tmux and the specific child process before restarting work.
An observation timeout is not process failure. Recheck the same handle.
If exec_command returns session_id without exit_code, poll that session before dependent operations.
Wait for scp to finish before hashing its destination. A partial-file hash does not prove transfer corruption.
Verify an existing immutable transfer destination before skipping it. Do not overwrite a matching read-only file on a shared receiver.
An empty or malformed exit record is not exit zero. Preserve the launcher and report unknown exit status.
Use terminal worker events and process state to establish completion when the launcher record is defective.
A queued correction must wait for the prior writer to exit and its terminal record to exist.
Never edit a live worker's owned files to fix a review finding.
Record that finding outside its source files and hand it to the next bounded attempt.

## Worker completion contract

A zero worker exit code does not mean its task is complete.
Before reporting completion, compare the result with every requested deliverable.
Implement requested tests; a test plan is not a substitute.
If work remains missing, label the handoff INCOMPLETE and list it explicitly.
Inspect the committed code for each behavior claimed in the handoff.
Use `git show COMMIT:PATH` to verify the relevant code path.
A successful edit script or build does not prove that an intended change exists.

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

Read [references/git-handoff.md](references/git-handoff.md) when checking in, pushing, or handing work to another worker.
Use its checked Git helper for ordinary staged commits and explicit box-git pushes.
The primary agent and Luna use the same receipt and review rules.

Origin for this project is dflanag3@box-git:/srv/data/git/spacial_desktop-clean.git.
Preserve repository remotes, history, and repository-restricted SSH keys.
Use repository-local identity David <david@code.contractors>.
Verify the current path, branch, remote, status, and staged diff before committing.

Stage explicit files. Never use git add . or git add -A for these large worker checkouts.
Exclude credentials, local/ run directories, databases, environments, and build outputs.
Include concise qualification summaries and artifact checksums. Keep raw evidence outside Git.
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
Record a checkpoint receipt: host, checkout, branch, base, commit, qualification status, and remaining dirty paths.
Record the destination ref and its verified remote commit after a push.
Distinguish LOCAL COMMIT, BOX-GIT VERIFIED, and INTEGRATED. One state does not imply the next.
If a push fails, preserve the local commit and report the failure. Do not recreate the commit to retry transport.
On a rejected push, fetch and inspect divergence. Do not overwrite the other worker's history.
GitHub publication remains a separate authorization decision.

## Frozen payload publication and recovery

A cross-parent directory rename can require write permission on both parents and the moved directory. The moved directory may need permission to update `..`. A frozen payload root may lack that permission.

Prefer publishing a writable private enclosing directory. Move the enclosing directory, not the frozen payload root. If that is not possible, pin the exact owned moved directory with a file descriptor. Temporarily grant owner write permission to that directory. Restore its original mode in `finally`. Preserve all frozen payload modes.

After publication, verify the pinned inode at its final name. Compare the complete final inventory with the expected inventory. Recompute report paths after every rename. Hash and inspect the final path, not a stale staging path.

If a command fails after a publication side effect, inspect the final destination before retrying. Verify its inode, hash, and complete inventory. Reconcile completed publication and preserve the earlier failure evidence. Do not blindly rerun deployment, transfer, or extraction.

Use deployment and recovery scripts whose hashes and review receipts match the intended artifact. Do not reuse an older same-version draft when a newer script repairs a recorded defect.

## Primary review and integration

Check source and tests against the production caller, server, and authority contract.
Review all changed files, including generated build registration and schemas.
For rejection regressions, use otherwise valid requests and check the intended rejection reason.
An arbitrary exception from malformed input does not prove an authorization or generation guard.
Use a successful authorized control case when needed to show the request can reach the intended behavior.
Inspect source-level error and lifetime paths before running expensive qualification.
Send concrete defects back with the failed invariant and required regression.
Keep evidence from earlier failures.

Freeze source hashes or a commit before distributing binaries.
After a build-input change, rebuild each executable that will enter the package.
Check source-derived build IDs when available. A sibling target build does not refresh every executable.
Compare the worker base with integrated fixes to imported fixtures and packaging before deployment.
Inspect a commit's tree before declaring a source file absent.
Use `git ls-tree` or `git show COMMIT:PATH`. A merge commit's displayed diff can omit files present in its tree.
Apply needed fixes only after the worker stops. Record the resulting source and rebuild affected artifacts.
Do not silently combine new fixture source with old binaries. Record and review any test-only overlay explicitly.
Verify artifact hashes after transfer to each test host.
Read root-created inputs with the actual caller's permissions before launch. Use sudo reads or pinned readable copies when required.
Verify artifact-only deployment identity through provenance, receipts, and source hashes. Test-host packages do not require a Git checkout.
Match reused XLibre receipt extension_sources to the candidate's patch, overlay, and protocol hashes.
Verify the actual server binaries and wrapper targets. An older passing server may lack a newly required capability.
Launch the qualified server wrapper when its receipt supplies patched graphics libraries or environment variables.
Matching the raw Xvfb hash does not verify that graphics dependency closure. Check the wrapper and library hashes too.
Check the runtime launcher's required broker configuration, delegated cgroup, helper paths, and service dependencies before qualification.
Use each suite's declared CMake launch environment. Record its delegation and resource-wrapper requirements before deployment.
For focused M08 closures, preserve full-gate setup wrappers and CTest metadata; see [focused qualification setup](references/focused-m08-qualification-setup.md).
When copying CTest metadata, preserve each registration's original working directory explicitly.
CTest derives implicit WORKING_DIRECTORY from the metadata location, so copying can change execution behavior.
Compare the full relocated inventory, including working directories, before executing tests.
Validate a new metadata-copy launcher without test execution before using it for runtime evidence.
Compare translated launcher variables with the selected qualified lane before launch, including sanitizer options and graphics extensions.
Before transferring a package to another authorized host, inspect host restrictions in its driver, preflight, installer, and verifier.
Check the selected host and its qualified runtime wrapper through that complete launch path.
Record excluded graphics coverage explicitly. A core-only sanitizer pass cannot replace graphics qualification.
Do not apply one suite's broker wrapper to all suites. Different fixtures can observe different cgroup ownership paths.
After a launcher-only setup failure, preserve the failure and rerun only affected cases with the corrected launcher.
Inspect imported fixtures for generated bindings, executable-adjacent runtime configuration, and helper binaries before packaging.
Trace helper launches through imported fixtures for the selected test methods.
Include reachable helpers in both target dependencies and the package manifest.
Verify a target-only build produces its required helpers before distributing the bundle.
Declare required generated-file targets in CMake. A successful executable build does not prove its test bundle is complete.
For production Python entrypoints, verify transitive protobuf imports in BUILD_TESTING=OFF artifacts too.
Keep one generator for each output. Move its ownership when needed instead of creating duplicate generation commands.
Do not unset required security configuration merely to make an artifact bundle self-contained.
Record missing prerequisites as setup failures. Preserve the production security boundary when correcting the launcher.
Run focused Debug and sanitizer checks on both test hosts when the affected boundary requires them.
Inspect output assertions and sanitizer diagnostics as well as process exit status.
Do not accept a test harness that exits zero after reporting failures.
Derive progress counts from completed result records. Do not infer counts from test order or names.
Use terminal suite summaries for final totals. Keep partial progress separate from completed suites.

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
For deadline regressions, require an observation bound that distinguishes absolute deadlines from per-fragment timeout resets.
Separate error detection time from bounded cleanup time. A later nonzero exit alone does not prove the deadline.
When builds fail on missing packages, fix and record normal dependencies before rebuilding.
After adding CMake targets, run an explicit configure before requesting those targets from an existing Makefile build.
When transport quoting corrupts scripts, transfer script files instead of adding another escaping layer.
Use SSH with Python code on stdin for nested fleet probes. Pass subprocess arguments as lists.
Do not diagnose missing files or mounts from a failed quoted remote command. Repeat through the checked transport first.
Windows OpenSSH joins remote command arguments into shell text. PowerShell argument quotes may not survive that step.
Use separate `rg -e PATTERN` arguments for simple searches. Do not send an unquoted regex containing `|`.
Transfer an LF script for commands containing quoted spaces, pipelines, Python code, or tmux format strings with `#`.
If a search waits on stdin, inspect and stop its exact owned process before retrying through a script.
When two branches change the same API, merge once and test the combined API before adding more dependent work.
Keep improvements specific to observed failures. Do not create unrelated approval or compliance procedures.
For fleet cleanup, read [references/artifact-cleanup.md](references/artifact-cleanup.md).

## Source and artifact separation

Keep all generated data outside source checkouts.
Use /home/dflanag3/spatial-artifacts for worker runs, receipts, packages, and retained evidence.
Use separate external build directories.
Configure legacy output links with scripts/configure_artifact_links.py.
Use explicit current branch pins for source coordination.
Do not clone the archived repository into active worker checkouts.
Check free space and projected output size before launches or transfers.
Report insufficient capacity before proceeding.
The checkpoint helper validates staged source with repository_data_guard.py.
The new box-git repository also validates incoming commit trees.
Keep old qualification records through source mappings, not duplicated raw files.
