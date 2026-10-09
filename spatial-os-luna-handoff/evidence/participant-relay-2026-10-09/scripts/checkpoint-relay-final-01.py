import difflib, json, pathlib, shutil, tarfile

r = pathlib.Path(__file__).resolve().parents[1]
relay = r / 'evidence/participant-relay-2026-10-09'
owned = r / 'evidence/owned-participant-2026-10-09'
supervisor = r / 'evidence/authority-supervisor-2026-10-09'
supervisor.mkdir(exist_ok=True)
for host in ('sb16', 'sb16-2'):
    for name, dest in (
        ('participant-relay-final-' + host + '-01.tar.gz', relay),
        ('owned-participant-full-failure-' + host + '-02.tar.gz', owned),
        ('owned-startup-diagnostic-' + host + '-01.tar.gz', owned),
        ('authority-supervisor-baseline-' + host + '-01.tar.gz', supervisor),
    ):
        with tarfile.open(r / 'local' / name) as archive:
            archive.extractall(dest, filter='data')
for dest, names in (
    (owned, ['collect-owned-participant-full-failure-02.py', 'diagnose-owned-startup-01.py', 'collect-owned-startup-diagnostic-01.py']),
    (supervisor, ['prepare-authority-supervisor-baseline-01.py', 'qualify-authority-supervisor-baseline-01.py', 'collect-authority-supervisor-baseline-01.py']),
):
    (dest / 'scripts').mkdir(exist_ok=True)
    for name in names:
        shutil.copyfile(r / 'local' / name, dest / 'scripts' / name)
shutil.copyfile(r / 'local/authority-supervisor-baseline-01.json', supervisor / 'baseline-source-01.json')
before = r / 'local/participant-relay-draft-01'
after = r / 'local/authority-supervisor-draft-01'
names = ['CMakeLists.txt', 'proto/spatial/supervisor/v1/launch.proto', 'tests/native/test_authority_supervisor.py']
patch = ''
for name in names:
    old = (before / name).read_text().splitlines(keepends=True) if (before / name).exists() else []
    new = (after / name).read_text().splitlines(keepends=True)
    patch += ''.join(difflib.unified_diff(old, new, fromfile='a/' + name if old else '/dev/null', tofile='b/' + name))
(supervisor / 'baseline-01.patch').write_text(patch, encoding='utf-8', newline='\n')
status = r / 'CURRENT_STATE.md'
entry = '''## 2026-10-09: Relay focused matrix passes; broader startup failures remain unresolved

Relay candidate01 passed focused Debug and runtime ASan/UBSan on both hosts.
Each profile passed 366 checks across 23 suites and 51 harness checks.
No case failed or skipped. All 277 source hashes and 41 locked hashes match.
Full cumulative continuation now runs on the same binaries, without overlapping project builds.
No cumulative relay result is claimed yet.

Owned participant candidate01 passed full Debug on both hosts.
Its full sanitizer run failed one startup check on sb16 and two on sb16-2.
The 1339-check core totals were 1338 passed and one failed, then 1337 passed and two failed.
Runtime P10 was unrun after these failures. No overall sanitizer pass is claimed.
Retained service logs show startup transport failures, including an issuer deadline timeout.
The affected logs contain no ASan or UBSan memory report.
Each of the three affected methods then passed five unchanged repetitions on each host.
These 30 diagnostic passes do not establish a cause or replace the original failed qualification.
Production code, fixture deadlines, and assertions remained unchanged.
Read evidence/owned-participant-2026-10-09/full-candidate-01 and startup-diagnostic-01 there.

Both hosts confirmed the missing production supervisor executable.
The baseline builds and 51 harness checks passed; its one new test failed as expected.
The baseline proves a missing executable only. Supervisor behavior remains unqualified.
Read docs/AUTHORITY_SUPERVISOR_RUNTIME.txt and evidence/authority-supervisor-2026-10-09.
No new candidate source is integrated. M02 and Spatial-M1 remain incomplete.

'''
text = status.read_text(encoding='utf-8')
assert entry.splitlines()[0] not in text
status.write_text(text.replace('# Implementation status\n\n', '# Implementation status\n\n' + entry, 1), encoding='utf-8', newline='\n')
task = r / 'tasks/m1/identity.participant_relay.json'
value = json.loads(task.read_text())
value['runtime_sanitizers'] = 'Both hosts passed 366 focused checks across 23 suites and 51 harness checks; no failures or skips.'
value['remaining'][0] = 'Complete both-host cumulative qualification.'
value['next'] = 'Full cumulative continuation is running. Implement the production supervisor event loop, then wire worldd and workspaced.'
task.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8', newline='\n')
driver = r / 'local/driver-state.json'
value = json.loads(driver.read_text())
value['completed_session_handles'].update(value['qualification_sessions'])
value['completed_session_handles'].update(owned_startup_diagnostic_sb16=36866, owned_startup_diagnostic_sb16_2=28366, authority_supervisor_baseline_sb16=15102, authority_supervisor_baseline_sb16_2=82255)
value['qualification_sessions'] = dict(participant_relay_full_sb16=77212, participant_relay_full_sb16_2=87281)
value['current_step'] = 'participant_relay_full_and_authority_supervisor_implementation'
value['previous_goal_turn'] = 'progress: verified completion of both unchanged startup diagnostic runs; 15 passes per host.'
value['last_turn'] = 'Collected relay focused matrix, original owned sanitizer failures, unchanged diagnostics, and missing-supervisor baseline. Started full relay qualification on both hosts.'
value['next_test'] = 'Poll 77212 and 87281. Do not overlap builds. Implement isolated production supervisor and meaningful runtime tests; preserve original failures.'
value['owned_full_sanitizer'] = 'failed: sb16 one startup error; sb16-2 two startup errors; runtime P10 unrun. Exact unchanged repetitions passed 15 per host; cause unresolved.'
driver.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8', newline='\n')
print('Evidence extracted; status, task, and live handles updated.')
