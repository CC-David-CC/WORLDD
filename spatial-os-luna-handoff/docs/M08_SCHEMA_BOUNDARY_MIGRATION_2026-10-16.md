# M08 actor cabinet schema migration

This draft adds actor cabinet messages. Cabinet runtime actions still return `UNIMPLEMENTED`.
The source base is `7b584e0251fe18abf523ca47d92cb4d6e7e18013`.

`file_actions.proto` contains file-operation data and replies. It has no authority imports.
Twenty existing messages move from `cabinet.proto`. Their fully qualified names and wire fields remain unchanged.
Python callers must import relocated data messages from `file_actions_pb2`.
No existing Python caller was found in `src`, `tests`, or `tools`.

`FileActionRequest` remains internal. It carries the trusted principal, workspace identity, and authority components.
The authenticated runtime must derive these values. Public actor requests cannot supply them.

The migration adds three messages: `ActorCabinetRequest`, `ActorCabinetReply`, and `ActorCabinetFailure`.
The request identifies a representation, generation, operation, and typed file action.
The reply identifies the operation and representation. It contains typed results or a typed failure code.
Actor request field 30 and actor reply field 8 connect these messages to the existing envelopes.

The runtime must check authority before file operations and result projection.
It must clear or sanitize `FileCopyJobReceipt.detail`. It must keep internal exception text private.
The schema alone does not prove metadata isolation. Runtime regressions must check unauthorized filenames, host paths, and diagnostic text.

The acceptance lock advances from `0.16.0` to `0.17.0`.
Only the existing core schema pin changes. The new file-actions schema receives a pin.
All 151 acceptance IDs, assertions, oracles, witnesses, and unrelated locked files remain unchanged.

| Source | SHA-256 |
|---|---|
| Base core schema | `8241b30befeda1d11215e7faa1145c88b9fdbcbf14711140135233481ddb9873` |
| Base cabinet schema | `f2961feb0e581b0a25065a3c6eaaa632261f70e0e2599e4e8c37b51fc4531c62` |
| New core schema | `ad7ab93ff1666259273e3c6a7f2bc3658e84e60c0748a94f67f72717566d50c4` |
| New file-actions schema | `2cbb6454fc90ff23eb459b132c63de19f7bd3d10d6083ab452e2c1fb0145ce66` |

The first descriptor comparison passed with protoc 3.21.12.
It preserved 170 existing messages and 16 enums. It checked the declared relocation and additions.
Evidence is under `/home/dflanag3/spatial-artifacts/m08-cabinet-schema-boundary-20261016/descriptor-review` on middle-child.

| Descriptor set | SHA-256 |
|---|---|
| Base | `6998cc033a969064529e335ca3a6d658b0b12c5ad79639767dcd841629600630` |
| Candidate | `7c5f322708282fc8c06e049c940d88a652dd3c13675338463342be0e6402f3a4` |

The strict descriptor comparison also passed. It requires the exact actor additions and public cabinet message shapes.
The descriptor sets have the same hashes as the first comparison.
Each qualification host passed one valid descriptor and 12 deliberately invalid descriptors.
Each invalid descriptor failed for its intended reason.
The primary pair audit is under the evidence root in strict-inputs-02/PRIMARY_CONTROL_PAIR_AUDIT_01.json.
Its SHA-256 is b03836ed28fac75a297da92cf385ccf6072d6b4cd82b43a23bf4db1fe20c1001.

Both Debug configurations compiled their generated Python bindings and C++ spatial-protobuf library.
BUILD_TESTING=OFF built 18 selected Python targets. BUILD_TESTING=ON built all 23 selected targets.
These targets also build their file-actions and compositor dependencies.
The first targeted build omitted compositor_native_pb2 from the presentation binding closure.
Static analysis reproduced the failure. Its original 1/1/COMPLETE evidence remains under cmake-binding-review-02.
CMake now gives both presentation binding targets a compositor binding dependency.
Replacement static analysis found 27 generated modules with testing off and 32 with testing on.
All 58 and 75 respective import edges resolved.
Primary rechecked actual commands, retired processes, source pins, generated hashes, and the C++ archive member.
The primary compile audit is cmake-binding-review-02/PRIMARY_COMPILE_AUDIT_01.json under the evidence root.
Its SHA-256 is c956f462652078363d87b92ddd242e6168914971f51ed1e9b573edbda7db4db2.

These checks do not execute application binaries or generated Python imports.
Application runtime, broker integration, sanitizer, native, and acceptance checks remain UNRUN.
M07's frozen candidate and deployments remain unchanged. M08 remains UNQUALIFIED.
