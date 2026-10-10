"""Integrate only the exact retained-launch reply candidate qualified on both hosts."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser()
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
r = Path(__file__).resolve().parents[1]
e = r / 'evidence/control-recovery-2026-10-09'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def sha(data):
    return hashlib.sha256(data).hexdigest()

def normalized(path):
    return path.read_bytes().replace(b'\r\n', b'\n')

def clean(log):
    assert '... skipped' not in log and 'skipped=' not in log
    assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL', log)

def direct(log, count):
    clean(log)
    assert re.findall(r'^Ran (\d+) tests? in ', log, re.M) == [str(count)]
    assert log.rstrip().endswith('OK')
    assert len(re.findall(r'^test_.* \.\.\. ok$', log, re.M)) == count

base = read(r / 'local/control-reply-candidate-01.json')['files']
base_draft = r / 'local/control-reply-draft-01'
manifest = read(r / 'local/supervisor-handoff-candidate-01.json')
files = manifest['files']
draft = r / 'local/supervisor-handoff-draft-01'
assert len(base) == 303 and len(files) == 304 and set(base) <= set(files)
assert all(sha(normalized(base_draft / n)) == h for n, h in base.items())
contents = {n: normalized(draft / n) for n in files}
assert {n: sha(data) for n, data in contents.items()} == files
changed = sorted(n for n in files if files[n] != base.get(n))
assert len(changed) == 3
locks = read(r / 'acceptance.lock.json')['files']
assert len(locks) == 41 and locks == manifest['locked_files']
assert all(files[n] == h for n, h in locks.items())
static = read(e / 'handoff-static-review-01.json')
assert static['reviewer'] == 'primary agent'
assert static['source_files'] == files and static['reviewed_files'] == changed
assert static['unresolved_static_defects'] == []
patch = []
for n in changed:
    before = normalized(base_draft / n).decode().splitlines(True) if n in base else []
    patch.extend(difflib.unified_diff(before, contents[n].decode().splitlines(True),
        fromfile='a/' + n if n in base else '/dev/null', tofile='b/' + n))
(e / 'integration-candidate-handoff-01.patch').write_bytes(''.join(patch).encode())
prepared = dict(source_files=files, base_files=base, changed_files=changed, integrated=False,
    expected_core_checks=1498, expected_core_suites=103, expected_native_world_checks=3,
    expected_harness_checks=51, expected_P10_cases=151, production_recovery_complete=False)
(e / 'integration-prepared-handoff-01.json').write_bytes((json.dumps(prepared, indent=2) + '\n').encode())
if not args.apply:
    print('Prepared exact three-file handoff integration. Full qualification and final review remain required.')
    raise SystemExit(0)

if not all((r / n).exists() and sha(normalized(r / n)) == h for n, h in base.items()):
    raise SystemExit('Integration refused: exact reply01 production base is not integrated')
assert all(not (r / n).exists() for n in set(files) - set(base))
parent = read(e / 'integration-applied-reply-01.json')
assert parent['integrated'] and parent['source_files'] == base

proofs = {}
for host in ('sb16', 'sb16-2'):
    folder = e / 'qualified-handoff01' / host
    if not (folder / 'summary-final.json').exists():
        raise SystemExit('Integration refused: missing final handoff01 evidence for ' + host)
    summary = read(folder / 'summary-final.json')
    assert summary['host'] == host and summary['candidate'] == 'handoff01' and summary['mode'] == 'final'
    assert summary['source_manifest'] == files and summary['locked_files'] == 41
    for name, digest in summary['attachment_hashes'].items():
        target = (folder / name).resolve()
        assert target.is_relative_to(folder.resolve())
        assert sha(target.read_bytes()) == digest
    assert read(folder / 'source-manifest.json') == manifest
    run = read(folder / 'run-final.json')
    assert run['host'] == host and run['status'] == 'passed' and not run['compiler_overlap']
    assert run['source_manifest'] == files
    monitor = [json.loads(line) for line in (folder / 'compiler-monitor-final.jsonl').read_text().splitlines()]
    assert monitor and not any(sample['compilers'] for sample in monitor)
    assert set(summary['profiles']) == {'debug', 'runtime-sanitize'}
    for profile in ('debug', 'runtime-sanitize'):
        value = summary['profiles'][profile]
        assert tuple(value[k] for k in ('core_checks', 'core_suites', 'handoff_checks', 'native_world_checks',
            'harness_checks', 'P10_cases', 'failed', 'skipped')) == (1498, 103, 2, 3, 51, 151, 0, 0)
        commands = run['profiles'][profile]['results']
        assert [x['name'] for x in commands] == ['configure', 'build', 'harness', 'handoff', 'world', 'core', 'P10']
        assert all(x['exit_code'] == 0 for x in commands)
        assert value['commands'] == commands and value['binaries'] == run['profiles'][profile]['binaries']
        assert monitor[0]['time'] <= commands[2]['started']
        assert monitor[-1]['time'] >= commands[-1]['ended'] - 2
        p = folder / profile
        focused = (p / 'handoff.log').read_text()
        clean(focused)
        assert re.findall(r'^\s*Start\s+\d+:\s+(\S+)', focused, re.M) == ['supervisor-handoff-lifetime']
        assert re.findall(r'^\d+: Ran (\d+) tests? in ', focused, re.M) == ['2']
        assert '100% tests passed, 0 tests failed out of 1' in focused
        direct((p / 'world.log').read_text(), 3)
        core = (p / 'core.log').read_text()
        clean(core)
        names = re.findall(r'^\s*Start\s+\d+:\s+(\S+)', core, re.M)
        counts = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', core, re.M)))
        assert len(names) == len(set(names)) == len(counts) == 102 and sum(counts) == 1496
        assert '100% tests passed, 0 tests failed out of 102' in core
        assert value['core_parts'] == dict(zip(names, counts))
        assert value['core_parts']['control-owner-channels'] == 21
        assert value['core_parts']['authority-control-binding'] == 6
        assert value['core_parts']['control-supervisor-reply'] == 16
        assert {x['name'] for x in read(p / 'ctest-inventory.json')['tests']} == set(names) | {'supervisor-handoff-lifetime'}
        harness = (p / 'harness.log').read_text()
        clean(harness)
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        gate = read(p / 'P10.json')
        assert gate['status'] == 'passed' and gate['executed'] == len(gate['tests']) == 151
        assert all(x['status'] == 'passed' for x in gate['tests'])
        clean((p / 'P10.log').read_text())
        config = read(p / 'sanitizer-config.json')
        assert config['cmake_sanitizers'] == ('ON' if profile == 'runtime-sanitize' else 'OFF')
        assert config['ASAN_OPTIONS'] == 'detect_leaks=1:abort_on_error=1'
        assert config['UBSAN_OPTIONS'] == 'halt_on_error=1:print_stacktrace=1'
        witnesses = list((p / 'witness').glob('*.json'))
        assert len(witnesses) == 3
        for witness_path in witnesses:
            witness = read(witness_path)
            assert witness['native_release_confirmed'] and witness['application_alive']
            assert not witness['production_supervisor_binding'] and not witness['production_recovery_release']
        baseline = (folder / ('baseline-' + profile) / 'handoff.log').read_text()
        assert run['baseline'][profile]['retained_copies_reproduced']
        assert run['baseline'][profile]['results'][0]['exit_code'] == 1
        assert 'Ran 2 tests' in baseline and 'FAILED (failures=2)' in baseline and 'ERROR:' not in baseline
        assert set(re.findall(r'^FAIL: (test_\w+)', baseline, re.M)) == {
            'test_selected_endpoint_has_no_supervisor_copy_after_handoff',
            'test_child_stdout_has_no_supervisor_writer_after_handoff'}
        assert 'Supervisor retained an endpoint after its only child exited' in baseline
    proofs[host] = sha((folder / 'summary-final.json').read_bytes())

final = read(e / 'integration-primary-review-handoff-01.json')
assert final['reviewer'] == 'primary agent' and final['integration_ready'] is True
assert final['source_files'] == files and final['reviewed_files'] == changed
assert final['summary_hashes'] == proofs and final['unresolved_defects'] == []
for n in changed:
    (r / n).parent.mkdir(parents=True, exist_ok=True)
    (r / n).write_bytes(contents[n])
assert all(sha(normalized(r / n)) == h for n, h in files.items())
prepared.update(integrated=True, summary_hashes=proofs)
(e / 'integration-applied-handoff-01.json').write_bytes((json.dumps(prepared, indent=2) + '\n').encode())
print('Integrated exact handoff01. Production supervisor recovery remains a separate required step.')

