import hashlib,json,os,pathlib,re,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
old=r/'local/m02-participant-channel-01/source';source=r/'local/m02-participant-channel-02/source';build=old.parent/'debug/build'
before=json.loads((r/'local/participant-channel-candidate-01.json').read_text())['files'];after=json.loads((r/'local/participant-channel-candidate-02.json').read_text())['files']
assert before.keys()==after.keys() and [n for n in before if before[n]!=after[n]]==['tests/core/test_participant_channel.py']
for tree,manifest in [(old,before),(source,after)]:
    for n,h in manifest.items():assert hashlib.sha256((tree/n).read_bytes()).hexdigest()==h,n
out=r/'local/participant-channel-cleanup-diagnostic-02';out.mkdir()
command=['python3',str(source/'tests/core/test_participant_channel.py'),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'spatial-participant-runtime-probe')]
with (out/'tests.log').open('wb') as log:run=subprocess.run(command,cwd=source,env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)),stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=host,command=command,exit_code=run.returncode,production_identical=True,binary_source='participant-channel-candidate-01',test_source='participant-channel-candidate-02'),indent=2)+'\n')
log=(out/'tests.log').read_text();assert run.returncode==0 and re.search(r'Ran 17 tests in ',log) and log.rstrip().endswith('OK'),log
with tarfile.open(r/('local/participant-channel-cleanup-'+host+'-02.tar.gz'),'w:gz') as a:
    for p in out.iterdir():a.add(p,arcname='fixture-correction-02/'+host+'/'+p.name)
print(host,'all17 child-channel checks passed against original production binaries after fixture cleanup',flush=True)
