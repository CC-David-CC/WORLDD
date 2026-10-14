"""Actual broker begin with real owner, Git, grants, and resource boundaries.

Run on sb16/sb16-2. Runtime arguments match test_workspace_authority.py.
SPATIAL_TEST_BROKER_CONFIG must supply the delegated production resource template.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch
from google.protobuf import text_format
import broker_pb2 as broker
import core_pb2 as core
import copy_pb2 as copy
import grants_pb2 as grants
import storage_pb2 as storage
from harness.git_fixture import GitRepo
from harness.resource_broker import ResourceBroker
from harness.wire import frame
import test_workspace_authority as fixtures

BUILD=fixtures.BROKER.parent
SOURCE=Path(__file__).resolve().parents[2]

class StorageBrokerProcess(fixtures.BrokerProcess):
    def __init__(self,fixture,state,config=None):
        self.stream,child=socket.socketpair();self.sequence=0
        self.log=(fixture.root/f'copy-broker-{len(fixture.brokers)}.log').open('wb')
        self.owner_log=(fixture.root/f'copy-resource-{len(fixture.brokers)}.log').open('wb')
        self.storage_log=(fixture.root/f'copy-storage-{len(fixture.brokers)}.log').open('wb')
        owner_end,broker_end=socket.socketpair()
        for end in (owner_end,broker_end):end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        command=[str(fixtures.BROKER),'--state-dir',str(state),'--storage-fd',str(broker_end.fileno())]
        if config:command+=['--authority-config',str(config)]
        configuration=str(fixture.copy_resource_config)
        self.resources=ResourceBroker(fixtures.BROKER,command,child,self.log,self.owner_log,configuration,
                                      extra_fds={'--storage-fd':broker_end.fileno()})
        self.proc=self.resources.process;self.pid=self.resources.pid
        broker_end.close();child.close()
        configuration=storage.StorageOwnerConfig.FromString(fixture.owner_configuration.read_bytes())
        configuration.broker_pid=self.pid
        local=fixture.root/f'owner-launch-{len(fixture.brokers)}.pb'
        local.write_bytes(configuration.SerializeToString());local.chmod(0o600)
        private=fixture.operator_directory/f'launch-{len(fixture.brokers)}.pb'
        fixture.operator_run(['install','-m','600',str(local),str(private)])
        code='import os,sys;os.dup2(0,3);os.set_inheritable(3,True);os.execv(sys.argv[1],sys.argv[1:])'
        owner_command=[str(BUILD/'spatial-storage-owner'),'--channel-fd','3','--config',str(private)]
        self.audit_path=None
        if getattr(fixture,'relay_mode',None) is not None:
            self.audit_path=fixture.operator_directory/f'relay-{len(fixture.brokers)}.json'
            owner_command=[sys.executable,'-B',str(SOURCE/'tests/native/storage_owner_receipt_relay.py'),
                '--channel-fd','3','--build',str(BUILD),'--config',str(private),'--audit',str(self.audit_path)]
            if fixture.relay_mode:owner_command+=['--mutate']
        self.owner=subprocess.Popen(['sudo','-n','env',
            'ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
            'UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
            sys.executable,'-B','-c',code,*owner_command],
            stdin=owner_end,stdout=subprocess.DEVNULL,stderr=self.storage_log)
        owner_end.close();fixture.brokers.append(self)
    def close(self):
        if self.stream.fileno()<0:return
        try:super().close()
        finally:
            result=self.owner.wait(timeout=10)
            self.storage_log.close()
            if result!=0:raise AssertionError('Actual storage owner exited '+str(result))

class WorkspaceCopyEngineTests(fixtures.WorkspaceAuthorityTests):
    def operator_run(self,command):
        result=subprocess.run(['sudo','-n',*map(str,command)],capture_output=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result.stdout
    def setUp(self):
        super().setUp()
        self.assertIn(socket.gethostname().split('.')[0],('sb16','sb16-2'))
        self.assertNotEqual(os.getuid(),0,'Run fixture actor as dflanag3')
        self.name='m07-engine-'+uuid.uuid4().hex[:16]
        self.entry=Path('/var/lib/spatial/volumes')/self.name
        self.execution_override=self.entry/'mount'
        self.operator_directory=Path('/run')/self.name
        self.operator_run(['mkdir','-m','700',self.operator_directory])
        definition=broker.StorageVolumeDefinition()
        text_format.Parse((SOURCE/'config/operator-storage.textproto').read_text(),definition)
        definition.name=self.name
        policy=definition.owner_policy;policy.broker_uid=os.getuid()
        policy.volume_bytes=512*2**20;policy.control_plane_bytes=64*2**20;policy.filesystem_reserve_bytes=64*2**20
        policy.default_policy.total_bytes=64*2**20;policy.default_policy.actor_state_default_bytes=4*2**20
        for profile in policy.default_policy.profiles:profile.state_bytes=4*2**20
        self.copy_evidence=Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])/self._testMethodName
        self.copy_evidence.mkdir(mode=0o700,parents=True,exist_ok=False)
        (self.copy_evidence/'volume.json').write_text(json.dumps(dict(name=self.name,entry=str(self.entry),volume=str(self.execution_override),
            operator_directory=str(self.operator_directory),definition_hex=definition.SerializeToString(deterministic=True).hex(),qualification='UNQUALIFIED',cleanup='pending'),indent=2)+'\n')
        self.definition_path=self.root/'volume.textproto'
        self.definition_path.write_text(text_format.MessageToString(definition))
        self.owner_configuration=self.root/'owner.pb'
        self.addCleanup(self.cleanup_volume)
        created=json.loads(self.provision('create'))
        inventory=self.copy_evidence/'volume.json';record=json.loads(inventory.read_text());record['created']=created
        inventory.write_text(json.dumps(record,indent=2)+'\n')
        self.owner_configuration.write_bytes(self.operator_run(['cat',self.entry/'owner.pb']))
        self.owner_configuration.chmod(0o600)
        resource=broker.BrokerProcessConfig()
        text_format.Parse(Path(os.environ['SPATIAL_TEST_BROKER_CONFIG']).read_text(),resource)
        device=Path(subprocess.run(['findmnt','-n','-o','SOURCE','--target',str(self.execution_override)],
            capture_output=True,text=True,check=True).stdout.strip()).stat()
        major,minor=os.major(device.st_rdev),os.minor(device.st_rdev)
        for budget in (resource.resources.broker_budget,resource.resources.manager_budget,
                       resource.resources.default_policy.total,resource.resources.default_policy.actor_default,
                       *(profile.maximum for profile in resource.resources.default_policy.profiles)):
            if not any(item.major==major and item.minor==minor for item in budget.io):
                budget.io.add(major=major,minor=minor,read_bytes_per_second=64*2**20,write_bytes_per_second=16*2**20)
        copy_profile=resource.resources.copy_profiles.add(name='copy-limited',revision=1)
        copy_profile.policy.CopyFrom(resource.resources.default_policy)
        copy_profile.policy.total.memory_bytes=256*2**20;copy_profile.policy.total.processes=32
        copy_profile.policy.actor_default.memory_bytes=128*2**20;copy_profile.policy.actor_default.processes=16
        copy_profile.policy.ClearField('actors')
        self.copy_resource_config=self.root/'copy-resources.textproto'
        self.copy_resource_config.write_text(text_format.MessageToString(resource));self.copy_resource_config.chmod(0o600)
        self.patcher=patch.object(fixtures,'BrokerProcess',StorageBrokerProcess)
        self.patcher.start();self.addCleanup(self.patcher.stop)
    def provision(self,operation):
        args=[sys.executable,'-B',SOURCE/'tools/provision_storage.py',operation,'--bindings-dir',BUILD/'python']
        args+=['--definition',self.definition_path] if operation=='create' else ['--name',self.name]
        return self.operator_run([*args,'--apply'])
    def retain_copy_journal(self):
        evidence=dict(operations=[],effects=[])
        if hasattr(self,'execution') and (self.execution/'execution.db').exists():
            with closing(sqlite3.connect('file:'+str(self.execution/'execution.db')+'?mode=ro',uri=True,timeout=1)) as db:
                for identity,phase,error,request,binding in db.execute('SELECT operation_id,phase,error,request,authorization_binding FROM copy_operations LIMIT 65'):
                    evidence['operations'].append(dict(operation_id=identity.hex(),phase=phase,error=error,
                        request_bytes=len(request),request_sha256=hashlib.sha256(request).hexdigest(),
                        binding_bytes=len(binding),binding_sha256=hashlib.sha256(binding).hexdigest()))
                for identity,kind,state,intent,receipt in db.execute('SELECT operation_id,kind,state,intent,receipt FROM copy_effects ORDER BY rowid LIMIT 1025'):
                    evidence['effects'].append(dict(operation_id=identity.hex(),kind=kind,state=state,intent_bytes=len(intent),
                        intent_sha256=hashlib.sha256(intent).hexdigest(),receipt_bytes=len(receipt),
                        receipt_sha256=hashlib.sha256(receipt).hexdigest()))
                self.assertLessEqual(len(evidence['operations']),64);self.assertLessEqual(len(evidence['effects']),1024)
        (self.copy_evidence/'journal.json').write_text(json.dumps(evidence,sort_keys=True,indent=2)+chr(10))
    def cleanup_volume(self):
        # Attempt every close. Capture durable evidence before the image is unmounted.
        failures=[]
        for item in [*reversed(self.worlds),*reversed(self.brokers)]:
            try:item.close()
            except BaseException as error:failures.append(repr(error))
        try:self.retain_copy_journal()
        except BaseException as error:failures.append('journal capture: '+repr(error))
        unmounted=False
        try:
            if self.entry.exists():self.provision('unmount')
            unmounted=True
        except BaseException as error:failures.append('volume cleanup: '+repr(error))
        if hasattr(self,'copy_evidence'):
            path=self.copy_evidence/'volume.json';record=json.loads(path.read_text())
            record['cleanup']='unmounted' if unmounted else 'unconfirmed';record['cleanup_failures']=failures
            path.write_text(json.dumps(record,indent=2)+chr(10))
        if not failures:self.operator_run(['rm','-r','--',self.operator_directory])
        self.assertFalse(failures,'Fixture cleanup failed: '+repr(failures))
    def after_workspace_storage_setup(self):
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            repo,project=db.execute('SELECT r.path,t.path FROM workspaces w JOIN repositories r ON r.id=w.repo JOIN worktrees t ON t.id=w.worktree WHERE w.id=?',('one',)).fetchone()
        self.repository_object=Path(repo).name.removesuffix('.git')
        self.project_object=Path(project).name
        self.assertEqual(len(self.project_object),32)
        # A filename grant matching this opaque storage ID must never read the root.
        (Path(project)/self.project_object).write_bytes(b'filename-shaped storage identity\n')
        config=storage.StorageOwnerConfig.FromString(self.owner_configuration.read_bytes())
        config.copy_catalog.protocol_version=1;config.copy_catalog.revision=1
        for data_class,kind,identity,schema in (
            (copy.PROJECT_TREE,storage.StorageObject.PROJECT,self.project_object,'project-tree'),
            (copy.GIT_HISTORY,storage.StorageObject.REPOSITORY,self.repository_object,'git-history')):
            item=config.copy_catalog.entries.add(workspace_id='one',data_class=data_class,
                schema_id='spatial.workspace.'+schema,schema_version=1,maximum_bytes=32*2**20,
                maximum_entries=10000,consistency=storage.CRASH_CONSISTENT_AFTER_QUIESCENCE)
            item.source_object.kind=kind;item.source_object.id=identity
        profile=config.copy_destination_profiles.add(name='copy-evaluation',revision=1,
            maximum_total_bytes=64*2**20,maximum_entries=20000,allowed_classes=[copy.PROJECT_TREE,copy.GIT_HISTORY],
            execution_resource_profile='copy-limited',application_profile='development',execution_policy=[core.WORKSPACE_SELECT,core.FILES_READ,core.FILES_WRITE,core.PROCESS_LAUNCH])
        profile.storage_policy.CopyFrom(config.default_policy);profile.storage_policy.total_bytes=32*2**20
        self.owner_configuration.write_bytes(config.SerializeToString())
        private=self.operator_directory/'migration.pb'
        self.operator_run(['install','-m','600',self.owner_configuration,private])
        self.operator_run([BUILD/'spatial-storage-owner','--config',private,'--migrate-copy-catalog'])
    def copy_roots(self,root_read):
        rights=[grants.WORKSPACE_ACCESS,grants.PROCESS_EXECUTE,grants.POLICY_ADMINISTER,
                grants.FILE_BROWSE,grants.WORKSPACE_COPY_PROJECT,grants.WORKSPACE_COPY_GIT]
        roots=[self.workspace_terms(rights=rights,depth=0)]
        roots.append(self.workspace_terms(rights=[grants.FILE_READ],depth=0,
                                         components=() if root_read else (self.project_object.encode('utf-8'),)))
        return roots
    def request_copy(self,peer=None):
        page=self.own(peer) if peer else self.broker.request(grants=broker.GrantListRequest(workspace='one',principal=self.principal())).grants
        return copy.WorkspaceCopyRequest(protocol_version=1,operation_id=uuid.uuid4().bytes,source_workspace_id='one',
            expected_source_authority_revision=page.revision,expected_source_policy_revision=1,
            destination_policy_profile='copy-evaluation',expected_destination_profile_revision=1,
            data_class=[copy.PROJECT_TREE,copy.GIT_HISTORY])
    def test_begin_rejects_storage_id_filename_read_before_requested(self):
        self.workspace(roots=lambda:self.copy_roots(False),registry=True,attach_world=False)
        request=self.request_copy()
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            before=db.execute('SELECT count(*) FROM copy_operations').fetchone()[0]
        reply=self.broker.request(workspace_copy_begin=broker.WorkspaceCopyBeginRequest(principal=self.principal(),request=request))
        self.assertEqual(reply.error.code,core.FORBIDDEN,reply)
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM copy_operations').fetchone()[0],before)
        self.assertFalse(list((self.execution/'snapshots').iterdir()),'Denied begin allocated a snapshot')
        self.assertFalse(list((self.execution/'staging').iterdir()),'Denied begin allocated staging')
    def test_begin_root_read_reaches_durable_requested_and_exact_retry(self):
        self.workspace(roots=lambda:self.copy_roots(True),registry=True,attach_world=False)
        request=self.request_copy();start=time.monotonic()
        begin=broker.WorkspaceCopyBeginRequest(principal=self.principal(),request=request)
        reply=self.broker.request(workspace_copy_begin=begin)
        self.assertLess(time.monotonic()-start,5)
        self.assertEqual(reply.workspace_copy_status.phase,copy.REQUESTED,reply)
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            row=db.execute('SELECT request,requester,authorization_binding FROM copy_operations WHERE operation_id=?',(request.operation_id,)).fetchone()
        self.assertEqual(row[0],request.SerializeToString());self.assertEqual(row[1],self.principal().issuer_id+self.principal().actor_id)
        binding=storage.WorkspaceCopyAuthorizationBinding.FromString(row[2])
        self.assertEqual(binding.owner_catalog.catalog_revision,1)
        self.assertEqual(binding.exact_request_digest,hashlib.sha256(row[0]).digest())
        retried=self.broker.request(workspace_copy_begin=begin)
        self.assertEqual(retried.workspace_copy_status.operation_id,request.operation_id,retried)
        changed=broker.WorkspaceCopyBeginRequest();changed.CopyFrom(begin);changed.request.destination_policy_profile='different'
        rejected=self.broker.request(workspace_copy_begin=changed)
        self.assertEqual(rejected.error.code,core.OPERATION_CONFLICT,rejected)

    def git_oracle(self,path,*arguments):
        result=subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-C',str(path),*arguments],
            capture_output=True,timeout=15,env=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',
                GIT_AUTHOR_NAME='Copy Witness',GIT_AUTHOR_EMAIL='copy@example.invalid',
                GIT_COMMITTER_NAME='Copy Witness',GIT_COMMITTER_EMAIL='copy@example.invalid'))
        self.assertEqual(result.returncode,0,result.stderr);return result.stdout
    @staticmethod
    def start_time(pid):
        value=Path(f'/proc/{pid}/stat').read_text();return value[value.rfind(')')+2:].split()[19]
    def test_actor_copy_publishes_independent_dirty_git_and_preserves_source_process(self):
        self.relay_mode=False
        world=self.workspace(roots=lambda:self.copy_roots(True),registry=True)
        peer=world.peers[0];self.select(peer)
        process=self.launch_process(peer,'import time;time.sleep(360)',operation='copy-source-continuity')
        owned={pid:self.start_time(pid) for pid in self.descendants()}
        self.assertTrue(owned)
        witness={}
        for pid,start in owned.items():
            status=Path(f'/proc/{pid}/status').read_text()
            ns=[int(value) for line in status.splitlines() if line.startswith('NSpid:') for value in line.split()[1:]]
            relative=Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1]
            witness[str(pid)]=dict(start_ticks=start,namespace_pids=ns,pid_namespace_inode=Path(f'/proc/{pid}/ns/pid').stat().st_ino,
                cgroup=relative,argv=Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(bytes([0])).decode().split(chr(0)))
        payloads=[value for value in witness.values() if Path(value['cgroup']).name=='payload']
        self.assertEqual(len(payloads),1,witness)
        self.assertEqual(payloads[0]['argv'],['/usr/bin/python3','-c','import time;time.sleep(360)'])
        self.assertGreaterEqual(payloads[0]['namespace_pids'][-1],2)
        (self.copy_evidence/'source-processes.json').write_text(json.dumps(witness,sort_keys=True,indent=2)+chr(10))
        (self.project/'README.txt').write_bytes(b'staged version\n')
        self.git_oracle(self.project,'add','--','README.txt')
        (self.project/'README.txt').write_bytes(b'unstaged version\n')
        (self.project/'untracked.bin').write_bytes(b'actual untracked project bytes\x00\xff')
        (self.project/'.env').write_bytes(b'arbitrary authorized project bytes stay unchanged\n')
        before={name:self.git_oracle(self.project,*args) for name,args in {
            'staged':('diff','--cached','--binary'),'unstaged':('diff','--binary'),
            'status':('status','--porcelain=v1','-z'),'history':('rev-list','--all')}.items()}
        source_repository=self.execution/'repositories'/(self.repository_object+'.git')
        canary=b'fixture-credential-canary-excluded-from-git-administration'
        (source_repository/'undeclared-admin-sidecar').write_bytes(canary)
        (source_repository/'logs').mkdir(exist_ok=True)
        (source_repository/'logs'/'HEAD').write_bytes(canary+b'\n')
        self.git_oracle(self.project,'config','--local','remote.origin.url','https://fixture.invalid/'+canary.decode())
        request=self.request_copy(peer);started=time.monotonic()
        result=peer.act(workspace_copy=request)
        self.assertTrue(result.HasField('action'),result)
        self.assertEqual(result.action.copy_status.phase,copy.REQUESTED,result)
        self.assertLess(time.monotonic()-started,5)
        deadline=time.monotonic()+300;observed_frozen_access=False
        while True:
            for pid,start in owned.items():self.assertEqual(self.start_time(pid),start,'Source PID/start identity changed during copy')
            frozen=[]
            for pid in owned:
                relative=Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1]
                group=Path('/sys/fs/cgroup')/relative.lstrip('/')
                if group.name=='payload' and 'frozen 1' in (group/'cgroup.events').read_text():frozen.append(pid)
            if frozen and not observed_frozen_access:
                started_access=time.monotonic()
                self.select(peer)
                self.assertLess(time.monotonic()-started_access,5)
                observed_frozen_access=True
            result=peer.act(workspace_copy_status=copy.WorkspaceCopyStatusRequest(protocol_version=1,operation_id=request.operation_id))
            self.assertTrue(result.HasField('action'),result)
            status=result.action.copy_status
            self.assertNotIn(status.phase,(copy.ABORTED,copy.UNCERTAIN),status)
            if status.phase==copy.PUBLISHED:break
            self.assertLess(time.monotonic(),deadline,'Actual copy did not publish within its operation budget')
            time.sleep(.02)
        self.assertTrue(observed_frozen_access,'No access/status witness observed the real frozen source payload')
        destination=status.authorized_destination_workspace_id;self.assertNotEqual(destination,'one')
        self.assertEqual(len(status.authorized_manifest_digest),32)
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            destination_repo,destination_project=db.execute('SELECT r.path,t.path FROM workspaces w JOIN repositories r ON r.id=w.repo JOIN worktrees t ON t.id=w.worktree WHERE w.id=?',(destination,)).fetchone()
            self.assertEqual(db.execute('SELECT count(*) FROM processes WHERE workspace=?',(destination,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM workspace_members WHERE workspace=?',(destination,)).fetchone()[0],0)
            binding_bytes=db.execute('SELECT binding FROM workspace_copy_policies WHERE workspace=?',(destination,)).fetchone()[0]
            binding=broker.WorkspaceCopyResourceProfile.FromString(binding_bytes)
            self.assertEqual(binding.name,'copy-limited');self.assertEqual(binding.policy.total.memory_bytes,256*2**20)
            self.assertEqual([profile.name for profile in binding.policy.profiles],['development'])
        target=Path(destination_project)
        for name,args in {'staged':('diff','--cached','--binary'),'unstaged':('diff','--binary'),
                          'status':('status','--porcelain=v1','-z'),'history':('rev-list','--all')}.items():
            self.assertEqual(self.git_oracle(target,*args),before[name],name)
        self.assertEqual((target/'untracked.bin').read_bytes(),(self.project/'untracked.bin').read_bytes())
        self.assertEqual((target/'.env').read_bytes(),(self.project/'.env').read_bytes())
        self.assertNotIn(str(self.execution/'repositories'/self.repository_object),(target/'.git').read_text())
        self.assertIn(destination_repo,(target/'.git').read_text())
        for entry in Path(destination_repo).rglob('*'):
            if entry.is_file():self.assertEqual(entry.stat().st_nlink,1,str(entry))
        self.assertFalse((Path(destination_repo)/'objects/info/alternates').exists())
        self.assertFalse((Path(destination_repo)/'undeclared-admin-sidecar').exists())
        self.assertFalse((Path(destination_repo)/'logs').exists())
        self.assertNotIn(canary,(Path(destination_repo)/'config').read_bytes())
        for pid,start in owned.items():self.assertEqual(self.start_time(pid),start,'Source PID/start identity changed')
        page=self.own(peer);base=next(item for item in page.grants if grants.WORKSPACE_COPY_PROJECT in item.terms.rights)
        self.revoke_grant(peer,base)
        redacted=peer.act(workspace_copy_status=copy.WorkspaceCopyStatusRequest(protocol_version=1,operation_id=request.operation_id))
        self.assertTrue(redacted.HasField('action'),redacted)
        self.assertFalse(redacted.action.copy_status.HasField('authorized_destination_workspace_id'))
        self.assertFalse(redacted.action.copy_status.HasField('authorized_manifest_digest'))

    def test_async_actor_copy_rejects_authenticated_malformed_begin_before_start(self):
        self.relay_mode=True
        world=self.workspace(roots=lambda:self.copy_roots(True),registry=True)
        request=self.request_copy(world.peers[0])
        reply=world.peers[0].act(workspace_copy=request)
        self.assertTrue(reply.HasField('action'),reply)
        self.assertEqual(reply.action.copy_status.phase,copy.REQUESTED,reply)
        self.assertNotEqual(self.broker.proc.wait(timeout=30),0,'Malformed owner receipt did not fence the actual broker')
        world.close();self.broker.close()
        audit=json.loads(self.operator_run(['cat',self.broker.audit_path]))
        self.assertEqual(len(audit['mutations']),1,audit)
        self.assertIn('copy_stage_begin',audit['requests'])
        self.assertNotIn('copy_stage_start',audit['requests'])
        self.assertNotIn('copy_stage_seal',audit['requests'])
        self.assertNotIn('copy_destination_publish',audit['requests'])
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            self.assertEqual(db.execute('SELECT phase FROM copy_operations WHERE operation_id=?',(request.operation_id,)).fetchone()[0],copy.UNCERTAIN)
            self.assertEqual(db.execute("SELECT count(*) FROM copy_effects WHERE operation_id=? AND kind='worker-launch'",(request.operation_id,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM workspaces').fetchone()[0],1)
        self.assertTrue(all(not any(directory.iterdir()) for directory in (self.execution/'staging').iterdir()),'Malformed BEGIN copied bytes before its exact root binding was validated')

def load_tests(loader,tests,pattern):
    return unittest.TestSuite(WorkspaceCopyEngineTests(name) for name in (
        'test_begin_rejects_storage_id_filename_read_before_requested',
        'test_begin_root_read_reaches_durable_requested_and_exact_retry',
        'test_actor_copy_publishes_independent_dirty_git_and_preserves_source_process',
        'test_async_actor_copy_rejects_authenticated_malformed_begin_before_start'))

if __name__=='__main__':unittest.main()
