"""Qualify the corrected frozen world candidate with both profiles and cumulative gates."""
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
proof=json.loads((r/'local/world-native-attribution-04/run.json').read_text())
assert proof['passed'] and proof['expected_checks']==13 and not proof['production_changed']
assert 'Ran 13 tests' in (r/'local/world-native-attribution-04/test.log').read_text()
out=r/'local/m02-supervisor-world-handoff-02';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/supervisor-world-handoff-candidate-02.tar') as archive:archive.extractall(source,filter='data')
manifest=json.loads((r/'local/supervisor-world-handoff-candidate-02.json').read_text())['files'];assert len(manifest)==290
def audit():
    for files in (manifest,json.loads((source/'acceptance.lock.json').read_text())['files']):
        for name,digest in files.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
audit()
oldsource=r/'local/m02-supervisor-broker-handoff-02/source'
oldmanifest=json.loads((r/'local/supervisor-broker-handoff-candidate-02.json').read_text())['files']
for name,digest in oldmanifest.items():assert hashlib.sha256((oldsource/name).read_bytes()).hexdigest()==digest,name
def descriptors(tree,label):
    schemas=sorted((tree/'proto').rglob('*.proto'))
    command=['protoc']+['--proto_path='+str(p) for p in sorted({p.parent for p in schemas})]
    target=out/(label+'.pb')
    subprocess.run(command+['--include_imports','--descriptor_set_out='+str(target)]+[str(p) for p in schemas],check=True)
    value=descriptor_pb2.FileDescriptorSet.FromString(target.read_bytes())
    return {p.name:p for p in value.file}
old=descriptors(oldsource,'before');new=descriptors(source,'after')
assert set(new)-set(old)=={'compositor_native.proto'} and set(old)<=set(new)
message=next(m for m in new['launch.proto'].message_type if m.name=='AuthoritySupervisorConfig')
field=next(f for f in message.field if f.name=='compositor_native_target_fd')
assert field.number==12 and field.proto3_optional and field.type==descriptor_pb2.FieldDescriptorProto.TYPE_UINT32
assert field.oneof_index==len(message.oneof_decl)-1 and message.oneof_decl[field.oneof_index].name=='_compositor_native_target_fd'
message.field.remove(field);del message.oneof_decl[-1]
assert all(old[n].SerializeToString(deterministic=True)==new[n].SerializeToString(deterministic=True) for n in old)
native=new['compositor_native.proto'];assert native.package=='spatial.render.v1' and len(native.message_type)==1
assert [(f.name,f.number) for f in native.message_type[0].field]==[
    ('protocol_version',1),('display_number',2),('compositor_cookie',3),('server_id',4),('server_pid',5),('server_uid',6)]
(out/'schema-audit.json').write_text(json.dumps(dict(existing_files=len(old),existing_descriptors_unchanged=True,
    added_optional_field='AuthoritySupervisorConfig.compositor_native_target_fd',field_number=12,
    added_message='spatial.render.v1.NativeCompositorConfig'),indent=2)+'\n')
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
pattern='^(authority-(broker-handoff|broker-handoff-regressions|supervisor|native-config|participant-relay|owned-participant|launch-coordinator|prepared-launch|process-scope|native-scope)|native-application-launch)$'
all_focused='^(authority-(world-handoff|world-handoff-regressions|broker-handoff|broker-handoff-regressions|supervisor|native-config|participant-relay|owned-participant|launch-coordinator|prepared-launch|process-scope|native-scope)|native-application-launch)$'
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
        ('handoff',['ctest','--test-dir',str(build),'-V','-R','^authority-world-handoff$']),
        ('regressions',['ctest','--test-dir',str(build),'-V','-R','^authority-world-handoff-regressions$']),
        ('focused',['ctest','--test-dir',str(build),'-V','-R',pattern])]
    results=[]
    for name,command in commands:
        audit()
        with (target/(name+'.log')).open('xb') as log:
            run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode))
        (target/'run.json').write_text(json.dumps(dict(host=host,source_files=290,results=results,server=server),indent=2)+'\n')
        print(host,profile,name,run.returncode,flush=True)
        if run.returncode:
            print((target/(name+'.log')).read_text()[-18000:],flush=True);raise SystemExit(run.returncode)
    profiles.append((profile,target,build,env,results))
# Finish both focused profiles before the longer cumulative suites.
for profile,target,build,env,results in profiles:
    commands=[('core',['ctest','--test-dir',str(build),'-V','-E',all_focused]),
        ('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(target/'P10.json')])]
    for name,command in commands:
        audit()
        with (target/(name+'.log')).open('xb') as log:
            run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode))
        (target/'run-full.json').write_text(json.dumps(dict(host=host,source_files=290,results=results,server=server),indent=2)+'\n')
        print(host,profile,name,run.returncode,flush=True)
        if run.returncode:
            print((target/(name+'.log')).read_text()[-18000:],flush=True);raise SystemExit(run.returncode)
audit();print(host,'full world handoff qualification complete',flush=True)
