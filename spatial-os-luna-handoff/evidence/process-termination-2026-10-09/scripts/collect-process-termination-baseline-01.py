import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host=='sb16'
folder=root/'local/m02-process-termination-baseline-01';manifest=json.loads((root/'local/process-termination-baseline-01.json').read_text())
for n,h in manifest['files'].items():assert hashlib.sha256((folder/'source'/n).read_bytes()).hexdigest()==h,n
run=json.loads((folder/'run.json').read_text());assert [r['exit_code'] for r in run['results']]==[0,0,8]
text=(folder/'baseline.log').read_text();assert 'Ran 4 tests' in text and 'FAILED (failures=3)' in text
assert 'AssertionError: 3 != 5 : Stop returned before descendant exit' in text
assert 'Cleanup did not report its kernel failure within the deadline' in text and 'AssertionError: 0 != 2' in text
summary=dict(host=host,checks=4,passed=1,failed=3,source_files=len(manifest['files']),failure_evidence=['two of five pinned descendant pidfds still not exited when stop returned','signal denial hangs cleanup','wait denial returns false success'],production='unchanged baseline process.cpp/process.hpp',binaries={n:hashlib.sha256((folder/'build'/n).read_bytes()).hexdigest() for n in ['spatial-process-termination-probe','spatial-app-init']})
(folder/'summary.json').write_text(json.dumps(summary,indent=2)+chr(10))
with tarfile.open(root/'local/process-termination-baseline-sb16-01.tar.gz','w:gz') as a:
    for n in ['configure.log','build.log','baseline.log','run.json','summary.json']:a.add(folder/n,arcname='baseline-01/sb16/'+n)
    a.add(root/'local/process-termination-baseline-01.json',arcname='baseline-01/sb16/source-manifest.json')
print('Preserved three baseline failures',flush=True)
