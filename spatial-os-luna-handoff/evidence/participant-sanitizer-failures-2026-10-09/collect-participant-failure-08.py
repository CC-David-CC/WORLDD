import hashlib,json,re,socket,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
root=r/'local/m02-participant-fixtures-06';out=root/'runtime-sanitize'
manifest=json.loads((r/'local/participant-fixtures-candidate-05.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((root/'source'/n).read_bytes()).hexdigest()==h,n
run=json.loads((out/'run.json').read_text());assert run['results'][-1]['name']=='core' and run['results'][-1]['exit_code']!=0
log=(out/'core.log').read_text();methods=sorted(set(re.findall(r'^\d+: (?:FAIL|ERROR): (\w+)',log,re.M)))
counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
assert len(counts)==66 and sum(counts)==1151
summary=dict(host=host,candidate=5,output=6,status='failed',runtime_core_checks=1151,runtime_core_suites=66,failed_methods=methods,P10='unrun after failure')
files=[(out/n,'candidate-05/'+host+'/runtime-sanitize/'+n) for n in ['run.json','configure.log','build.log','harness.log','focused.log','core.log']]
files.append((r/'local/participant-fixtures-candidate-05.json','candidate-05/'+host+'/source-manifest.json'))
for p in (out/'server').glob('*.log'):
    if any(method in p.name for method in methods):files.append((p,'candidate-05/'+host+'/runtime-sanitize/server/'+p.name))
repeat=r/'local/credential-control-repeat-01'
if repeat.exists():
    repeats=json.loads((repeat/'run.json').read_text());summary['candidate06_credential_repetitions']=repeats
    for p in repeat.rglob('*'):
        if p.is_file():files.append((p,'candidate-06-repetition/'+host+'/'+p.relative_to(repeat).as_posix()))
p=r/f'local/participant-failure-summary-{host}-08.json';p.write_text(json.dumps(summary,indent=2)+'\n');files.append((p,host+'-summary.json'))
idle=r/'local/bootstrap-owner-idle-02'
if idle.exists():
    for p in idle.rglob('*'):
        if p.is_file():files.append((p,'bootstrap-owner-idle/'+host+'/'+p.relative_to(idle).as_posix()))
with tarfile.open(r/f'local/participant-failure-{host}-08.tar.gz','w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print(host,json.dumps(summary))
