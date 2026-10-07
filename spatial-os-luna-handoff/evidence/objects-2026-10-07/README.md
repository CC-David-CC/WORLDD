# Object qualification evidence

Both hosts passed the full unfiltered P00-P07 gate in Debug and ASan/UBSan runtime builds.
Each final run passed 113 acceptance tests, 135 core checks, and 39 harness self-tests.
The core count includes 21 object checks.
Legacy coverage is 113 passed, zero current failures, and 28 unrun.

Debug and sanitize directories contain command logs, gate reports, and source hashes.
Native directories contain independent client events.
Frame directories contain real compositor captures of a shared preview and configured object appearance.
Private RPC traces and runtime databases remain outside Git.

The sb16 baseline records successful preceding checks and the original UNKNOWN_METHOD object diagnostic.
First-debug reports retain the initial passing 20-case object suite.
Initial object source and tests are retained under sb16/first-debug.
Final runs add a target-pose recheck after renderer I/O and a real-time configuration case.

The sb16 mutation directory contains the isolated mutant, commands, source hashes, and detected failure.
That mutant incorrectly saves an object preview during movement.
The crash-recovery check detects restored X = 0.5 instead of saved X = 0.
The production implementation keeps previews outside durable storage.

All final reports contain 84 reviewed source hashes, checked against both execution hosts.
Debug build: sha256:481f29966318031d085155e595d5ad57fda24ea17ae996b09fe91b608f362af7.
Sanitizer build: sha256:4f7315225926367d77625329471b02cc68ea6841563bc2795173ca36e7a9a535.
Reports identify each private XLibre binary.
Sanitizers cover the runtime and compositor, not the XLibre server.
No operator session or system configuration changed.

P07 automated software qualification is complete.
The remaining Spatial-M1 authority, confinement, recovery, inference, and physical milestones remain mandatory.
