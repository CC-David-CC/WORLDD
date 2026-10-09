"""Prepare an exact source comparison; apply only after both complete qualification records exist."""
import argparse,hashlib,json,pathlib,tarfile
root=pathlib.Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
evidence=root/'evidence/bootstrap-workspace-adapters-2026-10-09'
baseline=json.loads((root/'local/domain-production-tree-01.json').read_text())['files']
candidate=json.loads((root/'local/adapters-tested-tree-04.json').read_text())
fix=json.loads((root/'evidence/process-witness-exit-2026-10-09/source-audit.json').read_text())
def digest(data):return hashlib.sha256(data).hexdigest()
def current(name):return (root/name).read_bytes().replace(b'\r\n',b'\n')
proposed={}
with tarfile.open(root/'local/bootstrap-adapters-candidate-04.tar') as archive:
    for name,expected in candidate['files'].items():
        path=pathlib.PurePosixPath(name)
        assert not path.is_absolute() and '..' not in path.parts and '\\' not in name,name
        member=archive.getmember(name);assert member.isfile(),name
        data=archive.extractfile(member).read();assert digest(data)==expected,name
        proposed[name]=data
assert digest(proposed['tests/core/test_processes.py'])==fix['source_sha256']
proposed['tests/core/test_processes.py']=current('tests/core/test_processes.py')
assert digest(proposed['tests/core/test_processes.py'])==fix['candidate_sha256']
hashes={name:digest(data) for name,data in proposed.items()}
assert len(hashes)==223
changed=[]
for name,data in proposed.items():
    path=root/name
    if path.exists():
        old=current(name)
        if old==data:continue
        assert name in baseline and digest(old)==baseline[name],('Unreviewed current source change',name)
    else:assert name not in baseline,('Missing existing source',name)
    changed.append(name)
assert len(changed)==18,(len(changed),changed)
for name,expected in json.loads((root/'acceptance.lock.json').read_text())['files'].items():
    assert digest(current(name))==expected,name
    if name in proposed:assert digest(proposed[name])==expected,name
record={'status':'prepared, not integrated','candidate_version':4,'source_files':223,'changed_files':changed,
    'preserved_process_witness':fix['candidate_sha256'],'files':hashes}
(root/'local/adapter-integration-prepared-04.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
if args.apply:
    for host in ('sb16','sb16-2'):
        folder=evidence/host
        summary=json.loads((folder/'summary-final-04.json').read_text())
        finish=json.loads((folder/'finish-04/run.json').read_text())
        manifest=json.loads((folder/'finish-04/integration-manifest.json').read_text())
        assert manifest['files']==hashes,host
        assert finish['host']==host and finish['status']=='passed',host
        assert [(r['name'],r['exit_code']) for r in finish['results']]==[('matrix-collector',0),('debug',0),('runtime-sanitize',0)]
        assert finish['original_matrix_core_checks']==1037 and finish['additional_witness_checks']==6
        assert finish['rerun_original_process_checks']==finish['original_test_bodies_unchanged']==12
        for label in ('debug','runtime-sanitize'):
            value=summary[label]
            assert (value['core_checks'],value['core_suites'],value['harness_checks'],value['P10_cases'],value['failed'],value['skipped'])==(1037,55,51,151,0,0)
    # No mutation occurs until all source and both-host evidence checks above succeed.
    for name in changed:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(proposed[name])
    for name,expected in hashes.items():assert digest(current(name))==expected,name
    record['status']='applied; commit and host adoption pending'
    (root/'local/adapter-integration-applied-04.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
print(record['status'],len(changed),'reviewed files;',len(hashes),'source hashes; corrected process witness retained')
