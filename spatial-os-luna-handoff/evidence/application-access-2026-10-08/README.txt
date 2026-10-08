Broker application-access qualification

Base commit: e69fd6d5b7bf53b1f5f1e4346bf9f37d4a83bb19
The primary agent reviewed the change without local-model review.

Both hosts passed prerequisite harness and P10 checks.
The corrected 22-case baseline failed against both old broker binaries.
They rejected the additive application-access operation as an unknown protobuf field.
baseline-test.py preserves that corrected test version.
Earlier baseline logs preserve setup and teardown fixture mistakes.
The root M04_APPLICATION_ACCESS_DEBUG.txt explains those mistakes.

Both corrected brokers passed 28 focused cases.
The tests use real issuers, persistent grants, confined processes, and native server credentials.
The caller is a trusted private-channel test supervisor.
The suite does not establish worldd actor-channel, pixel-filtering, or input-revocation behavior.

Both Debug profiles passed 771 core checks across 40 suites.
Each also passed 51 harness checks and all 151 P10 cases.
Both runtime ASan/UBSan profiles passed the same 771 core, 51 harness, and 151 P10 checks.
Both native-server ASan/UBSan profiles passed all 28 new application-access checks with GLX disabled.
No final profile failed or skipped a case.

Each host's schema audit preserves 14 existing messages and 59 existing fields.
The acceptance lock is unchanged. It retains all 151 acceptance identifiers.
The additive broker protocol does not modify the core schema or require a database migration.

production.patch records the reviewed production change.
tested-inputs.json records the ten source and test inputs compared across both hosts and Windows.
source-sha256.json records normalized final source and contract inputs.
review.txt records the primary review.
Scripts preserve commands, selected binaries, sanitizer options, and evidence locations.
Host logs retain failure evidence as well as final qualification results.
Credentials, runtime databases, generated bindings, actor traces, and binaries are excluded.

No native XLibre server source changed.
The focused native-server sanitizer run uses the existing instrumented server with GLX disabled.
It does not qualify instrumented GLX or native GPU output.
Worldd consumers, physical input, performance, and soak remain required.
M04, M09, and Spatial-M1 remain incomplete.
