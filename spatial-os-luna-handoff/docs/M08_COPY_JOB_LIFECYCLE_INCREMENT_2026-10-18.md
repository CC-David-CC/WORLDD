# M08 cabinet copy-job lifecycle increment

Status: implementation candidate; unqualified. This file describes the current branch only.
It does not change the locked M08 copy contract.

## Implemented candidate behavior

- File copy START, POLL, and CANCEL use the cabinet FileEngine request path.
- A job belongs to its workspace and the fixed-width issuer and actor identifiers of its initiating principal.
- POLL and CANCEL repeat fresh FILE_COPY authority checks. A denied request from the matching principal stops the worker and attempts identity-checked staging cleanup before it returns UNAUTHORIZED. A foreign principal cannot stop the owner's job.
- When the broker authority watcher consumes an authority change, it rechecks active copy-job owners and cancels jobs that lost FILE_COPY. This path does not wait for another POLL or CANCEL. If the change is observed during file-action dispatch, reconciliation runs after that dispatch returns.
- Job start validates source and destination handle generations and filesystem identities.
- Directory copies reject a destination parent inside the source before creating staging.
- Copy limits are at most 4 GiB, 200,000 entries, and 16 active jobs per FileEngine.
- An entry is each copied filesystem object, including the selected source root and every descendant. The focused 4-entry tree rejects a limit of 3 and completes at a limit of 4.
- Output stays in a private sibling staging directory.
- Worker rejection before publication returns additive terminal FAILED with a typed FileActionError code only after ProcessScope emptiness and staging cleanup are proven. Otherwise it returns UNCERTAIN.
- Publication uses no-replace rename after worker completion and identity verification.
- Cancellation accepts completion only after ProcessScope returns the expected stopped identity.
- Cleanup rejects mount/device changes, symlinks, hard links, special files, over 128 levels, or over 200,000 entries.
- Finished jobs expire after ten minutes and may be evicted to keep the job table bounded.
- FileEngine shutdown attempts to stop workers and clean staging.

## Current verification

Build and regression results must be added after the focused tests complete.
No runtime or host acceptance result is claimed here.
The regression source covers positive regular-file and directory copies, byte/mode/digest checks, unchanged source, repeated terminal polls, no-overwrite, foreign-principal denial without disturbing the owner job, live cancellation, denied-authority cleanup, revocation before publication, nested-destination rejection, and the entry-limit boundary. The dedicated no-follow-up-request revocation regression starts a large copy, verifies its worker is active, revokes the actor, then checks that the worker exits, staging disappears, and no destination is published without another job request. Test-host runs remain pending, so this behavior is implemented and source-tested but not runtime-qualified.

## Contract work that remains

The combined candidate now persists durable job identity and terminal receipts. Recovery and retained parent generations are described in M08_COPY_JOB_JOURNAL.md. These paths remain unqualified until resource-backed tests run on both test hosts.
Live progress remains unimplemented. WorkerResult returns final outcomes only. RUNNING counters must not be interpreted as completed work.
Cancellation and poll share the FileEngine mutex. Cancellation can delay another FileEngine request while ProcessScope proves the worker empty.
