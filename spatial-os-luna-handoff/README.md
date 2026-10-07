# Spatial OS — Luna test-first handoff, v0.1.0

A **full spatial Linux/X11 operating environment**, not a 3D launcher over an existing desktop. Humans and agents occupy rooms, use live application surfaces, and work in real Git worktrees. The X server may be modified. Existing application compatibility is not a requirement.

**The production runtime now passes the 151-case P00-P10 automated baseline on both authorized hosts.** It includes worldd, spatialwm, workspaced, and the maintained XLibre input extension.
Debug and ASan/UBSan results are recorded in `CURRENT_STATE.md` and `docs/WALKTHROUGHS.md`.
Mandatory Spatial-M1 requirements and native hardware qualification remain unfinished.
Use `docs/MILESTONES.md` for the full plan. A passing baseline does not establish full Spatial-M1 or LOCAL_READY.

Start with `START_HERE_LUNA.md`. Codex instructions are in `AGENTS.md`. A ready-to-paste driver prompt is in `PASTE_TO_CODEX.txt`.

## What is executable

- `tests/acceptance/`: 151 named acceptance tests, plus seeded and parameterized checks within those tests, in 11 cumulative phases P00–P10. They call a production test-host interface that Luna must implement.
- `native/xprobe.c`: independent real X client. It draws known and random patterns, records actual X events, creates a transient, resizes, unmaps/remaps, and captures X drawables.
- `harness/`: strict subprocess RPC, deadlines, concurrent response matching, external Git fixtures, independent geometry and pixel oracles.
- `tests/harness/`: tests of the test apparatus, including negative cases. These do not certify the OS.
- `tools/gate.py`: cumulative phase runner. Missing SUT, changed locked tests, zero selected tests, and absent required display configuration do not pass. Skips and expected failures do not pass.
- `tools/delegate.py`: foreground request to a configured local Chat Completions helper. It retains a proposed patch but does not apply or execute it.
- `tools/probe_smoke.py`: tests the C witness on a disposable Xvfb it starts and stops itself.

## Commands

Run from this directory. Python 3.11+ and its standard library are sufficient for the harness; no pip dependency is needed.

```sh
python3 -m unittest discover -s tests/harness -t . -v
python3 tools/build_probe.py
python3 tools/probe_smoke.py
python3 tools/gate.py --list
```

The XI2 build is required for multi-actor target tests:

```sh
python3 tools/build_probe.py --xi2
python3 tools/probe_smoke.py --xi2 --report evidence/native-probe-xi2-report.json
```

It requires development headers/libraries for Xlib and libXi. Inspect the target distribution before installing dependencies. The initial core-only witness smoke is not an XI2 certification.

After Luna creates the production adapter:

```sh
python3 tools/gate.py --through P01 \
  --sut '["/absolute/path/to/build/spatial-test-host"]'
```

For P04 onward, explicitly select a dedicated test X display and its correct authority, then run the cumulative gate. Do not copy a made-up display number:

```sh
export SPATIAL_TEST_DISPLAY="$ACTUAL_DEDICATED_TEST_DISPLAY"
export XAUTHORITY="$ACTUAL_TEST_XAUTHORITY"
python3 tools/gate.py --through P10 \
  --sut '["/absolute/path/to/build/spatial-test-host"]'
```

`--case SUBSTRING` is a diagnostic filter, not a completed milestone. A normal successful cumulative run exits 0; a tested failure exits 1; missing prerequisites/integrity problems exit 2. Physical hardware acceptance is separate: `docs/HARDWARE_ACCEPTANCE.md`.

## Files to read

| File | Purpose |
|---|---|
| `START_HERE_LUNA.md` | Exact driver loop and first task |
| `AGENTS.md` | Repository-wide rules for Luna and helpers |
| `docs/CONTRACT.md` | Normative inputs, outputs, units, lifecycle and error semantics |
| `docs/ARCHITECTURE.md` | Small architecture and changes from the earlier proposal |
| `docs/TEST_STRATEGY.md` | What each type of evidence does and does not prove |
| `docs/PRIMITIVE_MATRIX.md` | Every test mapped to its primitive and phase |
| `tasks/INDEX.json` | Phase instructions and per-primitive tickets |
| `docs/HELPER.md` | Helper setup and review/merge discipline |
| `docs/BASE_AND_MACHINE.md` | Upstream selection, source pinning and machine recovery |
| `docs/HARDWARE_ACCEPTANCE.md` | Real SpaceMouse, GPU, native session and reboot gates |
| `docs/SOURCES.md` | Official upstream references checked for this handoff |
| `evidence/VALIDATION.md` | Actual archive validation, with unrun work explicitly separated |

The only authorized project hosts are `sb16` and `sb16-2`; each has 6 GB VRAM, 16 GB RAM, and an SSD, as reported by the user. See `AGENTS.md` and `docs/BASE_AND_MACHINE.md` for host restrictions and the verified package baseline. No private helper endpoint, local model ID, GPU driver version, or existing source repository has been assumed. Discover those on an authorized host or supply them in local configuration. The user's reported helper throughput is not a benchmark guarantee.
