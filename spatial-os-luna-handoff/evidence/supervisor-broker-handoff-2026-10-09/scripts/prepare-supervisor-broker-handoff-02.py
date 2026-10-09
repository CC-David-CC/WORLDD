"""Preserve candidate01 and freeze the explicit observer correction separately."""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

r = Path(__file__).resolve().parents[1]
evidence = r / 'evidence/supervisor-broker-handoff-2026-10-09'
with tarfile.open(r / 'local/supervisor-broker-handoff-failure-sb16-01.tar.gz') as archive:
    archive.extractall(evidence, filter='data')
failure = (evidence / 'candidate-01/sb16/debug/regressions.log').read_text()
assert 'Ran 8 tests' in failure and 'FAILED (errors=2)' in failure
assert failure.count("PermissionError: [Errno 13] Permission denied:") == 2
old = r / 'local/supervisor-broker-handoff-draft-01'
new = r / 'local/supervisor-broker-handoff-draft-02'
old_manifest = json.loads((r / 'local/supervisor-broker-handoff-candidate-01.json').read_text())
new.mkdir()
for name, digest in old_manifest['files'].items():
    assert hashlib.sha256((old / name).read_bytes()).hexdigest() == digest, name
    target = new / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(old / name, target)
name = 'tests/native/test_supervisor_broker_handoff_regressions.py'
path = new / name
before = path.read_text()
after = before.replace('import os\n', 'import json\nimport os\n', 1)
start = '''        # NativeLaunch consumes the sealed configuration descriptor during startup.
        self.assertFalse(Path(f'/proc/{child_pid}/fd/38').exists())
        pin = os.pidfd_open(child_pid)
        self.addCleanup(os.close, pin)
'''
replacement = '''        pin = os.pidfd_open(child_pid)
        self.addCleanup(os.close, pin)
        # NativeLaunch consumes this descriptor and disables unprivileged inspection.
        self.assertNotIn(38, self.observed_descriptors(child_pid, pin))
'''
assert after.count(start) == 1
after = after.replace(start, replacement)
marker = '    def create_application(self):\n'
addition = '''    def observed_descriptors(self, pid, pin):
        # The pidfd must remain live across observation. PID reuse cannot pass.
        self.assertEqual(select.select([pin], [], [], 0)[0], [])
        observer = subprocess.run(
            ['sudo', '-n', 'python3', '-c',
             'import json, os, sys; '
             'print(json.dumps(sorted(int(fd) for fd in os.listdir("/proc/" + str(int(sys.argv[1])) + "/fd"))))',
             str(pid)], capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(select.select([pin], [], [], 0)[0], [])
        descriptors = json.loads(observer.stdout)
        self.assertTrue({0, 1, 2}.issubset(descriptors), descriptors)
        return descriptors

    def test_privileged_observer_detects_open_and_closed_fd_on_non_dumpable_process(self):
        code = (
            'import ctypes, os, sys; '
            'fd = os.open("/dev/null", os.O_RDONLY); os.dup2(fd, 38); '
            'assert ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) == 0; '
            'print("open", flush=True); sys.stdin.readline(); '
            'os.close(38); print("closed", flush=True); sys.stdin.readline()')
        child = subprocess.Popen(['python3', '-c', code], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.addCleanup(cleanup)
        pin = os.pidfd_open(child.pid)
        self.addCleanup(os.close, pin)
        self.assertTrue(select.select([child.stdout], [], [], 5)[0])
        self.assertEqual(child.stdout.readline().strip(), 'open')
        self.assertIn(38, self.observed_descriptors(child.pid, pin))
        child.stdin.write('close\\n'); child.stdin.flush()
        self.assertTrue(select.select([child.stdout], [], [], 5)[0])
        self.assertEqual(child.stdout.readline().strip(), 'closed')
        self.assertNotIn(38, self.observed_descriptors(child.pid, pin))

'''
assert after.count(marker) == 1
after = after.replace(marker, addition + marker)
ast.parse(after)
path.write_text(after, newline='\n')
manifest = dict(old_manifest)
manifest.update(candidate=2, base='supervisor-broker-handoff-candidate-01', new_checks=10,
                focused_checks=112, expected_full_checks=1393)
manifest['files'] = {name: hashlib.sha256((new / name).read_bytes()).hexdigest() for name in old_manifest['files']}
changed = [name for name in manifest['files'] if manifest['files'][name] != old_manifest['files'][name]]
assert changed == ['tests/native/test_supervisor_broker_handoff_regressions.py'], changed
for name, digest in json.loads((new / 'acceptance.lock.json').read_text())['files'].items():
    assert manifest['files'][name] == digest
(r / 'local/supervisor-broker-handoff-candidate-02.json').write_text(json.dumps(manifest, indent=2) + '\n')
with tarfile.open(r / 'local/supervisor-broker-handoff-candidate-02.tar', 'w') as archive:
    for name in manifest['files']:
        archive.add(new / name, arcname=name)
(evidence / 'observer-correction-02.patch').write_text(''.join(difflib.unified_diff(
    before.splitlines(True), after.splitlines(True), fromfile='candidate01/' + changed[0], tofile='candidate02/' + changed[0])), newline='\n')
review = dict(candidate=2, changed_files=changed, production_unchanged_from_candidate01=True,
    locked_files_unchanged=41, reviewer='primary agent', status='static_review_complete_runtime_unrun',
    reason='NativeLaunch sets PR_SET_DUMPABLE=0. Unprivileged pathlib.exists raises PermissionError before application checks.',
    invariant='The consumed native credential descriptor must be absent while the exact broker remains alive.',
    correction='Privileged read-only observer lists descriptor numbers. Live pidfd checks bracket observation. Standard descriptors must exist. Inspection errors fail.',
    counterexample='New control proves open descriptor 38 is detected, then its actual closure is detected on a non-dumpable process.',
    files=manifest['files'])
(evidence / 'observer-review-02.json').write_text(json.dumps(review, indent=2) + '\n')
for kind in ('qualify', 'collect'):
    source = (r / ('local/' + kind + '-supervisor-broker-handoff-01.py')).read_text()
    source = source.replace('m02-supervisor-broker-handoff-01', 'm02-supervisor-broker-handoff-02')
    source = source.replace('supervisor-broker-handoff-candidate-01', 'supervisor-broker-handoff-candidate-02')
    if kind == 'collect':
        source = source.replace('candidate=1,', 'candidate=2,').replace('candidate-01/', 'candidate-02/')
        source = source.replace("host + '-01.tar.gz'", "host + '-02.tar.gz'")
        source = source.replace("('regressions', 8, 1)", "('regressions', 9, 1)")
        source = source.replace('checks=111, suites=11, new_checks=9', 'checks=112, suites=11, new_checks=10')
    ast.parse(source)
    target = r / ('local/' + kind + '-supervisor-broker-handoff-02.py')
    target.write_text(source, newline='\n')
    shutil.copyfile(target, evidence / 'scripts' / target.name)
print('Frozen candidate02: 283 files; one test file changed; production and 41 locks unchanged.')
