"""Validate an explicitly selected qualification attempt for candidate02."""
import hashlib
import json


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_qualification(base, host, attempt):
    assert host in ('sb16', 'sb16-2')
    assert attempt in ('m02-phase-gate-02', 'm02-untraced-03')
    gate = base/'m02-phase-gate-02'
    selected = base/attempt
    read = lambda path: json.loads(path.read_bytes())
    manifest = read(gate/'manifest.json')
    original = read(gate/'summary.json')
    summary = read(selected/'summary.json')
    review = read(selected/'primary-review.json')
    assert sha(selected/'manifest.json') == sha(gate/'manifest.json')
    assert original['host'] == summary['host'] == review['host'] == host
    assert manifest['candidate'] == original['candidate'] == summary['candidate'] == review['candidate'] == 'm02-phase-gate-02'
    assert manifest['commit'] == original['commit'] == summary['commit'] == review['commit']
    assert summary['status'] == review['run_status'] == 'commands_passed_results_need_review'
    assert 'ended' in original and 'ended' in summary
    assert not summary['compiler_overlap'] and not summary['monitor_errors']
    assert summary['server'] == original['server']
    assert review['attachments'][attempt+'/summary.json'] == sha(selected/'summary.json')
    assert review['attachments'][attempt+'/manifest.json'] == sha(selected/'manifest.json')
    if attempt == 'm02-untraced-03':
        assert original['status'] == 'failed'
        assert summary['attempt'] == attempt
        assert summary['source_root'] == str(gate/'source')
        assert summary['prior_summary_sha256'] == sha(gate/'summary.json')
        assert summary['prior_review_sha256'] == sha(gate/'primary-review.json')
        assert summary['prior_evidence_archive_sha256'] == sha(base/('m02-phase-gate-evidence-02-'+host+'.tar.gz'))
        assert summary['writeback_boundary']['exit_code'] == 0
        assert summary['writeback_boundary']['argv'] == ['sync', '-f', str(gate)]
        assert summary['writeback_boundary']['log_sha256'] == sha(selected/'writeback-sync.log')
    for profile in ('debug', 'runtime-sanitize'):
        built = {item['label']: item for item in original['profiles'][profile]['commands']}
        assert built['configure']['exit_code'] == built['build']['exit_code'] == 0
        entry = summary['profiles'][profile]
        assert entry['binaries'] and entry['binaries'] == original['profiles'][profile]['binaries']
        if attempt == 'm02-untraced-03':
            assert summary['build_roots'][profile] == str(gate/profile/'build')
        commands = {item['label']: item for item in entry['commands']}
        assert len(commands) == len(entry['commands'])
        assert all(item.get('exit_code') == 0 for item in commands.values())
        for label, part in (('core', 'core'), ('harness', 'harness'), ('P10', 'acceptance')):
            assert not commands[label]['traced'] and commands[label]['qualification']
            assert review['profiles'][profile][part]['status'] == 'passed'
        assert review['profiles'][profile]['core']['selected_suites'] == 124
        assert review['profiles'][profile]['harness']['executed'] == 51
        assert review['profiles'][profile]['acceptance']['executed'] == 151
    binding = dict(attempt=attempt, summary_sha256=sha(selected/'summary.json'),
        review_sha256=sha(selected/'primary-review.json'), manifest_sha256=sha(gate/'manifest.json'))
    return manifest, summary, review, binding
