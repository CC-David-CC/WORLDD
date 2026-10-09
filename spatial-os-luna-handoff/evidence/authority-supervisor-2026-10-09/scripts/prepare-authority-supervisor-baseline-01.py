import hashlib,json,tarfile
from pathlib import Path
r=Path(__file__).parent;source=r/'authority-supervisor-draft-01';source.mkdir()
with tarfile.open(r/'participant-relay-candidate-01.tar') as a:a.extractall(source,filter='data')
def edit(n,old,new):
    p=source/n;s=p.read_text();assert old in s,n;p.write_text(s.replace(old,new,1),encoding='utf-8',newline='\n')
edit('proto/spatial/supervisor/v1/launch.proto','message AuthorityParticipantChannelPrepare {','''// Private local supervisor configuration. No actor receives this interface.
message AuthoritySupervisorConfig {
  uint32 protocol_version = 1;
  string journal_directory = 2;
  string delegated_cgroup = 3;
  AuthorityLaunchBinding binding = 4;
  AuthorityParticipantAttach participant = 5;
  AuthorityLaunchAuthorize authorization = 6;
  AuthorityProcessLaunch launch = 7;
  string child_participant_config = 8;
  uint32 participant_target_fd = 9;
  repeated uint32 inherited_source_fds = 10;
}
message AuthorityParticipantChannelPrepare {''')
p=source/'CMakeLists.txt';s=p.read_text();start=s.index('    add_test(NAME authority-participant-relay');end=s.index('    add_test(NAME authority-owned-participant',start)
block=s[start:end].replace('authority-participant-relay','authority-supervisor').replace('spatial-participant-relay-','spatial-supervisor-').replace('test_authority_participant_relay.py','test_authority_supervisor.py')
p.write_text(s[:start]+block+s[start:],encoding='utf-8',newline='\n')
test='''"""The production supervisor launches and pumps a real participant child."""
import unittest
import test_authority_owned_participant as owned

class AuthoritySupervisorTests(owned.OwnedParticipantTests):
    def test_production_supervisor_is_available_for_owned_authority_execution(self):
        self.assertTrue(owned.existing.PROBE.with_name('spatial-authority-supervisor').is_file(),
            'Missing production authority supervisor; a manually ticked test probe does not provide its event loop')

if __name__=='__main__':
    suite=unittest.TestSuite(AuthoritySupervisorTests(n) for n in AuthoritySupervisorTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
'''
(source/'tests/native/test_authority_supervisor.py').write_text(test,encoding='utf-8',newline='\n')
base=json.loads((r/'participant-relay-candidate-01.json').read_text())['files']
files={n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in sorted([*base,'tests/native/test_authority_supervisor.py'])}
target=r/'authority-supervisor-baseline-01'
target.with_suffix('.json').write_text(json.dumps(dict(files=files,base='participant-relay-candidate-01'),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(target.with_suffix('.tar'),'w') as a:
    for n in files:a.add(source/n,arcname=n,recursive=False)
s=(r/'qualify-participant-relay-baseline-01.py').read_text().replace('participant-relay-baseline','authority-supervisor-baseline').replace("'^authority-participant-relay$'","'^authority-supervisor$'")
(r/'qualify-authority-supervisor-baseline-01.py').write_text(s,encoding='utf-8',newline='\n')
print('Frozen278file missing-production-supervisor baseline; no existing runtime code changed.')
