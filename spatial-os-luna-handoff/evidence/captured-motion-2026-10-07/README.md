# Captured motion qualification

Both hosts passed the final server, native component, Debug runtime, and sanitizer runtime runs.
Each runtime run passed 98 acceptance tests, 70 core counterexamples, and 39 harness self-tests.
Each native run passed 13 component checks.
Server reports include the upstream unit and modesetting symbol checks.

Final reports identify exact commands, source hashes, and binary hashes.
The runtime reports contain 76 source hashes.
The native component reports contain 46 source hashes.
The server reports contain five extension-source hashes.
All final source hashes matched the reviewed local source and both execution hosts.

Debug build: sha256:f79cf77631b5769e1dddd43dddb514149b36599ea5223e5f17a2699213fbc08b

Sanitizer build: sha256:b50161f890a883242ac613ee5cdd6aa9a88651c471d5f21e9bf5765aabbaa7bb

Selected native witness logs show actual motion and release coordinates.
Runtime databases, credentials, and actor-session RPC traces are excluded.

Separate sb16 diagnostics preserve:

- The missing captured-aim motion failure and its exact test source.
- The missing pose-driven motion failure and its actor source.
- The initial missing-header compiler error and corresponding server source.
- The discarded libXi witness failure.
- The discarded direct-Xlib locking witness failure and debugger backtrace.
- The deliberate mutation that disabled retained release coordinates.

The mutation failed at the required release coordinate, as intended.
It ran in an isolated source worktree and did not change the main runtime.
Discarded witness failures are not failures of the final checked XCB witness.
See docs/CAPTURED_MOTION.md for their causes and the final interface.

P06 automated qualification is complete.
Physical input, native GPU presentation, full workspace security, and Spatial-M1 remain incomplete.
Sanitizers instrument the runtime and compositor, not the XLibre server binary.
