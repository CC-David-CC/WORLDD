import hashlib,json,pathlib,re,socket,subprocess,tarfile
from google.protobuf import descriptor_pb2
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
base=root/'local/m02-participant-barrier-source-04';draft=root/'local/m02-participant-runtime-draft-04';source=draft/'source'
old=json.loads((root/'local/participant-barrier-candidate-04.json').read_text());new=json.loads((root/'local/participant-runtime-candidate-04.json').read_text())
for folder,manifest in [(base,old),(source,new)]:
    for n,h in manifest['files'].items():assert hashlib.sha256((folder/n).read_bytes()).hexdigest()==h,n
changed=[n for n in old['files'] if old['files'][n]!=new['files'][n]];assert sorted(changed)==['CMakeLists.txt','proto/spatial/identity/v1/issuer.proto']
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for n,h in locked.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
out=draft/'audit';out.mkdir();commands=[]
def descriptors(folder,label):
    schemas=sorted((folder/'proto').rglob('*.proto'));dest=out/(label+'.descriptor')
    command=['protoc','--include_imports','--descriptor_set_out='+str(dest),*['-I'+str(d) for d in sorted({p.parent for p in schemas})],*[str(p) for p in schemas]]
    r=subprocess.run(command,capture_output=True,text=True);assert r.returncode==0,r.stderr;commands.append(dict(command=command,exit_code=r.returncode))
    files=descriptor_pb2.FileDescriptorSet.FromString(dest.read_bytes());messages={};enums={}
    def enum(v,prefix):enums[prefix+'.'+v.name]={x.name:x.number for x in v.value}
    def message(v,prefix):
        name=prefix+'.'+v.name;messages[name]={f.number:f.SerializeToString().hex() for f in v.field}
        for x in v.nested_type:message(x,name)
        for x in v.enum_type:enum(x,name)
    for f in files.file:
        for v in f.message_type:message(v,f.package)
        for v in f.enum_type:enum(v,f.package)
    return messages,enums
before,before_enum=descriptors(base,'before');after,after_enum=descriptors(source,'after')
for n,fields in before.items():
    for number,value in fields.items():assert after[n][number]==value,(n,number)
for n,values in before_enum.items():
    for name,number in values.items():assert after_enum[n][name]==number,(n,name)
assert set(after)-set(before)=={'spatial.identity.v1.AuthorityParticipantRuntimeConfig','spatial.identity.v1.ParticipantCommand','spatial.identity.v1.ParticipantUpdate'}
summary=dict(host=host,source_files=len(new['files']),locked_files=len(locked),preserved_messages=len(before),preserved_fields=sum(len(f) for f in before.values()),preserved_enum_values=sum(len(v) for v in before_enum.values()),new_messages=3,commands=commands,scope='focused worker and related issuer regression; world and broker integration pending')
files=[]
for label in ['debug','runtime-sanitize']:
    folder=draft/label;build=folder/'build';run=json.loads((folder/'run.json').read_text());assert [r['name'] for r in run['results']]==['configure','build','runtime','regression'] and all(r['exit_code']==0 for r in run['results'])
    for part,checks,suites in [('runtime',14,1),('regression',96,6)]:
        log=(folder/(part+'.log')).read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)];assert len(counts)==suites and sum(counts)==checks and f'100% tests passed, 0 tests failed out of {suites}' in log and 'skipped' not in log
    summary[label]=dict(new_checks=14,regression_checks=96,failed=0,skipped=0,binaries={n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in ['identityd','spatial-issuer-client','spatial-participant-runtime-probe']})
    for name in ['configure.log','build.log','runtime.log','regression.log','run.json']:files.append((folder/name,label+'/'+name))
summarypath=out/'summary.json';summarypath.write_text(json.dumps(summary,indent=2)+'\n');files.extend([(summarypath,'summary.json'),(root/'local/participant-runtime-candidate-04.json','source-manifest.json')])
with tarfile.open(root/('local/participant-runtime-focused-'+host+'-04.tar.gz'),'w:gz') as archive:
    for path,name in files:archive.add(path,arcname='candidate-04/'+host+'/'+name)
print(host,'14new+96regression perprofile; schema preserved',len(before),'messages;',len(locked),'locked files',flush=True)
