"""Committed issuer history, event-driven TLS waits, and the production watcher."""
import os
from pathlib import Path
import select
import sqlite3
import struct
import subprocess
import sys
import time
import unittest

import core_pb2 as core
import issuer_pb2 as pb
from harness.wire import frame
from test_issuer_client import ClientFixture, FaultPeer, CLIENT

WATCH = Path(sys.argv.pop(1))


class WatchProcess:
    def __init__(self, config, log, after=None):
        self.stderr = log.open('wb')
        args = [str(WATCH), '--config', str(config)]
        if after is not None: args += ['--after-revision', str(after)]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr)

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try: self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=2); raise
        for stream in (self.proc.stdin, self.proc.stdout, self.stderr):
            if not stream.closed: stream.close()

    def receive(self, timeout=3):
        deadline = time.monotonic()+timeout
        def exact(size):
            data = b''
            while len(data) < size:
                if not select.select([self.proc.stdout], [], [], max(0, deadline-time.monotonic()))[0]:
                    raise AssertionError('Revocation notification deadline expired')
                part = os.read(self.proc.stdout.fileno(), size-len(data))
                if not part: raise EOFError('Revocation watcher closed')
                data += part
            return data
        length = struct.unpack('!I', exact(4))[0]
        if length > 1048576: raise AssertionError('Excessive revocation update')
        return pb.RevocationUpdate.FromString(exact(length))


class WatchFixture(ClientFixture):
    def watcher(self, port=None, after=None):
        process = WatchProcess(self.config_file(port), self.root/f'watch-{len(self.connections)}.log', after)
        self.connections.append(process)
        return process

    def approved(self, client):
        record = self.decide(client, self.enroll(client)).enrollment
        return self.claims(record, self.info)

    def revoke(self, client, claims, kind=pb.CREDENTIAL_TARGET, revision=0):
        target = pb.IdentityReference(kind=kind, id=claims.credential_id if kind == pb.CREDENTIAL_TARGET else claims.actor_id)
        operation = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(target=target,
            expected_security_revision=revision, reason='private revocation reason'))
        return self.execute(client, self.challenge(client, operation))

    def batch(self, client, after=None, wait=0):
        request = pb.RevocationRead(wait_ms=wait)
        if after is not None: request.after_revision = after
        reply = client.call(revocations=request)
        self.assertTrue(reply.HasField('revocations'), reply)
        result = reply.revocations
        self.assertEqual(result.issuer_id, self.info.issuer_id)
        return result


class WatchTests(WatchFixture):
    def test_current_baseline_and_committed_history_are_distinct(self):
        peer = self.ready(); claims = self.approved(peer)
        self.assertTrue(self.revoke(peer, claims).identity_status.credential_revoked)
        current = self.batch(peer); self.assertTrue(current.initial)
        self.assertEqual((current.from_revision,current.through_revision,current.head_revision), (1,1,1))
        self.assertEqual(len(current.events), 0)
        history = self.batch(peer, 0); self.assertFalse(history.initial)
        self.assertEqual((history.from_revision,history.through_revision,history.head_revision), (0,1,1))
        self.assertEqual(history.events[0].target.id, claims.credential_id)
        self.assertEqual(history.events[0].target.kind, pb.CREDENTIAL_TARGET)
        self.assertNotIn(b'private revocation reason', history.SerializeToString())

    def test_duplicate_revocation_does_not_create_another_event(self):
        peer = self.ready(); claims = self.approved(peer)
        self.revoke(peer, claims); self.revoke(peer, claims, revision=1)
        self.assertEqual(len(self.batch(peer, 0).events), 1)

    def test_actor_and_credential_events_keep_global_order(self):
        peer = self.ready(); claims = self.approved(peer)
        self.revoke(peer, claims); self.revoke(peer, claims, pb.ACTOR_TARGET, 1)
        events = self.batch(peer, 0).events
        self.assertEqual([e.revision for e in events], [1,2])
        self.assertEqual([e.target.kind for e in events], [pb.CREDENTIAL_TARGET,pb.ACTOR_TARGET])
        self.assertEqual(events[1].target.id, claims.actor_id)

    def test_reconnect_recovers_events_after_explicit_cursor(self):
        peer = self.ready(); claims = self.approved(peer); self.revoke(peer, claims)
        peer.close(); peer = self.connect()
        self.assertEqual(self.batch(peer, 0).through_revision, 1)
        self.assertEqual(len(self.batch(peer, 1).events), 0)

    def test_future_cursor_and_excessive_wait_are_rejected(self):
        peer = self.ready()
        for request in (pb.RevocationRead(after_revision=1), pb.RevocationRead(after_revision=2**64-1),
                        pb.RevocationRead(after_revision=0,wait_ms=1001)):
            self.denied(peer.call(revocations=request), core.INVALID_ARGUMENT)
        self.assertEqual(self.batch(peer).head_revision, 0)

    def test_tls_wait_has_heartbeat_and_does_not_block_another_peer(self):
        peer = self.ready(); waiting = self.connect()
        waiting.stream.sendall(frame(pb.IssuerRequest(protocol_version=1, request_id=42,
            revocations=pb.RevocationRead(after_revision=0,wait_ms=200))))
        start = time.monotonic(); self.assertEqual(peer.call(info=pb.IssuerInfoRequest()).info, self.info)
        self.assertLess(time.monotonic()-start, .15)
        result = waiting.receive(); elapsed = time.monotonic()-start
        self.assertEqual(result.request_id, 42); self.assertTrue(result.HasField('revocations'))
        self.assertGreater(elapsed, .14); self.assertLess(elapsed, .8)
        self.assertEqual(result.revocations.head_revision, 0)

    def test_committed_revocation_wakes_wait_without_waiting_for_heartbeat(self):
        peer = self.ready(); claims = self.approved(peer); waiting = self.connect()
        waiting.stream.sendall(frame(pb.IssuerRequest(protocol_version=1, request_id=42,
            revocations=pb.RevocationRead(after_revision=0,wait_ms=1000))))
        time.sleep(.05); start = time.monotonic(); self.revoke(peer, claims)
        event = waiting.receive().revocations
        self.assertEqual(event.through_revision, 1); self.assertEqual(event.events[0].target.id, claims.credential_id)
        self.assertLess(time.monotonic()-start, .5)

    def test_rolled_back_revocation_has_no_event(self):
        peer = self.ready(); claims = self.approved(peer)
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER deny_revoke BEFORE INSERT ON revocation BEGIN SELECT RAISE(ABORT,'fixture'); END")
        self.denied(self.revoke(peer, claims), core.STORAGE_ERROR)
        self.assertEqual(self.batch(peer, 0).head_revision, 0)
        with sqlite3.connect(self.state/'identity.db') as db: db.execute('DROP TRIGGER deny_revoke')
        self.assertEqual(self.revoke(peer, claims).identity_status.security_revision, 1)
        self.assertEqual(len(self.batch(peer, 0).events), 1)

    def test_missing_durable_history_is_a_storage_failure(self):
        peer = self.ready(); claims = self.approved(peer); self.revoke(peer, claims)
        with sqlite3.connect(self.state/'identity.db') as db: db.execute('DELETE FROM revocation')
        self.denied(peer.call(revocations=pb.RevocationRead(after_revision=0)), core.STORAGE_ERROR)

    def test_history_survives_issuer_restart(self):
        peer = self.ready(); claims = self.approved(peer); self.revoke(peer, claims)
        self.stop_daemons(); self.launch()
        result = self.batch(self.connect(), 0)
        self.assertEqual(result.events[0].target.id, claims.credential_id)

    def test_history_is_bounded_to_128_contiguous_events(self):
        peer = self.start(); revision = 0
        for i in range(65):
            key, public = self.key('bulk-'+str(i))
            record = self.decide(peer, self.enroll(peer,self.intent(public),key,public)).enrollment
            claims = pb.ActorCredential.FromString(record.credential.content.payload)
            self.revoke(peer,claims,revision=revision); revision += 1
            self.revoke(peer,claims,pb.ACTOR_TARGET,revision); revision += 1
        first = self.batch(peer, 0); self.assertEqual(len(first.events),128)
        self.assertEqual(first.through_revision,128); self.assertEqual(first.head_revision,130)
        second = self.batch(peer,128); self.assertEqual([e.revision for e in second.events],[129,130])

    def test_production_watcher_delivers_baseline_then_real_revocation(self):
        peer = self.ready(); claims = self.approved(peer); watcher = self.watcher()
        initial = watcher.receive(); self.assertEqual(initial.protocol_version,1); self.assertTrue(initial.batch.initial)
        self.revoke(peer,claims)
        update = watcher.receive(); self.assertEqual(update.batch.events[0].target.id,claims.credential_id)

    def test_watcher_explicit_cursor_delivers_durable_backlog(self):
        peer = self.ready(); claims = self.approved(peer); self.revoke(peer,claims)
        update = self.watcher(after=0).receive()
        self.assertFalse(update.batch.initial); self.assertEqual(update.batch.through_revision,1)

    def test_watcher_stops_promptly_on_supervisor_eof(self):
        self.ready(); watcher = self.watcher(); watcher.receive(); start = time.monotonic()
        watcher.close(); self.assertEqual(watcher.proc.returncode,0); self.assertLess(time.monotonic()-start,.5)

    def test_issuer_failure_disables_watcher_without_reconnect(self):
        peer = self.ready(); watcher = self.watcher(); watcher.receive()
        self.stop_daemons(); self.assertEqual(watcher.proc.wait(timeout=2),2)

    def test_invalid_feed_batches_disable_checked_client(self):
        for mode in ('issuer','cursor','gap','kind','target','head','empty-gap','oversized'):
            with self.subTest(mode=mode):
                def response(peer,request,reply):
                    batch = reply.revocations; batch.issuer_id=self.info.issuer_id
                    batch.from_revision=0; batch.through_revision=1; batch.head_revision=1
                    event=batch.events.add(revision=1,target=pb.IdentityReference(kind=pb.ACTOR_TARGET,id=b'x'*32))
                    if mode=='issuer': batch.issuer_id=b'y'*32
                    elif mode=='cursor': batch.from_revision=5
                    elif mode=='gap': event.revision=2
                    elif mode=='kind': event.target.kind=pb.IDENTITY_TARGET_UNSPECIFIED
                    elif mode=='target': event.target.id=b'bad'
                    elif mode=='head': batch.head_revision=0
                    elif mode=='empty-gap': del batch.events[:]
                    elif mode=='oversized':
                        for i in range(128): batch.events.add().CopyFrom(event)
                    peer.stream.sendall(frame(reply))
                peer=FaultPeer(self,response=response); watcher=self.watcher(peer.port,after=0)
                self.assertEqual(watcher.proc.wait(timeout=2),2)
                self.assertEqual(peer.connections,1); self.assertEqual(len(peer.requests),3)

    def test_bounded_queue_failure_is_visible_to_world_consumer(self):
        def response(peer,request,reply):
            after=request.revocations.after_revision
            batch=reply.revocations; batch.issuer_id=self.info.issuer_id
            batch.from_revision=after; batch.through_revision=after+1; batch.head_revision=after+1
            batch.events.add(revision=after+1,target=pb.IdentityReference(kind=pb.ACTOR_TARGET,id=(after+1).to_bytes(32,'big')))
            peer.stream.sendall(frame(reply))
        peer=FaultPeer(self,response=response)
        result=subprocess.run([str(CLIENT.parent/'spatial-issuer-watch-probe'),str(self.config_file(peer.port))],
                              capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(peer.connections,1)


if __name__=='__main__': unittest.main(verbosity=2)
