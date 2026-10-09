import hashlib,json,os,pathlib,runpy,socket,sys,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
out=r/'local/m02-participant-channel-baseline-01';source=out/'source';build=out/'debug/build'
manifest=json.loads((r/'local/participant-channel-baseline-01.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
run=json.loads((out/'debug/run.json').read_text());assert [x['exit_code'] for x in run['results']]==[0,0,0,8]
assert 'BrokenPipeError' in (out/'debug/baseline.log').read_text()
sys.path[:0]=[str(build/'python'),str(source),str(source/'tests/core')]
sys.argv=['diagnostic',str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'spatial-participant-runtime-probe')]
os.chdir(source)
module=runpy.run_path(str(source/'tests/core/test_participant_channel.py'),run_name='baseline_diagnostic')
case=module['ParticipantChannelTests']('test_inherited_channel_activates_and_releases_real_issuer_generation_without_child_key')
case.setUp()
try:
    child,peer,_,_=case.private();code=child.proc.wait(timeout=5);diagnostic=child.log.read_text()
    assert code==2 and 'Invalid authority participant configuration' in diagnostic,(code,diagnostic)
    assert child.proc.stdout.read()==b'' and peer.recv(1)==b''
    result=dict(host=host,source_files=len(manifest),exit_code=code,stderr=diagnostic,interpretation='Baseline rejects inherited configuration before channel authentication; original BrokenPipe failure preserved.')
finally:
    case.tearDown();case.doCleanups()
(out/'diagnostic.json').write_text(json.dumps(result,indent=2)+'\n')
with tarfile.open(r/('local/participant-channel-baseline-'+host+'-01.tar.gz'),'w:gz') as a:
    for p in [out/'diagnostic.json',*[out/'debug'/n for n in ['run.json','configure.log','build.log','harness.log','baseline.log']]]:
        a.add(p,arcname='baseline-01/'+host+'/'+str(p.relative_to(out)))
print(json.dumps(result),flush=True)
