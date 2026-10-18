# M08 timing gate integration, 2026-10-18

Status: UNQUALIFIED. This integration requires `copy-progress-timing-tooling` in the M08 profile inventory. The CTest registration checks absolute copy deadlines. The test also supports configured launch inventories. Existing helper tooling remains mandatory. Keep the exact 89-case harness IDs and frozen 51-case baseline hash. Preserve M06/M07 defaults, six storage cases, and native cross-host geometry and cabinet matrices.

Reviewed source mapping:

- `e007f354afd89c31c4b945a3a7d6de0cb01a8b4d` -> `b8ce80e` (`Repair M08 broker fixture storage budgets`).
- `980bb5207aa3c04c7c9729406214061dcd5c980b` -> `6e92e03` (`Correct bounded copy-progress observation and qualification profiles`).
- Base: `db61b9b6edadb70b73acf8902be32dc128c84669`.

Git auto-merged the shared CMake changes. Review confirmed both timing and copy-helper registrations remain present. No locked acceptance bytes, production C++, or schemas changed.

Receipts live in `/home/dflanag3/spatial-artifacts/m08-timing-gate-integration-20261018`. Copy-helper tooling passed 22 tests. The corrected M08 gate suite passed 32 tests. Timing controls passed 15 tests using actual Debug and ASan-UBSan inventories. Full harness discovery found 89 cases with zero failed imports, and all 89 passed.

The first harness attempt lacked generated bindings. Discovery returned 68 cases and four `_FailedTest` modules. Missing `core_pb2` blocked three modules; missing `broker_pb2` blocked one. The 89-ID manifest then appeared to contain 21 extra IDs. Verified source63 Debug bindings corrected discovery. The original failure logs and setup attempts remain preserved.

Supervised recovery, native cross-host qualification, and the combined M08/full Spatial-M1 gate remain pending. This tooling integration does not establish runtime qualification.
