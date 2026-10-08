import pathlib,tarfile
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
with tarfile.open(p/'local/surface-identity-migration.tar') as t:t.extractall(p,filter='data')

import pathlib,subprocess,json
from google.protobuf import descriptor_pb2 as pb
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=p/'local/m04-surface-identity-schema-audit-01';out.mkdir(exist_ok=False)
names={'core.proto':'proto/spatial/core/v1/core.proto','input.proto':'proto/spatial/input/v1/input.proto','security.proto':'proto/spatial/security/v1/security.proto'}
for mode in ['before','after']:
 d=out/mode;d.mkdir()
 for name,path in names.items():
  data=subprocess.check_output(['git','show','972b61f:spatial-os-luna-handoff/'+path],cwd=p) if mode=='before' else (p/path).read_bytes()
  (d/name).write_bytes(data)
 subprocess.run(['protoc','-I'+str(d),'--descriptor_set_out='+str(out/(mode+'.pb')),*(str(d/name) for name in names)],check=True)
before=pb.FileDescriptorSet.FromString((out/'before.pb').read_bytes())
after=pb.FileDescriptorSet.FromString((out/'after.pb').read_bytes())
counts={'existing_fields':0,'existing_enum_values':0,'existing_messages':0}
def check_message(a,b):
 counts['existing_messages']+=1
 assert list(a.oneof_decl)==list(b.oneof_decl),a.name
 fields={f.number:f for f in b.field}
 for f in a.field:
  assert f.number in fields and f==fields[f.number],a.name+'.'+f.name
  counts['existing_fields']+=1
 for m in a.nested_type:check_message(m,next(n for n in b.nested_type if n.name==m.name))
 for e in a.enum_type:check_enum(e,next(n for n in b.enum_type if n.name==e.name))
def check_enum(a,b):
 for v in a.value:
  assert any(w==v for w in b.value),a.name+'.'+v.name
  counts['existing_enum_values']+=1
for a in before.file:
 b=next(x for x in after.file if x.name==a.name)
 assert a.package==b.package and a.syntax==b.syntax,a.name
 for m in a.message_type:check_message(m,next(n for n in b.message_type if n.name==m.name))
 for e in a.enum_type:check_enum(e,next(n for n in b.enum_type if n.name==e.name))
old=json.loads(subprocess.check_output(['git','show','972b61f:spatial-os-luna-handoff/acceptance.lock.json'],cwd=p))
new=json.loads((p/'acceptance.lock.json').read_text())
assert old['test_ids']==new['test_ids'] and len(new['test_ids'])==151
assert {k for k,v in old['files'].items() if new['files'][k]!=v}=={'proto/spatial/core/v1/core.proto'}
assert set(new['files'])-set(old['files'])=={'proto/spatial/security/v1/security.proto'}
counts.update(acceptance_ids=151,status='passed')
(out/'run.json').write_text(json.dumps(counts,indent=2)+'\n')
print(json.dumps(counts),flush=True)

import os,json,subprocess,pathlib,socket,hashlib
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
binary=json.loads((p/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
for build,sanitize,label in [('build','OFF','debug'),('build-sanitize','ON','runtime-sanitize')]:
 out=p/('local/m04-surface-identity-'+label+'-03');out.mkdir(exist_ok=False)
 env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(p/build/'python')+':'+str(p),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),SPATIAL_SUT=json.dumps([str(p/build/'spatial-test-host')]),SPATIAL_XPROBE=str(p/build/'spatial-xprobe'))
 commands=[('configure',['cmake','-S','.','-B',build,'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS='+sanitize,'-DSPATIAL_INPUT_TESTS=ON']),('build',['cmake','--build',build,'--parallel','2']),('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('core',['ctest','--test-dir',build,'-V']),('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]
 results=[]
 for name,cmd in commands:
  with (out/(name+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=p,env=env,stdout=log,stderr=subprocess.STDOUT)
  results.append({'name':name,'command':cmd,'exit_code':r.returncode})
  (out/'run.json').write_text(json.dumps({'host':socket.gethostname(),'results':results,'binary':binary,'broker_sha256':hashlib.sha256((p/build/'workspaced').read_bytes()).hexdigest()},indent=2)+'\n')
  print(label,name,r.returncode,flush=True)
  if r.returncode: print((out/(name+'.log')).read_text()[-9000:]); raise SystemExit(1)

import os,json,pathlib,subprocess
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=p/'local/m04-surface-identity-native-sanitize-01';out.mkdir(exist_ok=False)
binary=json.loads((p/'local/m04-surface-identity-sanitize-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(p/'build/python')+':'+str(p),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_SECURITY_TEST_CORE_ONLY='1',ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
results=[]
for name in ['surface_identity','native_launch','actor_binding','application_input','device_administration','global_isolation','selection_isolation','extension_isolation','resource_isolation','security_identity']:
 cmd=['python3','tests/native/test_'+name+'.py',str(p/'build/spatial-security-auth')]
 with (out/(name+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=p,env=env,stdout=log,stderr=subprocess.STDOUT)
 results.append({'name':name,'command':cmd,'exit_code':r.returncode})
 (out/'run.json').write_text(json.dumps({'results':results,'binary':binary,'ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS']},indent=2)+'\n')
 print(name,r.returncode,flush=True)
 if r.returncode:print((out/(name+'.log')).read_text()[-5000:]);raise SystemExit(r.returncode)

