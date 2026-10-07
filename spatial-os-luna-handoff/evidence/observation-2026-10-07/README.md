# Observation qualification

Both authorized hosts passed Debug and sanitizer runtime qualification.
Each run passed 98 cumulative acceptance tests, 79 core checks, and 39 harness checks.
The focused P07 observation test passed separately in both build modes.
This makes 99 passed legacy acceptance tests and 42 unrun tests.
P07 and the full Spatial-M1 goal remain incomplete.

Debug build: sha256:09e588670180943e3f688db40d36e999571a5f9220df581bac5a1b8f13d1f7b9

Sanitizer build: sha256:d8be6cdbed66e41c1b86e99feaa28b47ab19f0a56a6c2f817692d1203a2fc473

The final run.json files record commands, server hashes, and 77 source hashes.
All hashes matched the reviewed source and both execution hosts.
The focused reports reference the same source and build identities.
Selected native witness logs contain actual input events.
The presentation witness retains its captured PPM after observation and an expose event.
Actor-session RPC logs and runtime databases are excluded.

The baseline records the previous UNKNOWN_METHOD failure on commit 8e1a229.
The mutation changes only the isolated renderer's observation depth test.
Its occlusion test fails, as required.
The primary working trees retain the correct implementation.

The runtime and compositor use ASan/UBSan in sanitizer runs.
XLibre remains the prior pinned Debug build on a disposable authenticated Xvfb display.
These results do not qualify native GPU timing, physical controls, or workspace confidentiality.
See docs/OBSERVATION.md for precise limitations.
