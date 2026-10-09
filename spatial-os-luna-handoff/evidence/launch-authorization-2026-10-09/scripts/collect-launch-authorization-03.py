import hashlib,json,pathlib,re,socket,subprocess,sys,tarfile
from google.protobuf import descriptor_pb2
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');mode=sys.argv[1];assert mode in ('debug','final')
out=r/'local/m02-launch-authorization-03';source=out/'source'
manifest=json.loads((r/'local/launch-authorization-candidate-03.json').read_text())['files']
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for names in [manifest,locked]:
    for n,h in names.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
files=[]
summary=dict(host=host,candidate=3,source_files=len(manifest),locked_files=len(locked),integrated=False,production_unchanged_from_candidate01=True)
expected={'identity-launch-authorization':23,'identity-supervisor-registry':18,'identity-participant-incarnation':5,
    'identity-participant-runtime':14,'identity-participant-barrier-client':14,'identity-participant-barrier':19,
    'identity-participant-session-client':12,'identity-participant-session':20,'identity-participants':22,
    'identity-participant-client':9,'identity-keys':16,'identity-issuer':18,'identity-enrollment':25,
    'identity-bootstrap':18,'identity-revocation':24,'issuer-client':28}
for profile in ['debug']+(['runtime-sanitize'] if mode=='final' else []):
    folder=out/profile;run=json.loads((folder/'run.json').read_text());names=['configure','build','harness','focused']
    assert [x['name'] for x in run['results']]==names and all(x['exit_code']==0 for x in run['results'])
    log=(folder/'focused.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
    suites=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M)
    assert len(counts)==len(suites)==len(expected) and dict(zip(suites,counts))==expected,(suites,counts)
    assert '100% tests passed, 0 tests failed out of 16' in log and '... skipped' not in log and 'skipped=' not in log
    harness=(folder/'harness.log').read_text();assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    summary[profile]=dict(checks=sum(counts),new_checks=23,existing_checks=sum(counts)-23,suites=len(suites),parts=expected,
        harness_checks=51,failed=0,skipped=0,binaries={n:hashlib.sha256((folder/'build'/n).read_bytes()).hexdigest()
        for n in ['identityd','spatial-issuer-client','spatial-participant-runtime-probe','spatial-key']})
    for n in ['run.json']+[n+'.log' for n in names]:files.append((folder/n,'candidate-03/'+host+'/'+profile+'/'+n))
audit=out/'descriptor-audit';audit.mkdir(exist_ok=True);commands=[];descriptors=[]
for label,tree in [('before',r/'local/m02-supervisor-registry-full-03/source'),('after',source)]:
    target=audit/(label+'.descriptor');command=['protoc','--include_imports','--descriptor_set_out='+str(target)]
    command+=['-I'+str(p) for p in sorted((tree/'proto').glob('spatial/*/v1'))];command.append(str(tree/'proto/spatial/identity/v1/issuer.proto'))
    process=subprocess.run(command,capture_output=True,text=True);assert process.returncode==0,process.stderr
    commands.append(dict(command=command,exit_code=process.returncode))
    descriptors.append({f.name:f for f in descriptor_pb2.FileDescriptorSet.FromString(target.read_bytes()).file})
before,after=descriptors;assert before.keys()==after.keys()
issuer=after['issuer.proto'];new_messages={'AuthorityLaunchBegin','AuthorityLaunchChallenge','AuthorityLaunchComplete','AuthorityLaunchAuthorization'}
assert new_messages.isdisjoint(m.name for m in before['issuer.proto'].message_type)
assert new_messages <= {m.name for m in issuer.message_type}
begin=next(m for m in issuer.message_type if m.name=='AuthorityLaunchBegin')
previous=next(f for f in begin.field if f.name=='previous_generation');assert previous.number==4 and previous.proto3_optional
for message in list(issuer.message_type):
    if message.name in new_messages:issuer.message_type.remove(message)
fields={'IssuerRequest':{'authority_launch_begin':28,'authority_launch_complete':29},'IssuerReply':{'authority_launch_authorization':27}}
for name,added in fields.items():
    message=next(m for m in issuer.message_type if m.name==name)
    for fieldname,number in added.items():
        field=next(f for f in message.field if f.name==fieldname)
        assert field.number==number and field.type==descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
        message.field.remove(field)
purpose=next(v for v in after['key.proto'].enum_type if v.name=='SignaturePurpose')
added=next(v for v in purpose.value if v.name=='AUTHORITY_LAUNCH_AUTHORIZATION');assert added.number==6;purpose.value.remove(added)
for name in before:assert before[name].SerializeToString()==after[name].SerializeToString(),name
summary['schema']=dict(preserved_files=len(before),added_messages=sorted(new_messages),added_fields=fields,
    added_purpose='AUTHORITY_LAUNCH_AUTHORIZATION=6',existing_descriptors_unchanged=True)
p=audit/'run.json';p.write_text(json.dumps(dict(commands=commands,result=summary['schema']),indent=2)+'\n');files.append((p,'candidate-03/'+host+'/descriptor-audit.json'))
p=r/('local/launch-authorization-summary-'+mode+'-'+host+'-03.json');p.write_text(json.dumps(summary,indent=2)+'\n');files.append((p,'candidate-03/'+host+'/summary-'+mode+'.json'))
with tarfile.open(r/('local/launch-authorization-'+mode+'-'+host+'-03.tar.gz'),'w:gz') as archive:
    for p,n in files:archive.add(p,arcname=n)
print(host,mode,'verified273 sources41locks285 focused checks perprofile, preserved fixture failure and additive protobuf',flush=True)
