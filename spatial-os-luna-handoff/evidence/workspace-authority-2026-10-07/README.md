# Workspace authority qualification

Base commit: `a5812399b1109d180d2f73b299826791963c13de`.
The source hashes in each host's `source-audit.json` identify the tested increment.
All 154 hashes match both hosts and the reviewed repository files.

| Host | Build | P00-P10 | Core checks | Harness checks |
| --- | --- | --- | --- | --- |
| sb16 | Debug | 151 passed | 512 passed | 51 passed |
| sb16 | ASan/UBSan | 151 passed | 512 passed | 51 passed |
| sb16-2 | Debug | 151 passed | 512 passed | 51 passed |
| sb16-2 | ASan/UBSan | 151 passed | 512 passed | 51 passed |

Each baseline run has zero failures, errors, skips, or unrun cases.
The 19 new workspace-authority checks are included in the core count.
The focused run has 71 checks across workspace authority, world authority, workspace context, and process confinement.
The acceptance lock and locked assertions remain unchanged.

## Reproduction and scope

Run Linux commands only on sb16 or sb16-2.
Use the exact project XLibre path recorded in each build's `run.json`.
That record also includes source hashes, server hashes, and exact commands.
`p10-command.json` records the complete cumulative acceptance command.
The build uses two parallel jobs.

The qualifier runs `tools/qualify_core.py --through P07`, then the complete P00-P10 gate.
The sanitizer run adds `--sanitize --build-dir build-sanitize`.
The tests use real TLS enrollment, actor proofs, worldd, workspaced, bare Git repositories, and writable worktrees.
They execute real namespace-confined processes and inspect actual files and process trees.
They verify denial, persistence, revocation, and preservation of independent authority.
The process retry test delays a real issuer exchange across real actor revocation.

No model service was required. David's local-model hardware waiver remains active.
No system service, normal login session, or actual David identity changed.
Native graphics regressions use private project XLibre software displays.
These results do not qualify physical controls, GPU output, native performance, or a complete spatial login session.

## Counterexamples and corrections

`contract` records the old broker rejecting the new authority seed operation.
The earlier fixture attempts used a conflicting helper name and the wrong policy argument type.
Their logs remain under `fixture-error-1` and `fixture-error-2`.
`initial-build` records a compiler warning promoted to an error.
`initial-focused` records the existing denial-code regression; its assertion remains unchanged.
`race-contract` records the stale retry acknowledgement and two new fixture construction errors.
The corrected fixture uses byte-valued scope components and a right valid in either domain.
It preserves the cross-domain denial assertion.
`focused` records the final passing focused run on each host.

The first mutation setup omitted a generated Python binding.
`sb16/mutation-setup-failure` retains that error.
The corrected isolated mutation run generated the binding through its CMake target.
`sb16/mutation/mutation.json` records three deliberately broken source variants:

- Omitting process cancellation leaves revoked confined processes alive.
- Accepting one write right makes an unauthorized whole-project write succeed.
- Omitting retry reconciliation acknowledges a process revoked during status preparation.

Each independent counterexample detected its corresponding defect.
The primary tested source never contained these mutations.

## Remaining requirements

Native application observation and control require XLibre security identities and resource isolation.
Cabinet actions, bootstrap profiles, remote actor transport, and distributed revocation acknowledgement remain pending.
All complete M01-M16 and H01-H08 qualification items remain unfinished.
This increment does not establish full Spatial-M1 or unrelated LOCAL_READY targets.
