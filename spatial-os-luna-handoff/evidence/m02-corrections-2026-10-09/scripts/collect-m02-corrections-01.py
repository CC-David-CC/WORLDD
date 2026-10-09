import hashlib,json,re,socket,sys,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
mode=sys.argv[1];assert mode in ('baseline','debug','final')
files=[];summary=dict(host=host,mode=mode,integrated=False)
def add_tree(root,label):
    for p in root.rglob('*'):
        if p.is_file():files.append((p,label+'/'+p.relative_to(root).as_posix()))
for name in ['bootstrap-owner-idle-03','world-revision-interleave-01','native-launch-discovery-01']:
    folder=r/'local'/name;run=json.loads((folder/'run.json').read_text())
    expected=[1,0,1,0] if name=='native-launch-discovery-01' else [1,1]
    assert [x['exit_code'] for x in run['results']]==expected
    add_tree(folder,'baselines/'+host+'/'+name)
for name in ['test_workspace_bootstrap_owner_idle.py','test_world_revision_interleave.py']:
    files.append((r/'local'/name,'baselines/'+host+'/scripts/'+name))
if host=='sb16-2':
    folder=r/'local/m02-participant-fixtures-07/runtime-sanitize'
    run=json.loads((folder/'run.json').read_text());assert run['results'][-1]['name']=='core' and run['results'][-1]['exit_code']==8
    log=(folder/'core.log').read_text();assert 'test_exit_revokes_credentials_without_a_wait_request' in log and 'BrokenPipeError' in log
    for name in ['run.json','core.log','focused.log','harness.log']:files.append((folder/name,'candidate-06-native-failure/'+host+'/'+name))
    for p in (folder/'server').glob('*test_exit_revokes_credentials_without_a_wait_request*'):
        if p.is_file():files.append((p,'candidate-06-native-failure/'+host+'/server/'+p.name))
for candidate,folder_name in [(8,'m02-world-freshness-02'),(9,'m02-native-witness-02')]:
    manifest=json.loads((r/f'local/participant-fixtures-candidate-{candidate:02}.json').read_text())['files']
    root=r/'local'/folder_name
    for n,h in manifest.items():assert hashlib.sha256((root/'source'/n).read_bytes()).hexdigest()==h,n
    files.append((r/f'local/participant-fixtures-candidate-{candidate:02}.json',f'candidate-{candidate:02}/{host}/source-manifest.json'))
    for profile in ([] if mode=='baseline' else ['debug']+(['runtime-sanitize'] if mode=='final' else [])):
        folder=root/profile;run=json.loads((folder/'run.json').read_text())
        assert all(x['exit_code']==0 for x in run['results'])
        if candidate==8:
            assert [x['name'] for x in run['results']]==['configure','build','harness','freshness']
            log=(folder/'freshness.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
            assert len(counts)==10 and '100% tests passed, 0 tests failed out of 10' in log and 'skipped' not in log
            summary[profile+'_freshness']=dict(checks=sum(counts),suites=10,failed=0,skipped=0)
            assert 'Ran 51 tests' in (folder/'harness.log').read_text()
        else:
            assert [x['suite'] for x in run['results']]==['test_native_launch','test_security_identity']
            counts=[]
            for name in ['test_native_launch','test_security_identity']:
                log=(folder/(name+'.log')).read_text();count=re.findall(r'^Ran (\d+) tests? in ',log,re.M)
                assert len(count)==1 and log.rstrip().endswith('OK') and 'skipped' not in log
                counts.append(int(count[0]))
            assert counts[0]==21
            summary[profile+'_native']=dict(checks=sum(counts),suites=2,parts=counts,failed=0,skipped=0)
        for p in folder.iterdir():
            if p.is_file():files.append((p,f'candidate-{candidate:02}/{host}/{profile}/'+p.name))
        if (folder/'server').exists():add_tree(folder/'server',f'candidate-{candidate:02}/{host}/{profile}/server')
p=r/f'local/m02-corrections-{host}-{mode}-summary.json';p.write_text(json.dumps(summary,indent=2)+'\n');files.append((p,host+'-'+mode+'-summary.json'))
with tarfile.open(r/f'local/m02-corrections-{host}-{mode}.tar.gz','w:gz') as archive:
    for p,n in files:archive.add(p,arcname=n)
print(json.dumps(summary))
