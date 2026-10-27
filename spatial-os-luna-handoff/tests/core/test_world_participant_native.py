"""Independent native input witnesses for durable world revocation completion."""
import os
import json
from pathlib import Path
import signal
import select
import sqlite3
import time
import unittest
from unittest.mock import patch
import core_pb2 as core
import world_session_pb2 as session
from test_world_participant import WorldParticipantFixture
from test_world_identity import receive
from harness.wire import frame


class WorldParticipantNativeTests(WorldParticipantFixture):
    def world(self, grants=(), native=False, fixture_faults=False, native_fd=None, broker=None):
        self.assertTrue(native)
        self.assertEqual(set(grants), {'navigate','interact','observe','process.launch'})
        if native_fd is None:
            native_fd=self.setup_native_input_workspace()
            broker=self._native_input_relay
        world=self.start_world(native=True,fixture_faults=fixture_faults,native_fd=native_fd,broker=broker,
            actor_grants=['navigate','interact','observe','process.launch'])
        world.management('system.hello')
        return world

    def compositor(self,world):
        children=Path(f'/proc/{world.proc.pid}/task/{world.proc.pid}/children').read_text().split()
        matches=[int(pid) for pid in children if Path(f'/proc/{pid}/comm').read_text().strip()=='spatialwm']
        self.assertEqual(len(matches),1)
        return matches[0]

    def independent_release(self,probe):
        rows=self.until(probe.input_events,lambda events:
            any(e['event']=='key' and not e['pressed'] for e in events) and
            any(e['event']=='button' and not e['pressed'] for e in events))
        self.assertIsNone(probe.proc.poll())
        return rows

    def world_activations(self):
        with sqlite3.connect(self.state/'identity.db') as db:
            return db.execute('SELECT generation,applied_revision,active FROM authority_activation '
                'WHERE participant_id=? ORDER BY generation',(self.definition.id,)).fetchall()

    def test_completion_waits_for_native_key_and_button_release(self):
        world,peer,probe=self.held()
        before=probe.input_events()
        self.assertTrue(any(e['event']=='key' and e['pressed'] for e in before))
        self.assertTrue(any(e['event']=='button' and e['pressed'] for e in before))
        compositor=self.compositor(world)
        os.kill(compositor,signal.SIGSTOP)
        try:
            started=time.monotonic()
            self.revoke_now()
            released_key=probe.wait(lambda row:row.get('event')=='key' and not row['pressed'],timeout=.22)
            released_button=probe.wait(lambda row:row.get('event')=='button' and not row['pressed'],timeout=.22)
            self.assertLess(time.monotonic()-started,.25,'Native release waited for the stopped compositor')
            # Native release precedes completion of compositor cleanup.
            def incomplete_barrier():
                status=self.barrier()
                self.assertFalse(status.complete,'Revocation completed while the compositor was stopped')
                return status
            status=self.until(incomplete_barrier,lambda value:value.pending_count==1)
            # The native fixture registers a workspace participant as well as worldd.
            self.assertEqual((status.participant_count,status.pending_count,status.complete),(2,1,False))
            during=probe.input_events()
            self.assertIn(released_key,during)
            self.assertIn(released_button,during)
        finally:
            os.kill(compositor,signal.SIGCONT)
        complete=self.until(self.barrier,lambda result:result.complete)
        self.assertEqual(complete.participant_count,2)
        self.independent_release(probe)
        self.denied(peer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.assertIsNone(probe.proc.poll())
        world.close()
        self.assertEqual(world.proc.returncode,0)
        self.assertEqual(self.world_activations(),[(1,1,0)])

    def test_revocation_during_renderer_request_does_not_ack_or_replay_late_input(self):
        world,peer,probe=self.held()
        compositor=self.compositor(world)
        with probe.condition:offset=len(probe.events)
        os.kill(compositor,signal.SIGSTOP)
        try:
            peer.sequence+=1;peer.action_sequence+=1
            request=session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,
                action=core.ActorActionRequest(seq=peer.action_sequence,key=core.ActorKeyRequest(keysym='b',pressed=True)))
            peer.stream.sendall(frame(request))
            self.assertFalse(select.select([peer.stream],[],[],.1)[0])
            revoke_started=time.monotonic()
            self.revoke_now()
            released_key=probe.wait(lambda row:row.get('event')=='key' and not row['pressed'],after=offset,timeout=.22)
            released_button=probe.wait(lambda row:row.get('event')=='button' and not row['pressed'],after=offset,timeout=.22)
            self.assertLess(time.monotonic()-revoke_started,.25,
                'Seat releases waited for the stopped renderer or exceeded the native input expiry')
            self.assertIsNone(probe.proc.poll())
            time.sleep(.15)
            status=self.barrier()
            self.assertEqual((status.participant_count,status.pending_count,status.complete),(2,1,False))
        finally:
            os.kill(compositor,signal.SIGCONT)
        reply=receive(peer.stream.recv,peer.stream,session.WorldSessionReply)
        self.assertEqual(reply.request_id,request.request_id)
        self.assertTrue(reply.HasField('error'),reply)
        self.until(self.barrier,lambda result:result.complete)
        rows=probe.input_events(after=offset)
        self.assertTrue(any(row==released_key for row in rows))
        self.assertTrue(any(row==released_button for row in rows))
        self.assertFalse(any(e.get('text_hex')=='62' for e in rows))
        self.denied(peer.act(key=core.ActorKeyRequest(keysym='c',pressed=True)),core.UNAUTHORIZED)
        world.close()
        self.assertEqual(world.proc.returncode,0)
        self.assertEqual(self.world_activations(),[(1,1,0)])

    def test_issuer_commit_prevents_queued_native_key_while_world_is_stopped(self):
        world,peer,probe=self.held()
        compositor=self.compositor(world)
        log=Path(world.log.name)
        world.log.flush()
        offset_bytes=log.stat().st_size
        with probe.condition:offset=len(probe.events)
        world_stopped=False
        compositor_stopped=False
        try:
            os.kill(compositor,signal.SIGSTOP);compositor_stopped=True
            deadline=time.monotonic()+1
            while time.monotonic()<deadline:
                status=Path(f'/proc/{compositor}/status').read_text()
                if any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()):break
                time.sleep(.005)
            self.assertTrue(any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()))
            peer.sequence+=1;peer.action_sequence+=1
            request=session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,
                action=core.ActorActionRequest(seq=peer.action_sequence,key_tap_batch=core.ActorKeyTapBatchRequest(
                    chords=[core.ActorKeyChord(keysyms=['F1'])])))
            peer.stream.sendall(frame(request))
            deadline=time.monotonic()+2
            while 'role=renderer_input_sent' not in log.read_text()[offset_bytes:] and time.monotonic()<deadline:time.sleep(.005)
            self.assertIn('role=renderer_input_sent',log.read_text()[offset_bytes:],
                'World did not submit the native input request before revocation')
            os.kill(world.proc.pid,signal.SIGSTOP);world_stopped=True
            deadline=time.monotonic()+1
            while time.monotonic()<deadline:
                status=Path(f'/proc/{world.proc.pid}/status').read_text()
                if any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()):break
                time.sleep(.005)
            self.assertTrue(any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()))
            self.revoke_now();ack=time.monotonic()
            barrier=self.barrier()
            self.assertFalse(barrier.complete,'Revocation completion was acknowledged while worldd was stopped')
            self.assertFalse(any(row.get('keysym')==65470 for row in probe.input_events(after=offset)))
            os.kill(compositor,signal.SIGCONT);compositor_stopped=False
            try:probe.wait(lambda row:row.get('event')=='key' and row.get('keysym')==65470,after=offset,timeout=.5)
            except AssertionError:pass
            with probe.condition:
                events=[row for row in probe.events[offset:] if row.get('event')=='key' and row.get('keysym')==65470]
            target=Path(os.environ.get('SPATIAL_SECURITY_EVIDENCE',str(self.root)))/'queued-key-while-world-stopped.json'
            target.write_text(json.dumps(dict(revocation_ack_monotonic=ack,barrier_pending=barrier.pending_count,
                world_stopped=True,forbidden_key_events=events),sort_keys=True,indent=2)+'\n')
            target.chmod(0o600)
            self.assertIsNone(probe.proc.poll(),'Independent input witness exited')
            self.assertEqual(events,[],'Queued key reached the application after issuer revocation commit')
        finally:
            if compositor_stopped:os.kill(compositor,signal.SIGCONT)
            if world_stopped:os.kill(world.proc.pid,signal.SIGCONT)

    def test_first_pointer_prepares_empty_seat_before_delivery(self):
        import concurrent.futures
        import hashlib
        import re
        self.setup_native_input_workspace()
        marker=self.root/'first-pointer-prepared'
        environment={'SPATIAL_TEST_RENDER_INPUT_DELAY_MS':'2000',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND':'2',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_MARKER':str(marker),'SPATIAL_TRACE_STOP':'1'}
        with patch.dict(os.environ,environment):
            world,peer,probe=self.held(fixture_faults=True,hold_native_input=False)
        with probe.condition:offset=len(probe.events)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            action=pool.submit(peer.act,pointer=core.ActorPointerRequest(button=1,pressed=True))
            deadline=time.monotonic()+4
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(marker.exists(),'First pointer did not reach the renderer delay')
            world.log.flush()
            trace=Path(world.log.name).read_text()
            self.assertIn('role=native_seat_prepared',trace,
                'World did not retain the prepared seat before the pointer request')
            prepared=re.findall(r'role=native_seat_prepared id=(\d+) .*phase=begin',trace)
            bound=re.findall(r'role=native_seat_authority_bound id=(\d+) .*phase=credential_hash .*value=(\d+)',trace)
            expected=int.from_bytes(hashlib.sha256(self.identity.credential_id).digest()[:8],'big')
            self.assertEqual(len(prepared),1,prepared)
            self.assertEqual(bound,[(prepared[0],str(expected))],bound)
            self.assertFalse(any(row.get('event')=='button' and row.get('pressed')
                                 for row in probe.input_events(after=offset)))
            reply=action.result(timeout=5)
        self.assertTrue(reply.HasField('action') and reply.action.accepted,reply)
        probe.wait(lambda row:row.get('event')=='button' and row.get('pressed'),after=offset,timeout=2)
        self.assertTrue(peer.act(pointer=core.ActorPointerRequest(button=1,pressed=False)).action.accepted)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_invalid_prepared_seat_is_closed_without_input(self):
        import struct
        import subprocess
        from test_world_identity import WORLD
        forbidden_env=dict(os.environ,SPATIAL_TEST_RENDER_INVALID_PREPARED_SEAT='1')
        command=[str(WORLD.parent/'spatialwm')]
        if os.environ.get('TSAN_OPTIONS'):
            command=['/usr/bin/setarch',os.uname().machine,'-R',*command]
        rejected=subprocess.run(command,env=forbidden_env,capture_output=True,timeout=5)
        self.assertEqual(rejected.returncode,2,rejected.stderr)
        self.assertIn(b'fixture delay requires --fixture-faults',rejected.stderr)
        self.setup_native_input_workspace()
        with patch.dict(os.environ,SPATIAL_TEST_RENDER_INVALID_PREPARED_SEAT='1'):
            world,peer,probe=self.held(fixture_faults=True,hold_native_input=False)
        def device_count():
            name=b'XInputExtension'
            head,_=self.native_supervisor.request(98,struct.pack('<HH',len(name),0)+name)
            self.assertTrue(head[8])
            opcode=head[9]
            self.native_supervisor.request(opcode,struct.pack('<HH',2,0),minor=47)
            head,_=self.native_supervisor.request(opcode,struct.pack('<HH',0,0),minor=48)
            return struct.unpack_from('<H',head,8)[0]
        before=device_count()
        with probe.condition:offset=len(probe.events)
        reply=peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(device_count(),before,'Invalid preparation leaked native input devices')
        self.assertFalse(any(row.get('event')=='button' and row.get('pressed')
                             for row in probe.input_events(after=offset)))
        self.assertIsNone(probe.proc.poll(),'Independent input witness exited')
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_revocation_fences_prepared_seat_before_first_pointer(self):
        import concurrent.futures
        self.setup_native_input_workspace()
        marker=self.root/'prepared-seat-revocation'
        environment={'SPATIAL_TEST_RENDER_INPUT_DELAY_MS':'2000',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND':'2',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_MARKER':str(marker),'SPATIAL_TRACE_STOP':'1'}
        with patch.dict(os.environ,environment):
            world,peer,probe=self.held(fixture_faults=True,hold_native_input=False)
        with probe.condition:offset=len(probe.events)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            action=pool.submit(peer.act,pointer=core.ActorPointerRequest(button=1,pressed=True))
            deadline=time.monotonic()+4
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(marker.exists(),'First pointer did not enter the renderer delay')
            world.log.flush()
            self.assertIn('role=native_seat_prepared',Path(world.log.name).read_text())
            self.revoke_now()
            reply=action.result(timeout=6)
        self.assertTrue(reply.HasField('error'),reply)
        self.until(self.barrier,lambda result:result.complete)
        self.assertIsNone(probe.proc.poll(),'Independent input witness exited')
        self.assertFalse(any(row.get('event')=='button' and row.get('pressed')
                             for row in probe.input_events(after=offset)))
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_revocation_during_seat_preparation_discards_first_pointer(self):
        import concurrent.futures
        import re
        self.setup_native_input_workspace()
        marker=self.root/'pending-seat-revocation'
        environment={'SPATIAL_TEST_RENDER_INPUT_DELAY_MS':'2000',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND':'1',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_MARKER':str(marker),'SPATIAL_TRACE_STOP':'1'}
        with patch.dict(os.environ,environment):
            world,peer,probe=self.held(fixture_faults=True,hold_native_input=False)
        with probe.condition:offset=len(probe.events)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            action=pool.submit(peer.act,pointer=core.ActorPointerRequest(button=1,pressed=True))
            deadline=time.monotonic()+4
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(marker.exists(),'Seat preparation did not enter the renderer delay')
            self.revoke_now()
            reply=action.result(timeout=6)
        self.assertTrue(reply.HasField('error'),reply)
        self.until(self.barrier,lambda result:result.complete)
        world.log.flush();trace=Path(world.log.name).read_text()
        prepared=re.findall(r'role=native_seat_prepared id=(\d+) .*phase=begin',trace)
        closed=re.findall(r'role=native_seat_closed id=(\d+) .*phase=begin',trace)
        self.assertEqual(len(prepared),1,prepared)
        self.assertIn(prepared[0],closed,'Prepared seat was not closed after cancellation')
        self.assertIsNone(probe.proc.poll(),'Independent input witness exited')
        self.assertFalse(any(row.get('event')=='button' and row.get('pressed')
                             for row in probe.input_events(after=offset)))
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_issuer_commit_prevents_first_pointer_while_world_is_stopped(self):
        self.setup_native_input_workspace()
        marker=self.root/'first-pointer-delay-entered'
        environment={'SPATIAL_TEST_RENDER_INPUT_DELAY_MS':'2000',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND':'2',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_MARKER':str(marker)}
        with patch.dict(os.environ,environment):
            world,peer,probe=self.held(fixture_faults=True,hold_native_input=False)
        compositor=self.compositor(world)
        log=Path(world.log.name);world.log.flush();offset_bytes=log.stat().st_size
        with probe.condition:offset=len(probe.events)
        world_stopped=False;compositor_stopped=False
        try:
            peer.sequence+=1;peer.action_sequence+=1
            request=session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,
                action=core.ActorActionRequest(seq=peer.action_sequence,
                    pointer=core.ActorPointerRequest(button=1,pressed=True)))
            peer.stream.sendall(frame(request))
            deadline=time.monotonic()+2
            while 'role=renderer_input_sent' not in log.read_text()[offset_bytes:] and time.monotonic()<deadline:
                time.sleep(.005)
            self.assertIn('role=renderer_input_sent',log.read_text()[offset_bytes:])
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(marker.exists(),'Compositor did not enter first-input delay')
            os.kill(compositor,signal.SIGSTOP);compositor_stopped=True
            deadline=time.monotonic()+1
            while time.monotonic()<deadline:
                if any(line.startswith('State:') and 'T' in line.split()[1]
                       for line in Path(f'/proc/{compositor}/status').read_text().splitlines()):break
                time.sleep(.005)
            else:self.fail('Compositor did not stop')
            os.kill(world.proc.pid,signal.SIGSTOP);world_stopped=True
            deadline=time.monotonic()+1
            while time.monotonic()<deadline:
                if any(line.startswith('State:') and 'T' in line.split()[1]
                       for line in Path(f'/proc/{world.proc.pid}/status').read_text().splitlines()):break
                time.sleep(.005)
            else:self.fail('World did not stop')
            self.revoke_now();ack=time.monotonic()
            barrier=self.barrier()
            self.assertFalse(barrier.complete,'Revocation completed while worldd was stopped')
            self.assertFalse(any(row.get('event')=='button' and row.get('pressed')
                                 for row in probe.input_events(after=offset)))
            os.kill(compositor,signal.SIGCONT);compositor_stopped=False
            try:probe.wait(lambda row:row.get('event')=='button' and row.get('pressed'),after=offset,timeout=2.5)
            except AssertionError:pass
            with probe.condition:
                events=[row for row in probe.events[offset:] if row.get('event')=='button' and row.get('pressed')]
            target=Path(os.environ.get('SPATIAL_SECURITY_EVIDENCE',str(self.root)))/'first-pointer-while-world-stopped.json'
            target.write_text(json.dumps(dict(revocation_ack_monotonic=ack,barrier_pending=barrier.pending_count,
                world_stopped=True,forbidden_button_events=events),sort_keys=True,indent=2)+'\n')
            target.chmod(0o600)
            self.assertIsNone(probe.proc.poll(),'Independent input witness exited')
            self.assertEqual(events,[],'First pointer press reached the application after issuer revocation commit')
        finally:
            if compositor_stopped:os.kill(compositor,signal.SIGCONT)
            if world_stopped:os.kill(world.proc.pid,signal.SIGCONT)

    def test_independent_compositor_credential_fences_prepared_seat(self):
        import re
        import struct
        import compositor_native_pb2 as compositor_pb
        import input_pb2 as native_input
        import security_pb2 as security
        self.setup_native_input_workspace()
        from test_security_identity import NativeClient
        configuration=compositor_pb.NativeCompositorConfig.FromString(
            os.pread(self._native_security_fd,os.fstat(self._native_security_fd).st_size,0))
        owner=NativeClient(self.native_fixture.display,configuration.compositor_cookie)
        other_cookie=self.native_supervisor.issue(security.COMPOSITOR,b'',b'').cookie
        other=NativeClient(self.native_fixture.display,other_cookie)
        self.addCleanup(owner.close);self.addCleanup(other.close)
        self.assertNotEqual(owner.inspect().credential_id,other.inspect().credential_id)
        def revoke_seat(client,seat):
            name=b'SPATIAL-INPUT'
            head,_=client.request(98,struct.pack('<HH',len(name),0)+name)
            self.assertTrue(head[8])
            request=native_input.Request(protocol_version=1,request_id=1,
                revoke=native_input.Seat(id=seat))
            data=request.SerializeToString()
            head,body=client.request(head[9],struct.pack('<I',len(data))+data)
            size=struct.unpack_from('<I',head,8)[0]
            reply=native_input.Reply.FromString(body[:size])
            self.assertEqual((reply.protocol_version,reply.request_id),(1,1))
            return reply
        marker=self.root/'independent-seat-fence'
        environment={'SPATIAL_TEST_RENDER_INPUT_DELAY_MS':'800',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND':'2',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_MARKER':str(marker),'SPATIAL_TRACE_STOP':'1'}
        with patch.dict(os.environ,environment):
            world,peer,probe=self.held(fixture_faults=True,hold_native_input=False)
        compositor=self.compositor(world);world_stopped=False;compositor_stopped=False
        with probe.condition:offset=len(probe.events)
        try:
            peer.sequence+=1;peer.action_sequence+=1
            request=session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,
                action=core.ActorActionRequest(seq=peer.action_sequence,
                    pointer=core.ActorPointerRequest(button=1,pressed=True)))
            peer.stream.sendall(frame(request))
            deadline=time.monotonic()+4
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(marker.exists(),'First pointer did not enter renderer delay')
            world.log.flush()
            prepared=re.findall(r'role=native_seat_prepared id=(\d+) .*phase=begin',Path(world.log.name).read_text())
            self.assertEqual(len(prepared),1,prepared);seat=int(prepared[0]);self.assertGreater(seat,0)
            for pid in (compositor,world.proc.pid):
                os.kill(pid,signal.SIGSTOP)
                if pid==compositor:compositor_stopped=True
                else:world_stopped=True
                deadline=time.monotonic()+1
                while time.monotonic()<deadline:
                    if any(line.startswith('State:') and 'T' in line.split()[1]
                           for line in Path(f'/proc/{pid}/status').read_text().splitlines()):break
                    time.sleep(.005)
                else:self.fail('Process did not stop')
            self.revoke_now()
            self.assertFalse(self.barrier().complete)
            self.assertEqual(revoke_seat(other,seat).code,native_input.FORBIDDEN)
            self.assertEqual(revoke_seat(owner,seat).code,native_input.OK)
            os.kill(compositor,signal.SIGCONT);compositor_stopped=False
            try:probe.wait(lambda row:row.get('event')=='button' and row.get('pressed'),after=offset,timeout=1.1)
            except AssertionError:pass
            self.assertIsNone(probe.proc.poll(),'Independent input witness exited')
            self.assertFalse(any(row.get('event')=='button' and row.get('pressed')
                                 for row in probe.input_events(after=offset)))
        finally:
            if compositor_stopped:os.kill(compositor,signal.SIGCONT)
            if world_stopped:os.kill(world.proc.pid,signal.SIGCONT)
        try:
            reply=receive(peer.stream.recv,peer.stream,session.WorldSessionReply)
            self.assertEqual(reply.request_id,request.request_id)
            self.assertTrue(reply.HasField('error'),reply)
        except EOFError:
            world.log.flush()
            self.assertIn('reason=credential_revocation',Path(world.log.name).read_text())
            self.assertIsNone(world.proc.poll(),'World exited instead of closing the revoked actor channel')
        self.until(self.barrier,lambda result:result.complete)
        self.assertFalse(any(row.get('event')=='button' and row.get('pressed')
                             for row in probe.input_events(after=offset)))
        world.close();self.assertEqual(world.proc.returncode,0)


if __name__=='__main__':unittest.main(verbosity=2)
