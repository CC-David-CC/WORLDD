"""Independent process, namespace, resource, revocation, and recovery witnesses."""
import json
from contextlib import closing
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import unittest
from test_workspaces import WorkspaceTests, BROKER, RIGHTS


class ProcessTests(WorkspaceTests):
    def setUp(self):
        super().setUp()
        self.client.timeout = 8
        _, self.work = self.workspace()
        self.project = Path(self.work['path'])
        self.select(self.a)

    def launch(self, code, operation='launch', actor=None, profile='development'):
        return self.act(actor or self.a, 'process.launch', argv=['/usr/bin/python3', '-c', code], operation_id=operation, profile=profile)

    def wait_job(self, process, expected=0):
        self.assertEqual(self.call('process.wait', id=process['process_id'], timeout_seconds=5), {'exit_code': expected})

    def until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() >= deadline: self.fail('Independent process witness did not reach its condition')
            time.sleep(.01)

    def descendants(self):
        broker = self.call('runtime.identities')['workspaced_pid']
        todo = [broker]; found = set()
        while todo:
            parent = todo.pop()
            try: children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
            except FileNotFoundError: continue
            for child in map(int, children):
                if child not in found: found.add(child); todo.append(child)
        return found

    @staticmethod
    def gone(pids):
        for pid in pids:
            try: state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError): continue
            if state not in ('Z', 'X'): return False
        return True

    def test_native_namespaces_capabilities_resource_limits_and_descriptor_closure(self):
        code = """import json,os,pathlib,resource
fds=[]
for fd in range(3,256):
    try: os.fstat(fd);fds.append(fd)
    except OSError: pass
data={'cwd':os.getcwd(),'uid':os.getuid(),'status':pathlib.Path('/proc/self/status').read_text(),
      'namespaces':{k:os.readlink('/proc/self/ns/'+k) for k in ['mnt','pid','user','net','ipc','uts']},
      'env':dict(os.environ),'fds':fds,
      'limits':{k:resource.getrlimit(getattr(resource,k)) for k in ['RLIMIT_AS','RLIMIT_FSIZE','RLIMIT_CPU','RLIMIT_NOFILE','RLIMIT_NPROC','RLIMIT_CORE']}}
pathlib.Path('witness.json').write_text(json.dumps(data))
"""
        self.wait_job(self.launch(code))
        data = json.loads((self.project / 'witness.json').read_text())
        self.assertEqual(data['cwd'], '/project'); self.assertEqual(data['uid'], os.getuid()); self.assertEqual(data['fds'], [])
        for name, value in data['namespaces'].items(): self.assertNotEqual(value, os.readlink('/proc/self/ns/' + name))
        self.assertIn('NoNewPrivs:\t1', data['status']); self.assertIn('Seccomp:\t2', data['status'])
        self.assertIn('CapEff:\t0000000000000000', data['status'])
        for key in ['DISPLAY', 'XAUTHORITY', 'SSH_AUTH_SOCK', 'DBUS_SESSION_BUS_ADDRESS']: self.assertNotIn(key, data['env'])
        self.assertEqual(data['limits']['RLIMIT_CORE'], [0, 0]); self.assertEqual(data['limits']['RLIMIT_NOFILE'], [256, 256])
        for limit in data['limits'].values(): self.assertGreaterEqual(limit[0], 0); self.assertEqual(limit[0], limit[1])

    def test_host_paths_symlinks_processes_and_abstract_sockets_are_inaccessible(self):
        canary = self.root / 'secret'; canary.write_text('private')
        (self.project / 'escape').symlink_to(canary)
        listener = socket.socket(socket.AF_UNIX); self.addCleanup(listener.close)
        address = '\0spatial-' + self.root.name; listener.bind(address); listener.listen()
        broker = self.call('runtime.identities')['workspaced_pid']
        code = f"""import pathlib,socket
for path in [{str(canary)!r},'/project/escape','/etc/shadow','/proc/{broker}/environ']:
    try: pathlib.Path(path).read_bytes()
    except OSError: pass
    else: raise SystemExit(20)
s=socket.socket(socket.AF_UNIX)
try: s.connect({address!r})
except OSError: pass
else: raise SystemExit(21)
"""
        self.wait_job(self.launch(code)); self.assertEqual(canary.read_text(), 'private')

    def test_syscall_policy_blocks_new_namespaces_mounts_and_process_memory_access(self):
        code = """import ctypes,errno
lib=ctypes.CDLL(None,use_errno=True)
for number,args in [(272,[0x10000000]),(308,[-1,0]),(165,[0,0,0,0,0]),(101,[0,0,0,0]),(321,[0,0,0]),(425,[1,0])]:
    ctypes.set_errno(0)
    assert lib.syscall(number,*args)==-1 and ctypes.get_errno()==errno.EPERM,(number,ctypes.get_errno())
assert lib.prctl(38,0,0,0,0)==-1
"""
        self.wait_job(self.launch(code))

    def test_nonzero_signal_exits_and_wait_timeout_preserve_the_actual_child_result(self):
        self.wait_job(self.launch('raise SystemExit(37)', 'nonzero'), 37)
        self.wait_job(self.launch('import os,signal;os.kill(os.getpid(),signal.SIGTERM)', 'signal'), -signal.SIGTERM)
        process = self.launch("import pathlib,time\nwhile not pathlib.Path('release').exists(): time.sleep(.01)\n", 'waiting')
        self.error('TIMEOUT', 'process.wait', id=process['process_id'], timeout_seconds=.02)
        (self.project / 'release').touch(); self.wait_job(process)
        self.wait_job(process)

    def test_invalid_requests_create_no_process_record_and_missing_exec_is_not_success(self):
        for argv, profile in [([], 'development'), (['/usr/bin/true'], 'unknown'), (['/usr/bin/true', '\0'], 'development')]:
            self.actor_error('INVALID_ARGUMENT', self.a, 'process.launch', argv=argv, operation_id='bad', profile=profile)
        with self.registry() as db: self.assertEqual(db.execute('SELECT count(*) FROM processes').fetchone()[0], 0)
        self.actor_error('STORAGE_ERROR', self.a, 'process.launch', argv=['/absent'], operation_id='missing', profile='development')
        self.actor_error('UNAVAILABLE', self.a, 'process.launch', argv=['/absent'], operation_id='missing', profile='development')
        with self.registry() as db: self.assertEqual(db.execute('SELECT state FROM processes').fetchone()[0], 'uncertain')
        self.wait_job(self.launch('pass', 'after-failure'))

    def test_operation_identity_is_actor_scoped_and_binds_arguments_and_profile(self):
        code = "from pathlib import Path\nf=Path('count').open('a');f.write('once\\n');f.close()"
        first = self.launch(code); self.assertEqual(first, self.launch(code)); self.wait_job(first)
        self.actor_error('OPERATION_CONFLICT', self.a, 'process.launch', argv=['/usr/bin/true'], operation_id='launch', profile='development')
        self.actor_error('OPERATION_CONFLICT', self.a, 'process.launch', argv=['/usr/bin/python3', '-c', code], operation_id='launch', profile='read-only')
        self.select(self.b); self.wait_job(self.launch(code, actor=self.b))
        self.assertEqual((self.project / 'count').read_text().splitlines(), ['once', 'once'])

    def test_damaged_application_supervisor_does_not_poison_future_launches(self):
        process = self.launch("import os,pathlib,signal,time\npathlib.Path('ready').touch()\nwhile not pathlib.Path('kill-supervisor').exists(): time.sleep(.01)\nos.kill(os.getppid(),signal.SIGKILL)\ntime.sleep(10)")
        self.until(lambda: (self.project / 'ready').exists())
        descendants = self.descendants(); (self.project / 'kill-supervisor').touch()
        self.error('UNAVAILABLE', 'process.wait', id=process['process_id'], timeout_seconds=5)
        self.until(lambda: self.gone(descendants))
        self.wait_job(self.launch('pass', 'independent'))

    def test_read_only_project_and_git_aliases_stay_read_only_but_private_state_is_writable(self):
        code = f"""from pathlib import Path
assert Path('README.txt').read_text()=='base\\n'
admin=Path('.git').read_text().strip().split('gitdir: ',1)[1]
for path in ['README.txt',{str(self.project / 'README.txt')!r},admin+'/HEAD']:
    try: Path(path).write_text('escape')
    except OSError: pass
    else: raise SystemExit(22)
Path('/state/home/settings').write_text('own state');Path('/tmp/temporary').write_text('own tmp')
"""
        self.wait_job(self.launch(code, profile='read-only')); self.assertEqual((self.project / 'README.txt').read_text(), 'base\n')

    def test_actor_disconnect_does_not_terminate_workspace_application(self):
        process = self.launch("import pathlib,time\npathlib.Path('ready').touch()\nwhile not pathlib.Path('release').exists(): time.sleep(.01)\npathlib.Path('done').touch()")
        self.until(lambda: (self.project / 'ready').exists())
        self.call('actor.close', token=self.a['token']); (self.project / 'release').touch(); self.wait_job(process)
        self.assertTrue((self.project / 'done').exists())

    def test_revocation_terminates_detached_descendants_and_preserves_other_actor_execution(self):
        self.select(self.b)
        other = self.launch("import pathlib,time\nwhile not pathlib.Path('release-other').exists(): time.sleep(.01)", actor=self.b, operation='other')
        before = self.descendants()
        victim = self.launch("import os,pathlib,time\npid=os.fork()\nif pid==0:\n os.setsid();pathlib.Path('detached').touch()\n while True: time.sleep(.1)\nelse:\n pathlib.Path('parent').touch();os.waitpid(pid,0)", operation='victim')
        self.until(lambda: (self.project / 'detached').exists())
        victim_pids = self.descendants() - before; self.assertGreaterEqual(len(victim_pids), 3)
        self.grant(self.a, rights=[]); self.until(lambda: self.gone(victim_pids))
        self.error('UNAVAILABLE', 'process.wait', id=victim['process_id'], timeout_seconds=0)
        (self.project / 'release-other').touch(); self.wait_job(other)

    def test_manager_loss_kills_children_and_never_replays_uncertain_operations(self):
        process = self.launch("import pathlib,time\nf=pathlib.Path('count').open('a');f.write('once\\n');f.close()\nwhile True: time.sleep(.1)")
        self.until(lambda: (self.project / 'count').exists()); descendants = self.descendants(); self.assertTrue(descendants)
        self.client.stop(crash=True); self.until(lambda: self.gone(descendants))
        self.start(); self.a = self.actor('a')
        self.error('UNAVAILABLE', 'process.wait', id=process['process_id'], timeout_seconds=0)
        self.actor_error('UNAVAILABLE', self.a, 'process.launch', argv=['/usr/bin/python3', '-c', "import pathlib,time\nf=pathlib.Path('count').open('a');f.write('once\\n');f.close()\nwhile True: time.sleep(.1)"], operation_id='launch', profile='development')
        self.assertEqual((self.project / 'count').read_text().splitlines(), ['once'])

    def test_completed_record_survives_restart_and_v3_migration_preserves_workspaces(self):
        process = self.launch('raise SystemExit(19)'); self.wait_job(process, 19)
        self.client.stop(); self.start(); self.wait_job(process, 19)
        self.client.stop()
        legacy_tables = ('repositories', 'worktrees', 'operations', 'removals', 'workspaces', 'workspace_members')
        with closing(self.registry()) as db, db:
            before = {name: db.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
                      for name in legacy_tables}
            db.executescript('DROP TABLE configured_process_states; DROP TABLE configured_application_states; '
                             'DROP TABLE workspace_copy_policies; DROP TABLE processes; DROP TABLE copy_effects; '
                             'DROP TABLE copy_operations; PRAGMA user_version=3')
            self.assertEqual({row[0] for row in db.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}, set(legacy_tables))
            self.assertEqual(db.execute('PRAGMA user_version').fetchone(), (3,))
        state = self.state / 'execution'
        denied = subprocess.run([BROKER, '--state-dir', str(state)], input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(denied.returncode, 0); self.assertIn(b'--migrate-v3', denied.stderr)
        for _ in range(2):
            result = subprocess.run([BROKER, '--state-dir', str(state), '--migrate-v3'], input=b'', capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            with closing(self.registry()) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone(), (8,))
                self.assertEqual({name: db.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
                                  for name in legacy_tables}, before)
                self.assertTrue({'configured_process_states', 'configured_application_states', 'workspace_copy_policies'} <=
                    {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")})
        self.start(); self.assertEqual(self.call('workspace.get', id='one')['worktree'], self.work['id'])


class ProcessWitnessTests(unittest.TestCase):
    def read_results(self, results, pids):
        from unittest.mock import patch
        with patch.object(Path, 'read_text', side_effect=results):
            return ProcessTests.gone(pids)

    def test_process_exit_during_stat_read_is_gone(self):
        import errno
        self.assertTrue(self.read_results([ProcessLookupError(errno.ESRCH, 'No such process')], [123]))

    def test_missing_stat_path_is_gone(self):
        self.assertTrue(self.read_results([FileNotFoundError()], [123]))

    def test_missing_process_does_not_hide_a_live_process(self):
        self.assertFalse(self.read_results([FileNotFoundError(), '124 (live) S 1'], [123,124]))

    def test_only_dead_process_states_are_gone(self):
        for state in ('Z','X'):
            self.assertTrue(self.read_results(['123 (name with ) parentheses) '+state+' 1'], [123]))
        for state in ('R','S','D','T','t','I'):
            self.assertFalse(self.read_results(['123 (live) '+state+' 1'], [123]))

    def test_unrelated_read_errors_are_not_process_exit(self):
        import errno
        for error in (PermissionError(errno.EACCES, 'denied'), OSError(errno.EIO, 'I/O error')):
            with self.assertRaises(type(error)):
                self.read_results([error], [123])

    def test_malformed_stat_is_not_process_exit(self):
        for value in ('','123 (name)'):
            with self.assertRaises(IndexError):
                self.read_results([value], [123])


if __name__ == '__main__':
    # Reuse fixtures, without rerunning the separate workspace suite here.
    suite = unittest.TestSuite(ProcessTests(name) for name in ProcessTests.__dict__ if name.startswith('test_'))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ProcessWitnessTests))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
