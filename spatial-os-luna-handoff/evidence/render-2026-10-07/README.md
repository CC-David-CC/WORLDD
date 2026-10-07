# P04 qualification evidence

Both hosts passed the cumulative 70-test acceptance gate in debug and sanitizer builds.
Each run also passed 39 harness tests and 47 production counterexamples.
Thirteen counterexamples exercise rendering and compositor lifecycle.

The run.json files record commands, exit codes, source hashes, and build identities.
The acceptance.json files record each selected acceptance result.
The core.log files include all production counterexample results.
renderer.log records the actual software OpenGL implementation.

The pixel files contain compositor readback and independent XGetImage readback.
Each pair is byte-identical.
verification.json records their dimensions and SHA-256 hashes.
The PNG preview is a format conversion of the retained sb16 debug witness PPM.

The sb16/edge-before record preserves the observed top-edge rasterization defect.
The current half-open pixel-center counterexample passes.
The sb16/mutation record preserves an isolated GL_GREATER-to-GL_ALWAYS mutation.
The coplanar ordering counterexample detected that mutation.
The production source contains GL_GREATER.

Debug build identity:
sha256:b9d3eeb9ea2bd3a2f5d47f7289d1ab8c0a2320b0a51ecd6a05486da5e07ea362

Sanitizer build identity:
sha256:640f6bcf3e9d1c6e2b1ade298a5d385f019715c4a812a4f79ae355b71efe919d

Additional workflow pins:

- docs/RENDERING.md: bc65c283cbb7abd31d312bee9cd0536cbfc47e7c58a6ae6004c19079796cc4c8
- tools/with_test_display.py: c1a071ee0dcafbb2beb1fc46cb5b4244d755c276c879332578bb9d2bc76ff29f

These are software-display results.
Native GPU scanout, physical devices, live application surfaces, and later milestones remain unqualified.
