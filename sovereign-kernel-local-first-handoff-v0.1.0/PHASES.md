# Gated implementation order

The gate graph is in `tasks/gates.json`. Phases organize work; they do not override gate
semantics. None of P00–P06 permits a peer socket or service. Preparation downloads are not
part of a local kernel run and require separate permission.

| Phase | Work | Exit artifact | Stop condition |
|---|---|---|---|
| P00 | Reproduce and inspect delivered baseline | Verified local reference evidence + defect list | Stop; no new features |
| P01 | Freeze encoding/protocol profiles, independent vectors, dependency closure | Versioned vector pack + independent byte comparisons | Any parser differential blocks progress |
| P02 | Harden authority, policy, resource/version distinction and budgets | Model/property/mutation evidence | Any privilege or accounting counterexample blocks |
| P03 | Implement one local transactional broker and complete replay evidence | Reconstructible state root + failure-injection evidence | No acknowledged unreceipted or unaccounted effect |
| P04 | OS-separated custodian, broker and adversarial dummy agents | Native sandbox and network-zero evidence | Any alternate credential/IPC/file/device route blocks |
| P05 | Recovery, current authority epochs and independent rollback anchor | Recovery/rollback qualification artifacts | Rollback ambiguity cannot be called PASS |
| P06 | Independent readiness supervisor and frozen-runtime startup binding | First genuine locally verifiable LOCAL_READY | No missing gate, stale root or self-report-only trust |
| P07 | Explicitly authorized two-node immutable canary | Compatibility transcript + rejection cases | No mutable replication or real adapters |

Each phase adds tests to the same primitive model, not a “production path” beside a test
model. Replace the trusted fixture simulator with a broker at the genuine untrusted-process
boundary. Retain its counterexamples and common authority evaluator.

P07 is a later phase, not part of this requested local implementation milestone. Distributing
consumption or mutable authority requires a separate protocol design; do not stretch a local
SQLite lock into a claim of distributed exactly-once execution.
