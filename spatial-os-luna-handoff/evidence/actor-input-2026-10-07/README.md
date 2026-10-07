# Actor input evidence

Each host has final Debug and sanitizer reports.
Each run passed 98 acceptance tests, 65 core counterexamples, and 39 harness self-tests.
The reports record the exact commands, 76 input hashes, and the private XLibre binary hash.
Every input hash matched both the execution host and the reviewed local source.
Selected witness logs contain native events from the independent C client.
Runtime databases and actor-session RPC traces are excluded from this archive.

The sanitizer builds instrument the runtime and compositor.
They use the previously qualified Debug XLibre server.
These software-display results do not certify native hardware, GPU performance, or Spatial-M1.

The first click diagnostic on sb16 resolved the earlier UNKNOWN_METHOD error.
The additional scroll-during-drag counterexample initially failed.
The before-scroll-fix folder retains the failing log, test, production actor source, and preceding build report.
The retained actor source hash matches that report.
Its correction rejects a scroll aimed at another surface while a button is captured.
The final runs include the corrected check.

The baseline gate does not prove captured pointer motion.
That P06 requirement remains pending and prevents phase completion.
Full broker-owned application identity and workspace authority also remain mandatory future work.

Build identities are identical on both hosts:

- Debug: sha256:2f5de2409c476b5c6234b93d22f769227dc5114266fca76a42701d49ccf5b1e3
- Sanitizers: sha256:eb4a3601386fa0e54067d0b1592c629df3df6444da8660cd946a65a139f74477
