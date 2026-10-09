"""A real child and native clients independently witness durable coordinated ownership."""
import fcntl
import os
from pathlib import Path
import select
import signal
import sqlite3
import struct
import subprocess
import sys
import time
import unittest
import launch_pb2 as pb
import native_scope_pb2 as native_pb
import process_scope_pb2 as process_pb
import security_pb2 as security
from harness.wire import frame

PROBE=Path(sys.argv.pop(1)).resolve()
import test_security_identity as native
RELATIVE=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
CGROUP=Path('/sys/fs/cgroup')/RELATIVE.lstrip('/')
assert RELATIVE!='/' and CGROUP.stat().st_uid==os.getuid()

class AuthorityLaunchTests(native.SecurityIdentityTests):
    def setUp(self):
        super().setUp()
        self.cgroup=CGROUP/('launch-'+self.root.name);self.cgroup.mkdir()
        self.addCleanup(self.cleanup_cgroups)
        self.journal=self.root/'journal';self.journal.mkdir(mode=0o700)
        self.binding=pb.AuthorityLaunchBinding(issuer_id=os.urandom(32),participant_id=os.urandom(32),
            registered_revision=7,service_incarnation=os.urandom(32))

    def cleanup_cgroups(self):
        for path in self.cgroup.iterdir():
            if not path.is_dir():continue
            (path/'cgroup.kill').chmod(0o600);(path/'cgroup.kill').write_text('1\n')
            limit=time.monotonic()+3
            while 'populated 1' in (path/'cgroup.events').read_text():
                self.assertLess(time.monotonic(),limit);time.sleep(.005)
            path.rmdir()
        self.cgroup.rmdir()

    def probe(self, start_server=True):
        if start_server:self.start()
        config=native_pb.NativeScopeConfig(protocol_version=1,display_number=self.display,supervisor_cookie=self.cookie,
            server_id=self.supervisor.inspect().server_id,server_pid=self.server.pid,server_uid=os.getuid(),
            launch_id=os.urandom(32),role=security.BROKER)
        fd=os.memfd_create('coordinator-test',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        os.write(fd,config.SerializeToString())
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        path=self.root/('coordinator-'+str(time.monotonic_ns())+'.log');log=path.open('wb')
        try:proc=subprocess.Popen([str(PROBE),str(self.journal),str(self.cgroup),str(fd)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,pass_fds=(fd,))
        finally:os.close(fd)
        def cleanup():
            if proc.poll() is None:
                proc.stdin.close()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
            if not proc.stdin.closed:proc.stdin.close()
            proc.stdout.close();log.close()
            text=path.read_text(errors='replace')
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,text)
        self.addCleanup(cleanup)
        return proc

    def send(self,proc,**op):
        proc.stdin.write(frame(pb.AuthorityLaunchRequest(protocol_version=1,request_id=55,**op)));proc.stdin.flush()

    def reply(self,proc):
        def exact(size):
            data=b''
            while len(data)<size:
                self.assertTrue(select.select([proc.stdout],[],[],5)[0],'Coordinator reply deadline')
                part=os.read(proc.stdout.fileno(),size-len(data));self.assertTrue(part,'Coordinator closed its reply channel');data+=part
            return data
        size=struct.unpack('!I',exact(4))[0];self.assertLessEqual(size,1048576)
        value=pb.AuthorityLaunchReply.FromString(exact(size));self.assertEqual(value.protocol_version,1);self.assertEqual(value.request_id,55)
        return value

    def call(self,proc,**op):self.send(proc,**op);return self.reply(proc)

    def read_record(self):
        with sqlite3.connect('file:'+str(self.journal/'launch.db')+'?mode=ro',uri=True) as db:
            value=db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()
        return pb.AuthorityLaunchRecord.FromString(value[0]) if value else pb.AuthorityLaunchRecord()

    def prepared(self,proc):
        reply=self.call(proc,prepare=self.binding);self.assertTrue(reply.HasField('view'),reply)
        record=reply.view.record;self.assertEqual(record,self.read_record());self.assertEqual(record.stage,pb.PREPARED)
        self.assertEqual(record.binding,self.binding);self.assertEqual(record.process.launch_id,record.native.launch_id)
        self.assertEqual(record.native.credential_id,reply.view.ready.credential.id)
        self.assertEqual((self.cgroup/record.process.scope_name/'cgroup.procs').read_text(),'')
        return reply.view

    def run_child(self,proc,code):
        return self.call(proc,start=process_pb.AuthorityProcessLaunch(executable='/usr/bin/python3',arguments=['-c',code]))

    def wait_file(self,path):
        limit=time.monotonic()+4
        while not path.exists():self.assertLess(time.monotonic(),limit);time.sleep(.005)

    def stopped(self,proc):
        reply=self.call(proc,stop=pb.AuthorityLaunchQuery());self.assertTrue(reply.HasField('view'),reply)
        record=reply.view.record;self.assertEqual(record.stage,pb.OWNED_SCOPES_STOPPED)
        self.assertEqual(record,self.read_record());self.assertEqual(record.process_stopped.identity,record.process)
        self.assertEqual(record.native_stopped.identity,record.native);return record

    def test_child_reads_exact_durable_start_before_execution(self):
        proc=self.probe();view=self.prepared(proc);observed=self.root/'observed'
        code=('import pathlib,sqlite3,time\n'
            f'db=sqlite3.connect("file:"+{str(self.journal/"launch.db")!r}+"?mode=ro",uri=True)\n'
            'data=db.execute("SELECT record FROM launch WHERE singleton=1").fetchone()[0]\n'
            f'p=pathlib.Path({str(observed)!r});t=p.with_suffix(".tmp");t.write_bytes(data);t.replace(p);time.sleep(30)')
        reply=self.run_child(proc,code);self.assertTrue(reply.HasField('view'),reply);self.wait_file(observed)
        record=pb.AuthorityLaunchRecord.FromString(observed.read_bytes());self.assertEqual(record,reply.view.record)
        self.assertEqual(record.stage,pb.START_ATTEMPTED);self.assertEqual(record.process,view.record.process)
        self.stopped(proc)

    def test_checked_stop_terminates_descendants_and_revokes_only_owned_native_grants(self):
        proc=self.probe();view=self.prepared(proc);broker=self.connect(view.ready.credential.cookie)
        app=broker.issue();client=self.connect(app.cookie)
        independent=self.supervisor.issue(workspace=b'other',application=b'other');survivor=self.connect(independent.cookie)
        ready=self.root/'ready';reply=self.run_child(proc,f'import os,pathlib,time\nif os.fork()==0:\n os.setsid();pathlib.Path({str(ready)!r}).touch()\ntime.sleep(30)')
        self.assertTrue(reply.HasField('view'),reply);self.wait_file(ready)
        pids=[int(p) for p in (self.cgroup/view.record.process.scope_name/'cgroup.procs').read_text().split()]
        self.assertEqual(len(pids),2);pins=[os.pidfd_open(p) for p in pids]
        for fd in pins:self.addCleanup(os.close,fd)
        self.stopped(proc);self.assertEqual(len(select.select(pins,[],[],0)[0]),2)
        self.assert_closed(broker);self.assert_closed(client);self.assertEqual(survivor.inspect().credential_id,independent.id)

    def test_paused_native_server_keeps_durable_cleanup_pending(self):
        proc=self.probe();self.prepared(proc)
        self.assertTrue(self.run_child(proc,'import time;time.sleep(30)').HasField('view'))
        os.kill(self.server.pid,signal.SIGSTOP)
        try:
            self.send(proc,stop=pb.AuthorityLaunchQuery())
            self.assertFalse(select.select([proc.stdout],[],[],.15)[0])
            record=self.read_record();self.assertEqual(record.stage,pb.CLEANUP_PENDING)
            self.assertTrue(record.HasField('process_stopped'));self.assertFalse(record.HasField('native_stopped'))
        finally:os.kill(self.server.pid,signal.SIGCONT)
        reply=self.reply(proc);self.assertEqual(reply.view.record.stage,pb.OWNED_SCOPES_STOPPED)

    def test_failed_journal_commit_prevents_execution(self):
        proc=self.probe();self.prepared(proc)
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute("CREATE TRIGGER deny_update BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'injected durability failure'); END")
        ready=self.root/'unexpected'
        reply=self.run_child(proc,f'import pathlib;pathlib.Path({str(ready)!r}).touch()')
        self.assertTrue(reply.HasField('error'),reply);self.assertFalse(ready.exists())
        self.assertEqual(self.read_record().stage,pb.PREPARED)
        with sqlite3.connect(self.journal/'launch.db') as db:db.execute('DROP TRIGGER deny_update')
        self.stopped(proc)

    def test_failed_exec_retains_start_attempt_and_cannot_retry(self):
        proc=self.probe();self.prepared(proc)
        reply=self.call(proc,start=process_pb.AuthorityProcessLaunch(executable='/missing-authority-executable'))
        self.assertTrue(reply.HasField('error'),reply);self.assertEqual(self.read_record().stage,pb.START_ATTEMPTED)
        self.assertTrue(self.run_child(proc,'pass').HasField('error'));self.stopped(proc)

    def test_failed_prepared_commit_can_stop_retained_native_scope(self):
        proc=self.probe()
        self.assertFalse(self.call(proc,inspect=pb.AuthorityLaunchQuery()).view.record.revision)
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute("CREATE TRIGGER deny_prepared BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'injected prepared commit failure'); END")
        reply=self.call(proc,prepare=self.binding);self.assertTrue(reply.HasField('error'),reply)
        record=self.read_record();self.assertEqual(record.stage,pb.PREPARING)
        self.assertFalse(record.native.credential_id)
        with sqlite3.connect(self.journal/'launch.db') as db:db.execute('DROP TRIGGER deny_prepared')
        stopped=self.stopped(proc);self.assertEqual(len(stopped.native.credential_id),32)
        self.assertEqual(stopped.process,record.process)

    def test_missing_native_grant_never_commits_stopped_record(self):
        proc=self.probe();view=self.prepared(proc)
        result=self.supervisor.call(revoke=security.Revoke(credential_id=view.record.native.credential_id))
        self.assertEqual(result.code,security.OK)
        reply=self.call(proc,stop=pb.AuthorityLaunchQuery());self.assertTrue(reply.HasField('error'),reply)
        record=self.read_record();self.assertEqual(record.stage,pb.CLEANUP_PENDING)
        self.assertTrue(record.HasField('process_stopped'));self.assertFalse(record.HasField('native_stopped'))
        proc.stdin.close();self.assertNotEqual(proc.wait(timeout=5),0)

    def test_invalid_binding_cannot_create_an_external_scope(self):
        proc=self.probe();self.binding.service_incarnation=b'wrong'
        reply=self.call(proc,prepare=self.binding);self.assertTrue(reply.HasField('error'),reply)
        self.assertFalse(self.read_record().revision)
        self.assertFalse([path for path in self.cgroup.iterdir() if path.is_dir()])
        self.assertEqual((self.cgroup/'cgroup.procs').read_text(),'')

    def test_stopping_prepared_launch_prevents_later_execution(self):
        proc=self.probe();self.prepared(proc);self.stopped(proc);unexpected=self.root/'unexpected'
        reply=self.run_child(proc,f'import pathlib;pathlib.Path({str(unexpected)!r}).touch()')
        self.assertTrue(reply.HasField('error'),reply);self.assertFalse(unexpected.exists())

    def test_supervisor_crash_preserves_unresolved_record_and_denies_replacement(self):
        proc=self.probe();view=self.prepared(proc);ready=self.root/'ready'
        self.assertTrue(self.run_child(proc,f'import pathlib,time;pathlib.Path({str(ready)!r}).touch();time.sleep(30)').HasField('view'))
        self.wait_file(ready);before=self.read_record();proc.kill();proc.wait(timeout=3)
        reopened=self.probe(start_server=False)
        self.assertEqual(self.call(reopened,inspect=pb.AuthorityLaunchQuery()).view.record,before)
        self.assertTrue(self.call(reopened,prepare=self.binding).HasField('error'))
        self.assertTrue(self.run_child(reopened,'pass').HasField('error'))
        self.assertTrue(self.call(reopened,stop=pb.AuthorityLaunchQuery()).HasField('error'))
        self.assertEqual(self.read_record(),before)
        # The witness cleans only its own grant. This is not a supervisor recovery receipt.
        self.supervisor.call(revoke=security.Revoke(credential_id=view.record.native.credential_id))

    def test_stopped_receipt_survives_restart_without_repeating_effects(self):
        proc=self.probe();self.prepared(proc);record=self.stopped(proc)
        proc.stdin.close();self.assertEqual(proc.wait(timeout=5),0)
        reopened=self.probe(start_server=False);self.assertEqual(self.stopped(reopened),record)
        self.assertTrue(self.call(reopened,prepare=self.binding).HasField('error'))

    def test_journal_excludes_native_secrets(self):
        proc=self.probe();view=self.prepared(proc);self.stopped(proc)
        for path in self.journal.iterdir():
            data=path.read_bytes();self.assertNotIn(self.cookie,data);self.assertNotIn(view.ready.credential.cookie,data)
            self.assertEqual(path.stat().st_mode & 0o077,0)

    def test_second_coordinator_cannot_open_owned_journal(self):
        proc=self.probe();self.prepared(proc);other=self.probe(start_server=False)
        self.assertNotEqual(other.wait(timeout=3),0);self.stopped(proc)

    def test_unknown_schema_is_rejected_without_mutation(self):
        proc=self.probe();self.prepared(proc);self.stopped(proc);proc.stdin.close();self.assertEqual(proc.wait(timeout=5),0)
        with sqlite3.connect(self.journal/'launch.db') as db:db.execute('PRAGMA user_version=99')
        before=(self.journal/'launch.db').read_bytes();other=self.probe(start_server=False)
        self.assertNotEqual(other.wait(timeout=3),0);self.assertEqual((self.journal/'launch.db').read_bytes(),before)

if __name__=='__main__':
    suite=unittest.TestSuite(AuthorityLaunchTests(n) for n in AuthorityLaunchTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
