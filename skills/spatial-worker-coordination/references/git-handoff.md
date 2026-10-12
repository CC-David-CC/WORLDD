# Git checkpoints and handoffs

Use this procedure for Luna workers and primary-agent check-ins.
The assigned checkout must have one writer. Stop the coding worker before controller-owned commits.
Do not stop unrelated workers in separate checkouts.

## Check in a task

Read the assignment and inspect the complete diff.
Record tests as passed, failed, blocked, skipped, or unrun.
Use UNQUALIFIED for drafts. Use FOCUSED-PASS only when the recorded focused checks passed.
Neither label proves milestone completion.

Stage exact reviewed paths with `git add -- path1 path2`.
Inspect `git diff --cached` and `git diff --cached --check`.
Check evidence for secrets before staging it. The helper does not detect secrets or review code.
Keep the message and receipt in an ignored local run directory.
Use a new receipt path for every operation.

Run the helper with the full reviewed HEAD, expected branch, and each staged path:
Resolve `git-checkpoint.py` from this skill's `scripts` directory.
Use `python3` on Linux when `python` is unavailable.

```text
python git-checkpoint.py commit --checkout CHECKOUT --branch BRANCH --head FULL_SHA --qualification UNQUALIFIED --message-file MESSAGE_FILE --receipt COMMIT_RECEIPT --path FILE1 --path FILE2
```

The helper checks repository identity, destinations, staged scope, tree, and parent.
It also checks effective author and committer identities for environment overrides.
It does not stage files, discard changes, merge branches, or publish to GitHub.
If the sandbox denies Git writes, hand the paths and diff to the controller.
Do not repeatedly retry a denied mutation.

Push the created commit separately:

```text
python git-checkpoint.py push --checkout CHECKOUT --branch BRANCH --head CREATED_SHA --qualification UNQUALIFIED --receipt PUSH_RECEIPT
```

Keep the default origin for project operations.
The origin override exists for isolated helper tests only.
A failed push preserves the local commit. Inspect the receipt before retrying transport.
On divergence, fetch and review. Do not force-push or recreate the commit.
The push receipt verifies the exact remote ref. It does not prove integration.

The helper refuses whitespace failures, including raw evidence warnings.
Preserve raw evidence bytes. Use reviewed manual Git commands for a documented narrow exception.
Record the same receipt fields and verify every command's exit status.

## Handoff to another worker

Give the recipient the verified remote ref and full commit ID.
Include the base commit, owned paths, test evidence, known failures, and next safe command.
Fetch the explicit task branch into a separate checkout.
Verify its commit before building on it.
Do not infer completion from worker exit zero, successful compilation, or a pushed branch.

## Repair recurring process failures

Preserve the first failing log and its exact input.
Name the failed invariant and fix the narrowest reusable mechanism that allowed it.
Add a behavioral regression when code automates that mechanism.
Validate the correction before installing it on coding hosts.
Verify installed hashes. Active workers adopt changes at their next bounded attempt.
After changing the Git helper, run `python scripts/test_git_checkpoint.py` from the skill directory.
These checks use temporary repositories and local file transport only.

Observation timeout does not authorize starting a duplicate job.
For shutdown, send one accepted stop request and observe the recorded process identity.
Do not resend stop requests merely because process exit is delayed.
Retry a connection only when no request was sent and the protocol permits it.
Keep action success separate from cleanup success.

Use concrete directories with `rg -g` filters in PowerShell.
PowerShell does not expand shell-style glob arguments for native programs.
Check native exit codes before dependent commands.
