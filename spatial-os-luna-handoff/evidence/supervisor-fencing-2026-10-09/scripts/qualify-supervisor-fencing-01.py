"""Qualify frozen supervisor fencing on an authorized world host."""
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
baseline=json.loads((r/'local/supervisor-fencing-baseline-01/summary.json').read_text())
assert all(v['passed'] and v['exit_code']==1 and v['ran']==1 and not v['sanitizer_marker'] for v in baseline['profiles'].values())
out=r/'local/m02-supervisor-fencing-01';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/supervisor-fencing-candidate-01.tar') as archive:archive.extractall(source,filter='data')
manifest=json.loads((r/'local/supervisor-fencing-candidate-01.json').read_text())['files'];assert len(manifest)==293
def audit():
    for files in (manifest,json.loads((source/'acceptance.lock.json').read_text())['files']):
        for n,digest in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==digest,n
audit()
def descriptors(tree,label):
    schemas=sorted((tree/'proto').rglob('*.proto'))
    command=['protoc']+['--proto_path='+str(p) for p in sorted({p.parent for p in schemas})]
    target=out/(label+'.pb')
    subprocess.run(command+['--include_imports','--descriptor_set_out='+str(target)]+[str(p) for p in schemas],check=True)
    return {p.name:p for p in descriptor_pb2.FileDescriptorSet.FromString(target.read_bytes()).file}
oldsource=r/'local/m02-authority-fencing-01/source'
oldmanifest=json.loads((r/'local/authority-fencing-candidate-01.json').read_text())['files']
assert all(hashlib.sha256((oldsource/n).read_bytes()).hexdigest()==v for n,v in oldmanifest.items())
old=descriptors(oldsource,'before');new=descriptors(source,'after')
assert set(old)==set(new) and len(old)==13
for message_name,number,type_name in [('AuthorityLaunchRecord',10,'.spatial.identity.v1.AuthorityFenceState'),
                                     ('AuthorityLaunchRequest',20,'.spatial.supervisor.v1.AuthorityLaunchQuery')]:
    message=next(m for m in new['launch.proto'].message_type if m.name==message_name)
    field=next(f for f in message.field if f.name=='fence')
    assert field.number==number and field.type_name==type_name
    if message_name=='AuthorityLaunchRequest':assert field.oneof_index==0
    message.field.remove(field)
assert all(old[n].SerializeToString(deterministic=True)==new[n].SerializeToString(deterministic=True) for n in old)
(out/'schema-audit.json').write_text(json.dumps(dict(existing_files=13,existing_descriptors_unchanged=True,
    added_record_field=10,added_request_field=20),indent=2)+'\n')
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
profiles=[]
for profile,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    target=out/profile;target.mkdir();build=target/'build'
    env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),
        SPATIAL_SECURITY_EVIDENCE=str(target/'server'),PYTHONPATH=str(build/'python')+':'+str(source),
        SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),SPATIAL_EVIDENCE_DIR=str(target/'cases'),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[
        ('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),
        ('build',['cmake','--build',str(build),'--parallel','2']),
        ('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
        ('fencing',['ctest','--test-dir',str(build),'-V','-R','^authority-supervisor-fencing$']),
        ('focused',['ctest','--test-dir',str(build),'-V','-R','^authority-','-E','^authority-supervisor-fencing$'])]
    results=[]
    for name,command in commands:
        audit()
        with (target/(name+'.log')).open('xb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode))
        (target/'run.json').write_text(json.dumps(dict(host=host,source_files=293,results=results,server=server),indent=2)+'\n')
        print(host,profile,name,run.returncode,flush=True)
        if run.returncode:
            print((target/(name+'.log')).read_text()[-16000:],flush=True);raise SystemExit(run.returncode)
    profiles.append((profile,target,build,env,results))
for profile,target,build,env,results in profiles:
    for name,command in [('core',['ctest','--test-dir',str(build),'-V','-E','^authority-']),
        ('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(target/'P10.json')])]:
        audit()
        with (target/(name+'.log')).open('xb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode))
        (target/'run-full.json').write_text(json.dumps(dict(host=host,source_files=293,results=results,server=server),indent=2)+'\n')
        print(host,profile,name,run.returncode,flush=True)
        if run.returncode:
            print((target/(name+'.log')).read_text()[-16000:],flush=True);raise SystemExit(run.returncode)
audit();print(host,'supervisor fencing full qualification complete',flush=True)
