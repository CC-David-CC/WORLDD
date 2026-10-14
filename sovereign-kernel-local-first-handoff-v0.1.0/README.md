# Sovereign Kernel — local-first conformance Codex handoff

Version **0.1.0** · Prepared **2026-10-06** · Owner: David Flanagan

**Deliverable:** an implementation-grade testing specification, a runnable local reference,
negative tests, a native Linux socket-denial launcher, machine-readable acceptance work,
and bounded Codex phases. This is not a Deep Research request or a deployed security product.

The first objective is to establish the bytes, hashes, signatures, authority algebra,
accounting and persistence semantics **before any peer connection exists**.

## Start here

Read `AGENTS.md`, `CURRENT_STATE.md`, `docs/TESTING_SPEC.md`, then `START_CODEX.md`.
The implementation status is deliberately separate from the target acceptance contract.
A green reference suite **does not issue LOCAL_READY**. No networking implementation is
included. Pending gates cannot be waived by editing a boolean or by a successful LAN demo.

## What runs now

The Python reference implements a restricted canonical JSON profile, one immutable object
envelope, SHA-256 content IDs, Ed25519 signatures, finite attenuating capability chains,
a deterministic authorization evaluator, an encrypted SQLite object store, a signed linear
fixture log, an atomic fake-effect budget simulator and a readiness-statement verifier.

The native Linux launcher installs its filter **before Python starts**, rejects inherited
socket stdio, closes inherited non-stdio descriptors, drops effective Linux capabilities,
and denies socket creation including Unix sockets, relevant networking syscalls, and
`io_uring` entry points. This is a socket-denial boundary for a trusted test harness;
it is **not a complete adversarial-agent filesystem/process sandbox**.

## Run locally

Prerequisites: Python 3.11+, the pinned `cryptography` package, and for the Linux native run,
a C compiler and Linux headers. No Docker, Node, server, cloud credential, LLM, wallet,
peer, or API token is needed. Test signing keys are public disposable fixtures.

Dependency preparation is outside the kernel's network-zero lifecycle. Prefer a prefilled,
verified wheelhouse. No included command downloads dependencies or opens a service.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --no-index --find-links /path/to/verified-wheelhouse -r requirements.txt
PYTHON="$PWD/.venv/bin/python" sh tools/run_local.sh
python tools/verify_evidence.py evidence/latest
```

When dependencies are already available in the selected interpreter:

```sh
PYTHON=python3 sh tools/run_local.sh
```

The shell script compiles the launcher to a temporary file and removes it afterward.
It does not install or enable a service and does not change firewall rules.

Windows/portable semantic development, **not network-zero qualification**:

```powershell
py -3 tools/run_suite.py --out evidence/windows-reference
py -3 tools/verify_evidence.py evidence/windows-reference
```

An absent compiler, unsupported native ABI, denied seccomp installation or failed probe is
a blocker, not grounds to fall back silently. WSL tests qualify the WSL process only, not
a Windows agent. Native Windows confinement is an unimplemented target.

## Output and guarantees

`evidence/latest/` contains canonical, hash-addressed build inputs, individual test results,
environment/probe observations, requirements and a signed `local-conformance-run` object.
`run.json` is the convenient entry point. It is signed with an ephemeral test-evidence key,
**not your root key**. `verify_evidence.py` checks self-consistency from local bytes; it does
not claim that a self-signed report is an independent security attestation.

Expected current readiness output: `NOT_ISSUED`, even when every reference test is green.
See `evidence/release/summary.json` for the actual delivered run, rather than assuming a
count in a README is the current result. Distribution hashes are in `SHA256SUMS`.

## Important design decisions

* Hashing commits to bytes; passing tests establish limited conformance evidence.
* A resource descriptor identifies the enduring resource; revision objects identify its
  changing content. Both use the same object envelope, not competing ownership models.
* Delegation may only narrow rights. Consumption also charges every ancestor atomically,
  so many individually valid child grants cannot multiply a shared allowance.
* A linear local receipt log is sufficient before multi-writer replication exists.
* An old valid prefix is still a valid hash chain. Detecting full rollback requires an
  independently trusted checkpoint outside the rollback domain.
* The primitive process never acquires networking. A later, separate supervisor may
  authorize a restricted transport process after all local gates are satisfied.

## Package map

| Path | Purpose |
|---|---|
| `docs/TESTING_SPEC.md` | Normative local acceptance contract and exact failure conditions |
| `docs/ARCHITECTURE.md` | Minimal model, trust boundaries and deliberate design corrections |
| `docs/THREAT_MODEL.md` | What the proof/test claims do and do not cover |
| `docs/NETWORK_BOUNDARY.md` | First two-node canary; unavailable until local acceptance |
| `docs/SOURCES.md` | Primary references and architecture provenance |
| `tasks/acceptance.json` | Executable test inventory plus explicitly pending acceptance cases |
| `tasks/gates.json` | Directed gate dependency data, not a picture |
| `tasks/codex_tasks.json` | Ordered work, prerequisites, artifacts and stop conditions |
| `schemas/` | Draft JSON Schema contracts, with semantic invariants in the spec |
| `kernel/`, `tests/`, `vectors/` | Runnable reference and test fixtures |
| `native/`, `tools/` | Local enforcement, runner and evidence checking |

Start Codex with the supplied prompt and complete **P00 only** first. Do not turn this
handoff into a broad networking, blockchain, DAO or agent-platform implementation.
