# The first network boundary — P07, not implemented

No included program opens a service or contacts a peer. This document is a later acceptance
contract, not authorization for Codex to start a listener.

## Precondition

Each node has passed the complete LOCAL_READY target profile, not just the delivered
reference suite. The owner separately authorizes two explicit test endpoints and a short,
scoped transport lease. The kernel remains network-denied. A supervisor launches a separate
transport process against a verified read-only artifact bundle and minimal credentials.

## Minimal transcript

1. Authenticated canary channel with explicit endpoint identities; no broadcast discovery.
2. Exchange supported protocol/profile identifiers, public test-vector root and fresh nonce.
3. Exchange only allowlisted public conformance information. Do not publish root grants,
   key topology, private policies, object inventories, budgets or plaintext-derived IDs of
   private content merely to prove “readiness.”
4. Transfer small synthetic immutable canonical objects from a pinned fixture corpus.
5. Receiver checks size/encoding/schema, recomputes IDs, verifies expected signatures and
   responds with a bounded acknowledgment referencing the exact object ID and session nonce.
6. Repeat with corruption, truncation, reordered fields, old versions, wrong digest, invalid
   proofs, replay, unexpected type, oversized body, duplicate object and interrupted transfer.
7. Close the transport lease. Verify that neither node's authoritative application state,
   capabilities, consumption or current resource heads changed.

The received remote readiness report is a claim. Local policy decides whether its verifier
key/profile is trusted. Parsing a self-signed manifest never grants the sender authority.
Do not download remote policy code or accept a remote choice of mandatory local tests.

## Acceptance cases

`NET1-101`: same immutable fixture → same canonical bytes and ID on both implementations.
`NET1-102`: corrupted/malformed/unknown input rejected without resource exhaustion.
`NET1-103`: session/identity replay rejected; response binds to current nonce and exact object.
`NET1-104`: no application mutation, budget consumption, agent invocation or hidden adapter.
`NET1-105`: private manifest fields never exposed by default.
`NET1-106`: transport loss/change does not rewrite resource IDs or grant ancestry.
`NET1-107`: kernel still cannot create sockets even while its transport adapter is running.

Only after this canary should a separate spec propose mutable synchronization, conflicts,
revocation freshness, disconnected budgets and effect delivery. A local lock cannot enforce
global scarcity, and a content hash does not select one of two concurrently valid heads.
