# Protobuf transport change

David requested Protocol Buffers on 2026-10-07.
He explicitly authorized changing the acceptance transport and its tests.
This change replaces JSON lines in production and acceptance.

The previous contract and lock remain in Git commit `7283fd9`.
The new contract is version 2, with wire protocol `spatial-protobuf/1`.
The acceptance lock records the new schema, codec, tests, and contract hashes.

| Previous behavior | Protobuf behavior |
|---|---|
| JSON lines | Four-byte big-endian length and protobuf payload |
| Signed or unsigned JSON request ID | uint64 request ID, including zero |
| Method string and untyped parameters | Typed operation oneof |
| Result or error object | Typed outcome oneof |
| Unknown method string | Missing recognized operation gives UNKNOWN_METHOD |
| Recover after malformed JSON line | Recover after malformed payload with a valid length |
| Recover after oversized line | Close on excessive or truncated binary framing |
| Duplicate JSON keys rejected | Protobuf scalar merge rules apply; one decoded operation is dispatched |

Framing errors cannot safely locate the next request.
Closing the connection prevents an attacker-controlled length from redirecting the parser.
Malformed payload recovery still requires exactly one error, followed by the next correlated success.
Unknown fields and non-finite numbers fail validation, including nested messages.

The P00 acceptance count remains five.
All P01 geometry tests and numerical assertions remain unchanged.
Transport self-tests retain correlation, Unicode, deadline, EOF, invalid payload, missing outcome, and process-death checks.
Binary boundary tests add wrong wire type, unsupported version, nested unknown fields, required presence, and uint64 precision cases.
Existing later-phase tests remain pending. Their protobuf operations must be defined before those phases can pass.

Local-model proposals are compared per file and retained under `local/peer` on sb16.
The primary agent reviews and tests the integrated code.
Proposals with guessed generated paths, incomplete patches, or weakened assertions are not applied.
The kernel reference's canonical signing format is unchanged.
Protobuf wire bytes are not a replacement canonical signature format.
