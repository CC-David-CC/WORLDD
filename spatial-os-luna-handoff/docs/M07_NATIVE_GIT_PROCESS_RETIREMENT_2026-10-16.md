# M07 native Git process-retirement fixture repair

The native storage-contention run observed `ProcessLookupError(ESRCH)` while reading `/proc/<pid>/comm`. The Git child retired between PID sampling and procfs reading. The failure occurred before revocation assertions. Sixteen other methods passed.

Evidence remains at `sb16:/home/dflanag3/spatial-artifacts/m07-storage-ack-primary-runtime-20261016/runtime-evidence/sb16/Debug-suite-01/spatial_security_evidence/ctest.xml`. The run log is `sb16:/home/dflanag3/spatial-artifacts/m07-storage-ack-primary-runtime-20261016/suite-registration03/registrations/sb16/Debug/Testing/Temporary/LastTest.log`.

The fixture now treats only `ENOENT` and `ESRCH` as process retirement. It propagates `EACCES`, `EIO`, and other read failures. The `git_running` witness uses this helper.

Five deterministic cases cover normal comm text, `ENOENT`, `ESRCH`, `EACCES`, and `EIO`. The existing `session-storage-contention` registration includes them through its explicit unittest selector. The method count grows by five.

The pending Git witness, early-reply failure, timing bounds, and revocation assertions remain unchanged. No acceptance oracle or production C++ code changes.

These cases are implemented but UNRUN on the coding host. Runtime qualification remains pending on both required test hosts.
