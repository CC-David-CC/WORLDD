# M10 qualification retry, 2026-10-30

Status: unqualified. Candidate `499bf4898e2abe0654d04b88d8ab022e9b2c0552` stopped before completing its required gate.

## Interrupted run findings

The Debug gate completed 103 of 305 cases on sb16. It passed 102 cases.
It completed 96 cases on sb16-2 and passed 94.
The runner preserved each failure and the interrupted gate receipts.

`workspace-file-action-storage-owner-btrfs` failed on both hosts.
Its retained worker record named a cgroup from an earlier run.
The current cgroup differed, so the storage broker rejected the stale parent.
The rejection is correct. The test reused persistent Btrfs state.
The retry will provision one new volume for each host and test profile.

`authority-supervisor-fencing` also failed on sb16-2.
The test injected an error after issuer activation committed.
It changed the supervisor signing key only after the child reported that error.
Automatic supervisor cleanup could run first and finish the request.
The test then read an unrelated request-zero reply instead of its stop reply.
This exposed an uncontrolled test ordering race.

The relay now holds the activation result behind a test event.
The test changes the key while the result remains held, then releases it.
The same barrier covers lost-reply and typed-error cases.
No production code or acceptance assertion changes.
The relay and activation waits allow up to thirty seconds under host load.

## Timing and qualification disposition

The 250 ms freshness measurement applies to movement refresh.
The approved one-third-second processing window applies only to the scale fixture.
Ordinary commands keep their own longer deadlines.
The copy test keeps its sixty-second writer window and 300-second operation deadline.

The first-pointer reply-close issue remains a conditional exception.
It is the only approved exception, and it is not counted as a pass.
Every other required case must pass on both hosts in Debug and ASan/UBSan.
See `M10_REPLY_CLOSE_KNOWN_ISSUE_DISPOSITION_2026-10-30.txt`.

## Retry checks

1. Validate the relay barrier and Python syntax.
2. Create unique Debug and ASan/UBSan Btrfs volumes on both hosts.
3. Run `authority-supervisor-fencing` and `workspace-file-action-storage-owner-btrfs` on both hosts.
4. Freeze the corrected candidate and run the complete Debug and ASan/UBSan gates.
5. Run the required cross-host gate after both local gates pass.
