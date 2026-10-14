# Minimal architecture and corrections

## Preserved design

The reference follows the user's “A Unified User-Control Plane for Replacing Intermediary
Control” report: local cryptographic authority, hash-addressed objects, attenuating grants,
deterministic policy, signed events/receipts, and replaceable external adapters. Local state
is authoritative. A provider ID is a locator in an adapter, never the authority root.

No current DAO design is inferred from old conversations. No blockchain or token is required.
Economic claims are application facts; operational permission requires a validated grant.

## One representation, necessary boundaries

Every semantic record is an object with `v`, `type`, `body`, `refs`, `proof`. A resource,
revision, grant, intent, policy, decision, receipt, checkpoint and readiness assertion use
this envelope. The schema for each `type` constrains its body. References are typed by the
keys of `refs` and are content IDs, not embedded mutable object pointers.

Three distinctions are necessary rather than redundant representations:

1. **Identity versus revision.** An immutable genesis descriptor identifies a resource.
   Content revisions are different immutable objects referencing that descriptor. An
   authorized head event selects the current revision. Editing a document must not
   inadvertently create an unrelated resource or detach its capability tree.
2. **Meaning versus encrypted storage.** A private canonical object has a local content ID;
   a random encrypted envelope has different transport/storage bytes. Deduplication and
   public exposure of plaintext hashes are policy decisions, not automatic properties.
3. **Authorization versus effects.** A capability describes permitted operations; a ledger
   records consumed scarce rights. A signed $100 ceiling duplicated into 100 child grants
   cannot by itself enforce a $100 family total.

The current Python fixtures exercise the first object model, plaintext-ID encrypted rows,
and an atomic fake ledger. They do not claim a complete encrypted transport envelope design.

## The local write contract

An untrusted agent submits a typed, signed intent. The trusted broker resolves the resource
and capability chain against a single frozen authority/policy snapshot. It validates the
chain, current epoch/revocation, exact action/target, and all ancestor budgets. It then
atomically reserves or consumes the required rights, produces canonical effect objects,
and commits the effect and signed receipt. An acknowledgment is emitted only after durable
commit. A later provider projection is a different side-effect boundary with its own receipt.

There is no general “execute shell command” primitive. The initial tool registry contains
small typed fake effects. No tool or agent can choose its own charge after execution.

## Why the local log is linear

A single local serialized writer needs sequence numbers and one previous-event link.
Resource revisions and capability ancestry can still form content DAGs. Making the local
event log a DAG before there are multiple independent writers adds conflict semantics without
solving a present requirement. Distributed ordering is outside this phase.

## Why readiness does not enable the same process's sockets

The primitive process is permanently restricted. A filter inherited across exec is not a
configuration value the application later clears. A separate trusted supervisor, outside
the agent trust domain, owns the ability to launch a narrow network process. It evaluates
fresh evidence bound to the exact immutable runtime and authority snapshot before doing so.
The core remains socket-denied after that launch.

A Python `VerifiedReadiness` object is a convenience value, not an OS capability. Any Python
code in the same process can fabricate values. The actual security boundary is process
privilege and supervisor-controlled startup, not class naming or a private constructor.

## Five false assurances removed from the earlier formulation

* “It is hashed” does not mean it is correct, safe, owned, available or authorized.
* A signed report authenticates the reporting key, not the honesty of its test results.
* A local hash chain cannot identify a complete restoration of an earlier valid snapshot
  when every purported checkpoint was restored with it.
* Stateless attenuation cannot enforce aggregate quotas across siblings or concurrent calls.
* A receipt cannot guarantee exactly-once behavior at an arbitrary external service without
  an appropriate external idempotency/transaction protocol.

These corrections preserve the goal while removing impossible acceptance tests. Their
positive alternatives are pinned specifications/vectors, an independently controlled
verifier, an external rollback anchor, atomic ancestor accounting and explicit effect
semantics at the adapter boundary.
