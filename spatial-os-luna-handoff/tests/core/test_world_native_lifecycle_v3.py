"""Independent native lifetime receipts joined to real worldd actor mechanisms."""
import json
import os
import select
import socket
import struct
import time
import unittest
from pathlib import Path

# The existing fixture imports consume the four registered executable arguments.
# Import their owner first, before native helpers that consume the final argument.
from test_world_native_input_witness import WorldNativeInputWitnessTests
import test_world_application_control as control
import test_resource_isolation as resources
import native_input_lifecycle_v3_pb2 as lifecycle
import native_input_observer_control_v2_pb2 as endpoint
import native_input_observer_v2_pb2 as native_pb
import input_state_observer_setup_v2 as setup
import input_state_observer_v3 as oracle
import input_state_observer_v3_collect as collector


class LifecycleXI2ReceiptObserver(control.NativeXI2Observer):
    # Select relevant receipts only. Every received key/button gets an ordinal.
    EVENT_NAMES = {2: 'key', 3: 'key', 4: 'button', 5: 'button'}

    def __init__(self, client, old_window, peer_window):
        self.windows = {old_window, peer_window}
        super().__init__(client, old_window)
        if peer_window != old_window:
            mask = bytes([sum(1 << kind for kind in self.EVENT_NAMES), 0, 0, 0])
            client.stream.settimeout(3)
            try:
                client.send(self.opcode, struct.pack('<IHHHH', peer_window, 1, 0, 1, 1) + mask, minor=46)
            finally:
                client.stream.setblocking(False)
            self.request(43)

    def _packet(self, deadline, header=None):
        if len(self.events) >= getattr(self, 'receipt_ceiling', 256):
            raise AssertionError('Independent lifecycle receipt count exceeded')
        if header is None:
            header = self._exact(32, deadline)
        if (header[0] & 127) != self.GENERIC_EVENT or header[1] != self.opcode:
            raise AssertionError('Unexpected independent lifecycle XI2 packet')
        size = struct.unpack_from('<I', header, 4)[0] * 4
        if size > 65536:
            raise AssertionError('Excessive lifecycle XI2 packet')
        packet = header + self._exact(size, deadline)
        if len(packet) < 80:
            raise AssertionError('Truncated lifecycle XI2 device header')
        kind = struct.unpack_from('<H', packet, 8)[0]
        if kind not in self.EVENT_NAMES:
            raise AssertionError('Unselected lifecycle XI2 event')
        blen, vlen = struct.unpack_from('<HH', packet, 48)
        end = 80 + 4 * (blen + vlen)
        if end > len(packet):
            raise AssertionError('Truncated lifecycle XI2 masks')
        valuators = packet[80 + 4 * blen:end]
        if len(packet) != end + 8 * sum(byte.bit_count() for byte in valuators):
            raise AssertionError('Invalid lifecycle XI2 valuator payload')
        window = struct.unpack_from('<I', packet, 24)[0]
        device = struct.unpack_from('<H', packet, 10)[0]
        source = struct.unpack_from('<H', packet, 52)[0]
        if window not in self.windows or device <= 1 or source <= 1:
            raise AssertionError('Unexpected lifecycle XI2 window/device/source')
        return {'kind': self.EVENT_NAMES[kind], 'type': kind, 'pressed': kind in (2, 4),
                'window': window, 'device': device, 'source': source,
                'detail': struct.unpack_from('<I', packet, 16)[0],
                'event_time_ms': struct.unpack_from('<I', packet, 12)[0],
                'synthetic': bool(header[0] & 128), 'monotonic_ns': time.monotonic_ns(),
                'receipt_ordinal': len(self.events) + 1}


def record_frame(record):
    raw = record.SerializeToString(deterministic=True)
    if not 0 < len(raw) <= 8192:
        raise AssertionError('Lifecycle record exceeds its bound')
    value = len(raw)
    prefix = bytearray()
    while value > 127:
        prefix.append((value & 127) | 128)
        value >>= 7
    prefix.append(value)
    return bytes(prefix) + raw


class WorldNativeLifecycleV3Tests(WorldNativeInputWitnessTests):
    cause = lifecycle.TARGET_DESTRUCTION

    def prepare(self, prime_peer=True):
        request = super().prepare(prime_peer=prime_peer)
        if self.cause == lifecycle.TARGET_DESTRUCTION:
            self.peer_surface, self.peer_position = self.m09_create_survivor(self.app)
        else:
            self.peer_surface, self.peer_position = self.surface, (0, 0, -2)
        self.ready_before = [row for row in self.probe_events(self.app) if row.get('event') == 'ready']
        self.assertEqual(len(self.ready_before), 1)
        return request

    def lifecycle_reply(self, kind):
        raw = self.native_endpoint.recv(4097)
        self.assertTrue(raw, 'Lifecycle endpoint closed before acknowledgement')
        self.assertLessEqual(len(raw), 4096)
        reply = lifecycle.LifecycleEndpointReplyV3.FromString(raw)
        oracle.v2._canonical(reply, raw)
        self.assertEqual(reply.WhichOneof('message'), kind, reply)
        return getattr(reply, kind)

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
        self.observer=LifecycleXI2ReceiptObserver(probe,self.surface['xid'],self.peer_surface['xid'])
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
        peer_generation=self.native_target_generation(inspector,self.peer_surface['xid'])
        lifecycle_selector=lifecycle.LifecycleSetupRequestV3(common=selector,
            peer_target_xid=self.peer_surface['xid'],peer_target_spatial_generation=peer_generation,cause=self.cause)
        self.native_endpoint.sendall(lifecycle.LifecycleControlEnvelopeV3(
            lifecycle_setup_request=lifecycle_selector).SerializeToString(deterministic=True))
        lifecycle_pins=self.lifecycle_reply('lifecycle_setup_reply')
        self.assertEqual(lifecycle_pins.accepted_request,lifecycle_selector)
        self.assertEqual(lifecycle_pins.peer_window.xid,self.peer_surface['xid'])
        self.assertEqual(lifecycle_pins.peer_window.spatial_generation,peer_generation)
        self.assertEqual(lifecycle_pins.peer_window.application,request.expected_application)
        pins=lifecycle_pins.old_pins
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
        common_raw=setup.validate_arm(arm,validated,trusted,held,self.join_bindings)
        result=lifecycle.LifecycleArmV3(common=native_pb.ObserverArmV2.FromString(common_raw),
            peer_window=lifecycle_pins.peer_window,cause=self.cause)
        oracle._expected(result,self.join_bindings,result.common.session_id,result.common.arm_id)
        return result

    def start_receipt_process(self, arm):
        common = arm.common
        incoming, outgoing = os.pipe2(os.O_CLOEXEC)
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        pid = os.fork()
        if pid == 0:
            try:
                parent.close(); os.close(incoming)
                keep = {0, 1, 2, child.fileno(), outgoing, self.observer.client.stream.fileno()}
                for entry in Path('/proc/self/fd').iterdir():
                    fd = int(entry.name)
                    if fd not in keep:
                        try: os.close(fd)
                        except OSError: pass
                deadline = common.deadline_monotonic_ns

                def remaining():
                    value = (deadline - time.monotonic_ns()) / 1_000_000_000
                    if value <= 0:
                        raise AssertionError('Independent lifecycle receipt deadline')
                    return value

                peer_pointer = next(d.device_id for d in common.peer_actor_devices if d.kind == 1 and d.role == 1)
                self.observer.receipt_ceiling = common.prearm_receipt_watermark + 32
                self.observer.read_until(lambda rows: any(row['type'] == 4 and row['device'] == peer_pointer
                    for row in rows[common.prearm_receipt_watermark:]), remaining())
                child.settimeout(remaining())
                finish = lifecycle.LifecycleControlEnvelopeV3.FromString(child.recv(4097))
                if finish.WhichOneof('message') != 'lifecycle_arm' or finish.lifecycle_arm != arm:
                    raise AssertionError('Independent lifecycle receipt fence mismatch')
                self.observer.request(43, timeout=remaining())
                batch = native_pb.ProbeReceiptBatch(recipient=common.expected_recipient, window=arm.peer_window)
                devices = {d.device_id: d for d in list(common.old_actor_devices) + list(common.peer_actor_devices)}
                windows = {w.xid: w for w in [common.expected_window, arm.peer_window]}
                for event in self.observer.events[common.prearm_receipt_watermark:]:
                    # The independent decoder supplies each actual XID and synthetic flag.
                    batch.receipts.add(receipt_ordinal=event['receipt_ordinal'], monotonic_ns=event['monotonic_ns'],
                        event_type=event['type'], device=devices[event['device']], source=devices[event['source']],
                        window=windows[event['window']], detail=event['detail'], event_time_ms=event['event_time_ms'],
                        synthetic=event['synthetic'])
                if not 0 < len(batch.receipts) <= 32:
                    raise AssertionError('Independent lifecycle receipt count')
                record = lifecycle.NativeInputLifecycleRecordV3(protocol_version=3,
                    session_id=common.session_id, arm_id=common.arm_id, stream_sequence=4,
                    native_session_epoch=self.join_pins.native_session_epoch, probe_receipts=batch)
                raw = record_frame(record)
                os.set_blocking(outgoing, False)
                offset = 0
                while offset < len(raw):
                    if not select.select([], [outgoing], [], remaining())[1]:
                        raise AssertionError('Independent lifecycle receipt output stalled')
                    try: offset += os.write(outgoing, raw[offset:])
                    except BlockingIOError: continue
                os.close(outgoing); child.close(); self.observer.close(); os._exit(0)
            except BaseException as error:
                os.write(2, ('Independent lifecycle receipt failure: ' + repr(error) + '\n').encode())
                os._exit(1)
        self.probe_child = pid
        os.close(outgoing); child.close(); self.observer.close()
        self.addCleanup(parent.close); self.addCleanup(os.close, incoming)
        return incoming, parent

    def retain_artifact(self, arm, buffers, complete):
        destination = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if not destination:
            return
        root = Path(destination); root.mkdir(parents=True, exist_ok=True)
        name = self._testMethodName
        raw = b''.join(bytes(value) for value in buffers.values())
        (root / (name + '-native-v3.pb')).write_bytes(raw)
        (root / (name + '-trusted-arm-v3.pb')).write_bytes(arm.SerializeToString(deterministic=True))
        for label, binding in zip(['old', 'peer'], self.join_bindings):
            (root / (name + '-binding-' + label + '.pb')).write_bytes(binding.SerializeToString(deterministic=True))
        (root / (name + '-collection-v3.json')).write_text(json.dumps({
            'transport_complete': complete, 'producer_bytes': [len(value) for value in buffers.values()],
            'cause': lifecycle.LifecycleCauseV3.Name(arm.cause)}, indent=2) + '\n')

    def collect_joined_artifact(self, arm, probe_read):
        common = arm.common
        bounds = {self.native_output_read: 24582, probe_read: 8194}
        buffers = {fd: bytearray() for fd in bounds}
        complete = False
        try:
            for fd in bounds:
                os.set_blocking(fd, False)
            pending = set(bounds)
            while pending:
                timeout = (common.deadline_monotonic_ns - time.monotonic_ns()) / 1_000_000_000
                self.assertGreater(timeout, 0, 'Lifecycle producer deadline expired')
                ready = select.select(list(pending), [], [], timeout)[0]
                self.assertTrue(ready, 'Lifecycle producer stalled')
                for fd in ready:
                    try: chunk = os.read(fd, min(4096, bounds[fd] + 1 - len(buffers[fd])))
                    except BlockingIOError: continue
                    if not chunk:
                        pending.remove(fd)
                    else:
                        buffers[fd].extend(chunk)
                        self.assertLessEqual(len(buffers[fd]), bounds[fd], 'Lifecycle stream overflow')
            complete = True
        finally:
            self.retain_artifact(arm, buffers, complete)
        raw = b''.join(bytes(value) for value in buffers.values())
        reader, writer = socket.socketpair()
        try:
            remaining = (common.deadline_monotonic_ns - time.monotonic_ns()) / 1_000_000_000
            self.assertGreater(remaining, 0, 'Lifecycle validation deadline expired')
            writer.settimeout(remaining)
            writer.sendall(raw); writer.shutdown(socket.SHUT_WR)
            return collector.collect(reader.detach(), expected_session_id=common.session_id,
                expected_arm_id=common.arm_id, expected_native_session_epoch=self.join_pins.native_session_epoch,
                expected_arm=arm, expected_bindings=self.join_bindings, timeout_seconds=min(10, remaining))
        finally:
            reader.close(); writer.close()

    def native_lifecycle(self, trigger):
        arm = self.prepare_join()
        self.addCleanup(self.m09_release_probe, self.app)
        common = arm.common
        probe_read, probe_finish = self.start_receipt_process(arm)
        self.native_endpoint.sendall(lifecycle.LifecycleControlEnvelopeV3(
            lifecycle_arm=arm).SerializeToString(deterministic=True))
        self.assertEqual(self.lifecycle_reply('lifecycle_arm_accepted').accepted_command, arm)
        x, y, z = self.peer_position
        aim = self.actor_aim(self.peer, x=x, y=y, z=z)
        self.assertEqual(aim.hit.id, self.peer_surface['id'])
        self.denied(self.peer.act(pointer=control.core.ActorPointerRequest(button=1, pressed=True)), control.core.BUSY)
        if trigger == 'destroy':
            self.probe_command(self.app, 'destroy')
        elif trigger == 'close':
            reply = self.viewer.call(close=control.session.WorldSessionClose())
            self.assertTrue(reply.HasField('closed'), reply)
        elif trigger == 'disconnect':
            self.viewer.close()
        elif trigger == 'revocation':
            self.revoke_viewer_identity(control.issuer.ACTOR_TARGET)
        else:
            raise AssertionError('Unknown native lifecycle trigger')
        # Submit actual peer input before reading or waiting for release receipts.
        # Only BUSY may be retried. The original arm deadline never moves.
        while True:
            self.assertLess(time.monotonic_ns(), common.deadline_monotonic_ns)
            reply = self.peer.act(pointer=control.core.ActorPointerRequest(button=1, pressed=True))
            if not reply.HasField('error'):
                self.assertTrue(reply.HasField('action'), reply)
                break
            self.assertEqual(reply.error.code, control.core.BUSY, reply)
        probe_finish.sendall(lifecycle.LifecycleControlEnvelopeV3(lifecycle_arm=arm).SerializeToString(deterministic=True))
        self.assertIsNotNone(self.collect_joined_artifact(arm, probe_read))
        self.close_probe_child()
        self.actor_pointer(False, self.peer)
        self.send_key_event('c', True, self.peer); self.send_key_event('c', False, self.peer)
        self.control_action(self.peer, unfocus=control.core.ActorStopRequest())
        if trigger in ('close', 'revocation'):
            self.denied(self.viewer.call(inspect=control.session.WorldSessionInspect()), control.core.UNAUTHORIZED)
            self.denied(self.viewer.act(pointer=control.core.ActorPointerRequest(button=1, pressed=True)), control.core.UNAUTHORIZED)
        if trigger == 'destroy':
            state = self.actor_state()
            self.assertFalse(state.held_keys); self.assertFalse(state.held_buttons); self.assertFalse(state.HasField('focus'))
        self.assertTrue(self.world_process.management('surface.get', id=self.peer_surface['id'])['mapped'])
        rows = self.probe_command(self.app, 'sync')
        self.assertEqual([row for row in rows if row.get('event') == 'ready'], self.ready_before,
                         'The same application process and native connection must remain live')
        self.assertTrue(self.world_process.management('system.hello'))

    def test_target_destruction_preserves_survivor_and_orders_native_release(self):
        self.cause = lifecycle.TARGET_DESTRUCTION
        self.native_lifecycle('destroy')

    def test_actor_close_retires_devices_and_rejects_old_session(self):
        self.cause = lifecycle.ACTOR_DISCONNECT
        self.native_lifecycle('close')

    def test_transport_disconnect_retires_devices_and_preserves_application(self):
        self.cause = lifecycle.ACTOR_DISCONNECT
        self.native_lifecycle('disconnect')

    def test_actor_revocation_retires_devices_and_rejects_old_session(self):
        self.cause = lifecycle.ACTOR_REVOCATION
        self.native_lifecycle('revocation')


if __name__ == '__main__':
    suite = unittest.TestSuite(WorldNativeLifecycleV3Tests(name)
        for name in WorldNativeLifecycleV3Tests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
