#!/usr/bin/env python3
"""Nine M06 geometry oracles with real peer-host production presentation.

Run once on each authority host. The observer must be the other qualification host.
Runtime credentials live only in disposable, private fixture directories.
"""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import select
import shlex
import socket
import struct
import subprocess
import sys
import time
import unittest
import uuid
from google.protobuf import text_format
import actor_session_client_pb2 as actor_client
import core_pb2 as core
import grants_pb2 as grants
import geometry_native_pb2 as witness
import host_actor_controller_pb2 as transitions
import host_session_pb2 as host
import world_session_pb2 as session
import test_world_shared_state as baseline
from harness.oracles import pose
from harness.wire import frame
from geometry_process_identity import finish_observer
from geometry_host_control_handoff import adopt_initial_host_control


def exact(fd,count,timeout=90,deadline=None):
    result=bytearray()
    if deadline is None:deadline=time.monotonic()+timeout
    while len(result)<count:
        remaining=deadline-time.monotonic()
        if remaining<=0 or not select.select([fd],[],[],remaining)[0]:raise TimeoutError('Witness absolute frame deadline')
        part=os.read(fd,count-len(result))
        if not part:raise EOFError('Witness frame ended')
        result.extend(part)
    return bytes(result)


class NativeCrossHostTests(baseline.SharedWorldStateTests):
    def setUp(self):
        self.authority_host=socket.gethostname().split('.')[0]
        self.observer_host=os.environ['SPATIAL_GEOMETRY_OBSERVER_HOST']
        self.assertEqual({self.authority_host,self.observer_host},{'sb16','sb16-2'})
        self.address=os.environ['SPATIAL_GEOMETRY_AUTHORITY_ADDRESS']
        self.assertFalse(ipaddress.ip_address(self.address).is_loopback)
        self.assertFalse(ipaddress.ip_address(self.address).is_unspecified)
        self.remote_source=Path(os.environ['SPATIAL_GEOMETRY_REMOTE_SOURCE'])
        self.remote_bin=Path(os.environ['SPATIAL_GEOMETRY_REMOTE_BIN'])
        self.remote_python=Path(os.environ['SPATIAL_GEOMETRY_REMOTE_PYTHON'])
        self.assertTrue(self.remote_source.is_absolute() and self.remote_bin.is_absolute() and self.remote_python.is_absolute())
        super().setUp();self.enroll_second()
        self.active_worlds={};self.owners={};self.observation_grants={}
        for name,native in (('a',False),('b',True),('denied',False)):
            local=self.scoped_world(authenticate=False);local.close()
            world=baseline.PersistentTlsWorld(self,local,native=native,address=self.address)
            self.worlds.append(world);world.management('system.hello')
            self.active_worlds[name]=world
            peer=world.connect(self)
            spawned=core.Pose(position=core.Vec3(x=.7,z=-2),orientation=core.Quaternion(w=1))
            opened=self.authenticate(peer,pose=spawned)
            self.assertTrue(opened.HasField('opened'),opened);self.owners[name]=peer
            if name!='denied':
                self.world_info=world.management('world.info')
                parent=self.root_grant(peer)
                entry=self.delegate(peer,parent,self.terms(self.second,
                    rights=(grants.WORLD_ENTER,grants.WORLD_NAVIGATE),depth=0))
                observe=self.delegate(peer,parent,self.terms(self.second,rights=(grants.WORLD_OBSERVE,),depth=0))
                self.assertTrue(entry.HasField('grant'),entry);self.assertTrue(observe.HasField('grant'),observe)
                self.observation_grants[name]=observe.grant
        self.host_sequence=0;self.witness_sequence=0;self.generation=0
        self.capture_count=0;self.reply_count=0
        self.evidence=Path(os.environ['SPATIAL_GEOMETRY_EVIDENCE'])/self._testMethodName
        self.evidence.mkdir(parents=True,exist_ok=False)
        self.addCleanup(self.save_evidence)
        self.start_observer()
        self.initial_processes=self.exchange(identity=True).processes

    def save_evidence(self):
        for name in ('peer-native.stderr','peer-resource-launch.json','boundary.stdout','boundary.stderr'):
            path=self.root/name
            if path.exists():(self.evidence/name).write_bytes(path.read_bytes())

    def owner_channel(self,name):
        # Provisioning can exceed the fixture's idle timeout. Open a fresh actor channel.
        self.owners[name].close()
        peer=self.active_worlds[name].connect(self)
        result=self.authenticate(peer)
        self.assertTrue(result.HasField('opened'),result)
        self.owners[name]=peer
        return peer

    def remote(self,argv,**options):
        return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',self.observer_host,
            shlex.join([str(value) for value in argv])],check=True,timeout=options.pop('timeout',30),**options)

    def start_observer(self):
        created=self.remote(['mktemp','-d','/tmp/spatial-m06-geometry-XXXXXXXX'],capture_output=True,text=True)
        self.remote_root=created.stdout.strip()
        self.assertTrue(self.remote_root.startswith('/tmp/spatial-m06-geometry-'))
        self.assertNotIn('/',self.remote_root[len('/tmp/'):])
        self.addCleanup(self.remove_remote_profiles)
        profiles=self.root/'native-profiles';profiles.mkdir(mode=0o700)
        files=[(self.service_cert,'client.crt'),(self.service_key,'client.key'),(self.second_key,'actor.key')]
        for name,world in self.active_worlds.items():files.append((world.server_cert,'server-'+name+'.crt'))
        self.remote_files=[]
        for source,name in files:
            target=self.remote_root+'/'+name;self.remote_files.append(target)
            subprocess.run(['scp','-q',str(source),self.observer_host+':'+target],check=True,timeout=15)
        for name,world in self.active_worlds.items():
            value=actor_client.ActorSessionClientTlsConfig(protocol_version=1,address=self.address,port=world.port,
                server_name='world.test',expected_world_id=world.management('world.info')['world_id'],
                certificate_path=self.remote_root+'/client.crt',private_key_path=self.remote_root+'/client.key',
                server_ca_path=self.remote_root+'/server-'+name+'.crt',actor_signing_key_path=self.remote_root+'/actor.key',
                actor_credential=self.second_record.credential,io_timeout_ms=3000)
            path=profiles/('actor-'+name+'.textproto');path.write_text(text_format.MessageToString(value));path.chmod(0o600)
            target=self.remote_root+'/'+path.name;self.remote_files.append(target)
            subprocess.run(['scp','-q',str(path),self.observer_host+':'+target],check=True,timeout=15)
        environment=['env','PYTHONPATH='+':'.join(map(str,(self.remote_python,self.remote_source,
            self.remote_source/'tests/native',self.remote_source/'tests/core'))),
            'SPATIAL_TEST_XSERVER='+os.environ['SPATIAL_GEOMETRY_REMOTE_XSERVER'],
            'ASAN_OPTIONS=detect_leaks=1:abort_on_error=1','UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
            'SPATIAL_SECURITY_EVIDENCE='+os.environ['SPATIAL_GEOMETRY_REMOTE_EVIDENCE']+'/'+self._testMethodName]
        self.owned_unit='spatial-m03-resources-m06-geometry-'+uuid.uuid4().hex+'.service'
        argv=environment+['python3',self.remote_source/'tools/geometry_native_delegate.py',self.owned_unit,'--',
            'python3',self.remote_source/'tools/with_adapter_resources.py','--','python3',
            self.remote_source/'tests/native/geometry_native_host.py',self.remote_source,
            self.remote_bin/'spatial-authority-launch-probe',self.remote_bin/'spatial-security-auth',
            self.remote_bin/'identityd',self.remote_bin/'spatial-issuer-client',
            self.remote_bin/'spatial-host-session',self.remote_root]
        self.remote_log=(self.root/'peer-native.stderr').open('wb')
        self.observer_ready=False
        self.observer=subprocess.Popen(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',self.observer_host,
            shlex.join([str(value) for value in argv])],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.remote_log)
        self.addCleanup(self.stop_observer)
        startup_deadline=time.monotonic()+90
        # The trusted resource wrapper emits exactly one JSON launch receipt before
        # the child's strict protobuf stream. Keep its bytes as separate evidence.
        prefix=bytearray()
        while not prefix.endswith(b'\n'):
            self.assertLess(len(prefix),65536);prefix.extend(exact(self.observer.stdout.fileno(),1,deadline=startup_deadline))
        receipt=json.loads(prefix)
        self.assertEqual(receipt['unit'],self.owned_unit)
        (self.root/'peer-resource-launch.json').write_bytes(prefix)
        ready=self.receive(deadline=startup_deadline)
        self.assertEqual(ready.request_id,0);self.assertEqual(len(ready.processes),2)
        self.assertTrue(ready.HasField('control'),ready)
        self.assertFalse(ready.pixels or ready.error or ready.cleanup_complete,ready)
        cursor=adopt_initial_host_control(ready.control,expected_request_id=1)
        self.host_sequence=cursor.sequence;self.generation=cursor.generation
        self.observer_ready=True

    def remove_remote_profiles(self):
        if hasattr(self,'remote_files'):
            self.remote(['rm','-f','--',*self.remote_files]);self.remote(['rmdir','--',self.remote_root])

    def receive(self,deadline=None):
        if deadline is None:deadline=time.monotonic()+90
        fd=self.observer.stdout.fileno();header=exact(fd,4,deadline=deadline)
        size,=struct.unpack('!I',header);self.assertGreater(size,0);self.assertLessEqual(size,1048576)
        reply=witness.NativeGeometryWitnessReply.FromString(exact(fd,size,deadline=deadline))
        self.assertFalse(reply.error,reply)
        self.reply_count+=1
        (self.evidence/f'reply-{self.reply_count:04}.pb').write_bytes(frame(reply))
        return reply

    def exchange(self,**operation):
        self.witness_sequence+=1
        self.observer.stdin.write(frame(witness.NativeGeometryWitnessRequest(request_id=self.witness_sequence,**operation)))
        self.observer.stdin.flush();reply=self.receive()
        self.assertEqual(reply.request_id,self.witness_sequence,reply)
        return reply

    def recover_unit(self):
        # The controller selected this unique unit before launch. No reply chooses it.
        result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',self.observer_host,
            shlex.join(['sudo','-n','systemctl','stop',self.owned_unit])],capture_output=True,timeout=15)
        (self.evidence/'exact-unit-recovery.stdout').write_bytes(result.stdout)
        (self.evidence/'exact-unit-recovery.stderr').write_bytes(result.stderr)
        # A collected unit may already be absent. Check the exact unit and cgroup.
        self.verify_unit_gone()

    def verify_unit_gone(self):
        deadline=time.monotonic()+10
        while True:
            result=subprocess.run(
                ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',self.observer_host,
                 shlex.join(['systemctl','show',self.owned_unit,'-p','ActiveState','-p','LoadState'])],
                capture_output=True,text=True,timeout=10)
            # systemctl show returns 0 or 4 for this absent-unit query on the
            # qualified systemd lane. SSH/other query failures are never cleanup.
            self.assertIn(result.returncode,(0,4),(result.returncode,result.stdout,result.stderr))
            if 'LoadState=not-found' in result.stdout and 'ActiveState=inactive' in result.stdout:break
            self.assertLess(time.monotonic(),deadline,(result.stdout,result.stderr));time.sleep(.1)
        self.remote(['test','!','-e','/sys/fs/cgroup/system.slice/'+self.owned_unit])
        (self.evidence/'exact-unit-terminal.json').write_text(json.dumps(dict(
            unit=self.owned_unit,state=result.stdout,query_exit=result.returncode,cgroup_absent=True))+'\n')

    def stop_observer(self):
        if not hasattr(self,'observer'):return
        original=None;cleanup=[];remaining=b''
        try:
            if self.observer.poll() is None:
                try:
                    finish_observer(self.observer_ready,self.exchange,self.recover_unit)
                except BaseException as error:
                    original=error
                    try:self.recover_unit()
                    except BaseException as recovery:cleanup.append('Exact-unit recovery: '+repr(recovery))
                finally:
                    self.observer.stdin.close();self.observer.stdin=None
            try:
                remaining=self.observer.communicate(timeout=25)[0]
            except subprocess.TimeoutExpired as error:
                if original is None:original=error
                try:self.recover_unit()
                except BaseException as recovery:cleanup.append('Exact-unit recovery: '+repr(recovery))
                try:remaining=self.observer.communicate(timeout=10)[0]
                except subprocess.TimeoutExpired:
                    # Only terminate the retained local SSH child. Remote cleanup
                    # remains separately uncertain and is never reported passed.
                    self.observer.terminate()
                    try:remaining=self.observer.communicate(timeout=5)[0]
                    except subprocess.TimeoutExpired:
                        self.observer.kill();remaining=self.observer.communicate(timeout=5)[0]
                    cleanup.append('Local SSH child required termination')
            try:self.verify_unit_gone()
            except BaseException as error:cleanup.append('Unit/cgroup audit: '+repr(error))
            if original is not None or cleanup:
                raise RuntimeError('Original witness error: '+repr(original)+'; cleanup: '+repr(cleanup)) from original
            self.assertLessEqual(len(remaining),65536)
            receipts=[json.loads(line) for line in remaining.splitlines() if line]
            adapter=[item for item in receipts if 'scope_removed' in item]
            terminal=[item for item in receipts if 'unit_after' in item]
            self.assertEqual(len(adapter),1,receipts);self.assertEqual(len(terminal),1,receipts)
            self.assertTrue(adapter[0]['scope_removed']);self.assertFalse(adapter[0]['unexpected_population'])
            if self.observer_ready:self.assertEqual(adapter[0]['exit_code'],0)
            self.assertEqual(terminal[0]['unit'],self.owned_unit)
            self.assertIn(terminal[0]['query_exit'],(0,4))
            self.assertIn('LoadState=not-found',terminal[0]['unit_after'])
            self.assertIn('ActiveState=inactive',terminal[0]['unit_after'])
            self.assertFalse(terminal[0]['cgroup_exists'])
            if self.observer_ready:self.assertEqual(self.observer.returncode,0)
        finally:
            self.remote_log.close()
            (self.evidence/'peer-resource-terminal.txt').write_bytes(remaining)

    def control(self,**operation):
        self.host_sequence+=1
        request=host.HostControlRequest(protocol_version=1,request_id=self.host_sequence,generation=self.generation,**operation)
        reply=self.exchange(control=request).control
        self.assertEqual(reply.request_id,self.host_sequence);self.generation=reply.generation
        return reply

    def transition(self,name,**spawn):
        result=self.control(transition=transitions.HostActorTransitionRequest(protocol_version=1,
            transition_id=os.urandom(32),departure_sequence=100+self.host_sequence,
            destination_world_id=self.active_worlds[name].management('world.info')['world_id'],**spawn))
        return result

    def actor_state(self):
        reply=self.control(inspect=True);self.assertTrue(reply.accepted,reply)
        return reply.inspection.state

    def displayed(self,revision=None,after_sequence=None):
        deadline=time.monotonic()+15
        while True:
            reply=self.control(geometry_status=True)
            self.assertTrue(reply.accepted,reply)
            status=reply.geometry_status
            if status.available and (revision is None or status.world_revision>=revision) and (
                    after_sequence is None or status.view_sequence>after_sequence):
                self.assertEqual(status.camera,self.actor_state().pose)
                return status
            self.assertLess(time.monotonic(),deadline,'No acknowledged foreground geometry');time.sleep(.15)

    def stable_pixels(self):
        deadline=time.monotonic()+10
        while True:
            before=self.capture();time.sleep(.2);after=self.capture()
            if before==after and (any(after[0::4]) or any(after[1::4]) or any(after[2::4])):return after
            self.assertLess(time.monotonic(),deadline,'No stable nonblank native baseline')

    def capture(self):
        pixels=self.exchange(capture=True).pixels
        self.capture_count+=1
        (self.evidence/f'capture-{self.capture_count:04}.sha256').write_text(hashlib.sha256(pixels).hexdigest()+'\n')
        return pixels

    def changed(self,before):
        deadline=time.monotonic()+10
        while True:
            pixels=self.capture()
            if pixels!=before:return pixels
            self.assertLess(time.monotonic(),deadline,'Native pixels did not change');time.sleep(.1)

    def black(self):
        deadline=time.monotonic()+10
        while True:
            pixels=self.capture()
            if not any(pixels[0::4]) and not any(pixels[1::4]) and not any(pixels[2::4]):return pixels
            self.assertLess(time.monotonic(),deadline,'Unauthorized geometry did not clear');time.sleep(.1)

    def object(self):
        world=self.active_worlds['b'];room=world.management('world.info')['entry_room']
        world.management('world.create',entity=dict(id='shared-object',kind='object',room=room,pose=pose((0,0,-4))))

    def stable(self):self.assertEqual(list(self.exchange(identity=True).processes),list(self.initial_processes))

    def test_01_real_remote_object_edit_changes_native_peer_pixels(self):
        self.object();self.assertTrue(self.transition('b').accepted)
        owner=self.owner_channel('b');context=self.actor_state()
        first=owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        baseline_status=self.displayed(revision=first.world_revision)
        before=self.stable_pixels()
        for operation in ({'object_grab':core.ObjectGrabRequest(id='shared-object')},
            {'object_move':core.ObjectMoveRequest(pose=core.Pose(position=core.Vec3(x=.5,z=-4),orientation=core.Quaternion(w=1)))},
            {'object_release':core.ObjectReleaseRequest(commit=True)}):
            reply=owner.act(**operation);self.assertTrue(reply.HasField('action'),reply);self.assertTrue(reply.action.accepted)
        later=owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        self.assertGreater(later.world_revision,first.world_revision)
        self.assertEqual(next(item for item in later.objects if item.id=='shared-object').pose.position.x,.5)
        updated=self.displayed(revision=later.world_revision,after_sequence=baseline_status.view_sequence)
        self.assertEqual(updated.presentation_generation,baseline_status.presentation_generation)
        self.changed(before);self.assertEqual(self.actor_state(),context);self.stable()

    def test_02_real_other_actor_motion_changes_marker_pixels_only(self):
        self.assertTrue(self.transition('b').accepted);owner=self.owner_channel('b')
        context=self.actor_state()
        first=owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        baseline_status=self.displayed(revision=first.world_revision)
        before=self.stable_pixels()
        reply=owner.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0]));self.assertTrue(reply.action.accepted)
        time.sleep(.35);self.assertTrue(owner.act(stop=core.ActorStopRequest()).action.accepted)
        later=owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        self.assertNotEqual(first.camera,later.camera);self.assertEqual(first.world_revision,later.world_revision)
        self.displayed(revision=first.world_revision,after_sequence=baseline_status.view_sequence)
        self.changed(before);self.assertEqual(self.actor_state(),context);self.stable()

    def test_03_a_b_a_restores_context_and_exact_native_processes(self):
        before=self.actor_state();pixels=self.capture()
        world=self.active_worlds['b'];world.management('world.create',entity=dict(id='destination-room',kind='room',label='Destination Room'))
        requested=core.Pose(position=core.Vec3(x=2,z=-1),orientation=core.Quaternion(w=1))
        self.assertTrue(self.transition('b',destination_room='destination-room',destination_pose=requested).accepted)
        destination=self.actor_state();self.assertEqual(destination.room,'destination-room');self.assertEqual(destination.pose,requested)
        self.assertEqual(destination.workspace,'');self.stable()
        self.assertTrue(self.transition('a').accepted)
        resumed=self.actor_state()
        self.assertEqual((resumed.id,resumed.room,resumed.pose,resumed.workspace),(before.id,before.room,before.pose,before.workspace))
        self.assertEqual(self.capture(),pixels);self.stable()

    def test_04_private_denial_preserves_source_pixels_and_actions(self):
        before=self.actor_state();pixels=self.capture();reply=self.transition('denied')
        self.assertFalse(reply.transition.actor_transition.destination_activation_confirmed)
        self.assertEqual(reply.transition.actor_transition.server_error.code,core.FORBIDDEN,reply)
        self.assertTrue(reply.transition.actor_control_available)
        self.assertEqual(self.actor_state(),before);self.assertEqual(self.capture(),pixels)
        action=self.control(action=core.ActorActionRequest(seq=1,stop=core.ActorStopRequest()))
        self.assertTrue(action.accepted);self.assertTrue(action.action.action.accepted);self.stable()

    def test_05_observation_revocation_clears_view_preserves_navigation(self):
        self.object();self.assertTrue(self.transition('b').accepted);self.displayed();before=self.stable_pixels()
        self.assertTrue(any(before[0::4]) or any(before[1::4]) or any(before[2::4]))
        owner=self.owner_channel('b');revoked=self.revoke_grant(owner,self.observation_grants['b'])
        self.assertTrue(revoked.HasField('grant'),revoked);self.black()
        status=self.control(geometry_status=True).geometry_status
        self.assertFalse(status.available);self.assertFalse(status.world_id);self.assertFalse(status.actor_id)
        self.assertFalse(status.HasField('camera'));self.assertEqual(status.view_sequence,0)
        action=self.control(action=core.ActorActionRequest(seq=1,stop=core.ActorStopRequest()))
        self.assertTrue(action.accepted,action);self.assertTrue(action.action.action.accepted)
        self.assertTrue(self.actor_state().id);self.stable()

    def boundary(self,names):
        env=['env','PYTHONPATH='+':'.join(map(str,(self.remote_python,self.remote_source,self.remote_source/'tests/native'))),
            'SPATIAL_TEST_XSERVER='+os.environ['SPATIAL_GEOMETRY_REMOTE_XSERVER'],
            'SPATIAL_GEOMETRY_BOUNDARY_CASES='+','.join(names),
            'ASAN_OPTIONS=detect_leaks=1:abort_on_error=1','UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1']
        result=self.remote(env+['python3',self.remote_source/'tests/native/test_geometry_presentation.py',
            self.remote_bin/'spatialwm',self.remote_bin/'spatial-security-auth'],capture_output=True,timeout=120)
        (self.root/'boundary.stdout').write_bytes(result.stdout);(self.root/'boundary.stderr').write_bytes(result.stderr)

    def test_06_delayed_source_wrong_epoch_actor_and_generation_fence(self):
        self.assertTrue(self.transition('b').accepted)
        context=self.actor_state();pixels=self.capture();current_generation=self.generation
        self.host_sequence+=1
        stale=host.HostControlRequest(protocol_version=1,request_id=self.host_sequence,
            generation=current_generation-1,action=core.ActorActionRequest(seq=1,
                input=core.ActorInputRequest(axes=[1,0,0,0,0,0])))
        rejected=self.exchange(control=stale).control
        self.assertFalse(rejected.accepted,rejected)
        self.assertEqual(rejected.generation,current_generation)
        self.assertIn('stale host actor context',rejected.detail.lower())
        self.assertEqual(self.actor_state(),context);self.assertEqual(self.capture(),pixels)
        self.host_sequence+=1
        stale_status=host.HostControlRequest(protocol_version=1,request_id=self.host_sequence,
            generation=current_generation-1,geometry_status=True)
        rejected=self.exchange(control=stale_status).control
        self.assertFalse(rejected.accepted);self.assertFalse(rejected.HasField('geometry_status'))
        self.assertEqual(self.displayed().world_id,self.active_worlds['b'].management('world.info')['world_id'])
        self.boundary(['test_delayed_source_cannot_repaint_destination',
            'test_wrong_actor_world_session_and_epoch_reject_before_pixels',
            'test_retirement_clears_pixels_and_fences_delayed_frame'])
        self.stable()

    def test_07_forged_native_application_fields_and_descriptors_reject(self):
        self.boundary(['test_geometry_schema_excludes_application_and_native_authority']);self.stable()

    def test_08_authority_loss_clears_peer_without_compositor_restart(self):
        self.object();self.assertTrue(self.transition('b').accepted);self.displayed();before=self.stable_pixels()
        self.assertTrue(any(before[0::4]) or any(before[1::4]) or any(before[2::4]))
        world=self.active_worlds['b'];world.proc.kill();self.assertEqual(world.proc.wait(timeout=5),-9)
        self.black();self.stable()
        status=self.control(geometry_status=True).geometry_status
        self.assertFalse(status.available);self.assertFalse(status.world_id);self.assertFalse(status.actor_id)
        self.assertFalse(status.HasField('camera'));self.assertEqual(status.view_sequence,0)
        action=self.control(action=core.ActorActionRequest(seq=1,stop=core.ActorStopRequest()))
        self.assertFalse(action.accepted,action)
        self.assertTrue(self.owner_channel('a').call(geometry_view=session.WorldGeometryViewRequest()).HasField('geometry_view'))

    def test_09_actor_leaving_preserves_shared_world_and_other_actor(self):
        initial_destination_context=self.actor_state()
        self.assertTrue(self.transition('b').accepted);owner=self.owner_channel('b')
        before=owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        actor=self.actor_state().id;self.assertIn(actor,[item.id for item in before.actors])
        world_id=before.world_id;self.assertTrue(self.transition('a').accepted)
        after=owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        # Departure ends session authority. Durable actor entities and saved
        # context may remain; oracle 9 does not require marker disappearance.
        self.assertEqual(after.world_id,world_id);self.assertEqual(after.actor_id,before.actor_id)
        self.assertGreater(after.sequence,before.sequence)
        self.assertTrue(owner.act(stop=core.ActorStopRequest()).action.accepted)
        resumed=self.actor_state()
        self.assertEqual((resumed.id,resumed.room,resumed.pose,resumed.workspace),
            (initial_destination_context.id,initial_destination_context.room,
             initial_destination_context.pose,initial_destination_context.workspace))
        destination_status=self.displayed()
        self.assertEqual(destination_status.actor_id,actor)
        self.assertEqual(destination_status.world_id,self.active_worlds['a'].management('world.info')['world_id'])
        self.assertIsNone(self.active_worlds['b'].proc.poll());self.stable()


if __name__=='__main__':
    names=[name for name in NativeCrossHostTests.__dict__ if name.startswith('test_')]
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(NativeCrossHostTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
