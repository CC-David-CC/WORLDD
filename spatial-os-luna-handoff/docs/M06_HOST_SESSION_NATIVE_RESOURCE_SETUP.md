# M06 host-session native resource setup

The frozen Debug registration at source commit 258920de00e45448f104eedcc97147cf878406b3
launched test_host_session_production.py directly inside systemd-run --user.
Its fixture calls broker_config(), which requires SPATIAL_TEST_BROKER_CONFIG.
The direct command did not create that owned resource template. All 25 errors in
the retry on each host stopped during fixture setup with
RuntimeError: Native bootstrap requires an owned broker resource template
at tests/native/test_session_application.py:110.

The runtime guard and test body remain unchanged. Existing qualified native
registrations compose with_resource_delegate.py --native with
with_adapter_resources.py. The first creates a system transient service running
as the project owner and its delegated cgroup. The second creates the broker
configuration, resource policy, and test environment inside that service. The
CMake change applies this pattern to the existing host-session-production-native
registration. It preserves the test name, body, arguments, CTest timeout, and
the 165-registration inventory.

The original failed retry evidence remains at
/home/dflanag3/spatial-qualification/m06-recovery-static-fixtures-20261013/{sb16,sb16-2}/retry-attempt01.
Each host ran 19 selected registrations; 18 passed and this registration failed
during setup. Both retry reports have phase_complete=false.

## Metadata-only verification

On coding host a5500, fresh CMake metadata trees were configured with the gate
flags: Debug, BUILD_TESTING=ON, SPATIAL_INPUT_TESTS=ON, and sanitizers OFF;
ASan-UBSan, the same flags, and sanitizers ON. Source environment placeholders
match the gate. Configure and CTest show-only commands completed with exit 0.
Each show-only JSON inventory contains 165 registrations. No build or test ran.

The inventories are under
/home/dflanag3/spatial-qualification/m06-host-session-resource-metadata-20261013/attempt01.
The Debug metadata build ID is
9eacea1d889d43579a075fd9ce30e2338f7b444c0f1847dc494679d7d9038768.
The ASan-UBSan metadata build ID is
efd7f383befaf99d0058de35c127b6e870346bcd08abe485d6b7e905ee543dca.
The CMake file is a build input, so these metadata IDs differ from the frozen
258920de binaries. The old host-session user-unit suffix using build_hash is
removed. The delegate helper instead uses its scoped random spatial-m03-resources
unit. Its native bounds are RuntimeMaxSec=240 seconds and TimeoutStopSec=5
seconds. Keep these bounds and the CTest TIMEOUT=1200 unchanged.

The Debug and ASan-UBSan target show-only records confirm the same registration
name, test arguments, environment variables, and timeout. The only command
change is the reviewed resource-wrapper chain:
with_resource_delegate.py --native -> with_adapter_resources.py.
New build and working-directory paths are expected metadata differences.

## Focused runtime overlay plan

For the first focused Debug and ASan-UBSan qualification, use the original
258920de production source and binaries on both test hosts. Create a temporary,
single-registration CTest overlay from the reviewed launcher correction. Its
test module and both wrappers must resolve to the original 258920de source;
replace only the host-session command with the reviewed wrapper chain. Keep the
original target name, arguments, environment, timeout, and working directory.
Map target binaries to the frozen deployment profile paths. Record the launcher
overlay source commit separately from production source and binary commit
258920de in each receipt. This isolates the launcher fix and does not qualify
the rebuilt combined candidate.

Run only host-session-production-native, once per profile per host, after the
test slots are free and primary approves the exact commands. Preserve the
original retry report. Record separate output, cleanup evidence, and source,
runtime, and binary pins. Do not claim a full gate. The CMake change remains a
build input for the later combined-candidate build and full gate.

No build, CTest execution, or runtime test has been run for this patch.
