import hashlib,json,pathlib,re,socket,subprocess,sys,tarfile
from google.protobuf import descriptor_pb2
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');mode=sys.argv[1];assert mode in ('debug','final')
source=r/'local/m02-participant-incarnation-01/source';out=r/'local/m02-participant-incarnation-02'
manifest=json.loads((r/'local/participant-incarnation-candidate-01.json').read_text())['files'];locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for names in [manifest,locked]:
    for n,h in names.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
files=[];summary=dict(host=host,candidate=1,output=2,source_files=len(manifest),locked_files=len(locked),integrated=False)
base=r/'local/m02-participant-incarnation-baseline-01';run=json.loads((base/'run.json').read_text());text=(base/'baseline.log').read_text()
assert run['exit_code']==1 and 'Ran 1 test' in text and 'FAILED (failures=1)' in text and 'has no field named "service_incarnation"' in text
for n in ['run.json','baseline.log','baseline_driver.py']:files.append((base/n,'baseline-01/'+host+'/'+n))
setup=source.parent/'debug';run=json.loads((setup/'run.json').read_text());text=(setup/'harness.log').read_text()
assert [x['exit_code'] for x in run['results']]==[0,0,1] and 'Ran 30 tests' in text and 'FAILED (failures=2, errors=5)' in text
assert "No module named 'core_pb2'" in text
for n in ['run.json','configure.log','build.log','harness.log']:files.append((setup/n,'missing-bindings-01/'+host+'/'+n))
expected={'identity-participant-incarnation':5,'identity-participant-runtime':14,'identity-participant-barrier-client':14,
    'identity-participant-barrier':19,'identity-participant-session-client':12,'identity-participant-session':20}
for profile in ['debug']+(['runtime-sanitize'] if mode=='final' else []):
    folder=out/profile;run=json.loads((folder/'run.json').read_text());names=['build','harness','focused']
    if profile!='debug':names.insert(0,'configure')
    assert [x['name'] for x in run['results']]==names and all(x['exit_code']==0 for x in run['results'])
    text=(folder/'focused.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
    suites=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',text,re.M)
    assert len(counts)==len(suites)==6 and dict(zip(suites,counts))==expected
    assert '100% tests passed, 0 tests failed out of 6' in text and '... skipped' not in text and 'skipped=' not in text
    harness=(folder/'harness.log').read_text();assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    summary[profile]=dict(checks=84,new_checks=5,existing_checks=79,suites=6,harness_checks=51,failed=0,skipped=0,
        binaries={n:hashlib.sha256((pathlib.Path(run['build'])/n).read_bytes()).hexdigest() for n in ['identityd','spatial-issuer-client','spatial-participant-runtime-probe']})
    for n in ['run.json']+[n+'.log' for n in names]:files.append((folder/n,'candidate-01/'+host+'/'+profile+'/'+n))
audit=out/'descriptor-audit';audit.mkdir(exist_ok=True);commands=[];descriptors=[]
for label,tree in [('before',r/'local/m02-authority-launch-coordinator-03/source'),('after',source)]:
    target=audit/(label+'.descriptor');command=['protoc','--include_imports','--descriptor_set_out='+str(target)]
    command+=['-I'+str(p) for p in sorted((tree/'proto').glob('spatial/*/v1'))]
    command.append(str(tree/'proto/spatial/identity/v1/issuer.proto'))
    process=subprocess.run(command,capture_output=True,text=True);assert process.returncode==0,process.stderr
    commands.append(dict(command=command,exit_code=process.returncode))
    value=descriptor_pb2.FileDescriptorSet.FromString(target.read_bytes());descriptors.append({f.name:f for f in value.file})
before,after=descriptors;assert before.keys()==after.keys()
config=next(m for m in after['issuer.proto'].message_type if m.name=='AuthorityParticipantRuntimeConfig')
added=next(f for f in config.field if f.name=='service_incarnation')
assert added.number==6 and added.type==descriptor_pb2.FieldDescriptorProto.TYPE_BYTES and added.proto3_optional
assert config.oneof_decl[added.oneof_index].name=='_service_incarnation' and len(config.oneof_decl)==1
config.field.remove(added);config.ClearField('oneof_decl')
for name in before:assert before[name].SerializeToString()==after[name].SerializeToString(),name
summary['schema']=dict(preserved_files=len(before),only_added_field='AuthorityParticipantRuntimeConfig.service_incarnation=6',explicit_presence=True)
p=audit/'run.json';p.write_text(json.dumps(dict(commands=commands,result=summary['schema']),indent=2)+'\n');files.append((p,'candidate-01/'+host+'/descriptor-audit.json'))
p=r/('local/participant-incarnation-summary-'+mode+'-'+host+'-02.json');p.write_text(json.dumps(summary,indent=2)+'\n');files.append((p,'candidate-01/'+host+'/summary-'+mode+'.json'))
with tarfile.open(r/('local/participant-incarnation-'+mode+'-'+host+'-02.tar.gz'),'w:gz') as archive:
    for p,n in files:archive.add(p,arcname=n)
print(host,mode,'verified271 sources,41 locks,84 focused checks per profile and additive descriptor',flush=True)
