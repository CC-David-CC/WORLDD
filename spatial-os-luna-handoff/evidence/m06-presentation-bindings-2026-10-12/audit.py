import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import xml.etree.ElementTree as ET

root = Path('/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff')
out = root / ('local/' + (sys.argv[1] if len(sys.argv)>1 else 'm06-presentation-input-03'))
record = json.loads((out / 'run.json').read_text())
assert record['status'] == 'commands_passed_needs_review', record['status']
assert record['host'] == socket.gethostname()
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert all(sha(root / name) == digest for name, digest in record['source_sha256'].items())
reviewed = json.loads((root/'local/m06-reviewed-source.json').read_text())
reviewed.update(record['source_sha256'])
assert all(sha(root/name)==digest for name,digest in reviewed.items())
locked = json.loads((root/'acceptance.lock.json').read_text())['files']
assert all(sha(root/name)==digest for name,digest in locked.items())
expected = set(record['selected'])
assert len(expected) == 7
audit = dict(host=record['host'], source_sha256=record['source_sha256'], profiles={},
             phase_complete=False, scope='Real Renderer world input bindings; no compositor process transport or world-transition qualification')
markers = re.compile(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|SUMMARY: .*Sanitizer|runtime error:|AddressSanitizer:DEADLYSIGNAL')
for profile in ('Debug', 'ASan-UBSan'):
    row = record['profiles'][profile]
    assert row['status'] == 'commands_passed_needs_review'
    assert all(r['exit_code'] == 0 for r in record['commands'])
    directory = out / profile
    suite = ET.parse(directory / 'results.xml').getroot()
    cases = suite.findall('testcase')
    assert len(cases) == len(expected) and {c.attrib['name'] for c in cases} == expected
    assert all(suite.attrib.get(k, '0') == '0' for k in ('failures', 'errors', 'disabled', 'skipped'))
    counts = {}
    verbose = (out / (profile + '-focused.log')).read_text()
    for case in cases:
        assert case.attrib['status'] == 'run'
        assert not any(case.find(k) is not None for k in ('failure', 'error', 'skipped'))
        # CTest truncates successful JUnit output. Review the retained verbose log.
        starts = re.findall(r'^\s*Start\s+(\d+): ' + re.escape(case.attrib['name']) + r'\s*$', verbose, re.M)
        assert len(starts) == 1
        prefix = starts[0] + ': '
        output = '\n'.join(line[len(prefix):] for line in verbose.splitlines() if line.startswith(prefix))
        matches = re.findall(r'Ran (\d+) tests? in ', output)
        assert len(matches) == 1, (profile, case.attrib['name'], matches)
        assert re.search(r'^OK\s*$', output, re.M), (profile, case.attrib['name'])
        assert not re.search(r'\bskipped=|\bFAILED\b', output)
        counts[case.attrib['name']] = int(matches[0])
    server = json.loads((root/'local/m06-scoped-input-05'/profile/'server-build/run.json').read_text())
    assert server['status'] == 'passed'
    assert server['sanitizers'] == (profile == 'ASan-UBSan')
    assert all(row['exit_code'] == 0 for row in server['commands'])
    for binary in server['binaries'].values():
        assert sha(Path(binary['path'])) == binary['sha256']
    logs = sorted(directory.rglob('*.log')) + sorted(out.glob(profile+'-*.log'))
    assert logs
    for path in logs:
        assert not markers.search(path.read_text(errors='replace')), str(path)
    audit['profiles'][profile] = dict(suites=len(expected), checks=sum(counts.values()), counts=counts,
        failed=0, skipped=0, logs_scanned=len(logs),
        server_binaries=server['binaries'],
        artifacts={str(p.relative_to(out)): sha(p) for p in [*sorted(directory.rglob('*')), *sorted(out.glob(profile+'-*.log'))] if p.is_file()})
audit['reviewed_source_sha256'] = reviewed
audit['status'] = 'passed_focused_artifact_review'
(out / 'audit.json').write_text(json.dumps(audit, indent=2) + '\n')
print(json.dumps({k: v for k, v in audit.items() if k != 'profiles'}, indent=2))
for profile, row in audit['profiles'].items():
    print(profile, {k: v for k, v in row.items() if k != 'artifacts'})
