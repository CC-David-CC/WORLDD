Broker control client evidence
Base: 01b49893f0931a0fe45ace7c6abcb9a40ee64bfd

Both hosts passed prerequisite 51 harness checks and 151 P10 cases.
The initial API scaffolding returned UNIMPLEMENTED for the missing client lease methods.
The initial 24 transport cases failed on both hosts: 20 failures and four errors.
This baseline used the existing transport plus explicit API scaffolding, not an unchanged release binary.

Focused-01 passed 24 transport cases on both hosts.
The live suite failed because the new test probe closed the broker socket fully during cleanup.
It also failed the blocked-wait notification case.
The probe now uses the normal broker write half-close and waits for shutdown.

Focused-02 passed 27 transport cases and 24 of 25 live cases on both hosts.
The remaining blocked-wait failure identified missing event draining in the probe wait callback.
The probe now polls notifications through that callback while the outer request remains active.
The client preserves ordinary replies that callback polling receives.

Focused-03 passed 29 transport cases and all 25 real issuer/broker cases on both hosts.
Full qualification passed on both hosts.
No final profile skipped a case.
The native-server profile disables GLX because these broker tests do not render.

Worldd native input cancellation and authenticated application control remain pending.
No source change enables labeled authenticated input in this component.
Native pixels, physical devices, GPU timing, and full Spatial-M1 remain unqualified here.
The primary agent performs review without the local model.

Final checked results, per host:
- Debug: 866 core checks / 44 suites, 51 harness checks, 151 P10 cases.
- Runtime ASan/UBSan: 866 core checks / 44 suites, 51 harness checks, 151 P10 cases.
- Native-server ASan/UBSan: 25 live client cases; GLX disabled; leak detection enabled.
- No final failed or skipped cases.
All six reviewed source/build/test inputs match both host manifests.
Worldd control integration and balanced native input cancellation remain pending.
