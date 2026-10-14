# run-command helper behavior evidence

Host: sb16, user dflanag3. Tests used checkout `/home/dflanag3/src/spacial_desktop` only for read-only metadata. They created temporary files under Python temporary directories and helper run directories under `/tmp`. No Git writes, network access, X, cgroups, world services, or runtime artifacts were used.

The final frozen source is in `spatial-run-command-behavior-20261012-m06next-run05`. The helper SHA256 is `a608e7eae1bdf408bc08504cb2ef07e8e03c408735b14a05685e4d473649abb0`. Test SHA256 is `f0d013d1d023c5d07ecbf1f6f292eae2f11f52784929b5aa4ce6f694ac1b7017`. Test-plan SHA256 is `31ca726cc1bff25bb3d355446b602ca90958db7fc2a4a42828c95b922101c26a`. sb16 independently reported these hashes before launch.

Run05 passed all seven tests with exit 0: exact argv and exit 0; exit 7 preservation; existing directory refusal; dangling symlink refusal; invalid checkout refusal; receipt-write failure prevents command start; and TERM while a child remains live. The final log records helper PID 1730056 and child PID 1730107. The helper exited with command exit UNKNOWN while the child remained alive. Cleanup targeted only PID 1730107, and the recorded final process check found no matching live processes.

Earlier attempts are preserved as separate directories. Run01 failed before tests because Python eagerly evaluated a platform-specific default path. Run02 returned zero but executed zero tests because the test file lacked its `unittest.main()` entrypoint. Run03 passed all seven tests but emitted ResourceWarnings for unclosed pipes. Run04 passed all seven without warnings but did not record process IDs. Run05 includes the corrections and is the accepted run.

No production code or canonical skill changed. This is temporary helper/test evidence for primary review; it does not qualify native runtime behavior.
