"""Real owner administration over TLS, plus explicit malformed-response witnesses."""
import unittest
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
from harness.wire import frame
from test_issuer_client import ClientFixture,FaultPeer

class ParticipantClientTests(ClientFixture):
    def value(self):
        return pb.AuthorityParticipant(id=b'p'*32,public_key=b'k'*32,
            domains=[grants.Domain(kind=grants.WORLD,id='park')])
    def register_op(self):
        return pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(
            participant=self.value(),expected_registry_revision=0))
    def test_checked_client_registers_and_reads_real_owner_signed_records(self):
        self.ready();client=self.client();_,key=self.key('participant')
        operation=self.register_op();operation.participant_register.participant.public_key=key
        reply=self.execute(client,self.challenge(client,operation))
        self.assertEqual(reply.participant.participant,operation.participant_register.participant)
        page=self.execute(client,self.challenge(client,pb.IssuerOperation(registry_read=pb.AuthorityRegistryRead()))).registry
        self.assertEqual(list(page.participants),[reply.participant]);self.assertEqual(page.registry_revision,1)
        self.assertEqual(page.issuer_id,self.info.issuer_id);self.assertEqual(self.counts(),(0,0,0))
    def test_transport_certificate_and_actor_key_do_not_authorize_registration(self):
        self.ready();client=self.client();challenge=self.challenge(client,self.register_op())
        proof=self.proof(challenge,self.actor_key,self.actor_public)
        self.denied(self.execute(client,challenge,proof=proof))
        page=self.execute(client,self.challenge(client,pb.IssuerOperation(registry_read=pb.AuthorityRegistryRead()))).registry
        self.assertEqual(page.registry_revision,0);self.assertFalse(page.participants)
    def test_rejected_registration_preserves_checked_connection(self):
        self.ready();client=self.client();self.assertTrue(self.execute(client,self.challenge(client,self.register_op())).HasField('participant'))
        self.denied(self.execute(client,self.challenge(client,self.register_op())),core.REVISION_CONFLICT)
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info,self.info)
    def malformed(self,mode):
        def respond(peer,request,reply):
            record=pb.AuthorityParticipantRecord(participant=self.value(),registered_revision=1,issuer_id=self.info.issuer_id)
            if mode=='issuer':record.issuer_id=b'x'*32
            elif mode=='missing':record.ClearField('participant')
            elif mode=='id':record.participant.id=b'x'
            elif mode=='key':record.participant.public_key=b'x'
            elif mode=='zero':record.registered_revision=0
            elif mode=='overflow':record.registered_revision=2**63
            elif mode=='no-domains':record.participant.ClearField('domains')
            elif mode=='kind':record.participant.domains[0].kind=grants.DOMAIN_UNSPECIFIED
            elif mode=='domain-text':record.participant.domains[0].id='bad\0id'
            elif mode=='domain-duplicate':record.participant.domains.add().CopyFrom(record.participant.domains[0])
            elif mode=='domain-order':record.participant.domains.add(kind=grants.WORLD,id='aaa')
            elif mode=='unknown':record.ParseFromString(record.SerializeToString()+b'\xf8\x07\x01')
            if mode.startswith('page-'):
                page=pb.AuthorityRegistryPage(issuer_id=self.info.issuer_id,registry_revision=1,participants=[record])
                if mode=='page-issuer':page.issuer_id=b'x'*32
                elif mode=='page-head':page.registry_revision=0
                elif mode=='page-overflow':page.registry_revision=2**63
                elif mode=='page-order':page.participants.add().CopyFrom(record)
                elif mode in ('page-key','page-domain','page-revision'):
                    second=page.participants.add();second.CopyFrom(record);second.participant.id=b'z'*32
                    page.registry_revision=2;second.registered_revision=2
                    if mode=='page-key':second.participant.domains[0].id='other'
                    elif mode=='page-domain':second.participant.public_key=b'q'*32
                    else:
                        second.participant.public_key=b'q'*32;second.participant.domains[0].id='other'
                        second.registered_revision=1
                elif mode=='page-cursor':page.next_after=b'x'*32
                elif mode=='page-empty-cursor':page.ClearField('participants');page.next_after=b'x'*32
                elif mode=='page-size':
                    page.ClearField('participants');page.registry_revision=17
                    for i in range(17):
                        item=page.participants.add();item.CopyFrom(record)
                        item.registered_revision=i+1
                        item.participant.id=bytes([i])*32;item.participant.public_key=bytes([i+32])*32;item.participant.domains[0].id=str(i)
                reply.registry.CopyFrom(page)
            else:reply.participant.CopyFrom(record)
            peer.stream.sendall(frame(reply))
        peer=FaultPeer(self,response=respond);client=self.client(peer.port)
        # The fault peer supplies no authority. This probes checked-client response validation only.
        request=pb.IssuerRequest(protocol_version=1,execute=pb.IssuerExecute(nonce=b'n'*32))
        output,_=client.proc.communicate(frame(request)*2,timeout=3)
        self.assertEqual(client.proc.returncode,2,mode);self.assertEqual(output,b'',mode)
        self.assertEqual(len(peer.requests),3);self.assertEqual(peer.connections,1)
    def test_invalid_record_identity_and_revision_poison_client(self):
        for mode in ('issuer','missing','id','key','zero','overflow','unknown'):
            with self.subTest(mode=mode):self.malformed(mode)
    def test_invalid_record_domains_poison_client(self):
        for mode in ('no-domains','kind','domain-text','domain-duplicate','domain-order'):
            with self.subTest(mode=mode):self.malformed(mode)
    def test_invalid_registry_context_poison_client(self):
        for mode in ('page-issuer','page-head','page-overflow'):
            with self.subTest(mode=mode):self.malformed(mode)
    def test_conflicting_registry_entries_poison_client(self):
        for mode in ('page-order','page-key','page-domain','page-size'):
            with self.subTest(mode=mode):self.malformed(mode)
    def test_invalid_registry_continuation_poison_client(self):
        for mode in ('page-cursor','page-empty-cursor'):
            with self.subTest(mode=mode):self.malformed(mode)
    def test_duplicate_registration_revision_poison_client(self):
        self.malformed('page-revision')

if __name__=='__main__':unittest.main(verbosity=2)
