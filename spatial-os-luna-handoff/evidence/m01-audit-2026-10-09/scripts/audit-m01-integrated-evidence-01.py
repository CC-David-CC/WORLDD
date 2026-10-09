"""Audit retained execution evidence; do not run Linux project tests on Windows."""
import hashlib, json, pathlib, re

r = pathlib.Path(__file__).resolve().parents[1]
e = r / 'evidence/process-termination-2026-10-09'
manifest = json.loads((e / 'integration-applied-02.json').read_text())['files']
for name, digest in manifest.items():
    assert hashlib.sha256((r / name).read_bytes()).hexdigest() == digest, name
required_suites = {
    'identity-keys':16, 'identity-issuer':18, 'identity-enrollment':25,
    'identity-bootstrap':18, 'identity-revocation':24, 'identity-tls':23,
    'issuer-client':28, 'identity-watch':17, 'actor-status':21,
    'world-identity':20, 'world-remote-retries':5, 'world-remote-security':13,
    'world-remote-lifecycle':6, 'world-remote-native':3, 'world-remote-races':2,
    'world-remote-config':12, 'world-remote':5, 'authority-bootstrap':32,
    'world-bootstrap':15, 'world-bootstrap-races':6, 'workspace-bootstrap':11,
    'workspace-bootstrap-races':10, 'broker-bootstrap-client':22,
}
explicit_tests = []
for name in ('identity.issuer_control', 'identity.manual_enrollment', 'identity.bootstrap_profile', 'identity.credential_revocation'):
    task = json.loads((r / 'tasks/m1' / (name + '.json')).read_text())
    explicit_tests += task['test_ids']
records = []
for host in ('sb16', 'sb16-2'):
    folder = e / 'candidate-02' / host
    summary = json.loads((folder / 'summary-final.json').read_text())
    assert json.loads((folder / 'source-manifest.json').read_text())['files'] == manifest
    adoption = json.loads((e / ('process-termination-adoption-' + host + '-02.json')).read_text())
    assert adoption['source_hashes_matched'] == 242 and adoption['commit'].startswith('94c6cb6')
    for profile in ('debug', 'runtime-sanitize'):
        result = summary[profile]
        assert tuple(result[key] for key in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped')) == (1192,69,51,151,0,0)
        assert all(result['parts']['core']['suites'].get(name) == count for name, count in required_suites.items())
        log = (folder / profile / 'core.log').read_text()
        assert '100% tests passed, 0 tests failed out of 68' in log
        for test in explicit_tests:
            method = test.split('.')[-1]
            assert method + ' (__main__.' + test + ') ... ok' in log, (host, profile, test)
        for name in ('core', 'termination'):
            text = (folder / profile / (name + '.log')).read_text()
            assert '... skipped' not in text and 'skipped=' not in text
        report = json.loads((folder / profile / 'P10.json').read_text())
        assert report['status'] == 'passed' and report['executed'] == 151 and all(item['status'] == 'passed' for item in report['tests'])
        records.append(dict(host=host, profile=profile, core_checks=1192, harness_checks=51, P10_cases=151,
            verified_explicit_identity_test_ids=len(explicit_tests), selected_identity_suite_counts=required_suites,
            source='evidence/process-termination-2026-10-09/candidate-02/' + host))
world = (r / 'src/world/identity_sessions.cpp').read_text()
workspace = (r / 'src/workspace/authority.cpp').read_text()
assert 'IssuerWatcher' in world and 'ParticipantRuntime' not in world
assert 'ParticipantRuntime' not in workspace
gap = json.loads((r / 'evidence/revocation-barrier-2026-10-09/baseline-01/sb16/observation.json').read_text())
assert gap['status'] == 'gap_reproduced' and gap['motion_after_commit_reply'] > 0
out = r / 'evidence/m01-audit-2026-10-09'
out.mkdir(exist_ok=True)
result = dict(status='M01 pending; substantial integrated component evidence verified.',
    integrated_source_commit='94c6cb6948f260898fb6c1bd4d1b7a5023d7c2d0', source_files=242,
    evidence=records, explicit_identity_test_ids=explicit_tests,
    remaining=['Production world and workspace services do not yet use ParticipantRuntime.',
               'Issuer durable commit and checked distributed cancellation remain separate.',
               'Finish participant/supervisor integration and test delayed, failed, and cross-host cancellation before full M01/M02 sign-off.'],
    historical_gap=gap, limitations=['This audits existing execution evidence. It does not run new runtime tests.',
        'Component counts do not establish an overall completion percentage.',
        'No M01 or Spatial-M1 completion status was changed.'])
(out / 'integrated-evidence-01.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps(dict(source_files=242, profiles=4, explicit_identity_tests=len(explicit_tests), M01='pending', new_runtime_tests=False)))
