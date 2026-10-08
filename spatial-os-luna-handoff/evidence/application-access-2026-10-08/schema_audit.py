import pathlib,subprocess,json,hashlib
from google.protobuf import descriptor_pb2 as pb
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=p/'local/m04-application-access-schema-audit-01';out.mkdir(exist_ok=False)
base='e69fd6d5b7bf53b1f5f1e4346bf9f37d4a83bb19'
for mode in ['before','after']:
 d=out/mode;d.mkdir()
 data=subprocess.check_output(['git','show',base+':spatial-os-luna-handoff/proto/spatial/workspace/v1/broker.proto'],cwd=p) if mode=='before' else (p/'proto/spatial/workspace/v1/broker.proto').read_bytes()
 (d/'broker.proto').write_bytes(data)
 cmd=['protoc','-I'+str(d),*['-I'+str(p/'proto/spatial'/kind/'v1') for kind in ['core','authority','security']],'--descriptor_set_out='+str(out/(mode+'.pb')),str(d/'broker.proto')]
 subprocess.run(cmd,check=True)
old=pb.FileDescriptorSet.FromString((out/'before.pb').read_bytes()).file[0]
new=pb.FileDescriptorSet.FromString((out/'after.pb').read_bytes()).file[0]
assert old.package==new.package and old.syntax==new.syntax and old.dependency==new.dependency
count=0
for a in old.message_type:
 b=next(m for m in new.message_type if m.name==a.name)
 assert a.oneof_decl==b.oneof_decl and a.enum_type==b.enum_type and a.nested_type==b.nested_type,a.name
 for field in a.field:
  assert field==next(f for f in b.field if f.number==field.number),a.name+'.'+field.name
  count+=1
expected={'ApplicationRef','ApplicationAccessRequest','ApplicationAccess','ApplicationAccessReply'}
assert {m.name for m in new.message_type}-{m.name for m in old.message_type}==expected
oldlock=subprocess.check_output(['git','show',base+':spatial-os-luna-handoff/acceptance.lock.json'],cwd=p)
assert oldlock==(p/'acceptance.lock.json').read_bytes()
result={'base_commit':base,'existing_messages_preserved':len(old.message_type),'existing_fields_preserved':count,'new_messages':sorted(expected),'acceptance_lock_sha256':hashlib.sha256(oldlock).hexdigest(),'acceptance_ids':len(json.loads(oldlock)['test_ids']),'status':'passed'}
assert result['acceptance_ids']==151
(out/'run.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
