# Codex operating contract

## Goal and boundaries

Build the smallest local cryptographic control kernel that can be qualified independently
of all transports. Preserve David's intent: one canonical object model, one attenuation
algebra, one authoritative event/effect model, and later replaceable adapters.

Read `CURRENT_STATE.md` before claiming anything is implemented. The prior research report
is architectural context, not evidence of software that already exists. Older DAO material
is historical and is not an authorization or ownership specification for this project.

## Non-negotiable rules

1. No networking, sockets, peer discovery, HTTP server, cloud API, LLM, real payments,
   telemetry or update checker in the primitive implementation. Dependency acquisition is
   a separately authorized host-preparation action, never a bootstrap fallback.
2. Do not modify the user's normal Codex configuration, PATH, profiles, SSH configuration,
   keys, services, firewall, bootloader or existing repositories.
3. Do not implement cryptographic algorithms. Use reviewed libraries and independent
   published vectors. Fixture seeds are public and must never become real authority keys.
4. Unknown schemas, actions, constraints, versions, issuer links and incomplete evidence
   fail closed. Do not make a parser silently coerce a boolean into an integer.
5. Keep scope components structural. Do not add wildcard exceptions or raw string-prefix
   authorization. OS paths are adapter data, not internal resource identity.
6. Capability attenuation is necessary but insufficient for quotas: all ancestors share
   transactional consumption. Do not create independent child wallets as a workaround.
7. No test flag, CLI override, Python class instance or self-signed claim grants network
   privilege. An OS-separated supervisor and verified, fresh evidence are required.
8. Do not weaken a test because the implementation fails. State the violated invariant;
   fix the abstraction, and preserve the counterexample as a regression case.
9. Separate PASS, FAIL, PENDING, UNSUPPORTED and SKIP. Missing or skipped mandatory cases
   block readiness. Reference-only tests do not satisfy production gates by implication.
10. Do not claim complete confinement, rollback resistance, exactly-once external effects,
    reproducible builds, or cross-language conformance without the specified evidence.
11. Keep network-free tests network-free even when a language tool wants to download a
    module. Stop with an explicit dependency blocker instead of calling a package index.
12. Work only on the phase named in the user's prompt. Networking stays absent until P07
    and a separate explicit authorization; a task file is not permission to start it.

## Definition of a phase completion

Deliver code, positive/negative/property cases, content-addressed evidence, refreshed
`CURRENT_STATE.md`, and a small explanation of any architectural change. Report commands,
platforms and results actually observed. A completed phase is not whole-system readiness.

`tasks/acceptance.json` is a tracked inventory, not a place to turn failures into PASS.
Test results belong in generated evidence. Freeze test inputs before running them and
invalidate qualification on changes to code, schemas, policies, dependencies or authority
state relevant to the assertion.

## Initial instruction

Run P00 from `START_CODEX.md`. Report its evidence and stop. Do not infer permission to
continue all phases just because the handoff includes them.
