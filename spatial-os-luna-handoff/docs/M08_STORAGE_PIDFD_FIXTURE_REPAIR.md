# M08 storage fixture PIDFD repair

The first combined Debug storage run reached all seven controls on both hosts.
Four passed and three errored while resolving /proc/PID/exe for the protected
root-owned supervisor. The retained logs are under
/home/dflanag3/spatial-artifacts/m08-storage-fixed-debug-e4d6699-runtime-20261019/runs/storage-debug-{sb16,sb16-2}-01/canonical-run/.

The fixture now opens each direct child's pidfd before inspection. A bounded
read-only sudo helper returns the exact executable path, parent PID, and start
time. The fixture checks pidfd readiness before and after inspection, matches
the exact build supervisor path and coordinator parent, and retains that same
pidfd. It closes other candidate descriptors. Production permissions and
nondumpable-process settings remain unchanged.

The launcher parser now treats unittest hyphen and equals separators as method
boundaries. It still reports a failed suite as failed while preserving the
individual passing methods. It accepts systemd's 1min 30.072s runtime format.

Selected checks and purpose:

- CTest proc-identity-observer-contract runs tests/native/test_proc_identity.py.
  Five mocked controls cover valid identity, malformed data, timeout, process
  disappearance, and inspection refusal.
- python3 -B tests/core/test_storage_session_launcher.py: seven controls for
  fail-closed result parsing, including mixed pass/error output and minute-form
  wrapper timing.
- The exact seven NativeStorageSessionTests methods through the reviewed Debug
  wrapper on both test hosts: exercise protected supervisor identity and the
  session-storage owner cleanup behavior.

These checks are UNRUN on this ROG coding worktree. The earlier two-host run
failed in three fixture inspections; it did not pass. No qualification claim is
made. No acceptance contract changed.
