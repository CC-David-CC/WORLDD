import hashlib, json, pathlib, sys
from google.protobuf import descriptor_pb2

p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
manifest=json.loads((p/'local/bootstrap-tested-tree-02.json').read_text())
for name, digest in manifest['files'].items():
    assert hashlib.sha256((p/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest, name
sys.path.insert(0,str(p/'build/python'))
import issuer_pb2
old=descriptor_pb2.FileDescriptorProto.FromString((p/'local/m01-bootstrap-baseline-01/issuer-before.descriptor').read_bytes())
new=descriptor_pb2.FileDescriptorProto.FromString(issuer_pb2.DESCRIPTOR.serialized_pb)
counts={'messages':0,'fields':0,'enum_values':0}
def compare(a,b):
    bm={x.name:x for x in b.message_type} if hasattr(b,'message_type') else {x.name:x for x in b.nested_type}
    am=a.message_type if hasattr(a,'message_type') else a.nested_type
    for message in am:
        other=bm[message.name]; counts['messages']+=1
        assert list(message.oneof_decl)==list(other.oneof_decl),message.name
        fields={f.number:f for f in other.field}
        for field in message.field:
            assert fields[field.number]==field,(message.name,field.name)
            counts['fields']+=1
        compare(message,other)
    enums={e.name:e for e in b.enum_type}
    for enum in a.enum_type:
        values={v.name:v.number for v in enums[enum.name].value}
        for value in enum.value:
            assert values[value.name]==value.number
            counts['enum_values']+=1
compare(old,new)
result=dict(status='passed',source_files=len(manifest['files']),preserved=counts)
(p/'local/m01-bootstrap-audit-02.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
