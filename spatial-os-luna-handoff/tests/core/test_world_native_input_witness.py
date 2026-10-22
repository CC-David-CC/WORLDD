"""Join actual authenticated worldd actors to a checked native observer arm."""
import os
import json
import select
import signal
import struct
from pathlib import Path
import socket
import time
import unittest
import native_input_observer_control_v2_pb2 as endpoint
import native_input_observer_v2_pb2 as native_pb
import test_world_input_witness as bindings
import input_state_observer_setup_v2 as setup
import input_state_observer_v2_collect as collector
import test_resource_isolation as resources
import test_world_application_control as control


class NativeXI2ReceiptObserver(control.NativeXI2Observer):
    def _packet(self,deadline,header=None):
        if header is None:header=self._exact(32,deadline)
        event=super()._packet(deadline,header)
        if event is not None:
            event['event_time_ms']=struct.unpack_from('<I',header,12)[0]
            event['monotonic_ns']=time.monotonic_ns()
            event['receipt_ordinal']=len(self.events)+1
        return event


def record_frame(record):
    raw=record.SerializeToString(deterministic=True)
    if not 0<len(raw)<=4096:raise AssertionError('Native witness frame exceeds its contract')
    value=len(raw);prefix=bytearray()
    while value>127:prefix.append((value&127)|128);value>>=7
    prefix.append(value)
    return bytes(prefix)+raw


class WorldNativeInputWitnessTests(bindings.WorldInputWitnessTests):
    def setUp(self):
        super().setUp()
        if os.environ.get('SPATIAL_TEST_OBSERVER_XSERVER'):
            self.enterContext(control.patch.dict(os.environ,{
                'SPATIAL_TEST_XSERVER':os.environ['SPATIAL_TEST_OBSERVER_XSERVER'],
                'SPATIAL_TEST_XSERVER_SHA256':os.environ['SPATIAL_TEST_OBSERVER_XSERVER_SHA256']}))
        self.native_endpoint,self.native_endpoint_child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        self.native_endpoint.settimeout(3)
        self.addCleanup(self.native_endpoint.close);self.addCleanup(self.native_endpoint_child.close)
        self.native_output_read,self.native_output_write=os.pipe2(os.O_CLOEXEC)
        self.addCleanup(self.close_output)
        self.probe_child=None
        self.addCleanup(self.close_probe_child)

    def scoped_world(self,*args,**kwargs):
        if getattr(self,'use_shared_presentation',False):
            from shared_presentation_fixture import SharedPresentationFixture
            def factory(state,info):
                self.shared_presentation=SharedPresentationFixture(self,state,info,
                    control.access.workspace.WORLD.parent/'spatialwm')
                return self.shared_presentation
            kwargs['presentation_factory']=factory
        return super().scoped_world(*args,**kwargs)

    def setup_application(self,*args,**kwargs):
        kwargs['native_observer_fd']=self.native_endpoint_child.fileno()
        kwargs['native_observer_output_fd']=self.native_output_write
        try:return super().setup_application(*args,**kwargs)
        finally:
            self.native_endpoint_child.close()
            os.close(self.native_output_write);self.native_output_write=None

    def close_output(self):
        for name in ['native_output_read','native_output_write']:
            fd=getattr(self,name,None)
            if fd is not None:
                try:os.close(fd)
                except OSError:pass
                setattr(self,name,None)

    def close_probe_child(self):
        if self.probe_child is None:return
        pid=self.probe_child;self.probe_child=None
        end=time.monotonic()+3
        while True:
            done,status=os.waitpid(pid,os.WNOHANG)
            if done:
                self.assertEqual(os.waitstatus_to_exitcode(status),0,'Independent XI2 receipt process failed')
                return
            if time.monotonic()>=end:
                os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)
                self.fail('Independent XI2 receipt process did not retire')
            time.sleep(.01)

    def start_receipt_process(self,arm):
        incoming,outgoing=os.pipe2(os.O_CLOEXEC)
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        pid=os.fork()
        if pid==0:
            try:
                parent.close();os.close(incoming)
                keep={0,1,2,child.fileno(),outgoing,self.observer.client.stream.fileno()}
                for entry in Path('/proc/self/fd').iterdir():
                    fd=int(entry.name)
                    if fd not in keep:
                        try:os.close(fd)
                        except OSError:pass
                deadline=arm.deadline_monotonic_ns
                timeout=lambda:max(.001,(deadline-time.monotonic_ns())/1_000_000_000)
                self.observer.read_until(lambda rows:any(r['type']==4 and
                    r['device']==next(d.device_id for d in arm.peer_actor_devices if d.kind==1 and d.role==1)
                    for r in rows[arm.prearm_receipt_watermark:]),timeout())
                child.settimeout(timeout())
                finish=endpoint.ObserverControlEnvelopeV2.FromString(child.recv(4097))
                if finish.WhichOneof('message')!='arm' or finish.arm!=arm:raise AssertionError('Receipt finish fence mismatch')
                self.observer.request(43,timeout=timeout())
                batch=native_pb.ProbeReceiptBatch(recipient=arm.expected_recipient,window=arm.expected_window)
                devices={d.device_id:d for d in list(arm.old_actor_devices)+list(arm.peer_actor_devices)}
                for event in self.observer.events[arm.prearm_receipt_watermark:]:
                    if event['type'] not in (2,3,4,5):continue
                    batch.receipts.add(receipt_ordinal=event['receipt_ordinal'],monotonic_ns=event['monotonic_ns'],
                        event_type=event['type'],device=devices[event['device']],source=devices[event['source']],
                        window=arm.expected_window,detail=event['detail'],event_time_ms=event['event_time_ms'])
                if not batch.receipts or len(batch.receipts)>32:raise AssertionError('Independent receipt count invalid')
                record=native_pb.NativeInputObservationRecordV2(protocol_version=2,session_id=arm.session_id,
                    arm_id=arm.arm_id,stream_sequence=4,native_session_epoch=self.join_pins.native_session_epoch,
                    probe_receipts=batch)
                raw=record_frame(record);os.set_blocking(outgoing,False);offset=0
                while offset<len(raw):
                    if time.monotonic_ns()>=deadline:raise AssertionError('Independent receipt output deadline')
                    if not select.select([], [outgoing], [], timeout())[1]:raise AssertionError('Independent receipt output stalled')
                    try:offset+=os.write(outgoing,raw[offset:])
                    except BlockingIOError:continue
                os.close(outgoing);child.close();self.observer.close();os._exit(0)
            except BaseException as error:
                os.write(2,('Independent XI2 receipt failure: '+repr(error)+'\n').encode())
                os._exit(1)
        self.probe_child=pid;os.close(outgoing);child.close();self.observer.close()
        self.addCleanup(parent.close);self.addCleanup(os.close,incoming)
        return incoming,parent

    def collect_joined_artifact(self,arm,probe_read):
        streams={self.native_output_read:12294,probe_read:4098};buffers={fd:bytearray() for fd in streams}
        for fd in streams:os.set_blocking(fd,False)
        pending=set(streams)
        while pending:
            timeout=(arm.deadline_monotonic_ns-time.monotonic_ns())/1_000_000_000
            self.assertGreater(timeout,0,'Native witness producer deadline expired')
            ready=select.select(list(pending),[],[],timeout)[0]
            self.assertTrue(ready,'Native witness producer stalled')
            for fd in ready:
                try:chunk=os.read(fd,min(4096,streams[fd]+1-len(buffers[fd])))
                except BlockingIOError:continue
                if not chunk:pending.remove(fd);continue
                buffers[fd].extend(chunk);self.assertLessEqual(len(buffers[fd]),streams[fd])
        raw=bytes(buffers[self.native_output_read])+bytes(buffers[probe_read])
        if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
            root=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);root.mkdir(parents=True,exist_ok=True)
            (root/(self._testMethodName+'-native-v2.pb')).write_bytes(raw)
            (root/(self._testMethodName+'-trusted-arm.pb')).write_bytes(arm.SerializeToString(deterministic=True))
        reader,writer=socket.socketpair()
        try:
            writer.settimeout(max(.001,(arm.deadline_monotonic_ns-time.monotonic_ns())/1_000_000_000))
            writer.sendall(raw);writer.shutdown(socket.SHUT_WR)
            return collector.collect(reader.detach(),expected_session_id=arm.session_id,expected_arm_id=arm.arm_id,
                expected_native_session_epoch=self.join_pins.native_session_epoch,expected_arm=arm,
                expected_bindings=self.join_bindings,
                timeout_seconds=min(10,max(.001,(arm.deadline_monotonic_ns-time.monotonic_ns())/1_000_000_000)))
        finally:reader.close();writer.close()

    def endpoint_reply(self,kind):
        raw=self.native_endpoint.recv(4097)
        self.assertTrue(raw,'Native witness endpoint closed; startup/setup/arm deadline may have expired')
        self.assertLessEqual(len(raw),4096)
        reply=endpoint.ObserverEndpointReplyV2.FromString(raw)
        self.assertEqual(reply.SerializeToString(deterministic=True),raw)
        self.assertEqual(reply.WhichOneof('message'),kind,reply)
        return getattr(reply,kind)

    def prepare_join(self):
        request=self.prepare()
        hello=self.endpoint_reply('hello')
        self.assertEqual(hello.server_pid,self.native.server.pid)
        request.session_id=hello.session_id
        request.arm_id=os.urandom(16)
        # Release the priming button before the privileged probe selects XI2 events.
        # The separate core-delivery regressions cover application event delivery.
        self.control_action(self.viewer,unfocus=control.core.ActorStopRequest())
        keyboard_client=resources.Client(self.native.display,self.native.cookie)
        keyboard=None
        try:
            keyboard=control.NativeXI2Observer(keyboard_client,self.surface['xid'],select_events=False)
            def selected_pointer():
                head,body=keyboard.request(keyboard.opcode,struct.pack('<I',0),minor=45)
                self.assertEqual(head[8],1);self.assertFalse(body)
                return struct.unpack_from('<H',head,10)[0]
            if getattr(self,'verify_repeat_disabled',False):
                keyboard_client.send(keyboard.opcode,struct.pack('<IHH',0,self.peer_pointer,0),minor=44)
                self.assertEqual(selected_pointer(),self.peer_pointer)
                self.assertNotEqual(self.peer_pointer,self.old_pointer)
            keyboard_client.send(keyboard.opcode,struct.pack('<IHH',0,self.old_pointer,0),minor=44)
            self.assertEqual(selected_pointer(),self.old_pointer)
            mapping,body=keyboard.request(101,struct.pack('<BBH',8,248,0))
            columns=mapping[1]
            self.assertGreater(columns,0);self.assertLessEqual(columns,64)
            self.assertEqual(len(body),248*columns*4)
            keysyms=struct.unpack('<'+'I'*(248*columns),body)
            def keycode(keysym):
                matches=[8+row for row in range(248) if keysym in keysyms[row*columns:(row+1)*columns]]
                self.assertEqual(len(matches),1,'Native keymap must resolve the requested key uniquely')
                return matches[0]
            modifier=keycode(0xffe1);codes={modifier,keycode(ord('a'))}
            before_head,before_body=keyboard.request(103)
            expected_repeat_map=bytearray((before_head+before_body)[20:52])
            self.assertEqual(len(expected_repeat_map),32)
            for code in codes:expected_repeat_map[code//8]&=~(1<<(code%8))
            for code in sorted(codes):
                keyboard_client.send(102,struct.pack('<III',(1<<6)|(1<<7),code,0))
            repeat_head,repeat_body=keyboard.request(103)
            self.assertEqual(repeat_head[1],before_head[1],'Preserve global repeat mode')
            self.assertEqual((repeat_head+repeat_body)[20:52],bytes(expected_repeat_map),
                'Preserve unrelated repeat bits')
        finally:
            if keyboard is not None:keyboard.close()
            else:keyboard_client.close()
        # Keep configuration traffic and keyboard mapping notifications off the receipt connection.
        probe=resources.Client(self.native.display,self.native.cookie)
        self.native.clients.append(probe)
        marker=probe.create_window()
        self.assertIsNone(probe.operation(2,probe.pack('III',marker,1<<9,1)))
        self.assertIsNone(probe.operation(8,probe.pack('I',marker)))
        self.observer=NativeXI2ReceiptObserver(probe,self.surface['xid'])
        self.addCleanup(self.observer.close)
        head,body=self.observer.request(self.observer.opcode,struct.pack('<I',0),minor=45)
        self.assertEqual(head[8],1);self.assertFalse(body)
        self.assertNotEqual(struct.unpack_from('<H',head,10)[0],self.old_pointer)
        self.actor_aim();self.actor_pointer(True)
        self.send_key_event('Shift_L',True);self.send_key_event('a',True)
        self.observer.read_until(lambda rows:
            {r['detail'] for r in rows if r['kind']=='key' and r['pressed']}>=codes and
            any(r['kind']=='button' and r['pressed'] for r in rows),3.0)
        if getattr(self,'verify_repeat_disabled',False):
            # Exceed the default repeat delay before starting the unchanged arm budget.
            time.sleep(.75)
        self.observer.request(43)
        for code in codes:
            self.assertEqual(sum(r['kind']=='key' and r['pressed'] and r['detail']==code
                for r in self.observer.events),1,'Held fixture keys must not auto-repeat')
        start=time.monotonic_ns();deadline=start+3_000_000_000
        request.deadline_monotonic_ns=deadline
        self.send(request);reply=self.receive_binding()
        self.assertEqual(reply.error_code,0,reply)
        old,peer=reply.actors
        for proof,identity,pointer in [(old,self.second,self.old_pointer),(peer,self.identity,self.peer_pointer)]:
            self.assertEqual((proof.binding.actor.issuer_id,proof.binding.actor.actor_id),
                (identity.issuer_id,identity.actor_id))
            self.assertEqual(proof.pointer_device,pointer)
        self.join_bindings=(old.binding,peer.binding)
        inspector=self.native.connect(self.native.supervisor.issue(control.security.COMPOSITOR,b'',b'').cookie)
        native_generation=self.native_target_generation(inspector,self.surface['xid'])
        selector=endpoint.ObserverSetupRequestV2(session_id=request.session_id,arm_id=request.arm_id,
            old_native_seat_id=old.binding.native_seat_id,peer_native_seat_id=peer.binding.native_seat_id,
            target_xid=self.surface['xid'],probe_marker_xid=marker,
            expected_application=request.expected_application,presentation_generation=request.expected_presentation_generation,
            target_spatial_generation=native_generation,expected_probe_role=native_pb.ClientIdentity.SUPERVISOR,
            deadline_monotonic_ns=deadline)
        trusted=setup.TrustedSetup(session_id=request.session_id,arm_id=request.arm_id,
            old_binding=old.binding,peer_binding=peer.binding,expected_application=request.expected_application,
            expected_target_xid=self.surface['xid'],expected_target_spatial_generation=native_generation,
            expected_presentation_generation=request.expected_presentation_generation,
            expected_probe_marker_xid=marker,expected_probe_role=native_pb.ClientIdentity.SUPERVISOR,
            start_monotonic_ns=start,deadline_monotonic_ns=deadline,
            expected_native_session_epoch=hello.native_session_epoch)
        self.native_endpoint.sendall(endpoint.ObserverControlEnvelopeV2(setup_request=selector).SerializeToString(deterministic=True))
        pins=self.endpoint_reply('setup_reply')
        validated=setup.validate_setup(selector,pins,trusted)
        for proof,devices in [(old,pins.old_actor_devices),(peer,pins.peer_actor_devices)]:
            master={(d.kind,d.role):d.device_id for d in devices}
            self.assertEqual(master[1,1],proof.pointer_device)
            self.assertEqual(master[2,1],proof.keyboard_device)
        held=setup.HeldInputContext(tuple(sorted(codes)),(1,),modifier,len(self.observer.events),1)
        arm=native_pb.ObserverArmV2(session_id=request.session_id,arm_id=request.arm_id,
            arm_monotonic_ns=time.monotonic_ns(),deadline_monotonic_ns=deadline,
            prearm_receipt_watermark=held.prearm_receipt_watermark,
            old_actor=old.binding.actor,peer_actor=peer.binding.actor,
            old_world_actor_id=old.binding.world_actor_id,peer_world_actor_id=peer.binding.world_actor_id,
            old_native_seat_id=old.binding.native_seat_id,peer_native_seat_id=peer.binding.native_seat_id,
            expected_application=request.expected_application,presentation_generation=request.expected_presentation_generation,
            expected_window=pins.app_target,expected_window_owner=pins.app_target_owner,
            expected_seat_owner=pins.compositor_seat_owner,expected_recipient=pins.probe_marker.privileged_owner,
            expected_grab_owner=pins.probe_marker.privileged_owner,
            old_actor_devices=pins.old_actor_devices,peer_actor_devices=pins.peer_actor_devices,
            expected_xi2_event_type=4,expected_detail=1,expected_held_keys=held.keys,
            expected_held_buttons=held.buttons,expected_modifier_keycode=held.modifier_keycode)
        self.join_trusted=trusted;self.join_pins=validated;self.join_held=held
        return arm

    def native_transfer(self,trigger):
        arm=self.prepare_join()
        raw=setup.validate_arm(arm,self.join_pins,self.join_trusted,self.join_held,self.join_bindings)
        probe_read,probe_finish=self.start_receipt_process(arm)
        self.native_endpoint.sendall(endpoint.ObserverControlEnvelopeV2(arm=native_pb.ObserverArmV2.FromString(raw)).SerializeToString(deterministic=True))
        self.assertEqual(self.endpoint_reply('arm_accepted').accepted_command,arm)
        self.actor_aim(self.peer)
        if trigger=='observation':self.revoke_grant(self.peer,self.observe_grant)
        elif trigger=='control':self.revoke_grant(self.peer,self.control_grant)
        elif trigger=='unfocus':self.control_action(self.viewer,unfocus=control.core.ActorStopRequest())
        elif trigger=='stop':self.control_action(self.viewer,stop=control.core.ActorStopRequest())
        else:raise AssertionError('Unknown native transfer trigger')
        # Submit before waiting for releases. BUSY is the only retryable refusal.
        reply=self.peer.act(pointer=control.core.ActorPointerRequest(button=1,pressed=True))
        if reply.HasField('error'):
            self.assertEqual(reply.error.code,control.core.BUSY,reply)
            reply=self.peer.act(pointer=control.core.ActorPointerRequest(button=1,pressed=True))
        self.assertTrue(reply.HasField('action'),reply)
        probe_finish.sendall(endpoint.ObserverControlEnvelopeV2(arm=arm).SerializeToString(deterministic=True))
        result=self.collect_joined_artifact(arm,probe_read)
        self.assertIsNotNone(result)
        self.close_probe_child()
        self.actor_pointer(False,self.peer)
        self.control_action(self.peer,unfocus=control.core.ActorStopRequest())
        old=self.actor_state()
        self.assertFalse(old.held_keys);self.assertFalse(old.held_buttons);self.assertFalse(old.HasField('focus'))
        if trigger in ('observation','control'):
            self.denied(self.viewer.act(pointer=control.core.ActorPointerRequest(button=1,pressed=True)),control.core.FORBIDDEN)
        self.assertTrue(self.world_process.management('surface.get',id=self.surface['id'])['mapped'])

    def test_observation_revocation_has_native_transition_and_independent_receipts(self):
        self.native_transfer('observation')

    def test_control_revocation_has_native_transition_and_independent_receipts(self):
        self.verify_repeat_disabled=True
        self.native_transfer('control')

    def test_voluntary_unfocus_has_native_transition_and_independent_receipts(self):
        self.native_transfer('unfocus')

    def test_stop_has_native_transition_and_independent_receipts(self):
        self.native_transfer('stop')

    def test_shared_renderer_query_joins_real_actor_seats_and_native_receipts(self):
        self.use_shared_presentation=True
        self.native_transfer('observation')
        self.assertIsNone(self.shared_presentation.proc.poll())
        self.assertNotEqual(self.shared_presentation.proc.pid,self.world_process.proc.pid)

    def test_authenticated_worldd_bindings_arm_actual_native_server(self):
        arm=self.prepare_join()
        raw=setup.validate_arm(arm,self.join_pins,self.join_trusted,self.join_held,self.join_bindings)
        self.native_endpoint.sendall(endpoint.ObserverControlEnvelopeV2(arm=native_pb.ObserverArmV2.FromString(raw)).SerializeToString(deterministic=True))
        accepted=self.endpoint_reply('arm_accepted')
        self.assertEqual(accepted.accepted_command,arm)
        state=self.actor_state()
        self.assertEqual(set(state.held_buttons),{1});self.assertEqual(len(state.held_keys),2)
        self.assertEqual(state.focus,self.surface['id'])
        self.control_action(self.viewer,unfocus=control.core.ActorStopRequest())

    def test_swapped_world_bindings_cannot_arm_native_witness(self):
        arm=self.prepare_join()
        with self.assertRaisesRegex(setup.SetupError,'ARM_BINDING_JOIN'):
            setup.validate_arm(arm,self.join_pins,self.join_trusted,self.join_held,self.join_bindings[::-1])
        self.control_action(self.viewer,unfocus=control.core.ActorStopRequest())

    def test_native_device_epoch_changed_after_join_is_refused(self):
        arm=self.prepare_join()
        arm.old_actor_devices[0].native_object_epoch+=1
        self.native_endpoint.sendall(endpoint.ObserverControlEnvelopeV2(arm=arm).SerializeToString(deterministic=True))
        self.assertEqual(self.native_endpoint.recv(4097),b'')
        state=self.actor_state();self.assertEqual(set(state.held_buttons),{1});self.assertEqual(len(state.held_keys),2)
        self.control_action(self.viewer,unfocus=control.core.ActorStopRequest())


if __name__=='__main__':
    suite=unittest.TestSuite(WorldNativeInputWitnessTests(name)
        for name in WorldNativeInputWitnessTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
