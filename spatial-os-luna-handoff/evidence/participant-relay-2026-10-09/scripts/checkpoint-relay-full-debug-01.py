import ast, hashlib, json, pathlib, shutil, tarfile

r = pathlib.Path(__file__).resolve().parents[1]
relay = r / 'evidence/participant-relay-2026-10-09'
diagnostic = r / 'evidence/native-scope-config-2026-10-09'
(diagnostic / 'scripts').mkdir(parents=True, exist_ok=True)
for host in ('sb16', 'sb16-2'):
    with tarfile.open(r / ('local/participant-relay-full-debug-' + host + '-01.tar.gz')) as archive:
        archive.extractall(relay, filter='data')
    summary = json.loads((relay / 'full-candidate-01' / host / 'summary-debug.json').read_text())
    result = summary['debug']
    assert result['core_checks'] == 1366 and result['core_suites'] == 89 and result['harness_checks'] == 51
    assert result['P10_cases'] == 151 and result['failed'] == result['skipped'] == 0
names = ('native-scope-config-witness.cpp', 'test_native_scope_config_ownership.py',
         'diagnose-native-scope-config-01.py', 'query-native-scope-build-01.py')
hashes = {}
for name in names:
    path = r / 'local' / name
    if name.endswith('.py'):
        ast.parse(path.read_text())
    shutil.copyfile(path, diagnostic / 'scripts' / name)
    hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
(diagnostic / 'preparation.json').write_text(json.dumps(dict(status='Witness prepared, not built or run.',
    production_changed=False, files=hashes, reason='Static borrowed/consumed descriptor mismatch in NativeScope caller chain.',
    next='Run after complete relay matrix; require the exact ownership failure, not an unrelated fixture error.'), indent=2) + '\n', encoding='utf-8', newline='\n')
path = r / 'CURRENT_STATE.md'
text = path.read_text(encoding='utf-8')
entry = '''## 2026-10-09: Relay full Debug passes; native configuration ownership witness prepared

Relay candidate01 completed full Debug on both sb16 and sb16-2.
Each host passed 1366 core checks across 89 suites, 51 harness checks, and 151 P10 cases.
No Debug case failed or skipped. All 277 source hashes and 41 locked hashes match.
Full runtime sanitizer qualification remains active on the same binaries.
Read evidence/participant-relay-2026-10-09/full-candidate-01.

Native handoff review identified a descriptor ownership mismatch in the existing isolated coordinator.
AuthorityLaunch retains a local configuration descriptor that NativeScope already consumes.
The later caller close can affect a reused descriptor number.
The prepared witness checks retained configuration ownership and unrelated descriptor survival.
It will link unchanged frozen native objects after the current timed qualification finishes.
No witness build or runtime reproduction is claimed yet.
Read docs/NATIVE_SCOPE_DESCRIPTOR_OWNERSHIP.txt and evidence/native-scope-config-2026-10-09.

Supervisor candidate01 qualification is held for this focused ownership diagnosis and correction.
Its copied build driver has not run. No supervisor code is integrated.
Do not attribute the earlier world startup failures to this ownership mismatch without evidence.
M02, native handoff, checked recovery, and full Spatial-M1 remain incomplete.

'''
assert entry.splitlines()[0] not in text
path.write_text(text.replace('# Implementation status\n\n', '# Implementation status\n\n' + entry, 1), encoding='utf-8', newline='\n')
for name in ('tasks/m1/identity.participant_relay.json', 'evidence/participant-relay-2026-10-09/source-review.json'):
    path = r / name; value = json.loads(path.read_text())
    value['full_Debug'] = 'Both hosts passed1366core/89suites,51harness,151P10; no failures or skips. Runtime full qualification remains active.'
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8', newline='\n')
for name in ('tasks/m1/identity.authority_supervisor.json', 'evidence/authority-supervisor-2026-10-09/source-review.json'):
    path = r / name; value = json.loads(path.read_text())
    value['native_scope_review'] = 'Static descriptor ownership mismatch found in inherited coordinator code. Witness prepared but unrun. Resolve before candidate qualification and integration.'
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8', newline='\n')
path = r / 'local/driver-state.json'; value = json.loads(path.read_text())
value['previous_goal_turn'] = 'progress:65112eb preserved failures and implemented isolated supervisor candidate with14 unrun tests.'
value['current_step'] = 'relay_full_sanitizers_and_native_descriptor_ownership_diagnosis'
value['last_turn'] = 'Progress: both-host full relay Debug collected;1366core89suites51harness151P10 each. Static NativeScope consumed/borrowed mismatch identified. Prepared independent unchanged-object witness; compile waits for timed matrix.'
value['next_test'] = 'Poll77212/87281. Collect final relay only if both profiles pass. Then run local/diagnose-native-scope-config-01.py on each host BEFORE supervisor candidate builds. Exact ownership failure required. Preserve failure; correct borrowed NativeScope input and consuming NativeLaunch adapter; add regression before supervisor candidate02 qualification. Do not start copied candidate01 supervisor driver yet.'
value['authority_supervisor']['qualification'] = 'held for native configuration ownership diagnosis; candidate01 unrun and unintegrated'
value['native_scope_config'] = dict(status='static mismatch; witness prepared and unrun', driver='local/diagnose-native-scope-config-01.py', production_changed=False)
path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8', newline='\n')
shutil.copyfile(pathlib.Path(__file__), relay / 'scripts' / pathlib.Path(__file__).name)
print('Verified both-host full Debug summaries; saved ownership witness and next action.')
