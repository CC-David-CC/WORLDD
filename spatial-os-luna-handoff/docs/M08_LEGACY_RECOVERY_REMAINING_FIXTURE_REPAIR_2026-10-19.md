# M08 legacy recovery fixture correction - 2026-10-19

Status: source only, UNQUALIFIED. Base: 07e0dec8904f0ffba47be7bb89bccf4854807535.
Branch: m08/legacy-recovery-remaining-repair-20261019.
This changes only tests/core/test_file_action_broker.py and this note.
No production guard, protobuf schema, or locked acceptance test changes.

The prior Debug matrix ran 11 supervised legacy cases on sb16 and sb16-2.
Each host passed eight and failed the same three.
Preserved failure evidence is under
/home/dflanag3/spatial-artifacts/m08-legacy-recovery-focused-daa077e-20261019/runs-matrix/Debug.
The two replacement cases left a RUNNING record after authentic supervisor cleanup.
They then placed a foreign root at the old same-boot path.
store.cpp refuses startup for any retained or active parent identity mismatch that matches a nonterminal job.
It raises "Active copy parent identity cannot be verified" before file-action dispatch.
Changing that production guard would weaken the boundary.

The two existing replacement cases now seed a valid terminal UNCERTAIN receipt after checked cleanup and issuer retirement.
They retain the prepared result and preserve the no-proof versus old-proof distinction.
Only then do they create the foreign replacement root and worker cgroup.
They require quarantine of workspace one, unchanged terminal record bytes, preserved foreign inodes, prepared output inode and payload, no publication, and workspace-two availability.
The missing-scope case remains nonterminal with no foreign replacement.
The independent-generation case already seeds terminal UNCERTAIN.
Its three authentic restarts now require the documented quarantine error on POLL and browse.
It still checks byte-identical journal evidence, stage identity, foreign-principal denial, and a real workspace-two copy and process in each generation.

One new case keeps RUNNING with the old drain proof after authentic cleanup.
It creates the foreign whole-parent identity and launches an actual replacement supervisor.
The candidate must exit at startup with the exact parent-identity error.
The test checks that no terminal receipt was fabricated, the prepared output inode and payload and foreign inodes remain, and no destination appears.
Its test-owned empty foreign cgroups have exact inode checks before cleanup.
The case consumes the candidate supervisor's checked cleanup receipt and retires its issuer generation.
The normal fixture lifecycle closes any remaining handles.
This case makes the whole-broker refusal distinct from terminal-worktree quarantine.

The new method is in load_tests and the same verified 224 MiB workspace-one / 64 MiB workspace-two source budget.
The selected supervised registration grows from 11 to 12 methods.
The file-action method total grows from 42 to 43; the separate storage quota case remains one.
The existing 43 method bodies are unchanged apart from adding the new method; two helper oracles and prepared-state construction changed explicitly.

Before runtime, the primary agent should review the diff and exact constructor-failure cleanup path.
Then run the three formerly failing cases plus the new refusal case on sb16 and sb16-2 in Debug.
The former failures check terminal quarantine and independent work; the new case checks startup refusal.
After they pass, run the related supervised 12-case selection and ASan/UBSan on both hosts.
The full M08 gate remains pending.
No runtime test, build, deployment, or pass claim is part of this source-only repair.
