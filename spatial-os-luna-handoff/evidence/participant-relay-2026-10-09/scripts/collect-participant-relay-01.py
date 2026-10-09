import hashlib,json,pathlib,re,socket,subprocess,sys,tarfile
from google.protobuf import descriptor_pb2
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');mode=sys.argv[1];assert mode in ('debug','final','failure')
out=r/'local/m02-participant-relay-01';source=out/'source'
manifest=json.loads((r/'local/participant-relay-candidate-01.json').read_text())['files'];locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for names in [manifest,locked]:
    for n,h in names.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
expected={'identity-launch-authorization':23,'identity-supervisor-registry':18,'identity-participant-incarnation':5,
    'identity-participant-runtime':14,'identity-participant-channel':17,'identity-participant-barrier-client':14,'identity-participant-barrier':19,
    'identity-participant-session-client':12,'identity-participant-session':20,'identity-participants':22,
    'identity-participant-client':9,'identity-keys':16,'identity-issuer':18,'identity-enrollment':25,
    'identity-bootstrap':18,'identity-revocation':24,'issuer-client':28,
    'authority-participant-relay':10,'authority-owned-participant':11,'authority-launch-coordinator':14,'authority-prepared-launch':8,
    'authority-process-scope':10,'authority-native-scope':11}
files=[];summary=dict(host=host,candidate=1,source_files=len(manifest),locked_files=len(locked),integrated=False,mode=mode)
for profile in ['debug']+(['runtime-sanitize'] if mode in ('final','failure') and (out/'runtime-sanitize/run.json').exists() else []):
    folder=out/profile;run=json.loads((folder/'run.json').read_text())
    if mode!='failure':
        assert [x['name'] for x in run['results']]==['configure','build','harness','focused'] and all(x['exit_code']==0 for x in run['results'])
        log=(folder/'focused.log').read_text()
        counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)];suites=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M)
        assert len(counts)==len(suites)==23 and dict(zip(suites,counts))==expected,(suites,counts)
        assert '100% tests passed, 0 tests failed out of 23' in log and '... skipped' not in log and 'skipped=' not in log
        harness=(folder/'harness.log').read_text();assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        summary[profile]=dict(checks=sum(counts),suites=23,new_checks=10,existing_checks=356,parts=expected,harness_checks=51,failed=0,skipped=0)
    else:summary[profile]=run
    for n in ['run.json','configure.log','build.log','harness.log','focused.log']:
        if (folder/n).exists():files.append((folder/n,'candidate-01/'+host+'/'+profile+'/'+n))
audit=out/'descriptor-audit';audit.mkdir(exist_ok=True);descriptors=[];commands=[]
for label,tree in [('before',r/'local/m02-participant-channel-02/source'),('after',source)]:
    target=audit/(label+'.descriptor');command=['protoc','--include_imports','--descriptor_set_out='+str(target)]
    command+=['-I'+str(p) for p in sorted((tree/'proto').glob('spatial/*/v1'))]
    command+=[str(tree/'proto/spatial/supervisor/v1/launch.proto')]
    process=subprocess.run(command,capture_output=True,text=True);assert process.returncode==0,process.stderr
    commands.append(dict(command=command,exit_code=process.returncode))
    descriptors.append({f.name:f for f in descriptor_pb2.FileDescriptorSet.FromString(target.read_bytes()).file})
before,after=descriptors;assert before.keys()==after.keys()
launch=after['launch.proto']
launch.message_type.remove(next(m for m in launch.message_type if m.name=='AuthorityParticipantChannelPrepare'))
for name,fields in [('AuthorityLaunchRequest',{'prepare_channel':18,'pump_channel':19}),('AuthorityLaunchReply',{'participant_config':13})]:
    message=next(m for m in launch.message_type if m.name==name)
    for fieldname,number in fields.items():
        field=next(f for f in message.field if f.name==fieldname);assert field.number==number;message.field.remove(field)
for name in before:assert before[name].SerializeToString()==after[name].SerializeToString(),name
summary['schema']=dict(existing_descriptors_unchanged=True,added_message='AuthorityParticipantChannelPrepare',added_request_fields={'prepare_channel':18,'pump_channel':19},added_reply_fields={'participant_config':13})
p=audit/'run.json';p.write_text(json.dumps(dict(commands=commands,result=summary['schema']),indent=2)+'\n');files.append((p,'candidate-01/'+host+'/descriptor-audit.json'))
p=out/('summary-'+mode+'.json');p.write_text(json.dumps(summary,indent=2)+'\n');files.append((p,'candidate-01/'+host+'/summary-'+mode+'.json'))
if mode=='failure':assert any(x['exit_code']!=0 for profile in ['debug','runtime-sanitize'] if profile in summary for x in summary[profile]['results'])
with tarfile.open(r/('local/participant-relay-'+mode+'-'+host+'-01.tar.gz'),'w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print(host,mode,'verified277sources41locks and additive protobuf; result',summary['debug'],flush=True)
