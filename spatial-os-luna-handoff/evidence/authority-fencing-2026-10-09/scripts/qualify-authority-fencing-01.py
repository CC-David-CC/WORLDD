"""Build and qualify frozen fencing source on one authorized Linux host."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
from google.protobuf import descriptor_pb2

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
baseline=json.loads((r/'local/authority-fencing-baseline-01/summary.json').read_text())
assert all(v['expected_missing_fencing'] and not v['sanitizer_marker'] for v in baseline['profiles'].values())
out=r/'local/m02-authority-fencing-01';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/authority-fencing-candidate-01.tar') as archive:archive.extractall(source,filter='data')
manifest=json.loads((r/'local/authority-fencing-candidate-01.json').read_text())['files'];assert len(manifest)==292
def audit():
    for files in (manifest,json.loads((source/'acceptance.lock.json').read_text())['files']):
        for name,digest in files.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
audit()
def descriptors(tree,label):
    schemas=sorted((tree/'proto').rglob('*.proto'))
    command=['protoc']+['--proto_path='+str(p) for p in sorted({p.parent for p in schemas})]
    target=out/(label+'.pb')
    subprocess.run(command+['--include_imports','--descriptor_set_out='+str(target)]+[str(p) for p in schemas],check=True)
    return {p.name:p for p in descriptor_pb2.FileDescriptorSet.FromString(target.read_bytes()).file}
oldsource=r/'local/m02-supervisor-world-handoff-02/source'
oldmanifest=json.loads((r/'local/supervisor-world-handoff-candidate-02.json').read_text())['files']
for n,digest in oldmanifest.items():assert hashlib.sha256((oldsource/n).read_bytes()).hexdigest()==digest,n
old=descriptors(oldsource,'before');new=descriptors(source,'after')
assert set(new)==set(old) and len(old)==13
issuer=new['issuer.proto'];extra={'AuthorityFenceBegin','AuthorityFenceChallenge','AuthorityFenceComplete','AuthorityFenceState'}
assert {v.name for v in issuer.message_type}-{v.name for v in old['issuer.proto'].message_type}==extra
for message in list(issuer.message_type):
    if message.name in extra:issuer.message_type.remove(message)
    elif message.name=='IssuerRequest':
        for name,number in [('authority_fence_begin',30),('authority_fence_complete',31)]:
            field=next(f for f in message.field if f.name==name);assert field.number==number and field.oneof_index==0
            message.field.remove(field)
    elif message.name=='IssuerReply':
        field=next(f for f in message.field if f.name=='authority_fence');assert field.number==28 and field.oneof_index==0
        message.field.remove(field)
enum=next(v for v in new['key.proto'].enum_type if v.name=='SignaturePurpose')
assert enum.value[-1].name=='AUTHORITY_FENCING' and enum.value[-1].number==7
del enum.value[-1]
assert all(old[n].SerializeToString(deterministic=True)==new[n].SerializeToString(deterministic=True) for n in old)
(out/'schema-audit.json').write_text(json.dumps(dict(existing_files=13,existing_descriptors_unchanged=True,
    added_messages=sorted(extra),request_fields=[30,31],reply_field=28,signature_purpose=7),indent=2)+'\n')
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
baseline_bindings=out/'baseline-python';baseline_bindings.mkdir()
subprocess.run(['protoc','--proto_path='+str(r/'local'),'--python_out='+str(baseline_bindings),
    str(r/'local/authority_fence_baseline.proto')],check=True)
profiles=[]
for profile,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    target=out/profile;target.mkdir();build=target/'build'
    env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),
        SPATIAL_SECURITY_EVIDENCE=str(target/'server'),PYTHONPATH=str(baseline_bindings)+':'+str(build/'python')+':'+str(source),
        SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),SPATIAL_EVIDENCE_DIR=str(target/'cases'),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[
        ('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),
        ('build',['cmake','--build',str(build),'--parallel','2']),
        ('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
        ('baseline',['python3',str(r/'local/test_authority_fencing_baseline.py'),str(source),str(build/'identityd'),str(build/'spatial-issuer-client')]),
        ('fencing',['ctest','--test-dir',str(build),'-V','-R','^identity-authority-fencing(-client)?$']),
        ('identity',['ctest','--test-dir',str(build),'-V','-R','^identity-','-E','^identity-authority-fencing(-client)?$'])]
    results=[]
    for name,command in commands:
        audit()
        with (target/(name+'.log')).open('xb') as log:
            run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode))
        (target/'run.json').write_text(json.dumps(dict(host=host,source_files=292,results=results,server=server),indent=2)+'\n')
        print(host,profile,name,run.returncode,flush=True)
        if run.returncode:
            print((target/(name+'.log')).read_text()[-18000:],flush=True);raise SystemExit(run.returncode)
    profiles.append((profile,target,build,env,results))
for profile,target,build,env,results in profiles:
    for name,command in [('core',['ctest','--test-dir',str(build),'-V','-E','^identity-']),
        ('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(target/'P10.json')])]:
        audit()
        with (target/(name+'.log')).open('xb') as log:
            run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode))
        (target/'run-full.json').write_text(json.dumps(dict(host=host,source_files=292,results=results,server=server),indent=2)+'\n')
        print(host,profile,name,run.returncode,flush=True)
        if run.returncode:
            print((target/(name+'.log')).read_text()[-18000:],flush=True);raise SystemExit(run.returncode)
audit();print(host,'full authority fencing qualification complete',flush=True)
