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

