import json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
files=[];summary={}
for name,expected in [('m01-bootstrap-adapter-baseline-01',15),('m01-workspace-bootstrap-baseline-01',5)]:
    folder=root/'local'/name;report=json.loads((folder/'run.json').read_text());log=(folder/'baseline.log').read_text()
    assert report['test_exit_code']==1
    assert re.search(r'Ran '+str(expected)+r' tests in ',log)
    assert 'FAILED (failures='+str(expected)+')' in log
    summary[name]={'executed':expected,'passed':0,'failed':expected,'test_exit_code':1,'reason':'bootstrap actor operations or application reports are absent'}
    for path in folder.iterdir():
        if path.is_file() and path.suffix in ('.json','.log'): files.append((path,'sb16/'+name+'/'+path.name))
    for path in (folder/'inputs').iterdir():
        if path.is_file(): files.append((path,'sb16/'+name+'/inputs/'+path.name))
record=root/'local/bootstrap-adapter-baseline-summary.json';record.write_text(json.dumps(summary,indent=2)+'\n')
files.append((record,'sb16/summary.json'))
with tarfile.open(root/'local/bootstrap-adapter-baselines-sb16.tar.gz','w:gz') as archive:
    for path,name in files: archive.add(path,arcname=name)
print('Preserved 15 world and five workspace baseline failures; no runtime databases, private keys, or binaries')
