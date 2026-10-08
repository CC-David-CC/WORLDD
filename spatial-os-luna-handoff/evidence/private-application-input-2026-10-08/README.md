# Private application input evidence

The primitive is application.private_input in M04.
The native witness is tests/native/test_application_input.py.
The source base is 59e37ed with the private-input working changes.
The XLibre pin remains f844d7a86c10fd5cc89083e7daf97b862b8ca1fd.

Both previous servers failed 12 of the final 15 checks.
All three same-application positive controls passed on that baseline.
The earlier 14-check baseline failed 11 checks.
The final witness adds explicit device-capacity exhaustion and recovery.
It also synchronizes connection cleanup through native resource disappearance.
The earlier witness and its hash remain with baseline-01.

Both initial private-device Debug servers passed all new checks.
One existing SendEvent fixture depended on the shared keyboard.
The corrected fixture retains its BadAccess assertion and targets the attacker's actual keyboard.
Both old and new servers pass the corrected 36-check resource suite.
That fixture change was reviewed and committed separately as 59e37ed.
See docs/INPUT_FOCUS_FIXTURE_REVIEW.md and evidence/input-focus-fixture-2026-10-08.

The first native sanitizer run found a 193-byte XKB geometry-copy leak.
The input-privacy assertion passed, but native-server cleanup failed.
Patch 0007 frees every old destination doodad's text, font, or logo string before replacement.
It preserves the existing cleanup operations and changes their starting index.
The source keymap remains separate. XkbCopyKeymap already returns early for self-copy.
No suppression or weaker assertion was introduced.
Both patched native sanitizer runs passed all 151 focused checks with GLX disabled.
Leak detection and immediate ASan/UBSan failure were enabled.

Debug and runtime sanitizer profile records identify the actual binaries and commands.
The initial full Debug profiles passed 663 checks across 34 CTest suites, 51 harness checks, and 151 P10 cases.
Final Debug and runtime sanitizer profiles passed on both hosts against the XKB cleanup patch.
Each passed 663 core checks, 51 harness checks, and 151 P10 cases, with no skipped cases.
Those records are in debug-03 and runtime-sanitize-01.
Generated logs retain their original contents.
Raw native-server logs and temporary case directories remain under local/m04-app-input-* on each host.

The primary agent reviewed authentication failure, identity grouping, cleanup, and callback denial preservation.
David authorized primary-agent review without the local model.
No model review is claimed for this increment.

This component establishes private idle application devices.
Actor-device binding, application-group control transfer, revocation, and event metadata remain required.
The private-device callback currently rejects application access to unbound actor devices.
No untrusted native session has been qualified.
Physical input, GPU timing, lifecycle qualification, and session soak remain unrun.
M04 and Spatial-M1 remain incomplete.
