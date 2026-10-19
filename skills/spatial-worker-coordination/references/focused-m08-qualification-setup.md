# Focused M08 qualification setup

Use this when a focused CTest subset comes from a full-gate stage.
Keep the full-gate stage wrapper and the exact CTest registrations.
A focused selector does not replace wrappers that prepare protected runtime state.

For the protected core stage, use `tools/with_copy_worker_helpers.py` around the registered CTest command, as `tools/m06_deployed_gate.py` does.
It installs profile-matched copy helpers at the fixed root-owned paths.
It restores prior helper state only after child retirement is confirmed.
Keep the four self-managed nested CTests outside that outer helper wrapper.
Read `COPY_HELPER_NESTED_CTESTS` from `tools/m06_deployed_gate.py` each run.
Do not duplicate that list in launch commands or notes.
Missing helpers mean the copy job did not start with verified authority; report uncertainty, not a copy result.

Preserve approved storage launch, volume, and quota variables.
Preserve native resource delegation, registered argv, CTest properties, and timeouts.
Retain explicit blank `--setenv` assignments when binding the host-approved X server and fresh evidence path.
Keep writable Dart/CTest bookkeeping separate from each test's relocated runtime working directory.
Do not let moving CTest metadata silently change the test working directory.

Before interpreting results, require nonzero expected unique discovery and match the selected names to relocated CTest metadata.
After the wrapped child exits, require child exit zero, confirmed cleanup, and helper state restored to its recorded prior values.
Report fixture/test failure separately from wrapper cleanup failure.
