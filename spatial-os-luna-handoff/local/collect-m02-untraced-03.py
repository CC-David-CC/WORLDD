"""Preserve a terminal M02 run and review its complete or failed segments."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile
import xml.etree.ElementTree as ET

host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
gate = base/'m02-phase-gate-02'
root = base/'m02-untraced-03'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
run = json.loads((root/'summary.json').read_bytes())
assert 'ended' in run and run['status'] not in ('building', 'qualifying')
assert run['host'] == host
manifest = json.loads((root/'manifest.json').read_bytes())
assert run['attempt'] == 'm02-untraced-03'
assert run['source_root'] == str(gate/'source')
assert run['prior_summary_sha256'] == sha(gate/'summary.json')
assert run['prior_review_sha256'] == sha(gate/'primary-review.json')
assert run['prior_evidence_archive_sha256'] == sha(base/('m02-phase-gate-evidence-02-'+host+'.tar.gz'))
assert len(manifest['files']) == 351 and len(manifest['locked_files']) == 41
assert all(sha(gate/'source'/name) == digest for name, digest in manifest['files'].items())
assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
assert sha(Path(run['server']['path'])) == run['server']['sha256']
attachments = {}

def add(path):
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base)
    assert path.suffix in ('.json', '.jsonl', '.log', '.xml', '.py', '.txt', '.bt')
    data = path.read_bytes()
    assert b'-----BEGIN '+b'PRIVATE KEY-----' not in data, path
    assert not re.search(rb'Bearer '+rb'fleet-[A-Za-z0-9_-]{12,}', data), path
    attachments[path.relative_to(base).as_posix()] = sha(path)

review = dict(host=host, candidate=manifest['candidate'], commit=manifest['commit'],
    status='reviewed_automated_segments_phase_pending', run_status=run['status'],
    source_files=351, locked_files_unchanged=41, profiles={},
    historical_failures_resolved=False, phase_complete=False)
review['writeback_boundary'] = run.get('writeback_boundary')
review['prior_evidence_archive_sha256'] = run['prior_evidence_archive_sha256']
for name in ('manifest.json', 'summary.json', 'resource-monitor.jsonl', 'writeback-sync.log'):
    if (root/name).exists():add(root/name)
for name in ('qualify-m02-untraced-03.py', 'collect-m02-untraced-03.py'):
    add(base/name)
for profile in ('debug', 'runtime-sanitize'):
    entry = run['profiles'].get(profile)
    result = dict(status='unrun', harness='unrun', core='unrun', acceptance='unrun', issuer_loop_delays=[])
    review['profiles'][profile] = result
    if not entry:
        continue
    build = gate/profile/'build'
    assert run['build_roots'][profile] == str(build)
    target = root/profile/'checks'
    commands = {item['label']: item for item in entry['commands']}
    for item in commands.values():
        if 'sha256' in item:
            assert sha(target/(item['label']+'.log')) == item['sha256']
    if 'binaries' in entry:
        assert all(sha(build/name) == digest for name, digest in entry['binaries'].items())
        cache = (build/'CMakeCache.txt').read_text()
        assert 'CMAKE_BUILD_TYPE:STRING=Debug' in cache
        assert 'SPATIAL_SANITIZERS:BOOL='+('OFF' if profile=='debug' else 'ON') in cache
        add(build/'CMakeCache.txt')
    result['status'] = entry['status']
    if not target.exists():
        assert not commands
        continue
    for path in target.iterdir():
        if path.is_file():
            add(path)
    if (target/'native').exists():
        for path in (target/'native').rglob('*'):
            if path.is_file() and path.suffix == '.log':
                add(path)
                for line in path.read_text(errors='replace').splitlines():
                    if line.startswith('issuer TLS loop delayed '):
                        result['issuer_loop_delays'].append(dict(file=path.relative_to(target).as_posix(), message=line))
    last_test = build/'Testing/Temporary/LastTest.log'
    if 'core' in commands and last_test.exists():
        add(last_test)
    if 'harness' in commands:
        log = (target/'harness.log').read_text()
        counts = re.findall(r'^Ran (\d+) tests? in ', log, re.M)
        assert len(counts) == 1
        passed = commands['harness'].get('exit_code') == 0
        result['harness'] = dict(status='passed' if passed else 'failed', executed=int(counts[0]))
        if passed:
            assert int(counts[0]) == len(entry['selected_harness_cases']) == 51
            assert log.rstrip().endswith('OK') and '... skipped' not in log and 'skipped=' not in log
    if 'core' in commands:
        assert commands['core']['traced'] is False and commands['core']['qualification'] is True
        log = (target/'core.log').read_text()
        selected = entry['selected_ctest_suites']
        counts = [(int(index), int(count)) for index, count in re.findall(r'^(\d+): Ran (\d+) tests? in ', log, re.M)]
        started = re.findall(r'^\s*Start\s+\d+:\s+(\S+)', log, re.M)
        result['core'] = dict(status='failed', selected_suites=len(selected), started_suites=len(started),
            unittest_case_executions=sum(count for _, count in counts),
            unrun_suites=sorted(set(selected)-set(started)), suite_results=[])
        assert len(started) == len(set(started)) and set(started) <= set(selected)
        if (target/'core.xml').exists():
            suites = ET.parse(target/'core.xml').getroot().findall('.//testcase')
            for suite in suites:
                status = 'failed' if suite.find('failure') is not None else 'skipped' if suite.find('skipped') is not None else 'passed'
                if suite.find('error') is not None:
                    status = 'failed'
                if suite.attrib.get('status') == 'notrun':
                    status = 'unrun'
                result['core']['suite_results'].append(dict(name=suite.attrib['name'], status=status,
                    seconds=float(suite.attrib.get('time', '0'))))
        if commands['core'].get('exit_code') == 0:
            assert len(counts) == len(started) == len(selected)
            assert {item['name'] for item in result['core']['suite_results']} == set(selected)
            assert all(item['status'] == 'passed' for item in result['core']['suite_results'])
            assert f'100% tests passed, 0 tests failed out of {len(selected)}' in log
            assert 'skipped=' not in log and '... skipped' not in log
            assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL', log)
            assert run['writeback_boundary']['exit_code'] == 0
            result['core']['status'] = 'passed'
    if 'P10' in commands:
        report = json.loads((target/'P10.json').read_bytes())
        result['acceptance'] = dict(status=report['status'], executed=report['executed'],
            selected=report['selected'], outcomes=report['tests'])
        if commands['P10'].get('exit_code') == 0:
            assert report['status'] == 'passed' and report['executed'] == report['selected'] == len(report['tests']) == 151
            assert {item['test'] for item in report['tests']} == set(entry['selected_acceptance_cases'])
            assert all(item['status'] == 'passed' for item in report['tests'])
    for item in commands.values():
        if item.get('exit_code', 1) != 0:
            result['status'] = 'failed'

observations = [json.loads(line) for line in (root/'resource-monitor.jsonl').read_text().splitlines()]
assert observations
qualified = [item for item in observations if item['phase'] == 'qualifying']
overlap = [item for item in qualified if item['compilers']]
assert bool(overlap) == bool(run['compiler_overlap'])
review['resources'] = dict(samples=len(observations), qualification_samples=len(qualified),
    compiler_overlap_samples=len(overlap), monitor_errors=run['monitor_errors'],
    minimum_free_bytes=min(item['disk_free_bytes'] for item in observations),
    maximum_sample_gap_seconds=max((b['monotonic_ns']-a['monotonic_ns'])/1e9
        for a, b in zip(observations, observations[1:])) if len(observations)>1 else None,
    limitation='One-second observations can miss shorter events. They do not establish a timeout cause.')
review['attachments'] = attachments
review_path = root/'primary-review.json'
with review_path.open('xb') as stream:
    stream.write((json.dumps(review, indent=2)+'\n').encode())
archive = base/('m02-untraced-evidence-03-'+host+'.tar.gz')
with tarfile.open(archive, 'x:gz') as tar:
    for name in sorted(attachments):
        tar.add(base/name, arcname=host+'/'+name, recursive=False)
    tar.add(review_path, arcname=host+'/primary-review.json', recursive=False)
print(json.dumps(dict(host=host, archive=str(archive), sha256=sha(archive), attachments=len(attachments),
    profiles={name:dict(status=result['status'],
        core=result['core'] if isinstance(result['core'], str) else {key:value for key,value in result['core'].items() if key!='suite_results'},
        harness=result['harness'], acceptance=result['acceptance'] if isinstance(result['acceptance'], str) else
        {key:value for key,value in result['acceptance'].items() if key!='outcomes'},
        issuer_loop_delays=len(result['issuer_loop_delays'])) for name,result in review['profiles'].items()})))
