import ast,json,pathlib,socket,subprocess
from google.protobuf import descriptor_pb2
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
candidate=root/'local/m01-bootstrap-domain-sanitize-01/source'
out=root/'local/m01-bootstrap-domain-audit-01';out.mkdir()
counts={'messages':0,'fields':0,'enum_values':0}
def compare(a,b):
    old_messages=a.message_type if hasattr(a,'message_type') else a.nested_type
    new_messages=b.message_type if hasattr(b,'message_type') else b.nested_type
    messages={m.name:m for m in new_messages}
    for old in old_messages:
        new=messages[old.name];counts['messages']+=1
        assert list(old.oneof_decl)==list(new.oneof_decl),old.name
        fields={f.number:f for f in new.field}
        for field in old.field:
            assert fields[field.number]==field,(old.name,field.name)
            counts['fields']+=1
        compare(old,new)
    enums={e.name:e for e in b.enum_type}
    for old in a.enum_type:
        values={v.name:v.number for v in enums[old.name].value}
        for value in old.value:
            assert values[value.name]==value.number
            counts['enum_values']+=1
commands=[]
for label,source in [('before',root),('candidate',candidate)]:
    output=out/(label+'.descriptor')
    command=['protoc','--descriptor_set_out='+str(output),'--include_imports',
        '-I'+str(source/'proto/spatial/authority/v1'),'-I'+str(source/'proto/spatial/core/v1'),
        '-I'+str(source/'proto/spatial/security/v1'),
        str(source/'proto/spatial/authority/v1/grants.proto'),str(source/'proto/spatial/authority/v1/grants_probe.proto')]
    result=subprocess.run(command,capture_output=True)
    (out/(label+'.log')).write_bytes(result.stdout+result.stderr)
    commands.append({'command':command,'exit_code':result.returncode})
    assert result.returncode==0
old={f.name:f for f in descriptor_pb2.FileDescriptorSet.FromString((out/'before.descriptor').read_bytes()).file}
new={f.name:f for f in descriptor_pb2.FileDescriptorSet.FromString((out/'candidate.descriptor').read_bytes()).file}
for name in ('grants.proto','grants_probe.proto'): compare(old[name],new[name])
def methods(path):
    tree=ast.parse(path.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AuthorityTests')
    return {n.name:ast.dump(n,include_attributes=False) for n in cls.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
before=methods(root/'tests/core/test_authority.py')
assert before==methods(candidate/'tests/core/test_authority.py') and len(before)==43
result={'status':'passed','preserved':counts,'unchanged_authority_test_methods':len(before),'commands':commands}
(out/'run.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result['preserved']),'; all 43 existing authority test bodies unchanged')
