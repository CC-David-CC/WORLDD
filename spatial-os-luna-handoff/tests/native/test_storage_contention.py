"""Real retained actors, bounded writers, issuer commits, and revocation during Git."""
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import time
import unittest
from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as issuer
import launch_pb2 as launch
import session_probe_pb2 as probe
from harness.git_fixture import GitRepo
from harness.wire import frame, make_request
import test_storage_session as existing
from test_issuer_client import ClientProcess


WRITER = """import json,os,time
from pathlib import Path
payload=b'x'*2**20
completed=0
deadline=time.monotonic()+40
while time.monotonic()<deadline and not Path('/state/data/stop').exists():
    with open('/state/data/payload','wb',buffering=0) as file:
        for i in range(4): file.write(payload)
        os.fsync(file.fileno())
    completed+=1
    with open('/state/data/writes.json','w') as file:
        json.dump(dict(completed=completed,bytes=completed*4*2**20),file)
        file.flush();os.fsync(file.fileno())
"""


class StorageContentionTests(existing.NativeStorageSessionTests):
    def volume_command(self, operation, *arguments):
        if operation=='prepare':arguments+=('--volume-bytes',str(1024*2**20))
        return super().volume_command(operation,*arguments)

    def setUp(self):
        super().setUp()
        original=self.issuer.config_file
        def production_configuration(*arguments,**overrides):
            path=original(*arguments,**overrides)
            if 'io_timeout_ms' not in overrides:
                configuration=text_format.Parse(path.read_text(),issuer.IssuerTlsClientConfig())
                configuration.ClearField('io_timeout_ms')
                path.write_text(text_format.MessageToString(configuration))
            return path
        # This new pressure fixture uses production defaults for every issuer caller.
        # Existing short-deadline fixtures and their tests remain unchanged.
        self.issuer.config_file=production_configuration
        self.measurements = dict(samples=[], phase_complete=False)
        self.addCleanup(self.save_measurements)

    def save_measurements(self):
        (self.volume_root/'contention.json').write_text(json.dumps(self.measurements,indent=2)+'\n')
        for path in self.issuer.root.glob('*.log'):
            (self.volume_root/('issuer-fixture-'+path.name)).write_bytes(path.read_bytes())

    def start_writers(self):
        self.writers = []
        # The native application and writer each reserve 128 MiB. Actor capacity is 256 MiB.
        for number in range(1):
            result = self.act(process_launch=core.ProcessLaunchRequest(operation_id=f'writer-{number}',
                profile='development', argv=['/usr/bin/python3','-c',WRITER]))
            self.writers.append(self.execution/'applications'/result.process.process_id/'data')
        self.until(lambda: all((path/'writes.json').exists() for path in self.writers),bool)
        group = self.broker_process_group().parent
        self.measurements['controls'] = {str(path.relative_to(group)): {
            name:(path/name).read_text().strip() for name in ('memory.max','pids.max','cpu.max','io.max')}
            for path in [group,*(p for p in group.rglob('*') if p.is_dir())]}
        self.sample_writes()
        return group

    def sample_writes(self):
        values=[]
        for path in self.writers:
            # A live producer can replace its JSON contents between reads.
            def read():
                try: return json.loads((path/'writes.json').read_text())
                except json.JSONDecodeError: return None
            values.append(self.until(read,lambda value:value is not None))
        self.measurements['samples'].append(dict(time=time.monotonic(),writers=values))
        self.save_measurements()
        return values

    def issuer_operation(self, client, operation):
        started=time.monotonic()
        reply=self.issuer.execute(client,self.issuer.challenge(client,operation))
        elapsed=time.monotonic()-started
        self.assertFalse(reply.HasField('error'),reply)
        self.measurements.setdefault('issuer_operations',[]).append(dict(
            operation=operation.WhichOneof('action'),seconds=elapsed,reply=reply.SerializeToString().hex()))
        self.save_measurements()
        self.assertLess(elapsed,5,'Issuer operation exceeded the accepted production deadline')
        return reply

    def default_client(self):
        # This new performance check uses the production default. Fault fixtures keep 700 ms.
        self.issuer.peer.close()
        path=self.issuer.config_file()
        configuration=text_format.Parse(path.read_text(),issuer.IssuerTlsClientConfig())
        configuration.ClearField('io_timeout_ms')
        path.write_text(text_format.MessageToString(configuration))
        self.assertFalse(configuration.HasField('io_timeout_ms'))
        client=ClientProcess(path,self.issuer.root/'contention-client.log')
        self.issuer.connections.append(client)
        return client

    def durable_updates(self, client):
        before=self.sample_writes()
        policy=self.issuer.policy(client)
        for number in range(3):
            operation=issuer.IssuerOperation(policy_set=issuer.PolicySet(policy=policy.policy,
                expected_revision=policy.revision))
            policy=self.issuer_operation(client,operation).policy
            with sqlite3.connect('file:'+str(self.issuer.state/'identity.db')+'?mode=ro',uri=True) as db:
                revision=db.execute('SELECT revision FROM issuer').fetchone()[0]
            self.assertEqual(revision,policy.revision,'Acknowledged policy was not independently committed')
            self.sample_writes()
        self.measurements['durable_policy_revision']=policy.revision
        self.until(self.sample_writes,lambda values:all(a['completed']>b['completed'] for a,b in zip(values,before)))

    def revoke_actor(self, client, kind):
        target=self.claims.actor_id if kind==issuer.ACTOR_TARGET else self.claims.credential_id
        operation=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
            target=issuer.IdentityReference(kind=kind,id=target),expected_security_revision=0,
            reason='bounded storage contention regression'))
        result=self.issuer_operation(client,operation)
        self.assertTrue(result.HasField('identity_status'),result)
        return result.identity_status.security_revision

    def barrier(self, client, revision):
        reply=self.issuer_operation(client,issuer.IssuerOperation(barrier_read=issuer.AuthorityBarrierRead(revision=revision)))
        self.assertTrue(reply.HasField('authority_barrier'),reply)
        return reply.authority_barrier

    def pins(self, group):
        pids={int(pid) for path in [group,*(p for p in group.rglob('*') if p.is_dir())]
              for pid in (path/'cgroup.procs').read_text().split()}
        pids.discard(self.world_ids.workspaced_pid)
        # Before administrative work starts, the other populated groups are application scopes.
        app_pids=pids
        self.assertTrue(app_pids,dict(all_pids=sorted(pids)))
        pins=[os.pidfd_open(pid) for pid in sorted(app_pids)]
        for pin in pins:self.addCleanup(os.close,pin)
        self.measurements['application_pids']=sorted(app_pids)
        self.assertEqual(select.select(pins,[],[],0)[0],[])
        return pins

    def finish_checked(self):
        stopped=self.request(stop_world=self.owner).cleanup
        self.assertEqual(stopped.stage,launch.OWNED_SCOPES_STOPPED)
        (self.volume_root/'world-cleanup.pb').write_bytes(stopped.SerializeToString())
        self.finish_session()
        (self.volume_root/'broker-cleanup.pb').write_bytes(self.checked_cleanup().SerializeToString())

    def test_native_storage_actor_revocation_and_durable_issuer_under_writes(self):
        self.held_application();group=self.start_writers();pins=self.pins(group)
        client=self.default_client();self.durable_updates(client)
        revision=self.revoke_actor(client,issuer.ACTOR_TARGET)
        completed=self.until(lambda:self.barrier(client,revision),lambda value:value.complete)
        self.assertEqual(completed.pending_count,0)
        self.assertEqual(select.select(pins,[],[],0)[0],pins,'Revocation completed before application exit')
        self.measurements['revocation_complete']=True
        self.assertIsNone(self.coordinator.poll())
        self.finish_checked()

    def test_native_storage_credential_revocation_preserves_execution_under_writes(self):
        self.held_application();group=self.start_writers();pins=self.pins(group)
        client=self.default_client();self.durable_updates(client)
        revision=self.revoke_actor(client,issuer.CREDENTIAL_TARGET)
        self.until(lambda:self.barrier(client,revision),lambda value:value.complete)
        self.released()
        before=self.sample_writes()
        after=self.until(self.sample_writes,lambda values:all(a['completed']>b['completed'] for a,b in zip(values,before)))
        self.assertEqual(select.select(pins,[],[],0)[0],[],'Credential loss killed actor-owned execution')
        self.measurements['credential_loss_execution_continues']=True
        self.finish_checked()

    def test_native_storage_actor_revocation_during_bounded_git_import(self):
        repository=GitRepo(self.volume_root)
        with (repository.path/'large.bin').open('wb') as file:
            for number in range(80):file.write(os.urandom(2**20))
            file.flush();os.fsync(file.fileno())
        repository.run('config','core.fsync','all')
        repository.run('add','large.bin');repository.run('commit','-m','bounded import witness')
        self.issuer.peer.close();self.issuer.peer=self.issuer.connect()
        self.held_application();group=self.start_writers();pins=self.pins(group)
        client=self.default_client()
        self.coordinator_sequence+=1
        request=probe.SessionProbeRequest(protocol_version=1,request_id=self.coordinator_sequence,
            broker=make_request('git.repo.import',dict(id='pressure-import',path=str(repository.path))))
        self.coordinator.stdin.write(frame(request));self.coordinator.stdin.flush()
        def git_running():
            values=[]
            for path in group.rglob('authority-*'):
                try:pids=(path/'cgroup.procs').read_text().split()
                except FileNotFoundError:continue # A completed scope can disappear during this independent sample.
                for pid in pids:
                    try:
                        if Path('/proc/'+pid+'/comm').read_text().strip()=='git': values.append(int(pid))
                    except FileNotFoundError:pass
            return values
        def observe_git():
            values=git_running()
            if not values and select.select([self.coordinator.stdout],[],[],0)[0]:
                reply=self.message(self.coordinator.stdout,probe.SessionProbeReply)
                (self.volume_root/'early-git-import-reply.pb').write_bytes(reply.SerializeToString())
                self.fail('Git import returned before the pending-worker witness: '+str(reply))
            return values
        observed=self.until(observe_git,bool)
        self.measurements['git_pids_at_revocation']=observed
        self.action_sequence+=1
        nested=self.actor_call(self.stream,action=core.ActorActionRequest(seq=self.action_sequence,
            process_launch=core.ProcessLaunchRequest(operation_id='during-import',profile='development',
                argv=['/usr/bin/python3','-c',"from pathlib import Path;Path('/project/nested-launch').touch()"])))
        self.assertTrue(nested.HasField('error'),nested)
        self.assertEqual(nested.error.code,core.BUSY,nested)
        self.assertFalse((self.project/'nested-launch').exists())
        with sqlite3.connect('file:'+str(self.execution/'execution.db')+'?mode=ro',uri=True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM processes WHERE operation=?',('during-import',)).fetchone()[0],0)
        self.measurements['nested_execution_rejected_without_effect']=True
        revision=self.revoke_actor(client,issuer.ACTOR_TARGET)
        started=time.monotonic()
        exited=select.select(pins,[],[],2)[0]
        # Select may report the first exit. Check each retained handle within the same bound.
        while len(exited)<len(pins) and time.monotonic()-started<2:
            time.sleep(.01);exited=select.select(pins,[],[],0)[0]
        self.measurements['exit_during_git']=dict(seconds=time.monotonic()-started,exited=len(exited),expected=len(pins),
            git_still_running=git_running(),pending_reply=not bool(select.select([self.coordinator.stdout],[],[],0)[0]))
        self.measurements['broker_wait_channel']=Path('/proc/'+str(self.world_ids.workspaced_pid)+'/wchan').read_text()
        inspect=("import json,os,sys;from pathlib import Path;"
            "p=Path('/proc')/sys.argv[1];assert (p/'exe').resolve()==Path(sys.argv[2]).resolve();"
            "print(json.dumps({name:(p/name).read_text() for name in ['wchan','syscall','stack']}))")
        snapshot=subprocess.run(['sudo','-n',sys.executable,'-c',inspect,str(self.world_ids.workspaced_pid),
            str(self.build/'workspaced')],capture_output=True,text=True,timeout=5)
        self.measurements['broker_kernel_wait']=dict(exit_code=snapshot.returncode,stdout=snapshot.stdout,stderr=snapshot.stderr)
        self.save_measurements()
        # Drain the real pending operation before reporting its independent lifetime assertion.
        deadline=time.monotonic()+305
        while not select.select([self.coordinator.stdout],[],[],.1)[0]:
            self.assertLess(time.monotonic(),deadline,'Bounded import reply deadline')
            self.barrier(client,revision)
        reply=self.message(self.coordinator.stdout,probe.SessionProbeReply)
        self.assertEqual(reply.request_id,request.request_id)
        (self.volume_root/'git-import-reply.pb').write_bytes(reply.SerializeToString())
        self.assertFalse(reply.HasField('error'),reply)
        self.assertFalse(reply.result.HasField('error'),reply)
        self.until(lambda:self.barrier(client,revision),lambda value:value.complete)
        self.finish_checked()
        self.assertTrue(self.measurements['exit_during_git']['pending_reply'],'Import finished before the concurrent lifetime witness')
        self.assertEqual(len(exited),len(pins),'Busy administrative Git delayed actor revocation')


if __name__=='__main__':
    unittest.main(verbosity=2,defaultTest=['StorageContentionTests.'+name for name in StorageContentionTests.__dict__ if name.startswith('test_')])
