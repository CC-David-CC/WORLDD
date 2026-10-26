"""Independent native input witnesses for durable world revocation completion."""
import os
from pathlib import Path
import signal
import select
import time
import unittest
import core_pb2 as core
import world_session_pb2 as session
from test_world_participant import WorldParticipantFixture
from test_world_identity import receive
from harness.wire import frame


class WorldParticipantNativeTests(WorldParticipantFixture):
    def world(self, grants=(), native=False, fixture_faults=False, native_fd=None):
        self.assertTrue(native)
        self.assertEqual(set(grants), {'navigate','interact','observe'})
        if native_fd is None:
            native_fd=self.native_compositor_authority()
        world=self.start_world(native=True,fixture_faults=fixture_faults,native_fd=native_fd)
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

    def test_completion_waits_for_native_key_and_button_release(self):
        world,peer,probe=self.held()
        before=probe.input_events()
        self.assertTrue(any(e['event']=='key' and e['pressed'] for e in before))
        self.assertTrue(any(e['event']=='button' and e['pressed'] for e in before))
        compositor=self.compositor(world)
        os.kill(compositor,signal.SIGSTOP)
        try:
            self.revoke_now()
            # A stopped production compositor cannot execute cancellation.
            # Query the independent issuer, without pumping the world channel.
            time.sleep(.15)
            status=self.barrier()
            self.assertEqual((status.participant_count,status.pending_count,status.complete),(1,1,False))
            during=probe.input_events()
            self.assertFalse(any(e['event'] in ('key','button') and not e['pressed'] for e in during))
        finally:
            os.kill(compositor,signal.SIGCONT)
        complete=self.until(self.barrier,lambda result:result.complete)
        self.assertEqual(complete.participant_count,1)
        self.independent_release(probe)
        self.denied(peer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.assertIsNone(probe.proc.poll())
        world.close()
        self.assertEqual(world.proc.returncode,0)
        self.assertEqual(self.activations(),[(1,1,0)])

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
            self.assertEqual((status.participant_count,status.pending_count,status.complete),(1,1,False))
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
        self.assertEqual(self.activations(),[(1,1,0)])


if __name__=='__main__':unittest.main(verbosity=2)
