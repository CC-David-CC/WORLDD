import hashlib,json,tarfile
from pathlib import Path
r=Path(__file__).parent;source=r/'participant-relay-draft-01';source.mkdir()
with tarfile.open(r/'participant-channel-candidate-02.tar') as a:a.extractall(source,filter='data')
def edit(n,old,new):
    p=source/n;s=p.read_text();assert old in s,n;p.write_text(s.replace(old,new,1),encoding='utf-8',newline='\n')
edit('proto/spatial/supervisor/v1/launch.proto','message AuthorityLaunchQuery {}','message AuthorityParticipantChannelPrepare { uint32 target_fd = 1; }\nmessage AuthorityLaunchQuery {}')
edit('proto/spatial/supervisor/v1/launch.proto','    spatial.identity.v1.ParticipantCommand participant_command = 17;','''    spatial.identity.v1.ParticipantCommand participant_command = 17;
    AuthorityParticipantChannelPrepare prepare_channel = 18;
    AuthorityLaunchQuery pump_channel = 19;''')
edit('proto/spatial/supervisor/v1/launch.proto','    spatial.identity.v1.ParticipantUpdate participant_update = 12;','''    spatial.identity.v1.ParticipantUpdate participant_update = 12;
    spatial.identity.v1.AuthorityParticipantRuntimeConfig participant_config = 13;''')
p=source/'CMakeLists.txt';s=p.read_text();start=s.index('    add_test(NAME authority-owned-participant');end=s.index('    add_test(NAME authority-launch-coordinator',start)
block=s[start:end].replace('authority-owned-participant','authority-participant-relay').replace('spatial-owned-participant-','spatial-participant-relay-').replace('test_authority_owned_participant.py','test_authority_participant_relay.py')
p.write_text(s[:start]+block+s[start:],encoding='utf-8',newline='\n')
test='''"""The supervisor passes exact approved identity through its owned child channel."""
import unittest
import test_authority_owned_participant as owned
import launch_pb2 as pb

class ParticipantRelayTests(owned.OwnedParticipantTests):
    def test_channel_configuration_requires_owned_approval_and_contains_no_signing_material(self):
        proc,_,auth,_=self.connected()
        self.assertTrue(self.authorize(proc).HasField('view'))
        result=self.call(proc,prepare_channel=pb.AuthorityParticipantChannelPrepare(target_fd=37))
        self.assertTrue(result.HasField('participant_config'),result)
        config=result.participant_config
        self.assertEqual(config.protocol_version,1)
        self.assertEqual(config.participant_id,auth.registration.participant.id)
        self.assertEqual(config.registered_revision,auth.registration.registered_revision)
        self.assertEqual(config.service_incarnation,auth.service_incarnation)
        self.assertEqual(config.supervisor_issuer_id,auth.issuer_id)
        self.assertEqual(config.supervisor_channel_fd,37)
        self.assertEqual(config.private_key_path,'');self.assertEqual(config.issuer_client_config,'')
        self.assertEqual(self.activation_rows(),[]);self.stopped(proc)

if __name__=='__main__':
    suite=unittest.TestSuite(ParticipantRelayTests(n) for n in ParticipantRelayTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
'''
(source/'tests/native/test_authority_participant_relay.py').write_text(test,encoding='utf-8',newline='\n')
base=json.loads((r/'participant-channel-candidate-02.json').read_text())['files']
files={n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in sorted([*base,'tests/native/test_authority_participant_relay.py'])}
target=r/'participant-relay-baseline-01';target.with_suffix('.json').write_text(json.dumps(dict(files=files,base='participant-channel-candidate-02'),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(target.with_suffix('.tar'),'w') as a:
    for n in files:a.add(source/n,arcname=n,recursive=False)
s=(r/'qualify-participant-channel-baseline-01.py').read_text().replace('participant-channel-baseline','participant-relay-baseline').replace("'^identity-participant-channel$'","'^authority-participant-relay$'")
s=s.replace("'-DSPATIAL_SANITIZERS=OFF'","'-DSPATIAL_SANITIZERS=OFF','-DSPATIAL_INPUT_TESTS=ON'")
begin=s.index("'--target','identityd'");end=s.index("'--parallel','2'",begin);s=s[:begin]+s[end:]
(r/'qualify-participant-relay-baseline-01.py').write_text(s,encoding='utf-8',newline='\n')
print('Relay baseline frozen277files: additive schema and new assertion only; production unchanged.')
