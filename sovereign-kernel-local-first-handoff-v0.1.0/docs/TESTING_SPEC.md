# Sovereign Kernel — local-first testing specification

**Protocol profile:** SK-LOCAL-1, proposed v1. **Handoff:** 0.1.0.
**Normative target, not a claim of completion.** See CURRENT_STATE.md for the delivered
reference. Case inventory and gate dependencies are machine-readable under `tasks/`.

## 0. The acceptance claim

Before any peer, discovery mechanism, transport, cloud adapter, real agent, payment rail or
network service can run, the owner must be able to reconstruct and verify locally:

1. The exact canonical bytes and object IDs used by the kernel.
2. The exact identities, signatures, capabilities, constraints and policy decisions.
3. The local authoritative state, consumed budgets, revocations and ordered receipts.
4. The measured implementation, pinned test inputs and complete evidence for the declared
   platform/isolation profile.
5. A fresh readiness assertion bound to those measurements and checked by an independently
   controlled supervisor that alone can launch the later network process.

This is conformance evidence under stated assumptions, not a mathematical proof that an
arbitrary program contains no bugs. A hash authenticates a byte commitment; it cannot certify
the semantics of the bytes. A cryptographic signature authenticates the signing key's
assertion; it cannot prove that the signer really performed the tests.

**Hard invariant:** a passing network integration test can never replace a failing local
primitive case. No peer data or external service is necessary to pass a local gate.

## 1. Small state machine, explicit evidence DAG

Use these lifecycle states, not a boolean:

```text
UNQUALIFIED
    | complete local conformance evidence for frozen inputs
    v
LOCAL_QUALIFIED
    | independent supervisor verifies evidence closure, live bindings and freshness
    v
LOCAL_READY
    | separate, explicitly authorized, tightly scoped transport lease
    v
TRANSPORT_PROBE
```

The starter can produce only a `local-conformance-run` with `REFERENCE_PASS` or
`REFERENCE_FAIL`. It cannot make the first transition above. Test matrices are not runtime
permissions. Per-primitive progress is a dependency DAG of assertions; it does not require
one global state per test family. This avoids awkward orderings such as “KEYED” before the
serialization and signature test oracle is known to work.

Two axes stay separate: **conformance of a particular build** and **validation of a particular
live state**. Re-running the entire fuzz suite after each document edit is unnecessary.
A startup verifier checks a fresh state against a still-valid build qualification. Code,
policy, schema, suite, dependency or relevant authority changes invalidate their associated
assertions. State transitions invalidate a snapshot-bound startup claim, not the mathematical
meaning of every past content hash.

Cases: `GATE-101` missing evidence blocks; `GATE-102` stale inputs block; `GATE-103` partial
results block; `GATE-104` no networking module present in P00–P06; `GATE-105` changed test
requirements cannot be authorized by the same untrusted process being tested.

## 2. Network-zero bootstrap

### 2.1 Target enforcement

The local test process MUST start in a target-specific OS boundary installed before any
untrusted module, plugin, `.pth` hook, model, test extension or agent is loaded. Its runtime,
fixtures and dependencies are already local. No installation fallback, DNS, telemetry,
certificate-status fetch, clock synchronization or remote attestation service is allowed.
Use reviewed library code; do not attempt to make Python monkeypatches a security boundary.

For the initial Linux target:

* Deny AF_INET, AF_INET6, AF_UNIX and other socket creation, including raw native calls.
* Close all nonapproved inherited FDs and reject socket stdio. No socket activation.
* Deny alternate network entry points such as `io_uring` unless an independently justified
  allowlist demonstrates they cannot submit network work.
* Restrict devices, writable runtime paths, process inspection and IPC to unrelated local
  services. Provide an isolated fixture filesystem and dedicated unprivileged identities.
* Use a separate network namespace with no configured external interface as defense in
  depth when the target allows it. Do not accept loopback networking as necessary for
  primitive tests; anonymous pipes and ordinary test files are sufficient.
* Drop privileges and prevent exec-based regain. Apply the boundary across spawned children
  and threads. Avoid importing test plugins before the boundary exists.

The delivered C launcher implements socket denial, descriptor cleanup and privilege
reduction; it does not implement the complete mount/process/device sandbox. Those missing
properties remain mandatory acceptance cases rather than being silently assumed.

### 2.2 Required evidence

Record launcher source/build identity, OS/kernel/ABI, isolation profile and configuration,
FD audit, native-denial probe results, sandbox-start boundary, and command/environment inputs.
A launcher environment variable is not proof of enforcement. Use actual negative operations
and independently observed restrictions. Tests may deliberately attempt forbidden socket
creation; expected denial is success. They must never send an application packet.

`NET0-101`: direct libc and raw syscalls denied.
`NET0-102`: inherited IPv4/IPv6/Unix sockets rejected or closed.
`NET0-103`: subprocess/exec/alternate ABI cannot bypass restriction.
`NET0-104`: `io_uring`, namespace changes, descriptor theft and local proxy paths denied.
`NET0-105`: fixture mount, device, FIFO/shared-memory and helper-process routes reviewed.
`NET0-106`: unsupported target profile blocks readiness; no silent portable fallback.
`NET0-107`: no source import, test discovery or dynamic-loader hook precedes enforcement.

**Limit of claim:** a trusted-host socket-denial test is not proof of no side channels, no
hostile-admin access or no communications through an allowed file/stdio channel. Declare
what the boundary actually rules out. Absence of a packet capture event alone is insufficient.

## 3. Canonical bytes before hashes

### 3.1 SK-JSON-1 profile

The proposed initial profile deliberately uses a **restricted JSON subset** instead of
pretending `json.dumps(sort_keys=True)` handles arbitrary cross-language JSON semantics.
It follows the relevant JCS string/literal/ordering conventions within its restricted
ASCII-key, safe-integer domain; it is not a complete implementation of RFC 8785 [S1].

| Concern | Rule |
|---|---|
| Object keys | Printable ASCII, length 1–128, unique; lexical ascending order recursively |
| Strings | Valid Unicode scalar values, UTF-8; preserve code points; no implicit normalization |
| Integers | Exact values from −(2^53−1) through +(2^53−1); no float tokens or negative zero |
| Floats | Forbidden, including NaN, infinities and exponent encodings |
| Booleans | Distinct type, never accepted as numeric quotas, versions or timestamps |
| Money/counters | Integer smallest units plus an explicit unit/currency identifier |
| Time | Integer UTC Unix seconds; injected trusted fact, never read inside pure policy code |
| Arrays | Order preserved; a schema-declared set must already be sorted and duplicate-free |
| Null | Distinct from absence; schemas choose explicitly; no automatic defaults |
| Bytes | In typed bodies, a schema-defined lowercase hex or unpadded base64url string; no ad hoc automatic conversion |
| Whitespace | None between serialized tokens; no BOM or trailing newline in hashed canonical bytes |
| Escapes | JSON-required escapes; lowercase control-code hex; do not escape ordinary Unicode |
| Limits | 1 MiB canonical object, nesting ≤32, traversal budget ≤20,000 values in this reference |
| Versions | Exact known envelope/schema/suite version; reject unknown values |

A string value `é` and the two-code-point value `e` + combining acute remain different.
Application normalization, when genuinely required, happens before construction under a
separately versioned schema; the hash layer never silently changes text. The string `;`
remains meaningful input, not optional formatting. Programmer-visible byte distinctions
must not disappear through “helpful” parsing or normalization.

Strict wire verification accepts only canonical bytes. A UI/import normalization function
may accept ordinary JSON and produce canonical bytes, but it is not the verifier path.
Duplicate keys are rejected **during parsing**, not after a dictionary has discarded them.

### 3.2 Tests and independent oracle

Run positive golden literals, map permutations, nested structures, escaped control
characters, non-BMP Unicode values, null/absence, integer endpoints, rejected floating
values, duplicates, malformed UTF-8, surrogates, nesting/size limits and cyclic in-memory
structures. Test process restarts, input insertion orders, locale and timezone changes.

`CAN-101`: a second independent implementation yields exactly the same accepted bytes.
`CAN-102`: malformed-byte parser differentials; both implementations reject the same corpus.
`CAN-103`: declared set encoding checked by each relevant schema, not universal list sorting.
`CAN-104`: every mutable authority type uses a closed body schema; unknown fields fail.

Cross-language comparison is mandatory before distributed interoperability, but does not
require Node or a server. A small Rust/Go/C implementation, or another reviewed codec,
can read the checked-in fixtures over stdin with no network. Preparing its dependencies
happens before isolation. Never generate expected vectors by calling the implementation
under test during the test. Version literal expected bytes and their provenance in Git.

Delivered evidence includes literal canonical vectors and cross-Python-process checks;
independent-language and large differential campaigns remain pending.

## 4. Hashes, objects, resources and revisions

### 4.1 One envelope

```json
{"body":{"answer":42},"proof":null,"refs":{},"type":"fixture","v":1}
```

Every semantic object uses that envelope shape. Known type handlers constrain `body` and
`refs`. Every cross-object reference lives in `refs` with a schema-defined role name and a
strict content ID. Do not duplicate the same relationship in a second mutable field.

Object identity in this profile is:

```text
canonical = SK_JSON_1(object)
frame = ASCII("sovereign-kernel/object/sk-json-1/sha256/v1") || 0x00
id = "sk1:sha256:" || lowercase_hex(SHA256(frame || canonical))
```

The algorithm and profile are explicit. Unknown suite IDs are rejected. New suites require
an explicit migration/translation boundary; do not guess algorithms from digest length.
Plain raw-file SHA-256 checksums in build/distribution manifests are labeled raw checksums,
not confused with semantic object IDs. They have a different purpose and framing.

The proof field is part of the object's identity. Signing produces a different object ID
from its unsigned body. A change to a signature or signer changes the object ID. Parents
refer to the complete signed grant ID, not an unsigned payload hash vulnerable to signer
or envelope substitution.

### 4.2 Stable resource identity

A resource descriptor is immutable and may include a random creation nonce. Its content
ID is the resource ID. Revisions reference the descriptor and their predecessor(s). A
current-head event changes which revision is selected. Rights bind to the descriptor and
structural scope, not accidentally to the latest revision's content ID.

Example: changing a file body changes the revision and affected snapshot/root IDs. It must
not change the enduring resource ID or force every valid capability to be reissued.
Changing the actual descriptor creates a different resource; it is not a silent rename.

### 4.3 Tests

Same canonical bytes → same ID across processes. All tested bit mutations must be rejected
against the old ID. This finite test is not a proof of collision impossibility. Include
truncated/overlong/uppercase/unknown-algorithm IDs, incorrect lengths, dangling references,
parents with mutated children, swapped signed proofs, and moved provider copies.

`HASH-101`: canonical DAG verifier recomputes every object and reference from bytes.
`HASH-102`: resource ID stable across authorized revisions; revision ID changes.
`HASH-103`: provider locator changes never change resource or revision identity.
`HASH-104`: no ID defaults, unhashed mutable references, “trust cache” repair or algorithm guessing.

## 5. Keys, principals, signatures and encryption

Principal identity is a domain-framed hash of the raw Ed25519 public key. It identifies a
key, not necessarily a natural person. Authorization roots are pinned grants or explicit
trusted authority state, not every self-signed key a caller presents.

The signing input is the canonical envelope with `proof: null`, prefixed by the fixed
signature-domain frame and the raw public key. The proof records algorithm, public key and
signature in strict lowercase hex. The object type/version and all references are therefore
inside the signed payload. Use `cryptography`'s Ed25519 implementation, not tutorial curve
arithmetic. The RFC 8032 vector in `vectors/` is an independent primitive oracle [S2,S3].

Test import/export lengths and permissions, generated-key sign/verify, wrong public key,
modified type/body/refs, missing/invalid proof, forbidden suite, raw-versus-framed signature
confusion, replay across intent/receipt domains and deterministic Ed25519 fixture signatures.
Use actual generated entropy for real keys; deterministic seeds exist only in fixtures.

The reference store uses ChaCha20-Poly1305 with the object's ID as associated data. A nonce
must never be reused with the same key for different plaintext. The prototype uses random
96-bit nonces with a store uniqueness constraint; this is not a demonstrated fleet-wide
or rollback-safe nonce-allocation design. Before production choose and test a documented
nonce/key lifecycle that accounts for retries, restore, snapshots, key copies and volumes.
No deterministic “nonce = plaintext hash” shortcut is allowed [S4,S5].

`KEY-101`: key custodian runs outside agents; agent can never read/import root key bytes.
`KEY-102`: secret-file permissions, environment, command line, logs, crash dumps, child
inheritance and `/proc` access are tested, not merely code reviewed.
`KEY-103`: wrong-key, nonce, AAD, tag, truncation and envelope-substitution rejection.
`KEY-104`: entropy failure and nonce allocation exhaustion fail closed.
`KEY-105`: reject seed/private key material at public protocol and model-context boundaries.

Hybrid encryption, MLS, FROST and hardware keys are optional later extensions. They are not
silently simulated and counted as implemented simply because Ed25519 fixtures pass.

## 6. Capability algebra and sub-ownership

### 6.1 Grant model

A grant is a signed object of type `capability`. Its references identify the resource and,
for a child, its **complete signed parent**. Body fields identify subject, structural scope,
finite action set, finite destination set, unit, time interval, remaining delegation depth,
and aggregate call/unit ceilings. Every field is explicit; unknown constraints fail closed.

For a child C of P:

```text
resource(C) = resource(P)
issuer(C) = subject(P)
parent_ref(C) = id(P)
scope(P) is a component-wise prefix of scope(C)
actions(C) ⊆ actions(P)
destinations(C) ⊆ destinations(P)
unit(C) = unit(P)
[not_before(C), expires(C)) ⊆ [not_before(P), expires(P))
0 ≤ depth(C) < depth(P)
0 ≤ max_calls(C) ≤ max_calls(P)
0 ≤ max_units(C) ≤ max_units(P)
```

The root must be pinned in current authority state, and all signatures must verify. Every
ancestor must be current, unrevoked and valid at the injected evaluation time. A chain is
ordered root-to-leaf, bounded in depth/size, and contains no repeats. The terminal subject
must prove possession by signing the actual intent. Merely copying a grant does not authorize
an unrelated caller.

The initial algebra uses finite sets and conjunction, not arbitrary policy expressions,
regular expressions or glob inclusion problems. Depth zero naturally means nondelegatable;
no separate flag may contradict it. An empty action set is a valid no-authority grant.
There is no ambient “owner bypass.” An authorized recovery/root transition is a separately
verified domain operation, not a bypass flag in ordinary calls.

### 6.2 Scope is structural

`["workspace", "research"]` contains `["workspace", "research", "notes"]`; it does not
contain `["workspace", "research-backup"]`. No slash parsing, `%2e%2e`, `..`, empty segments,
case folding, symlink resolution or platform-dependent separators occur inside the algebra.
Filesystem/API/device adapters later map real objects to this namespace under their own
checked boundary. Test symlink/hardlink/rename races against opened handles, not string
prefixes. Do not authorize an OS path and then resolve a different path for execution.

### 6.3 Required counterexamples

Rights expansion; time expansion; currency/unit switching; scope escape; destination
expansion; parent substitution; reordered or missing ancestors; cycles; sibling substitution;
forged issuer; modified proof; root injection; excessive depth; cross-resource replay;
wrong terminal subject; invalid constraints; regained rights after multiple narrowing steps;
union of siblings presented as one chain; and aliases that resolve outside a scope.

`CAP-101`: property generator creates arbitrary valid chains, then mutates each narrowing
coordinate independently; all expansions fail.
`CAP-102`: each grandchild is no more permissive than every ancestor, not just the parent.
`CAP-103`: adversarial malformed-chain corpus with bounded parsing and verification work.
`CAP-104`: same evaluator covers filesystem subtree, API method, device function, agent tool,
and a spending allowance. Only the resource descriptor/action vocabulary changes.
`CAP-105`: an economic claim, token balance, DAO vote or receipt alone grants no operational
permission. Only an explicit authorized policy/issuer bridge can create a capability.

The delivered finite algebra is a reference for these tests. Its deterministic seeded
sampling is not a substitute for a reviewed property-based generator and mutation campaign.

## 7. Scarce rights require local serialized accounting

The subset invariant alone cannot enforce budgets. Suppose a parent allows 100 units and
two children each allow 80. Each child is valid, but aggregate consumption may not exceed
100. Issuing children does not mint independent balances.

For every candidate consumption with cost k:

```text
for each ancestor A, including the leaf:
    usage[A].calls + 1 ≤ max_calls[A]
    usage[A].units + k ≤ max_units[A]
```

The checks and increments commit in **one serialization domain and one transaction**.
Every consumption is charged once to every ancestor. A rejected request changes no spend,
effect or committed-success receipt. All costs are nonnegative integers with an exact
unit; the trusted tool contract determines or bounds cost, not the agent's after-the-fact
claim. Unknown costs require a bounded reservation before effect execution.

`BUD-101`: race many sibling requests at the shared limit; no aggregate overspend.
`BUD-102`: reservation + effect + receipt crash matrix, including cancellation and retry.
`BUD-103`: max_calls=1 behaves as a one-shot capability under concurrency and restarts.
`BUD-104`: each descendant and ancestor remains within its own ceiling.
`BUD-105`: monetary units never aggregate across currencies or precision scales implicitly.

The SQLite fake broker demonstrates two concurrent sibling calls, ancestor charging and
idempotency. It does not establish distributed spend safety. Replicated or disconnected
nodes cannot safely spend the same shared allowance without partitioned budgets, a single
authority, or an appropriate coordination protocol. That design is outside the local gate.

## 8. Object store, atomicity and recoverable state

### 8.1 Store contract

`put(object) -> id` computes and checks the content ID. `get(id) -> object` authenticates
ciphertext and recomputes the ID from exact bytes; it does not trust a database key alone.
Repeated insertion of identical content is idempotent. A corrupt existing entry is an error,
not an opportunity to silently overwrite history. Writes fail on malformed objects.

Private plaintext IDs may be useful locally but must not be exposed as public lookup tokens
without considering dictionary attacks and equality leakage. Do not claim that encrypting
rows also hides access patterns, timing, object counts or the database index. Independent
random encryption means ciphertext exports need not be deterministic. Compare decrypted
canonical identities, not random ciphertext bytes, across export/import.

The included `export` is intentionally a **plaintext fixture export**, not a safe user-data
backup command. Production export must explicitly protect secrets and capability material.

### 8.2 Authoritative state

Use one committed signed event sequence and deterministic reducers. Derived heads, usage
and indexes are rebuildable projections, not competing authoritative databases. A snapshot
root commits to resource heads, authority/epoch state, revocations and consumed budgets.
The log head and snapshot root have separate roles; do not create a circular object that
contains its own final hash.

For local deterministic effects, new objects, state transition, budgets, receipt and head
must commit atomically. Use a transactional store with tested durability assumptions rather
than coordinating independent “state.json” and “receipt.json” renames without a protocol.
SQLite transaction/atomic-commit documentation identifies both the behavior and underlying
filesystem/hardware assumptions; an `os._exit` test does not simulate all of them [S6,S7].

### 8.3 Crash matrix

Inject failures before object write, during ciphertext write, after object write but before
receipt, before head update, after head update but before commit, after commit but before
acknowledgment, and during recovery. Include process death, disk-full, short writes, locked
DB, failed flush, corrupt journal, truncated object and interrupted import.

Recovery must yield either the old complete commit or the new complete commit. A confirmed
commit survives a normal restart. No acknowledged operation may disappear within the
declared durability model. Repeat recovery twice and require identical results.

`STORE-101`: full object/signature/reference closure verifies, not just individual digests.
`STORE-102`: deterministic semantic export/import to an empty store.
`STORE-103`: GC marks all pinned snapshots, capabilities, policy/evidence references and
in-flight transaction roots; no reachable object is deleted.
`STORE-104`: GC interrupted at every boundary and concurrent pin/commit tests.
`STORE-105`: malicious paths/symlinks/hardlinks and ownership boundaries on storage files.
`STORE-106`: fault-injected durability campaign on the actual target filesystem profile.

GC reachability is demonstrated; GC deletion and full power-loss qualification are pending.

## 9. Signed receipts and replay

Use a linear log for the initial single authoritative local writer. Each production receipt
must commit to sequence, previous receipt, intent, capability-chain evidence, evaluated
policy/facts, before/after state roots, result/effect, resource/revision IDs, software identity
and broker signer. Stable references belong in `refs`; transient display labels do not
replace them. Preserve the exact facts necessary to reproduce the decision later.

A signed receipt proves that the broker asserted the result. It is not proof that a physical
actuator moved or an external provider accepted an operation. Those need observed adapter
receipts and verification appropriate to the external effect.

Replay must verify content IDs, expected signer/epoch, sequence, parent links, closed schemas,
capability/policy evidence and deterministic reducers. Do not simply trust an arbitrary
signed `state_after` or an effect blob the writer supplied. Reconstruction must reproduce
the committed state root and aggregate accounting from local bytes alone.

`LOG-101`: deletion, insertion, reordering, duplicate, forged proof and fork detection.
`LOG-102`: valid signature but invalid historical authority or effect is rejected.
`LOG-103`: reconstructed heads/budgets/revocations equal checkpoint state.
`LOG-104`: malformed history is quarantined with last verified checkpoint; no silent repair.
`LOG-105`: suffix truncation detected relative to an independently trusted expected head.

**Mandatory limitation test:** truncate the log to a previously valid prefix and restore
all local state/checkpoints to that same point. Without an external anchor the result is
cryptographically valid and rollback cannot be identified. Keep this as an explicit
impossibility-boundary test, not an assertion that the hash chain “should catch it.”

## 10. Revocation, time and recovery

### 10.1 Revocation

Revocation is a signed, authorized local state transition. It is not deletion of an old
capability and not merely expiry. Revoking any ancestor immediately invalidates future
uses of every descendant at the serialization point. Previously committed historical
receipts remain verifiable as history. A policy decision must bind the revocation/epoch
snapshot it used.

Avoid clock-only revocation freshness. Use authoritative sequence/epoch commitments and
current-state roots. For expiry, inject time from a declared trusted time source; test
backward jumps, excessive skew, missing trust and the exact interval endpoints. A local
monotonic counter is not automatically a cross-device civil-time oracle.

`REV-101`: authorized revoke accepted; unauthorized/cross-resource revoke denied.
`REV-102`: descendant invalidation; unrelated sibling remains valid.
`REV-103`: authorize→revoke→execute race cannot commit under stale authority.
`REV-104`: restored stale epoch/checkpoint rejected against independent current anchor.
`REV-105`: expired and revoked failures are distinguishable and deterministic.

### 10.2 Recovery

Root key recovery is a predeclared authority rule, not a provider administrator reset. The
initial implementation may use an offline recovery key with explicit rotation authority;
threshold schemes are optional. Do not add a home-grown threshold algorithm. Simulating
participants in one process tests the chosen algebra/fixtures, not the real distributed
key-generation/signing protocol.

On recovery, the same resource identities survive; current authority moves to a new epoch
or explicit replacement grant. Old signatures remain mathematically verifiable, but old
operational authority is no longer accepted for new effects. Restoring a seed must not
resurrect revoked grants or reset consumed budgets.

`REC-101`: lose operational key/process, restore authority using authorized offline fixtures.
`REC-102`: old signer cannot authorize new effects after rotation.
`REC-103`: unauthorized recovery share/key/quorum fails.
`REC-104`: resource identity/history preserved; accounting and revocations not reset.
`REC-105`: missing/corrupt recovery material produces a real blocker, not a bypass.

### 10.3 Rollback anchor

Select an anchor outside the storage snapshot being protected: for example, a separately
held checkpoint with a trusted comparison procedure or appropriately managed hardware
monotonic state. No particular hardware is assumed. Specify provisioning, update atomicity,
failure handling and the trusted party/device. A second file on the same snapshotted volume
is not independent. Hardware/physical assumptions must be tested on the target, not mocked
and relabeled as production evidence.

`ANCHOR-101`: full store rollback detected with surviving newer anchor.
`ANCHOR-102`: anchor rollback/loss causes explicit UNKNOWN/PENDING, never inferred freshness.
`ANCHOR-103`: crashes between store commit and anchor update recover without falsely granting
fresh authority or silently discarding acknowledged effects.

## 11. Deterministic policy and agent/tool isolation

### 11.1 Policy function

```text
decision = evaluate(
  intent, capability_chain, authority_snapshot, revocation_snapshot,
  usage_snapshot, trusted_time_fact, policy_object, implementation_profile
)
```

All inputs are explicit canonical facts. Same inputs and implementation/policy versions
produce the same decision object ID and reason code. No hidden wall clock, environment,
network reputation lookup, randomness, LLM judgment or mutable global registry influences
the pure evaluator. Unknown inputs/types/actions are deny-by-default. Policy upgrades are
new objects, never in-place reinterpretations of old evidence.

`POL-101`: determinism across processes, clocks, locales and replay.
`POL-102`: unknown action/schema/constraint/version fails closed.
`POL-103`: conflicting constraints reduce authority rather than widen it.
`POL-104`: signed decision commits every consumed fact; omission/replacement is detected.
`POL-105`: decision is advisory until the broker revalidates/commits against the same
state version. A previously valid ALLOW is not a transferable permission ticket.

### 11.2 Agent model

Do not introduce an LLM first. An agent is an untrusted local process with a subject key,
a bounded capability and an explicit typed channel to a trusted broker. It has no root key,
store decryption key, unrestricted shell tool, provider credential, writable broker code,
network privileges or direct authority-table access. An agent cannot write its own receipt.
The model's generated arguments are untrusted data even when they look well formed.

`AGENT-101`: attempt root/encryption-key access via files, environment, argv, open FDs, `/proc`,
ptrace/process reads, crash dumps, inherited handles and subprocesses.
`AGENT-102`: direct store/log writes, policy replacement and evidence forgery denied.
`AGENT-103`: ungranted tool/action/destination and self-modified grant denied.
`AGENT-104`: output confusion, forged broker replies and confused-deputy cases denied.
`AGENT-105`: CPU/memory/FD/process/disk/output limits bound a hostile dummy agent.
`AGENT-106`: no alternate local helper/proxy reaches networking or privileged execution.

A permissions field in the prompt is not confinement. A process under the same unrestricted
UID as the root key is not a tested security boundary. The trusted fixture simulator in
this delivery is intentionally not an agent-accessible endpoint.

### 11.3 Local fake tools before external effects

Use a closed registry of typed intents and deterministic in-memory/transactional effects.
No provider, device, filesystem side effect outside the fixture root or real spend occurs.
Capabilities and policy must agree on the same resource, scope, destination and action.
Resolve authority and reserve spend at the same serialization boundary as the effect.

`EXEC-101`: one-shot capability consumed once despite retries/concurrency.
`EXEC-102`: same subject/request plus same signed intent returns the recorded result; same
key with different intent produces IDEMPOTENCY_CONFLICT; no second charge/effect.
`EXEC-103`: cancel-before-commit versus cancel-after-commit has explicit outcomes.
`EXEC-104`: TOCTOU races involving revocation, budgets, target rename and policy replacement.
`EXEC-105`: fault at every effect/receipt/ack boundary, with restart and replay.
`EXEC-106`: no successful or externally observable effect without durable evidence.

For initial DB-contained fake effects, atomicity is achievable by one transaction. For
arbitrary external effects, do not promise exactly-once. Use a durable intent/outbox,
provider idempotency keys or reconciliation where supported, and surface UNKNOWN where a
lost acknowledgment makes the outcome uncertain. Real external adapters are not part of
this local acceptance phase.

## 12. Fuzzing, mutation and proof obligations

Required generators: bounded canonical values; malformed byte streams; valid/invalid object
DAGs; capability chains and sibling trees; action/scope/destination sets; arbitrary revision
histories; concurrent consumption schedules; request replay/cancellation; revocation and
recovery epochs; crash points; incomplete evidence closures; and quota/resource exhaustion.

Every property failure saves a minimal deterministic replay fixture, seed, tool version,
implementation root, and expected result. Use Hypothesis/proptest/QuickCheck-style shrinking
or an equivalent reviewed harness. Libraries may be installed only in the preparation phase.
The reference's seeded loops are useful regressions, not an exhaustive assurance claim.

Highest-value properties:

```text
encode(decode(canonical_bytes)) = canonical_bytes
id(object) = id(decode(encode(object)))
verify(old_id, changed_bytes) = REJECT for the tested mutation corpus
accepted_child_rights ⊆ every_ancestor_rights
committed_usage(A) ≤ ceiling(A) for every ancestor A
denied_intent => no committed effect and no spend increment
same_request + same_intent => at most one committed effect
replay(committed_log) = committed_state
revoked_ancestor => no later descendant commit
missing_required_evidence => no readiness assertion
stale_build_or_state => no transport launch
```

Run mutation testing that removes each signature/parent/subset/budget/revocation/schema
check. The corresponding tests must fail. Do not use coverage percentage alone as evidence
that the invariants were exercised. Set exact campaign budgets in a versioned qualification
profile; no skipped mandatory corpus may pass by average score.

`FUZZ-101`: shrinkable authority model campaign.
`FUZZ-102`: parser differential campaign with independent implementation.
`FUZZ-103`: deterministic concurrent schedule/crash replay.
`FUZZ-104`: mutation score for security-critical checks, with surviving mutants explained.
`FUZZ-105`: bounded memory/time/verification work on hostile inputs.

## 13. Build identity, evidence and readiness root

### 13.1 Evidence DAG with no self-reference

Measure immutable inputs first: source/binaries, runtime/dependency closure, schemas, suites,
policy and test vectors. Record exact test IDs and results. Compute a test-results root.
Commit environment/isolation observations separately from deterministic semantic results.
Finally sign the assertion referencing those roots. The assertion must not contain its own
final ID, and its measured build inventory must exclude its own generated evidence directory.

The production evidence closure includes:

| Commitment | Required contents |
|---|---|
| Build | Source, actual executable/runtime, loaded libraries, wheel/package artifacts, flags, supported ABI |
| Schema/profile | Envelope/type schemas, SK-JSON profile, suites, domain frames, resource limits |
| Vectors/tests | Versioned independent vectors, test-code identities, enumerated required IDs/results |
| Authority | Trusted root grants, current key/issuer epochs, revocation root, checkpoint/anchor |
| State | Object-store closure, resource heads, usage state, log head, snapshot root |
| Policy | Exact immutable policy object and evaluator identity |
| Enforcement | Native supervisor/launcher identity, isolation policy, descriptor/probe evidence |
| Freshness | Supervisor-generated boot/challenge context plus current state binding |

A source-tree hash is not the entire running binary/dependency identity. A version string is
not an artifact hash. “Reproducible build” requires independent rebuilding and byte comparison,
not merely hashing one output twice. The initial runner deliberately labels incomplete
dependency closure instead of claiming production build attestation.

`BUILD-101`: reproducible qualified artifact or explicit reproducibility exception with
measured immutable runtime; no vague “latest dependency.”
`BUILD-102`: loaded-library/ABI/policy/schema change invalidates qualification.
`BUILD-103`: test discovery cannot drop required IDs without requirements-root change.
`BUILD-104`: files cannot be changed between measurement and exec; sealed/read-only runtime
or equivalent descriptor-based startup, not hash-then-run mutable path.

### 13.2 Production readiness object

Use the same object envelope. The proposed strict assertion is type `local-ready`:

```text
body:
  challenge: supervisor-owned fresh context
  gates: exact known required gate names, each PASS
refs:
  build: content ID of qualified build closure
  state: content ID of current validated local state
  requirements: content ID of exact required gates/cases/profiles
  evidence: content ID of complete locally verifiable evidence closure
proof:
  signature of a locally pinned, independently controlled verifier identity
```

The referenced evidence contains schemas, vectors, suites, policy, authority, revocation,
store and log roots; do not add redundant mutable copies to the statement. The verifier
must actually resolve and verify the entire closure and compare expected roots/freshness,
not accept the word `PASS` from the tested agent. The included Python function assumes the
caller performed that closure/pinning work; the complete caller is a future supervisor.

Trusted roots must be supplied outside the untrusted evidence bundle. A public key sitting
beside a report authenticates itself, not its authority. No fixture signer is accepted as
an owner's verifier merely because it appears in the ZIP. Readiness is scoped to local
launch policy, not a universal certificate a remote peer must trust.

`READY-101`: incomplete/forged/missing evidence closure rejected.
`READY-102`: wrong key, build, state, policy, authority, requirements or freshness rejected.
`READY-103`: no missing, skipped, expected-failed or unknown mandatory gate accepted.
`READY-104`: report cannot authorize itself or choose its own required-test list.
`READY-105`: broker runtime and loaded inputs are the measured immutable artifacts.
`READY-106`: revocation/state changes between verification and launch require revalidation.

## 14. Exact LOCAL_READY exit criteria

All required gates in `tasks/gates.json` must be complete for the declared target profile.
Each has complete locally resolvable, signed/pinned evidence with zero mandatory failures,
skips, unsupported cases or unexplained mutation survivors. Specifically:

* Canonical/golden/independent vectors match; malformed inputs are rejected consistently.
* All retained objects, signatures and references verify; revision/authority identities hold.
* Every accepted chain narrows authority; all ancestor quotas hold under concurrency.
* All committed effects, budgets, revocations and heads reconstruct from the complete log.
* Full target crash and recovery profiles pass; rollback freshness is justified against its
  declared independent anchor, not inferred from a self-consistent snapshot.
* Policy decisions are deterministic and fact-complete; root/agent isolation is observed.
* Adversarial agents cannot bypass the broker, touch root keys, mutate state or reach peers.
* Network-zero enforcement began before untrusted execution and all tested descendants
  retain it; the broader target filesystem/process/device boundary is qualified.
* Trusted supervisor verifies the complete evidence, live artifact/state binding and fresh
  context; it alone can start the later restricted transport process.

The delivered reference does not satisfy this list yet, and intentionally refuses to issue
the assertion. That refusal is part of a correct handoff, not a reason to weaken the list.

## 15. First network test, strictly after the boundary

See `NETWORK_BOUNDARY.md`. Two separately qualified nodes first exchange bounded protocol
profiles, public/allowlisted readiness disclosures and synthetic immutable objects. Compare
canonical bytes, IDs and signatures. No mutable replication, consensus, distributed spend,
agent-to-agent authority, private-root disclosure, discovery service or provider adapter is
allowed. A correct local manifest does not prove a remote peer is honest or compatible.

The next transport phase must bring its own transport confidentiality/authentication and
resource-limit tests. A hash is not a network authentication protocol. A network canary is
not permission to move the authority root to a remote provider.

## 16. Evidence and CI layout

Use the same tests in developer and isolated runs; record distinct enforcement profiles.
Do not create alternate core semantics for “test” and “production.” Trusted fake effects
are replaced at the genuine broker boundary while retaining primitive contract tests.

```text
vectors/                 reviewed immutable known-answer inputs
schemas/                 closed wire/body contracts
kernel/                  transport-free primitives
native/                  reviewed OS boundary
 tests/                  unit + negative + deterministic property regressions
 tasks/acceptance.json   required/reference test inventory, not mutable PASS flags
 evidence/<run-id>/       build, environment, results, closure and signed assertion
```

CI stages: verify frozen inputs → establish OS boundary → discover exact tests → run known
answers → run properties/fuzz/mutation/crash suites → validate live state → collect complete
evidence → independently verify it. Any failure produces a nonready diagnostic bundle;
it must not trigger a more permissive rerun automatically. Pin dependencies and disable
remote caches/uploads inside local qualification.

The human/Codex report must include commands, actual platforms, exact test count/status,
fixture/profile IDs, hash roots, unsupported assumptions and next bounded task. Never report
“secure,” “proven,” “deployed,” “cross-platform” or “network-ready” merely because this
reference example passed.

Primary-source identifiers [S1]–[S9] are defined in SOURCES.md. Normative architecture choices
here are proposed project requirements; the external references explain building blocks,
not an endorsement or security review of this implementation.
