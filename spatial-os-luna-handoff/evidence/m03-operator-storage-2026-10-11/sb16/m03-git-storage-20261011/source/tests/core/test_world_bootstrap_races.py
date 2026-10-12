"""Real issuer revocation must precede bootstrap grant materialization."""
import socket
import sqlite3
import struct
import time
import unittest
from google.protobuf import text_format
import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.wire import frame
import test_world_authority as existing
from test_world_bootstrap import BootstrapWorldFixture


class BootstrapRelay(existing.RelayingIssuer):
    """Delay one genuine issuer reply after its query, then revoke through the issuer."""
    def __init__(self, fixture, revoke, operation, occurrence=1):
        self.operation=operation; self.remaining=occurrence
        super().__init__(fixture,revoke)

    def relay(self, raw):
        upstream=None; stream=raw
        try:
            raw.settimeout(2); stream=self.context.wrap_socket(raw,server_side=True); self.streams.append(stream)
            upstream=self.fixture.connect()
            def exact(size):
                data=b''
                while len(data)<size:
                    part=stream.recv(size-len(data))
                    if not part: raise EOFError()
                    data+=part
                return data
            while not self.stop.is_set():
                size=struct.unpack('!I',exact(4))[0]
                if size>1048576: raise AssertionError('Excessive relay frame')
                request=issuer.IssuerRequest.FromString(exact(size)); sequence=request.request_id
                reply=upstream.request(request); reply.request_id=sequence
                if request.HasField(self.operation) and self.armed.is_set():
                    self.remaining-=1
                    if self.remaining==0:
                        self.armed.clear(); self.revoke()
                        if not self.delivered.wait(.3): raise AssertionError('Real revocation was not forwarded')
                        time.sleep(.08)
                stream.sendall(frame(reply))
                if reply.HasField('revocations') and reply.revocations.events: self.delivered.set()
        except (OSError,EOFError): pass
        except BaseException as error: self.errors.append(error)
        finally:
            stream.close()
            if upstream: upstream.close()


class WorldBootstrapRaceTests(BootstrapWorldFixture):
    def receipt_count(self, world):
        config=session.WorldIdentityConfig()
        text_format.Parse(world.config_path.read_text(),config)
        # Read the actual committed store. This witness never modifies authority.
        from pathlib import Path
        database=Path(config.authority_directory)/'authority.db'
        self.assertTrue(database.is_file(),database)
        with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
            return db.execute('SELECT count(*) FROM bootstrap_receipt').fetchone()[0]

    def revoked_during(self, operation, parent=False, occurrence=1):
        holder={}
        def revoke():
            claims=self.identity if parent else holder['actor'][3]
            target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET if parent else issuer.CREDENTIAL_TARGET,
                id=claims.actor_id if parent else claims.credential_id)
            request=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(target=target,
                expected_security_revision=0,reason='bootstrap delayed reply regression'))
            peer=self.connect()
            reply=self.execute(peer,self.challenge(peer,request))
            self.assertTrue(reply.HasField('identity_status'),reply)
        relay=BootstrapRelay(self,revoke,operation,occurrence)
        world=self.scoped_world(issuer_port=relay.port); self.publish(); self.binding(world)
        self.select_profile(); actor=self.enroll_bootstrap(); holder['actor']=actor
        relay.armed.set()
        reply=self.authenticate_bootstrap(world.peers[1],actor)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertIn(reply.error.code,(core.UNAUTHORIZED,core.UNAVAILABLE,core.FORBIDDEN) if parent
            else (core.UNAUTHORIZED,core.UNAVAILABLE))
        if reply.error.code==core.FORBIDDEN:
            # The recipient stays valid, but the revoked parent cannot delegate entry.
            result=self.check_report(reply.bootstrap_attempt,world)
            self.assertEqual(result.error.code,core.FORBIDDEN)
        self.assertTrue(relay.delivered.is_set())
        self.assertEqual(relay.remaining,0)
        self.assertEqual(self.receipt_count(world),0)

    def test_credential_revoked_during_profile_read_creates_no_receipt(self):
        self.revoked_during('profile_read')

    def test_parent_actor_revoked_during_profile_read_creates_no_receipt(self):
        self.revoked_during('profile_read',parent=True)

    def test_credential_revoked_during_binding_ancestry_read_creates_no_receipt(self):
        self.revoked_during('actor_status',occurrence=2)

    def test_parent_actor_revoked_during_binding_ancestry_read_creates_no_receipt(self):
        self.revoked_during('actor_status',parent=True,occurrence=2)

    def test_partial_profile_reports_missing_binding_and_preserves_one_grant_on_retry(self):
        world=self.scoped_world()
        first=self.terms(rights=(grant.WORLD_ENTER,grant.WORLD_NAVIGATE),depth=0); first.ClearField('subject')
        second=self.terms(rights=(grant.WORLD_OBSERVE,),depth=0); second.ClearField('subject')
        profile=issuer.BootstrapProfile(id='starter',grants=[issuer.BootstrapGrantTemplate(id='entry',terms=first),
            issuer.BootstrapGrantTemplate(id='view',terms=second)])
        request=issuer.IssuerOperation(profile_put=issuer.BootstrapProfilePut(profile=profile,expected_revision=0))
        self.assertTrue(self.execute(self.owner_peer,self.challenge(self.owner_peer,request)).HasField('profile'))
        self.binding(world); self.select_profile(); actor=self.enroll_bootstrap(); peer=world.peers[1]
        reply=self.authenticate_bootstrap(peer,actor); self.assertTrue(reply.HasField('opened'),reply)
        report=reply.bootstrap_attempt; self.assertFalse(report.HasField('error'),report)
        results={result.template_id:result for result in report.results}
        self.assertEqual(set(results),{'entry','view'})
        self.assertTrue(results['entry'].HasField('grant'))
        self.assertEqual(results['view'].error.code,core.NOT_FOUND)
        revision=self.own(peer).revision
        self.assertEqual(self.apply(peer).bootstrap,report)
        self.assertEqual(self.own(peer).revision,revision)
        self.assertEqual(self.receipt_count(world),1)
        self.assertTrue(self.navigation(peer).HasField('action'))
        self.denied(peer.act(observe=core.ActorObserveRequest()),core.FORBIDDEN)

    def test_independent_grant_survives_bootstrap_parent_revocation(self):
        world=self.scoped_world(roots=lambda:[
            self.terms(rights=(grant.WORLD_ENTER,grant.WORLD_NAVIGATE,grant.POLICY_ADMINISTER)),
            self.terms(rights=(grant.WORLD_ENTER,grant.WORLD_OBSERVE))])
        owner=world.peers[0]; roots=self.own(owner).grants
        parent=next(item for item in roots if grant.WORLD_NAVIGATE in item.terms.rights)
        other=next(item for item in roots if grant.WORLD_OBSERVE in item.terms.rights)
        self.publish(); self.binding(world,parent); self.select_profile(); actor=self.enroll_bootstrap()
        independent=self.delegate(owner,other,self.terms(actor[3],rights=(grant.WORLD_ENTER,grant.WORLD_OBSERVE),depth=0))
        self.assertTrue(independent.HasField('grant'),independent)
        peer=world.peers[1]; self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        self.assertTrue(self.revoke_grant(owner,parent).HasField('grant'))
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        self.denied(self.navigation(peer),core.FORBIDDEN)
        peer.call(close=session.WorldSessionClose())
        self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        self.assertIn(independent.grant.id,[item.id for item in self.own(peer).grants])
        self.assertEqual(self.receipt_count(world),1)


if __name__=='__main__': unittest.main(verbosity=2)
