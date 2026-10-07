# Collision qualification

Both authorized hosts passed Debug and sanitizer runtime qualification.
Each run passed 98 cumulative acceptance tests, 95 core checks, and 39 harness checks.
The focused P07 sweep, sliding, and observation tests passed in both modes on both hosts.
Legacy coverage is 101 passed and 40 unrun.
P07 and Spatial-M1 remain incomplete.

Debug build: sha256:76c07250da325580e82643f1021ead988b831e03d575961572f3386e47ab8ae1

Sanitizer build: sha256:f2d513cb38bb0182fe8454d527279c1ae2c1815b70177fabdc82121ed8b46243

Final run.json files record exact commands, the XLibre binary, and 79 source hashes.
All hashes matched the reviewed source and both execution hosts.
Selected native witness logs and barrier captures are retained.
Actor-session RPC logs and runtime databases are excluded.

The baseline records the missing protobuf operation on commit 1f7ecf8.
The manifest diagnostic records the omitted fixture configuration hash.
The corrected migration retains all locked assertions and adds the declared configuration hash.
The mutation preserves rendering and pure geometry but removes colliders from actor motion.
Its fast-motion witness fails at position 2.5 instead of 0.25, as required.
The primary working trees retain the correct implementation.

The runtime and compositor use ASan/UBSan in sanitizer runs.
XLibre remains the pinned Debug build on disposable authenticated Xvfb displays.
Physical controls, native GPU performance, workspace security, and remaining M1 milestones are pending.
See docs/COLLISION.md for numerical limits and the full test scope.
