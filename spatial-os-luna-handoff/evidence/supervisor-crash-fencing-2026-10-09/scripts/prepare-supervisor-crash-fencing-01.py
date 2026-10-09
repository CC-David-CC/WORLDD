import ast
import difflib
import hashlib
import json
from pathlib import Path
import tarfile

r = Path(__file__).resolve().parents[1]
base = r/'local/supervisor-fencing-draft-01'
manifest = json.loads((r/'local/supervisor-fencing-candidate-01.json').read_text())['files']
draft = r/'local/supervisor-crash-fencing-draft-01'
assert not draft.exists()
for name, digest in manifest.items():
    data = (base/name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest, name
    (draft/name).parent.mkdir(parents=True, exist_ok=True)
    (draft/name).write_bytes(data)
def replace(name, old, new):
    p = draft/name
    value = p.read_text(encoding='utf-8')
    assert value.count(old) == 1, name
    p.write_bytes(value.replace(old, new).encode())
replace('src/supervisor/launch.hpp', '    v1::AuthorityLaunchRecord fence();\n',
    '    v1::AuthorityLaunchRecord fence();\n'
    '    // Exceptional shutdown only. Checked graceful release needs no fence.\n'
    '    void fence_after_failure();\n')
replace('src/supervisor/launch.cpp', 'v1::AuthorityLaunchRecord AuthorityLaunch::fence() {\n',
    '''void AuthorityLaunch::fence_after_failure() {
    // A reopened journal has no retained ownership or signing configuration.
    if (!impl_->record.binding().supervised() || !impl_->record.has_authorization() ||
        impl_->record.stage() < v1::START_ATTEMPTED || impl_->relay_released ||
        !impl_->process || !impl_->native || !impl_->authorization_config) return;
    // The issuer, not this local eligibility check, proves exact activation.
    fence();
}
v1::AuthorityLaunchRecord AuthorityLaunch::fence() {
''')
replace('src/supervisor/main.cpp',
    '    std::unique_ptr<spatial::identity::ParticipantStream> management;\n',
    '    std::unique_ptr<spatial::identity::ParticipantStream> management;\n'
    '    bool explicit_shutdown = false;\n')
replace('src/supervisor/main.cpp',
    '                    // Cleanup errors are fatal and cannot resume child routing.\n',
    '                    // An explicit shutdown failure must not trigger an implicit fence retry.\n'
    '                    explicit_shutdown = stop;\n'
    '                    // Cleanup errors are fatal and cannot resume child routing.\n')
replace('src/supervisor/main.cpp',
    '        if (launch && launch->inspect().revision()) {\n            try { launch->stop(); }\n',
    '''        if (launch && launch->inspect().revision()) {
            if (!explicit_shutdown) {
                try { launch->fence_after_failure(); }
                catch (const std::exception& fencing) {
                    std::cerr << "authority supervisor crash fencing unresolved: " << fencing.what() << '\\n';
                }
            }
            try { launch->stop(); }
''')
test = (r/'local/test_supervisor_crash_fencing_baseline.py').read_text(encoding='utf-8')
test = test[:test.index("if __name__ == '__main__':")]
test += '''import sqlite3
import security_pb2 as security
import issuer_pb2 as identity


class SupervisorCrashFailureTests(SupervisorCrashFencingTests):
    def fail_child(self, proc, channel):
        os.kill(self.authority_pid, signal.SIGKILL)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(proc.wait(timeout=8), 2)
        return reply

    def test_termination_signal_fences_activated_service(self):
        proc, channel, active, before, activations = self.activated_with_pending_obligation()
        proc.send_signal(signal.SIGTERM)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertIn('termination requested', reply.error.message)
        self.assert_exact_crash_fence(proc, active, before, activations)

    def test_unconsumed_start_is_cleaned_without_fabricated_fence(self):
        proc, channel = self.running()
        self.fail_child(proc, channel)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        rows = self.issuer.snapshot_all()
        self.assertEqual(rows['authority_fence'], [])
        self.assertEqual(rows['authority_activation'], [])

    def test_graceful_release_does_not_create_fence(self):
        self.test_real_child_activates_releases_and_exits_without_manual_pumping()
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'], [])

    def test_explicit_stop_preserves_existing_unfenced_semantics(self):
        proc, channel, active, before, activations = self.activated_with_pending_obligation()
        reply = self.control(channel, stop=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('view'), reply)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.activation_rows(), activations)
        self.assertEqual(self.barrier().pending_count, 1)
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'], [])

    def test_changed_signing_key_preserves_cleanup_and_original_failure(self):
        proc, channel, active, before, activations = self.activated_with_pending_obligation()
        self.issuer.supervisor_key.write_bytes(self.issuer.signer.read_bytes())
        self.fail_child(proc, channel)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.activation_rows(), activations)
        self.assertEqual(self.barrier().pending_count, 1)
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'], [])

    def test_issuer_loss_cannot_prevent_local_cleanup_or_fabricate_fence(self):
        proc, channel = self.running()
        self.activate_child(proc)
        activations = self.activation_rows()
        self.issuer.stop_daemons()
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(proc.wait(timeout=8), 2)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.activation_rows(), activations)
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'], [])

    def test_native_cleanup_uncertainty_preserves_automatic_fence(self):
        proc, channel = self.running()
        self.activate_child(proc)
        before = self.read_record()
        result = self.supervisor.call(revoke=security.Revoke(credential_id=before.native.credential_id))
        self.assertEqual(result.code, security.OK)
        self.assertTrue(result.revoked)
        self.fail_child(proc, channel)
        record = self.read_record()
        self.assertEqual(record.stage, launch.CLEANUP_PENDING)
        self.assertTrue(record.HasField('fence'))
        self.assertTrue(record.HasField('process_stopped'))
        self.assertFalse(record.HasField('native_stopped'))
        self.assertEqual(len(self.issuer.snapshot_all()['authority_fence']), 1)

    def test_failed_journal_commit_retains_remote_fence_and_original_error(self):
        proc, channel = self.running()
        self.activate_child(proc)
        before = self.read_record()
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute("CREATE TRIGGER refuse_crash_update BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'crash journal fault'); END")
        self.fail_child(proc, channel)
        self.assertEqual(self.read_record(), before)
        self.assertEqual(len(self.issuer.snapshot_all()['authority_fence']), 1)


if __name__ == '__main__':
    suite = unittest.TestSuite(cls(name) for cls in (SupervisorCrashFencingTests, SupervisorCrashFailureTests)
        for name in cls.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
'''
name = 'tests/native/test_supervisor_crash_fencing.py'
(draft/name).write_bytes(test.encode())
tree = ast.parse(test)
checks = sum(isinstance(v, ast.FunctionDef) and v.name.startswith('test_')
    for cls in tree.body if isinstance(cls, ast.ClassDef) for v in cls.body)
assert checks == 10
p = draft/'CMakeLists.txt'
value = p.read_text(encoding='utf-8')
start = value.index('    add_test(NAME authority-supervisor-fencing ')
end = value.index('\n', value.index('    set_tests_properties(authority-supervisor-fencing PROPERTIES TIMEOUT 180)', start))+1
block = value[start:end].replace('supervisor-fencing', 'supervisor-crash-fencing').replace('test_supervisor_fencing.py', 'test_supervisor_crash_fencing.py')
p.write_bytes((value[:end]+block+value[end:]).encode())
files = sorted(set(manifest)|{name})
after = {n:hashlib.sha256((draft/n).read_bytes()).hexdigest() for n in files}
changed = [n for n in files if manifest.get(n) != after[n]]
assert len(files) == 294 and len(changed) == 5
assert all(after[n] == v for n,v in manifest.items() if n.startswith(('tests/', 'proto/')))
locked = json.loads((draft/'acceptance.lock.json').read_text())['files']
assert len(locked) == 41 and all(after[n] == v for n,v in locked.items())
record = dict(files=after, source_files=294, changed=changed, base_files=293)
(r/'local/supervisor-crash-fencing-candidate-01.json').write_bytes((json.dumps(record, indent=2)+'\n').encode())
with tarfile.open(r/'local/supervisor-crash-fencing-candidate-01.tar', 'w') as archive:
    for n in files: archive.add(draft/n, arcname=n)
e = r/'evidence/supervisor-crash-fencing-2026-10-09'
(e/'candidate-01-manifest.json').write_bytes((json.dumps(record, indent=2)+'\n').encode())
patch = []
for n in changed:
    before = (base/n).read_text(encoding='utf-8').splitlines(True) if n in manifest else []
    patch.extend(difflib.unified_diff(before, (draft/n).read_text(encoding='utf-8').splitlines(True),
        fromfile='a/'+n if n in manifest else '/dev/null', tofile='b/'+n))
(e/'candidate-01.patch').write_bytes(''.join(patch).encode())
print('Frozen automatic crash fencing294:5 changed files,10 new checks,41 locked files preserved.')
