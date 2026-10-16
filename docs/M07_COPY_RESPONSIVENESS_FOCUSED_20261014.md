# M07 copy responsiveness: focused Debug result

Status: UNQUALIFIED for M07. Three isolated Debug runs passed on sb16.

The broker advanced copies before dispatching ordinary root requests.
The reproduced status call exceeded five seconds during an owner BEGIN exchange.
The issuer remained alive. Timing-only reproduction and the failed image remain retained.

The broker now services bounded status and existing authority reads during copy owner waits.
It queues new effects and guards against recursive root dispatch.
Status checks fresh actor rights, policy, state mappings, resource profiles, and the authorization fence.
It checks the pinned owner's process liveness without nesting a storage exchange.
Owner configuration remains immutable until a stopped migration.
Every copy effect retains full owner-catalog revalidation and exact receipt handling.
The contract records this distinction explicitly. No deadline or locked acceptance assertion changed.

The regression requires a status reply to complete wholly within the delayed owner exchange.
It retains failed status intervals and relay audits before cleanup.

| sb16 Debug attempt | Test time | Maximum status time | Replies inside owner wait |
|---|---:|---:|---:|
| 1 | 23.359 s | 0.311 s | 30 |
| 2 | 23.580 s | 0.330 s | 30 |
| 3 | 23.401 s | 0.325 s | 30 |

Each run executed one named method and reported OK with command/wrapper exits 0/0.
Each copy published. The real owner reply was delayed approximately 4.101 seconds.
The primary independently checked the raw results, timings, restoration, and unit cleanup.
Helpers were restored. Volumes were unmounted and detached. Cleanup failures were empty.

Runtime source: base 9294323c75fc20dfacd5f4f572e0d3ab8049d8b1 plus the five reviewed paths in candidate-manifest.json.
Source manifest: f2b86880e4fb48bd51fbdbba535f30bb166caf949eba626fc6b3770869fd24e3.
Broker ELF: 8551d95361168b67c7039a008d2be92b3be8090d18b6b0a8c156ed8a4c662cc4.
Package metadata: 8370b5eaef419a11bdb75faf7ddd373db1ec9f0a16b2e63043b94f985371419a.
All other packaged executables match the recorded engine19 hashes.
This report is added after the frozen runtime source and does not change its code.

Evidence on sb16: /home/dflanag3/spatial-artifacts/runs/m07-engine20-copy-response-debug-v1-run{1,2,3}.
Attempt 1 uses m07-engine20-copy-response-debug-v1-evidence.
Attempts 2 and 3 use m07-engine20-copy-response-debug-v1-attempt{2,3}-evidence.
Primary audit script: /home/dflanag3/spatial-artifacts/m07-engine20-primary-collect-runs.py.
Earlier timing-only failure: /home/dflanag3/spatial-artifacts/runs/m07-copy-response-timing-20261014-run1.

Pending: related broker/owner regressions, root transport regressions, both-host Debug and sanitizer copy checks,
complete copy faults/recovery coverage, combined source integration, freeze, and the full M07 gate.
These three runs establish focused responsiveness only. They do not qualify M07.
