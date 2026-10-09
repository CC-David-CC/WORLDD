Actor pixel schema migration evidence
Base: 047d0075685d6a9a0bb9e3be710175dda5b1952c

Both hosts passed the preceding 51 harness checks and 151 P10 cases before the schema change.
Both then passed the schema-only Debug build, 35 focused core checks, 51 harness checks, and 151 P10 cases.
The focused core suites are core-protocol, actor-observation, and render-runtime.
All existing tests and their assertions remain unchanged.
No final schema qualification case failed or skipped.

The descriptor audit preserved 488 fields, 74 enum values, and 136 existing messages.
It also preserved all 151 locked acceptance identifiers.
Only core.proto changed among locked files. No new locked file was needed.
Per-host summary.json files pin the schema and acceptance-lock hashes.
The runner scripts preserve the commands and original checkout prerequisites.

Primary-agent review:
- Preserve all previous message fields and enum values.
- Add explicit pixel request flags without changing metadata-only defaults.
- Retain rejection of the existing unknown ActorObserveRequest tag 3.
- Define byte order, dimensions, image size, and the complete transport bound.
- Keep pixel export behind existing observation authority and queued-write revalidation.
- Keep image production separate from schema declarations.
- Leave acceptance assertions, oracles, and independent native witnesses unchanged.
- Record the migration explicitly in the contract and lock metadata.

No local-model review was used.
This schema-only commit does not implement image production or complete M10.
Full Debug/runtime sanitizer qualification follows production pixel implementation.
Native-server GLX sanitizer and physical hardware qualification remain separate.
