"""Prepare exact candidate pins. Apply only after both complete matrices pass."""
import argparse,hashlib,json,pathlib,re,tarfile
root=pathlib.Path(__file__).resolve().parents[1];parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
evidence=root/'evidence/participant-registry-2026-10-09'
baseline=json.loads((root/'evidence/world-remote-2026-10-09/candidate-02/integration-applied.json').read_text())['files']
candidate=json.loads((root/'local/participant-candidate-02.json').read_text())
def digest(data):return hashlib.sha256(data).hexdigest()
def current(name):return (root/name).read_bytes().replace(b'\r\n',b'\n')
proposed={}
with tarfile.open(root/'local/participant-candidate-02.tar') as archive:
    for name,expected in candidate['files'].items():
        path=pathlib.PurePosixPath(name);assert not path.is_absolute() and '..' not in path.parts and '\\' not in name
        member=archive.getmember(name);assert member.isfile();data=archive.extractfile(member).read();assert digest(data)==expected,name
        proposed[name]=data
assert len(proposed)==236 and set(baseline)<=set(proposed)
changed=[]
for name,data in proposed.items():
    if (root/name).exists():
        old=current(name)
        if old==data:continue
        assert name in baseline and digest(old)==baseline[name],('Unreviewed current source change',name)
    else:assert name not in baseline,('Missing current source',name)
    changed.append(name)
expected_changes={'CMakeLists.txt','proto/spatial/identity/v1/issuer.proto','src/identity/issuer.cpp','src/identity/issuer_client.cpp','src/identity/participants.hpp','src/identity/protocol.cpp','tests/core/test_identity_bootstrap.py','tests/core/test_identity_enrollment.py','tests/core/test_identity_revocation.py','tests/core/test_identity_participants.py','tests/core/test_identity_participant_client.py'}
assert set(changed)==expected_changes,changed
for name,expected in baseline.items():assert digest(current(name))==expected,name
for name,expected in json.loads((root/'acceptance.lock.json').read_text())['files'].items():
    assert digest(current(name))==expected and digest(proposed[name])==expected,name
record={'status':'prepared, not integrated','candidate':2,'source_files':236,'changed_files':changed,'files':candidate['files']}
(root/'local/participant-integration-prepared-02.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
if args.apply:
    for host in ('sb16','sb16-2'):
        folder=evidence/'candidate-02'/host
        summary=json.loads((folder/'summary-final.json').read_text());manifest=json.loads((folder/'source-manifest.json').read_text())
        assert manifest['files']==candidate['files'] and summary['scope']=='final' and summary['host']==host
        for profile in ('debug','runtime-sanitize'):
            v=summary[profile];assert (v['core_checks'],v['core_suites'],v['harness_checks'],v['P10_cases'],v['failed'],v['skipped'])==(1120,64,51,151,0,0)
        audit=json.loads((folder/'schema-audit.json').read_text());assert audit['status']=='passed' and audit['unchanged_existing_test_files']==84 and audit['locked_files']==41
        for profile in ('debug','runtime-sanitize'):
            folder_profile=folder/profile
            run=json.loads((folder_profile/'run.json').read_text())
            assert [r['name'] for r in run['results']]==['configure','build','harness','participants','core','P10']
            assert all(r['exit_code']==0 for r in run['results'])
            for part,suites,checks in [('participants',2,31),('core',62,1089)]:
                log=(folder_profile/(part+'.log')).read_text()
                counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
                assert len(counts)==suites and sum(counts)==checks
                assert f'100% tests passed, 0 tests failed out of {suites}' in log
                assert 'skipped=' not in log and '... skipped ' not in log
            p10=json.loads((folder_profile/'P10.json').read_text())
            assert p10['status']=='passed' and p10['selected']==p10['executed']==len(p10['tests'])==151
            assert all(t['status']=='passed' for t in p10['tests'])
            harness=(folder_profile/'harness.log').read_text()
            assert re.search(r'Ran 51 tests in ',harness) and 'OK' in harness and 'skipped=' not in harness
    for name in changed:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(proposed[name])
    for name,expected in candidate['files'].items():assert digest(current(name))==expected,name
    record['status']='applied; commit and host adoption pending'
    (root/'local/participant-integration-applied-02.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
print(record['status'],len(changed),'files;',len(proposed),'exact source hashes')
