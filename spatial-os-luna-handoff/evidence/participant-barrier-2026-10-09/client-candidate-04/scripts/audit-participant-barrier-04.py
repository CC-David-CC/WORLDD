import hashlib,json,pathlib,socket,subprocess,tarfile
from google.protobuf import descriptor_pb2
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=root/'local/m02-participant-barrier-audit-04';out.mkdir();candidate=out/'source';candidate.mkdir()
with tarfile.open(root/'local/participant-barrier-candidate-04.tar') as archive:archive.extractall(candidate,filter='data')
manifest=json.loads((root/'local/participant-barrier-candidate-04.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((candidate/name).read_bytes()).hexdigest()==digest,name
counts={'messages':0,'fields':0,'enum_values':0}
def compare(old,new):
    prior=old.message_type if hasattr(old,'message_type') else old.nested_type
    current=new.message_type if hasattr(new,'message_type') else new.nested_type
    messages={m.name:m for m in current}
    for before in prior:
        after=messages[before.name];counts['messages']+=1
        assert list(before.oneof_decl)==list(after.oneof_decl),before.name
        fields={field.number:field for field in after.field}
        for field in before.field:
            assert fields[field.number]==field,(before.name,field.name);counts['fields']+=1
        compare(before,after)
    enums={e.name:e for e in new.enum_type}
    for enum in old.enum_type:
        values={v.name:v.number for v in enums[enum.name].value}
        for value in enum.value:
            assert values[value.name]==value.number;counts['enum_values']+=1
commands=[]
for label,source in [('before',root/'local/m02-participant-session-source-03'),('candidate',candidate)]:
    command=['protoc','--include_imports','--descriptor_set_out='+str(out/(label+'.descriptor'))]
    command+=['-I'+str(source/'proto/spatial'/part/'v1') for part in ('authority','core','security','identity','workspace','input')]
    command += [str(p) for p in (source/'proto').rglob('*.proto')]
    result=subprocess.run(command,capture_output=True);(out/(label+'.log')).write_bytes(result.stdout+result.stderr)
    commands.append({'command':command,'exit_code':result.returncode});assert result.returncode==0
before={f.name:f for f in descriptor_pb2.FileDescriptorSet.FromString((out/'before.descriptor').read_bytes()).file}
after={f.name:f for f in descriptor_pb2.FileDescriptorSet.FromString((out/'candidate.descriptor').read_bytes()).file}
for name,old in before.items():
    assert old.package==after[name].package and old.syntax==after[name].syntax,name
    compare(old,after[name])
locked=json.loads((candidate/'acceptance.lock.json').read_text())
for name,digest in locked['files'].items():assert hashlib.sha256((candidate/name).read_bytes()).hexdigest()==digest,name
unchanged=[];changed=[]
base=json.loads((root/'local/participant-session-candidate-03.json').read_text())['files']
prior=root/'local/m02-participant-session-source-03'
allowed={'tests/core/test_identity_bootstrap.py','tests/core/test_identity_enrollment.py','tests/core/test_identity_revocation.py','tests/core/test_identity_participants.py'}
prefix='DROP TABLE authority_obligation; DROP TABLE authority_barrier; DROP TABLE authority_activation; '
for name,digest in base.items():
    assert hashlib.sha256((prior/name).read_bytes()).hexdigest()==digest,name
    if name.startswith('tests/'):
        old=(prior/name).read_text();new=(candidate/name).read_text()
        if name in allowed:
            assert new.replace(prefix,'').replace('fetchone()[0],6)','fetchone()[0],5)').replace('fetchone()[0], 6)','fetchone()[0], 5)')==old,name
            changed.append(name)
        else:
            assert old==new,name
            unchanged.append(name)
assert set(changed)==allowed
record={'status':'passed','host':host,'source_files':len(manifest['files']),'preserved':counts,
    'unchanged_existing_test_files':len(unchanged),'migration_fixture_changes':changed,'locked_files':len(locked['files']),'commands':commands}
(out/'run.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record),flush=True)
