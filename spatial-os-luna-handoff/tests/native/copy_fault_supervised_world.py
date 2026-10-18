"""Real broker/world supervision for the lost-publication-ACK fixture only."""
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import select
import socket
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch

import test_workspace_copy_engine as engine
import issuer_pb2 as issuer
import launch_pb2 as launch
import key_pb2 as keys
import native_scope_pb2 as native_scope
import process_scope_pb2 as process_scope
import security_pb2 as security
from test_world_identity import SessionPeer
from harness.wire import frame
from harness.copy_fault_evidence import durable_write

BaseWorld=engine.fixtures.WorldProcess



def fixture_version(fixture):
    version=getattr(fixture,'supervised_copy_recovery_fixture_version',1)
    if type(version) is not int or version not in (1,2):
        raise ValueError('Unsupported supervised copy recovery fixture version: '+repr(version))
    return version


def enforce_fixture_generation_policy(fixture,version,rows):
    if version==1:
        fixture.assertTrue(all(not row[-1] for row in rows),
                            'Previous participant generation is still active')


def context(fixture):
    if not hasattr(fixture,'lostack_supervisors'):
        fixture.lostack_supervisors=LostAckSupervisors(fixture)
    return fixture.lostack_supervisors


class LostAckSupervisors:
    def __init__(self,fixture):
        self.fixture=fixture;self.handles=[];self.services={};self.native=None
        self.fixture_version=fixture_version(fixture);self.delegation_parents={}
        # Register before helper imports, native setUp, launch/config creation, or owned processes.
        fixture.addCleanup(self.cleanup)
        self.delegation=None
        if self.fixture_version==2:
            import supervised_delegation_parent as delegation
            self.delegation=delegation
        sys.path.insert(0,str(engine.SOURCE/'tests/native'))
        with patch.object(sys,'argv',[__file__,str(engine.BUILD/'spatial-authority-supervisor')]):
            native_fixture=importlib.import_module('test_security_identity')
        self.native=native_fixture.SecurityIdentityTests('runTest')
        self.native._testMethodName=fixture._testMethodName
        self.native.setUp();self.native.start()

    def activation_rows(self,participants):
        with sqlite3.connect('file:'+str(self.fixture.state/'identity.db')+'?mode=ro',uri=True,timeout=.5) as database:
            return database.execute('SELECT participant_id,generation,service_incarnation,registration,'
                'initial_revision,applied_revision,active FROM authority_activation WHERE participant_id IN ('+
                ','.join('?' for _ in participants)+') ORDER BY participant_id,generation',tuple(participants)).fetchall()

    def service(self,runtime):
        fixture=self.fixture;identity=runtime.participant_id
        rows=self.activation_rows([identity])
        enforce_fixture_generation_policy(fixture,self.fixture_version,rows)
        if identity not in self.services:
            key,public=fixture.key('lostack-supervisor-'+identity.hex()[:12])
            peer=fixture.connect()
            try:
                operation=issuer.IssuerOperation(supervisor_bind=issuer.AuthoritySupervisorBind(
                    participant_id=identity,registered_revision=runtime.registered_revision,
                    public_key=public,expected_revision=0))
                reply=fixture.execute(peer,fixture.challenge(peer,operation))
                fixture.assertTrue(reply.HasField('supervision'),reply)
            finally:peer.close()
            self.services[identity]=(key,public,reply.supervision)
        key,public,binding=self.services[identity]
        fixture.assertEqual(binding.registration.participant.id,identity)
        fixture.assertEqual(binding.registration.registered_revision,runtime.registered_revision)
        fixture.assertEqual(binding.public_key,public)
        return key,public,binding,max((row[1] for row in rows),default=0)

    def retire_failed_pair(self,world,broker):
        fixture=self.fixture;handles=[world.handle,broker.resources.handle]
        fixture.assertEqual(broker.owner.poll(),0,'Actual root owner has not completed EOF cleanup')
        fixture.assertTrue(broker.resources.closed)
        audit_bytes=fixture.operator_run(['cat',broker.audit_path]);audit=json.loads(audit_bytes)
        durable_write(fixture.copy_evidence/'lost-publication-root-owner-relay.json',audit_bytes)
        fixture.assertEqual((audit['owner_exit'],audit['broker_exit']),(0,0))
        fixture.assertEqual(audit['cleanup_errors'],[])
        records=[handle.failed_cleanup() for handle in handles]
        participants=[record.binding.participant_id for record in records]
        fixture.assertEqual(len(set(participants)),2,'World and broker must have independent participants')
        before=self.activation_rows(participants)
        # This evidence manifest hashes actual checked records; issuer retirement authenticates the exact generation.
        manifest=dict(schema_version=1,root_owner_exit=broker.owner.returncode,
            root_owner_relay_sha256=hashlib.sha256(audit_bytes).hexdigest(),
            scopes=[dict(role=handle.role,supervisor_pid=handle.proc.pid,supervisor_exit=handle.proc.returncode,
                actual_child=handle.identity,cleanup_sha256=hashlib.sha256(record.SerializeToString()).hexdigest(),
                cleanup_file=str(handle.evidence/'cleanup.pb')) for handle,record in zip(handles,records)])
        encoded=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
        durable_write(fixture.copy_evidence/'lost-publication-dependent-cleanup.json',encoded)
        digest=hashlib.sha256(encoded).digest()
        for handle,record in zip(handles,records):
            handle.retire(record,digest)
            expected=[row[:-1]+(0,) if row[0]==record.binding.participant_id and row[1]==record.fence.generation else row
                      for row in before]
            fixture.assertEqual(self.activation_rows(participants),expected,'Retirement changed another generation')
            before=expected

    def cleanup(self):
        failures=[]
        for handle in reversed(self.handles):
            try:handle.close()
            except BaseException as error:failures.append(type(error).__name__+': '+str(error)[:256])
            finally:
                handle.close_control()
                handle.preserve()
        if self.fixture_version==2:
            try:
                self.delegation.cleanup_owned_parents(self.handles,self.delegation_parents)
            except BaseException as error:
                failures.append(type(error).__name__+': '+str(error)[:256])
        if self.native is not None and not self.native.doCleanups():failures.append('Owned XACE fixture cleanup failed')
        if failures:raise AssertionError('; '.join(failures))


class SupervisedHandle:
    def __init__(self,owner,role,config,config_type,executable,arguments,inherited,log,
                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,resources=None):
        self.owner=owner;self.fixture=owner.fixture;self.role=role;self.proc=None;self.control=None
        self.pidfd=None;self.identity=None;self.initial=None;self.terminal=None;self.retirement=None
        self.pending_sockets=[];self.pending_fds=[];self.cgroup=None;self.closed=False;self.diagnostic_errors=[]
        self.evidence=None;self.private=None;self.journal=None;self.log=log;self.scope=None
        owner.handles.append(self)
        fixture=self.fixture;number=len(owner.handles)-1
        self.evidence=fixture.copy_evidence/('lostack-'+role+'-'+str(number));self.evidence.mkdir(mode=0o700)
        self.private=fixture.root/('lostack-'+role+'-'+str(number));self.private.mkdir(mode=0o700)
        # The actual SQLite journal (including any WAL) lives outside TemporaryDirectory from creation.
        self.journal=self.evidence/'journal';self.journal.mkdir(mode=0o700)
        self.log=log
        configuration=engine.text_format.Parse(Path(config).read_text(),config_type())
        runtime=engine.text_format.Parse(Path(configuration.participant_runtime_config).read_text(),
                                         issuer.AuthorityParticipantRuntimeConfig())
        self.key,self.public,self.supervision,self.previous_generation=owner.service(runtime)
        self.binding=launch.AuthorityLaunchBinding(issuer_id=self.supervision.registration.issuer_id,
            participant_id=runtime.participant_id,registered_revision=runtime.registered_revision,
            service_incarnation=os.urandom(32),supervised=True)
        runtime.service_incarnation=self.binding.service_incarnation
        attach=self.private/'attach.textproto';attach.write_text(engine.text_format.MessageToString(runtime));attach.chmod(0o600)
        child_config=self.private/'child-participant.textproto'
        configuration.participant_runtime_config=str(child_config)
        self.config_path=self.private/'authority.textproto'
        self.config_path.write_text(engine.text_format.MessageToString(configuration));self.config_path.chmod(0o600)
        arguments=[str(self.config_path) if item=='@AUTHORITY@' else item for item in arguments]
        if owner.fixture_version==1:
            self.cgroup=Path(os.environ['SPATIAL_TEST_CGROUP_ROOT'])/('lostack-'+role+'-'+os.urandom(8).hex())
            fixture.assertEqual(self.cgroup.parent.stat().st_uid,os.getuid());self.cgroup.mkdir()
            required_controllers={'cpu','memory','pids','io'}
            available_controllers=set((self.cgroup/'cgroup.controllers').read_text().split())
            subtree_control=self.cgroup/'cgroup.subtree_control'
            before_subtree_control=subtree_control.read_text().split()
            parent_processes=(self.cgroup/'cgroup.procs').read_text().split()
            self.controller_delegation=dict(role_cgroup=str(self.cgroup),
                parent_controllers=sorted(available_controllers),
                parent_subtree_control_before=before_subtree_control,
                parent_processes_before=parent_processes)
            durable_write(self.evidence/'controller-delegation.json',
                (json.dumps(self.controller_delegation,sort_keys=True,indent=2)+'\n').encode())
            fixture.assertEqual(parent_processes,[],
                                'Supervisor delegation parent must be empty; observed '+repr(parent_processes))
            fixture.assertTrue(required_controllers.issubset(available_controllers),
                'Supervisor delegation parent lacks required controllers: expected '+repr(sorted(required_controllers))+
                ', observed '+repr(sorted(available_controllers)))
            fixture.assertEqual(before_subtree_control,[],
                'Supervisor delegation parent must start unconfigured; observed '+repr(before_subtree_control))
            subtree_control.write_text('+cpu +memory +pids +io\n')
            enabled_controllers=set(subtree_control.read_text().split())
            self.controller_delegation['parent_subtree_control_after']=sorted(enabled_controllers)
            durable_write(self.evidence/'controller-delegation.json',
                (json.dumps(self.controller_delegation,sort_keys=True,indent=2)+'\n').encode())
            fixture.assertEqual(enabled_controllers,required_controllers,
                'Supervisor delegation parent did not enable every required controller: expected '+
                repr(sorted(required_controllers))+', observed '+repr(sorted(enabled_controllers)))
        else:
            try:
                state_index=arguments.index('--state-dir')
                state_dir=arguments[state_index+1]
            except (ValueError,IndexError) as error:
                raise AssertionError('supervised launch must derive its state directory from --state-dir') from error
            key,canonical_state=owner.delegation.execution_key(state_dir,runtime.participant_id)
            root=Path(os.environ['SPATIAL_TEST_CGROUP_ROOT']).resolve(strict=True)
            parent_name='lostack-'+hashlib.sha256((str(key[0])+':'+str(key[1])+':').encode()+key[2]).hexdigest()[:24]
            self.cgroup=root/parent_name
            parent=owner.delegation_parents.get(key)
            if parent is None:
                if self.cgroup.exists():
                    raise AssertionError('unowned stable delegation parent already exists')
                self.cgroup.mkdir()
                descriptor=os.open(self.cgroup,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
                parent=dict(path=self.cgroup,fd=descriptor,uid=os.getuid(),key=key,state_dir=str(canonical_state))
                owner.delegation_parents[key]=parent
                required_controllers={'cpu','memory','pids','io'}
                available_controllers=set((self.cgroup/'cgroup.controllers').read_text().split())
                subtree_control=self.cgroup/'cgroup.subtree_control'
                before_subtree_control=subtree_control.read_text().split()
                fixture.assertEqual(before_subtree_control,[],
                    'New supervisor delegation parent must start unconfigured; observed '+repr(before_subtree_control))
                fixture.assertTrue(required_controllers.issubset(available_controllers),
                    'Supervisor delegation parent lacks required controllers: expected '+repr(sorted(required_controllers))+
                    ', observed '+repr(sorted(available_controllers)))
                subtree_control.write_text('+cpu +memory +pids +io\n')
            else:
                fixture.assertEqual(parent['state_dir'],str(canonical_state),
                    'Stable delegation key resolved to a different named state directory')
                fixture.assertEqual(parent['path'],self.cgroup)
            self.delegation_parent=parent
            owner.delegation.verify_pinned_directory(self.cgroup,parent['fd'],os.getuid())
            required_controllers={'cpu','memory','pids','io'}
            available_controllers=set((self.cgroup/'cgroup.controllers').read_text().split())
            subtree_control=self.cgroup/'cgroup.subtree_control'
            before_subtree_control=subtree_control.read_text().split()
            parent_processes=(self.cgroup/'cgroup.procs').read_text().split()
            self.controller_delegation=dict(role_cgroup=str(self.cgroup),execution_directory=str(canonical_state),
                execution_directory_device=key[0],execution_directory_inode=key[1],participant_id=key[2].hex(),
                parent_controllers=sorted(available_controllers),parent_subtree_control_before=before_subtree_control,
                parent_processes_before=parent_processes)
            durable_write(self.evidence/'controller-delegation.json',
                (json.dumps(self.controller_delegation,sort_keys=True,indent=2)+'\n').encode())
            fixture.assertEqual(parent_processes,[],
                'Supervisor delegation parent must have no direct processes; observed '+repr(parent_processes))
            fixture.assertTrue(required_controllers.issubset(available_controllers),
                'Supervisor delegation parent lacks required controllers: expected '+repr(sorted(required_controllers))+
                ', observed '+repr(sorted(available_controllers)))
            fixture.assertEqual(set(before_subtree_control),required_controllers,
                'Stable supervisor delegation parent controller set changed')
            enabled_controllers=set(before_subtree_control)
            self.controller_delegation['parent_subtree_control_after']=sorted(enabled_controllers)
            durable_write(self.evidence/'controller-delegation.json',
                (json.dumps(self.controller_delegation,sort_keys=True,indent=2)+'\n').encode())
            fixture.assertEqual(enabled_controllers,required_controllers,
                'Supervisor delegation parent did not enable every required controller: expected '+
                repr(sorted(required_controllers))+', observed '+repr(sorted(enabled_controllers)))
        specification=launch.AuthoritySupervisorConfig(protocol_version=1,journal_directory=str(self.journal),
            delegated_cgroup=str(self.cgroup),binding=self.binding,
            participant=launch.AuthorityParticipantAttach(runtime_config=str(attach),
                domains=self.supervision.registration.participant.domains),
            authorization=launch.AuthorityLaunchAuthorize(issuer_client_config=runtime.issuer_client_config,
                private_key_path=str(self.key),supervision_revision=self.supervision.revision,
                previous_generation=self.previous_generation),
            launch=process_scope.AuthorityProcessLaunch(executable=str(executable),arguments=arguments,
                inherited_fds=[target for _,target in inherited]),
            child_participant_config=str(child_config),participant_target_fd=140,
            inherited_source_fds=[stream.fileno() for stream,_ in inherited])
        self.resource_template=None
        if resources is not None:
            fixture.assertEqual(resources.delegated_cgroup,'')
            specification.broker_resources.CopyFrom(resources)
            fixture.assertEqual(specification.broker_resources.SerializeToString(),resources.SerializeToString())
            specification.broker_resource_target_fd=143;specification.broker_native_target_fd=142
            self.resource_template=engine.broker.BrokerResourceConfig();self.resource_template.CopyFrom(resources)
            (self.evidence/'resource-template.pb').write_bytes(resources.SerializeToString())
        self.specification=specification
        path=self.private/'supervisor.textproto';path.write_text(engine.text_format.MessageToString(specification));path.chmod(0o600)
        native=owner.native
        native_config=native_scope.NativeScopeConfig(protocol_version=1,display_number=native.display,
            supervisor_cookie=native.cookie,server_id=native.supervisor.inspect().server_id,
            server_pid=native.server.pid,server_uid=os.getuid(),launch_id=os.urandom(32),role=security.BROKER)
        descriptor=os.memfd_create('lostack-supervisor-native',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        self.pending_fds.append(descriptor);os.write(descriptor,native_config.SerializeToString())
        fcntl.fcntl(descriptor,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        self.control,child=socket.socketpair();self.control.settimeout(5);self.pending_sockets.append(child)
        try:
            self.proc=subprocess.Popen([str(engine.BUILD/'spatial-authority-supervisor'),str(path),
                str(descriptor),str(child.fileno())],stdin=stdin,stdout=stdout,stderr=log,
                pass_fds=(descriptor,child.fileno(),*[stream.fileno() for stream,_ in inherited]))
        finally:
            child.close();self.pending_sockets.remove(child);os.close(descriptor);self.pending_fds.remove(descriptor)
        self.initial=self.receive()
        fixture.assertTrue(self.initial.HasField('view'),self.initial)
        record=self.initial.view.record
        fixture.assertEqual(record.stage,launch.START_ATTEMPTED);fixture.assertEqual(record.binding,self.binding)
        fixture.assertEqual(record,self.record());(self.evidence/'initial.pb').write_bytes(record.SerializeToString())
        children=Path('/proc/'+str(self.proc.pid)+'/task/'+str(self.proc.pid)+'/children').read_text().split()
        fixture.assertEqual(len(children),1,'Supervisor must directly own the actual authority process')
        self.pid=int(children[0]);start=fixture.start_time(self.pid);self.pidfd=os.pidfd_open(self.pid)
        fixture.assertEqual(fixture.start_time(self.pid),start)
        fixture.assertEqual(select.select([self.pidfd],[],[],0)[0],[])
        fixture.assertEqual(os.readlink('/proc/'+str(self.pid)+'/exe'),str(executable))
        self.scope=self.cgroup/record.process.scope_name
        fixture.assertNotEqual(self.scope,self.cgroup)
        child_controllers=set((self.scope/'cgroup.controllers').read_text().split())
        child_subtree_control=(self.scope/'cgroup.subtree_control').read_text().split()
        child_processes=(self.scope/'cgroup.procs').read_text().split()
        self.controller_delegation.update(child_scope=str(self.scope),
            child_controllers=sorted(child_controllers),
            child_subtree_control=child_subtree_control,
            child_processes=child_processes)
        durable_write(self.evidence/'controller-delegation.json',
            (json.dumps(self.controller_delegation,sort_keys=True,indent=2)+'\n').encode())
        fixture.assertTrue(required_controllers.issubset(child_controllers),
            'Supervisor child scope lacks delegated controllers: expected '+repr(sorted(required_controllers))+
            ', observed '+repr(sorted(child_controllers)))
        self.identity=dict(pid=self.pid,start_ticks=start,executable=str(executable),owned_scope=str(self.scope),
            supervisor_pid=self.proc.pid,participant_id=self.binding.participant_id.hex(),generation=self.previous_generation+1,
            service_incarnation=self.binding.service_incarnation.hex())
        (self.evidence/'process-identity.json').write_text(json.dumps(self.identity,sort_keys=True,indent=2)+'\n')
        self.preserve()

    def receive(self,deadline=None):
        deadline=time.monotonic()+5 if deadline is None else deadline
        def exact(size):
            data=b''
            while len(data)<size:
                self.fixture.assertGreater(deadline-time.monotonic(),0,'Supervisor control reply exceeded five seconds')
                self.fixture.assertTrue(select.select([self.control],[],[],max(0,deadline-time.monotonic()))[0],
                                        'Supervisor control reply exceeded five seconds')
                part=self.control.recv(size-len(data));self.fixture.assertTrue(part,'Supervisor control channel closed')
                data+=part
            return data
        size=int.from_bytes(exact(4),'big');self.fixture.assertLessEqual(size,1048576)
        reply=launch.AuthorityLaunchReply.FromString(exact(size));self.fixture.assertEqual(reply.protocol_version,1)
        return reply

    def record(self):
        with sqlite3.connect('file:'+str(self.journal/'launch.db')+'?mode=ro',uri=True,timeout=.5) as database:
            row=database.execute('SELECT record FROM launch WHERE singleton=1').fetchone()
        self.fixture.assertIsNotNone(row,'Supervisor did not persist its launch record')
        return launch.AuthorityLaunchRecord.FromString(row[0])

    def verify_active(self):
        rows=self.owner.activation_rows([self.binding.participant_id]);active=[row for row in rows if row[-1]]
        self.fixture.assertEqual(len(active),1)
        self.fixture.assertEqual(active[0][1],self.previous_generation+1)
        self.fixture.assertEqual(active[0][2],self.binding.service_incarnation)
        self.fixture.assertEqual(issuer.AuthorityParticipantRecord.FromString(active[0][3]),self.supervision.registration)
        self.fixture.assertEqual(self.fixture.start_time(self.pid),self.identity['start_ticks'])
        self.fixture.assertEqual(select.select([self.pidfd],[],[],0)[0],[])
        relative=Path('/proc/'+str(self.pid)+'/cgroup').read_text().strip().split('::',1)[1]
        actual=Path('/sys/fs/cgroup')/relative.lstrip('/')
        self.fixture.assertEqual(actual,self.scope/'broker-manager' if self.role=='broker' else self.scope)
        self.identity['actual_cgroup']=relative
        (self.evidence/'active.json').write_text(json.dumps(dict(self.identity,active=True),sort_keys=True,indent=2)+'\n')

    def verify_resources(self):
        fixture=self.fixture;template=self.resource_template
        fixture.assertIsNotNone(template)
        specification=engine.text_format.Parse((self.private/'supervisor.textproto').read_text(),launch.AuthoritySupervisorConfig())
        fixture.assertEqual(specification.broker_resources.SerializeToString(),template.SerializeToString())
        bound=engine.broker.BrokerResourceConfig();bound.CopyFrom(template);bound.delegated_cgroup=str(self.scope)
        normalized=engine.broker.BrokerResourceConfig();normalized.CopyFrom(bound);normalized.ClearField('delegated_cgroup')
        fixture.assertEqual(normalized.SerializeToString(),template.SerializeToString())
        actual={}
        for name,path,budget in [('broker',self.scope,template.broker_budget),('manager',self.scope/'broker-manager',template.manager_budget)]:
            values={key:(path/key).read_text().strip() for key in ('memory.max','memory.swap.max','pids.max','cpu.max','io.max')}
            fixture.assertEqual(values['memory.max'],str(budget.memory_bytes))
            fixture.assertEqual(values['memory.swap.max'],str(budget.swap_bytes))
            fixture.assertEqual(values['pids.max'],str(budget.processes))
            fixture.assertEqual(values['cpu.max'],str(budget.cpu_microseconds)+' 100000')
            for limit in budget.io:
                rows=[line for line in values['io.max'].splitlines() if line.startswith(str(limit.major)+':'+str(limit.minor)+' ')]
                fixture.assertEqual(len(rows),1)
                fixture.assertIn('rbps='+str(limit.read_bytes_per_second),rows[0])
                fixture.assertIn('wbps='+str(limit.write_bytes_per_second),rows[0])
            actual[name]=values
        (self.evidence/'expected-bound-resource-config.pb').write_bytes(bound.SerializeToString())
        (self.evidence/'resource-equivalence.json').write_text(json.dumps(dict(
            only_changed_field='delegated_cgroup',
            sealed_config_observation='Exact input template; production sealed handoff; independent kernel root and manager limits. Consumed FD is not reread.',template_sha256=hashlib.sha256(template.SerializeToString()).hexdigest(),
            bound_sha256=hashlib.sha256(bound.SerializeToString()).hexdigest(),kernel_limits=actual),sort_keys=True,indent=2)+'\n')

    def close_control(self):
        if self.control is not None:self.control.close()

    def failed_cleanup(self):
        try:return self.validate_failed_cleanup()
        finally:self.close_control()

    def validate_failed_cleanup(self):
        fixture=self.fixture
        fixture.assertEqual(self.proc.wait(timeout=15),2,'Expected genuine supervisor failure exit')
        if self.terminal is None:self.terminal=self.receive()
        fixture.assertTrue(self.terminal.HasField('error'),self.terminal)
        fixture.assertTrue(self.terminal.error.HasField('cleanup_record'),self.terminal)
        record=self.terminal.error.cleanup_record;fixture.assertEqual(record,self.record())
        durable_write(self.evidence/'cleanup.pb',record.SerializeToString())
        fixture.assertEqual(record.stage,launch.OWNED_SCOPES_STOPPED)
        fixture.assertEqual(record.process_stopped.identity,record.process)
        fixture.assertEqual(record.native_stopped.identity,record.native)
        fixture.assertFalse(self.scope.exists())
        fixture.assertTrue(self.identity is not None and self.child_dead,'Actual authority death was not witnessed')
        self.death_observed=True;self.death_observed_monotonic_ns=time.monotonic_ns()
        fixture.assertTrue(record.HasField('fence'),'Missing exact failed-generation supervisor fence')
        fence=record.fence;authorization=self.initial.view.record.authorization
        fixture.assertEqual(record.binding,self.binding)
        fixture.assertEqual(record.process,self.initial.view.record.process)
        fixture.assertEqual(record.native,self.initial.view.record.native)
        fixture.assertEqual(record.authorization,self.initial.view.record.authorization)
        fixture.assertEqual(fence.registration,self.supervision.registration)
        fixture.assertEqual(fence.generation,self.previous_generation+1)
        fixture.assertEqual(fence.service_incarnation,self.binding.service_incarnation)
        fixture.assertEqual(fence.launch_id,record.process.launch_id)
        fixture.assertEqual(fence.authorization_id,authorization.authorization_id)
        fixture.assertEqual(fence.supervision_revision,self.supervision.revision)
        fixture.assertEqual(fence.supervisor_public_key,self.public)
        self.preserve();return record

    @property
    def child_dead(self):
        return bool(select.select([self.pidfd],[],[],0)[0]) if self.pidfd is not None else getattr(self,'death_observed',False)

    def retire(self,record,digest):
        fixture=self.fixture;fence=record.fence
        request=issuer.AuthorityRetireBegin(owner=issuer.AuthorityFenceBegin(
            participant_id=self.binding.participant_id,registered_revision=self.binding.registered_revision,
            generation=fence.generation,service_incarnation=fence.service_incarnation,launch_id=fence.launch_id,
            supervision_revision=fence.supervision_revision,authorization_id=fence.authorization_id),
            fence_id=fence.fence_id,cleanup_sha256=digest)
        peer=fixture.connect()
        try:
            result=peer.call(authority_retire_begin=request);fixture.assertTrue(result.HasField('challenge'),result)
            fixture.assertEqual(result.challenge.signing_request.purpose,keys.AUTHORITY_RETIREMENT)
            challenge=issuer.AuthorityRetireChallenge.FromString(result.challenge.signing_request.payload)
            fixture.assertEqual(challenge.protocol_version,1);fixture.assertEqual(challenge.issuer_id,self.binding.issuer_id)
            fixture.assertEqual(challenge.request,request);fixture.assertEqual(challenge.supervisor_public_key,self.public)
            fixture.assertEqual(len(challenge.connection_binding),32)
            result=peer.call(authority_retire_complete=issuer.AuthorityRetireComplete(nonce=result.challenge.nonce,
                proof=fixture.proof(result.challenge,self.key,self.public)))
            fixture.assertTrue(result.HasField('authority_retirement'),result);self.retirement=result.authority_retirement
        finally:peer.close()
        fixture.assertEqual(self.retirement.request,request);fixture.assertEqual(self.retirement.registration,self.supervision.registration)
        fixture.assertEqual(self.retirement.supervisor_public_key,self.public);fixture.assertEqual(len(self.retirement.retirement_id),32)
        with sqlite3.connect('file:'+str(fixture.state/'identity.db')+'?mode=ro',uri=True,timeout=.5) as database:
            saved=database.execute('SELECT state FROM authority_retirement WHERE participant_id=? AND generation=?',
                (self.binding.participant_id,fence.generation)).fetchone()
        fixture.assertIsNotNone(saved);fixture.assertEqual(saved[0],self.retirement.SerializeToString())
        (self.evidence/'retirement.pb').write_bytes(self.retirement.SerializeToString())

    def preserve(self):
        # Diagnostic failures are recorded separately and never replace a behavior exception.
        if self.evidence is None:return
        try:
            if self.log is not None:
                if not self.log.closed:self.log.flush()
                (self.evidence/'supervisor.log').write_bytes(Path(self.log.name).read_bytes())
            for name in ('supervisor.textproto','authority.textproto','attach.textproto','child-participant.textproto'):
                path=self.private/name if self.private is not None else None
                if path is not None and path.exists():(self.evidence/name).write_bytes(path.read_bytes())
            if self.journal is not None and (self.journal/'launch.db').exists():
                deadline=time.monotonic()+2
                def bounded(_status,_remaining,_total):
                    if time.monotonic()>deadline:raise TimeoutError('Supervisor journal snapshot exceeded two seconds')
                snapshot=self.evidence/('journal-snapshot-'+str(time.monotonic_ns())+'.partial')
                with sqlite3.connect('file:'+str(self.journal/'launch.db')+'?mode=ro',uri=True,timeout=.1) as source:
                    with sqlite3.connect(snapshot,timeout=.1) as destination:source.backup(destination,pages=64,progress=bounded,sleep=.01)
                snapshot.replace(self.evidence/'journal-snapshot.db')
        except Exception as error:self.diagnostic_errors.append(type(error).__name__+': '+str(error)[:256])
        try:
            (self.evidence/'retention.json').write_text(json.dumps(dict(supervisor_pid=None if self.proc is None else self.proc.pid,
                supervisor_exit=None if self.proc is None else self.proc.poll(),closed=self.closed,
                actual_child=self.identity,actual_child_dead=getattr(self,'death_observed',False),
                death_observed_monotonic_ns=getattr(self,'death_observed_monotonic_ns',None),diagnostic_errors=self.diagnostic_errors),sort_keys=True,indent=2)+'\n')
        except Exception as error:
            self.diagnostic_errors.append(type(error).__name__+': '+str(error)[:256])
            print('Supervisor retention diagnostic failed: '+type(error).__name__,file=sys.stderr)

    def close(self):
        if self.closed:return
        try:
            if self.proc is not None:
                if self.proc.poll() is None:
                    if self.proc.stdin is not None and not self.proc.stdin.closed:self.proc.stdin.close()
                    try:self.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.control.sendall(frame(launch.AuthorityLaunchRequest(protocol_version=1,request_id=1,
                            stop=launch.AuthorityLaunchQuery())))
                        deadline=time.monotonic()+5
                        while True:
                            reply=self.receive(deadline)
                            if reply.HasField('error'):
                                self.terminal=reply
                                self.fixture.assertTrue(reply.error.HasField('cleanup_record'),reply)
                                self.fixture.assertEqual(reply.error.cleanup_record.stage,launch.OWNED_SCOPES_STOPPED)
                                break
                            if reply.request_id==1:
                                self.fixture.assertTrue(reply.HasField('view'),reply)
                                self.fixture.assertEqual(reply.view.record.stage,launch.OWNED_SCOPES_STOPPED)
                                break
                            self.fixture.assertEqual(reply.request_id,0,'Unexpected supervisor control reply')
                        self.proc.wait(timeout=5)
                self.death_observed=self.child_dead
                self.death_observed_monotonic_ns=time.monotonic_ns()
                for stream in (self.proc.stdin,self.proc.stdout):
                    if stream is not None and not stream.closed:stream.close()
            if self.owner.fixture_version==1:
                if self.cgroup is not None and self.cgroup.exists():
                    self.fixture.assertNotIn('populated 1',(self.cgroup/'cgroup.events').read_text())
                    self.fixture.assertFalse(any(path.is_dir() for path in self.cgroup.iterdir()))
                    self.cgroup.rmdir()
            else:
                if self.scope is not None and self.scope.exists():
                    self.fixture.assertNotIn('populated 1',(self.scope/'cgroup.events').read_text())
                    self.fixture.assertFalse(any(path.is_dir() for path in self.scope.iterdir()))
                    raise AssertionError('Generation ProcessScope root remains after handle retirement')
                if self.cgroup is not None:
                    self.owner.delegation.verify_pinned_directory(
                        self.cgroup,self.delegation_parent['fd'],os.getuid())
            self.closed=True
        finally:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
                try:self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:self.diagnostic_errors.append('Supervisor termination cleanup remains unresolved')
            self.preserve()
            for stream in self.pending_sockets:stream.close()
            self.pending_sockets.clear()
            for descriptor in self.pending_fds:os.close(descriptor)
            self.pending_fds.clear()
            # Keep the failure/control reply available until failed_cleanup has validated it.
            if self.closed and self.proc is not None and self.proc.returncode==0 and self.control is not None:self.control.close()
            if self.pidfd is not None and self.closed:os.close(self.pidfd);self.pidfd=None


class SupervisedBrokerResources:
    def __init__(self,fixture,executable,command,channel,log,owner_log,configuration,extra_fds):
        self.closed=False
        fixture.assertEqual(set(extra_fds),{'--storage-fd'})
        config_path=command[command.index('--authority-config')+1]
        template=engine.text_format.Parse(Path(configuration).read_text(),engine.broker.BrokerProcessConfig())
        fixture.assertEqual(template.resources.delegated_cgroup,'')
        storage=socket.socket(fileno=os.dup(extra_fds['--storage-fd']))
        try:
            self.handle=SupervisedHandle(context(fixture),'broker',config_path,engine.broker.WorkspaceAuthorityConfig,
                executable,['--state-dir',command[command.index('--state-dir')+1],'--authority-config','@AUTHORITY@',
                    '--storage-fd','130','--native-security-fd','142'],[(storage,130)],log,
                stdin=channel,stdout=channel,resources=template.resources)
        finally:storage.close()
        self.process=self.handle.proc;self.pid=self.handle.pid;self.root=self.handle.scope

    def close(self):
        if self.closed:return
        self.handle.close();self.closed=True


class SupervisedCopyFaultWorld(BaseWorld):
    def __init__(self,fixture,config,state,native=False,count=2,runtime_config=None,broker=None,
                 fixture_faults=False,previous=None):
        fixture.assertFalse(native);fixture.assertFalse(fixture_faults);fixture.assertIsNotNone(broker)
        self.fixture=fixture;self.sequence=0;self.peers=[];self.broker_channel=None;self.handle=None
        self.original_config=Path(config);self.state_path=state;self.runtime_path=runtime_config
        self.pending=[];fixture.addCleanup(self.close)
        pairs=[]
        for _ in range(count):
            parent,child=socket.socketpair();pairs.append((parent,child));self.peers.append(SessionPeer(parent));self.pending.append(child)
        workspace,self.broker_channel,workspace_owned=BaseWorld.workspace_channel(broker)
        if workspace_owned:self.pending.append(workspace)
        args=['--state-dir',str(state),'--identity-config','@AUTHORITY@','--workspace-fd','130','--workspace-pid',str(broker.pid)]
        if runtime_config:args+=['--config',str(runtime_config)]
        for index in range(count):args+=['--actor-fd',str(131+index)]
        self.log=(fixture.root/('world-'+str(len(fixture.worlds))+'.log')).open('wb')
        try:
            self.handle=SupervisedHandle(context(fixture),'world',config,engine.fixtures.session.WorldIdentityConfig,
                engine.fixtures.WORLD,args,[(workspace,130),*[(server,131+index) for index,(_,server) in enumerate(pairs)]],self.log)
        finally:
            for stream in self.pending:stream.close()
            self.pending.clear()
        self.proc=self.handle.proc;self.config_path=self.handle.config_path;self.previous=previous

    def management(self,method,**arguments):
        result=super().management(method,**arguments)
        if method=='system.hello':
            self.handle.verify_active();self.fixture.broker.resources.handle.verify_active()
            self.fixture.broker.resources.handle.verify_resources()
            self.world_id=super().management('world.info')['world_id']
            if self.previous is not None:
                old=self.previous;self.fixture.assertIsNotNone(old.handle.retirement)
                self.fixture.assertEqual(self.world_id,old.world_id,'Restart changed world identity')
                self.fixture.assertEqual(self.handle.supervision,old.handle.supervision)
                self.fixture.assertEqual(self.handle.previous_generation,old.handle.previous_generation+1)
                self.fixture.assertNotEqual(self.handle.binding.service_incarnation,old.handle.binding.service_incarnation)
                before=old.crashed_broker.resources.handle;after=self.fixture.broker.resources.handle
                self.fixture.assertIsNotNone(before.retirement)
                self.fixture.assertEqual(after.supervision,before.supervision)
                self.fixture.assertEqual(after.previous_generation,before.previous_generation+1)
                self.fixture.assertNotEqual(after.binding.service_incarnation,before.binding.service_incarnation)
            (self.handle.evidence/'world-identity.json').write_text(json.dumps(dict(world_id=self.world_id,
                participant_id=self.handle.binding.participant_id.hex(),generation=self.handle.previous_generation+1),
                sort_keys=True,indent=2)+'\n')
        return result

    def retire_after_broker_death(self,broker):
        self.crashed_broker=broker
        context(self.fixture).retire_failed_pair(self,broker)

    def close(self):
        for peer in self.peers:peer.close()
        for stream in self.pending:stream.close()
        self.pending.clear()
        if self.handle is not None:self.handle.close()
        if self.broker_channel is not None:self.broker_channel.close();self.broker_channel=None
        if hasattr(self,'log') and not self.log.closed:self.log.close()
