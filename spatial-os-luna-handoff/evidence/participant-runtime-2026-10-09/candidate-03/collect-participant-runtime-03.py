import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host=='sb16-2'
base=root/'local/m02-participant-runtime-draft-02';extra=root/'local/m02-participant-runtime-tests-03'
old=json.loads((root/'local/participant-runtime-candidate-02.json').read_text());new=json.loads((root/'local/participant-runtime-candidate-03.json').read_text());test='tests/core/test_identity_participant_runtime.py'
assert [n for n in old['files'] if old['files'][n]!=new['files'][n]]==[test]
for n,h in old['files'].items():assert hashlib.sha256((base/'source'/n).read_bytes()).hexdigest()==h,n
run=json.loads((extra/'run.json').read_text());assert run['exit_code']==0 and run['test_sha256']==new['files'][test]
for n,h in run['binaries'].items():assert hashlib.sha256((base/'debug/build'/n).read_bytes()).hexdigest()==h,n
text=(extra/'tests.log').read_text();assert re.search(r'Ran 13 tests in ',text) and text.rstrip().endswith('OK') and 'skipped' not in text
reg=json.loads((base/'regression/run.json').read_text());assert reg['exit_code']==0
text=(base/'regression/tests.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)];assert len(counts)==6 and sum(counts)==96 and '100% tests passed, 0 tests failed out of 6' in text and 'skipped' not in text
build=json.loads((base/'run.json').read_text());assert all(r['exit_code']==0 for r in build['results'])
summary=dict(host=host,new_checks=13,regression_checks=96,failed=0,skipped=0,source_files=len(new['files']),compiled_source_manifest='candidate02',executed_test_manifest='candidate03',runtime_integration=False,full_qualification='pending')
summarypath=root/'local/participant-runtime-summary-03.json';summarypath.write_text(json.dumps(summary,indent=2)+'\n')
files=[(summarypath,'summary.json')]
for name in ['configure.log','build.log','run.json','runtime-tests.log']:files.append((base/name,'build02/'+name))
for name in ['run.json','tests.log']:files.extend([(base/'regression'/name,'regression/'+name),(extra/name,'runtime03/'+name)])
for candidate in ('02','03'):files.append((root/('local/participant-runtime-candidate-'+candidate+'.json'),'source-manifest-'+candidate+'.json'))
with tarfile.open(root/'local/participant-runtime-focused-sb16-2-03.tar.gz','w:gz') as archive:
    for path,name in files:archive.add(path,arcname='candidate-03/sb16-2/'+name)
print(json.dumps(summary),flush=True)
