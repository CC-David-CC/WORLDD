"""Prepare exact candidate pins. Apply only after both complete matrices and cross-host evidence pass."""
import argparse,hashlib,json,pathlib,tarfile
root=pathlib.Path(__file__).resolve().parents[1];parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
evidence=root/'evidence/world-remote-2026-10-09'
baseline=json.loads((root/'evidence/bootstrap-workspace-adapters-2026-10-09/integration-prepared-04.json').read_text())['files']
candidate=json.loads((root/'local/world-remote-source-02.json').read_text())
def digest(data):return hashlib.sha256(data).hexdigest()
def current(name):return (root/name).read_bytes().replace(b'\r\n',b'\n')
proposed={}
with tarfile.open(root/'local/world-remote-source-02.tar') as archive:
    for name,expected in candidate['files'].items():
        path=pathlib.PurePosixPath(name);assert not path.is_absolute() and '..' not in path.parts and '\\' not in name
        member=archive.getmember(name);assert member.isfile();data=archive.extractfile(member).read();assert digest(data)==expected,name
        proposed[name]=data
assert len(proposed)==233 and set(baseline)<=set(proposed)
changed=[]
for name,data in proposed.items():
    if (root/name).exists():
        old=current(name)
        if old==data:continue
        assert name in baseline and digest(old)==baseline[name],('Unreviewed current source change',name)
    else:assert name not in baseline,('Missing current source',name)
    changed.append(name)
assert len(changed)==17,(len(changed),changed)
for name,expected in json.loads((root/'acceptance.lock.json').read_text())['files'].items():
    assert digest(current(name))==expected and digest(proposed[name])==expected,name
record={'status':'prepared, not integrated','candidate':2,'source_files':233,'changed_files':changed,'files':candidate['files']}
(root/'local/world-remote-integration-prepared-02.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
if args.apply:
    for host,version in [('sb16','02'),('sb16-2','03')]:
        folder=evidence/'candidate-02'/host
        summary=json.loads((folder/'summary-final.json').read_text());manifest=json.loads((folder/'source-manifest.json').read_text())
        assert manifest['files']==candidate['files'] and summary['scope']=='final' and summary['host']==host
        for profile in ('debug','runtime-sanitize'):
            v=summary[profile];assert (v['core_checks'],v['core_suites'],v['harness_checks'],v['P10_cases'],v['failed'],v['skipped'])==(1089,62,51,151,0,0)
        audit=json.loads((folder/'schema-audit.json').read_text());assert audit['status']=='passed' and audit['unchanged_existing_test_files']==80 and audit['locked_files']==41
        crossing=json.loads((evidence/('crosshost-'+version)/host/'summary.json').read_text())
        assert crossing['status']=='passed' and crossing['authority_host']==host and crossing['worldd_sha256']==summary['debug']['binaries']['worldd']
    for name in changed:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(proposed[name])
    for name,expected in candidate['files'].items():assert digest(current(name))==expected,name
    record['status']='applied; commit and host adoption pending'
    (root/'local/world-remote-integration-applied-02.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
print(record['status'],len(changed),'files;',len(proposed),'exact source hashes')
