"""Require primary review and pinned evidence before downstream M04 checks."""
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_reviewed_gate(root):
    gate_path = root / 'local/m04-full-gate-01/run.json'
    gate = json.loads(gate_path.read_text())
    primary = json.loads((gate_path.parent / 'primary-review.json').read_text())
    assert primary['candidate'] == gate['candidate'] == 'f74c6b579c6149f41e1cbd17b025eb92fd453009'
    assert primary['gate_sha256'] == digest(gate_path)
    assert primary['host'] == gate['host']
    assert set(gate['profiles']) == {'Debug', 'ASan-UBSan'}
    for name, expected in gate['source_sha256'].items():
        assert digest(root / name) == expected, name
    if gate['status'] == 'commands_passed_needs_review':
        assert primary['status'] == 'reviewed_local_M04_gate_pending_crosshost'
        assert all(row['status'] == 'commands_passed_needs_review' for row in gate['profiles'].values())
        return gate

    assert gate['status'] == 'failed', 'Existing gate must finish'
    assert primary['status'] == 'reviewed_local_M04_gate_with_fixture_corrections'
    audit_path = gate_path.parent / 'artifact-check.json'
    assert primary['artifact_check_sha256'] == digest(audit_path)
    audit = json.loads(audit_path.read_text())
    assert audit['gate_sha256'] == digest(gate_path)
    assert set(audit['profiles']) == {'Debug', 'ASan-UBSan'}
    assert not audit['sanitizer_findings']
    corrected = {
        'storage': ({'session-storage-owner': 2, 'session-storage-contention': 3},
                    Path('/home/dflanag3/src/spacial_desktop-m04-fixture-20261011/spatial-os-luna-handoff/local/m04-storage-fixture-correction-01')),
        'deadlines': ({'world-revision-interleave': 4, 'workspace-bootstrap-owner-idle': 1},
                      root / 'local/m04-setup-deadline-correction-01'),
    }
    assert set(primary['corrections']) == set(corrected)
    covered = set()
    for label, (suites, directory) in corrected.items():
        report_path = directory / 'run.json'
        assert primary['corrections'][label] == digest(report_path)
        report = json.loads(report_path.read_text())
        assert report['status'] == 'passed_needs_primary_review'
        assert report['host'] == gate['host']
        assert report['gate_sha256'] == digest(gate_path)
        assert len(report['results']) == 4
        wanted = {(profile, suite) for profile in gate['profiles'] for suite in suites}
        assert {(row['profile'], row['suite']) for row in report['results']} == wanted
        for row in report['results']:
            assert row['passed'] and row['exit_code'] == 0
            assert row['checks'] == suites[row['suite']]
            assert digest(directory / row['profile'] / row['suite'] / 'test.log') == row['log_sha256']
        covered.update(suites)
    # Pin the complete audit. Permit only failures covered by reviewed corrections.
    expected_errors = ['Gate reports failure']
    for profile, row in audit['profiles'].items():
        assert (row['suites'], row['harness'], row['acceptance'], row['storage']) == (141, 51, 151, 5)
        failed = row['failed_or_skipped']
        assert set(failed) <= covered
        if failed:
            expected_errors.extend([profile + ': nonzero command exit',
                profile + ': failed, skipped, or unrun suites: ' + repr(failed)])
    assert audit['errors'] == expected_errors, audit['errors']
    return gate
