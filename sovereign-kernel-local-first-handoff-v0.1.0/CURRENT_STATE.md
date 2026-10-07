# Implementation status — 0.1.0

This status is intentionally stricter than “the tests are green.”

## Implemented reference behavior

* `kernel/canonical.py`: SK-JSON-1 restricted canonical encoding and strict wire parsing.
* `kernel/objects.py`: one envelope, domain-framed SHA-256 identity, byte verification.
* `kernel/crypto.py`: Ed25519 signatures through `cryptography`, framed contexts/principals.
* `kernel/authority.py`: finite capability chains, structural subtree scopes, explicit
  subject proof, attenuation, injected time, deterministic authorization decisions.
* `kernel/store.py`: ChaCha20-Poly1305 encrypted object rows, integrity checks, fixture
  export/import, inventory, graph reachability and SQLite transaction rollback.
* `kernel/log.py`: local signed linear fixture receipts and caller-supplied head checking.
* `kernel/simulation.py`: trusted fake-effect simulator; atomic ancestor accounting,
  request idempotency and fixture revocation; no real tool execution or agent process.
* `kernel/readiness.py`: a verifier for synthetic readiness statement tests. The caller is
  required to validate/pin the full evidence closure first; the OS supervisor is absent.
* `native/no_network.c`: Linux x86_64/aarch64-source launcher denying socket-related
  operations before exec; **only x86_64 has been run for this delivery**.
* `tools/run_suite.py`: records results and signed reference evidence, never LOCAL_READY.

The delivered reference suite contains **154 test methods**, all observed passing in the
recorded Linux native run. Eight additional native socket-denial probes passed. The target
specification enumerates **99 further acceptance cases**, not falsely counted as implemented
tests. Authoritative run details are in `evidence/release/summary.json`. No test is counted
twice merely because it also ran through the portable path.

## Deliberately NOT completed

No production root-key custodian; no agent OS confinement; no production execution broker;
no authenticated revocation API; no state-authority epoch migration; no threshold recovery;
no production anti-rollback anchor; no GC deletion implementation; no general filesystem
adapter; no complete resource exhaustion campaign; no independent second-language
canonicalizer; no complete reproducible binary/dependency closure; no readiness supervisor;
no transport, service, peer discovery, remote adapter, real LLM or real payment.

The `FakeBroker` is a trusted test simulator. Its log commits a fake effect, consumption and
receipt together, but it does not persist the full production authorization-evidence graph
and is not acceptable as an untrusted-agent RPC endpoint. `revoke_fixture` is test setup,
not a completed revocation authority path. `LocalLog` verifies fixture signatures and links,
not the production broker's entire historical policy/authority transition.

Store ciphertext is authenticated. Content IDs, counts, timing, row structure and the
SQLite filename remain observable. The fixture export intentionally contains plaintext.
No private user data is appropriate for this prototype.

## Delivered environment observations

Python 3.13.5, cryptography 46.0.4, Linux x86_64 were exercised in this environment.
The native socket-denial probes passed, including direct syscalls and exec inheritance.
Creating a fresh network namespace with `unshare -n` was denied by the host environment.
Namespace, mount isolation, device isolation, Windows confinement and hardware anti-rollback
are therefore **not claimed tested**. The native launcher is a narrower measured boundary,
not a substitute for the remaining full network-zero/agent acceptance cases.

The package contains a spec for these missing properties rather than fake PASS records.
No `LOCAL_READY` artifact has been issued. `REFERENCE_PASS` means only the included fixture
suite passed on the recorded build under the recorded test conditions.
