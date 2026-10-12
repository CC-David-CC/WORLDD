from pathlib import Path
import hashlib,json,os,subprocess,tarfile
root=Path('/home/dflanag3/src/spacial-m06-retained-recovery-20261012')
source=root/'spatial-os-luna-handoff';out=root/'local/m06-transition-combined'
assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()=='b7b684e1b83e3f67fa8237968759e1980f79685e'
assert not subprocess.check_output(['git','-C',str(root),'diff','--name-only'],text=True).strip()
paths=[]
for name in subprocess.check_output(['git','-C',str(root),'ls-files','-z','spatial-os-luna-handoff'],text=True).split(chr(0)):
 if not name:continue
 p=root/name;r=p.relative_to(source)
 if r.parts[0] in {'tests','harness','tools','config','native','proto','src','docs'} or r.name=='CMakeLists.txt':paths.append(p)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
(out/'source.sha256').write_text(''.join(sha(p)+'  '+str(p.relative_to(root))+chr(10) for p in paths))
bins=['spatial-actor-transition-integration-driver','spatial-actor-transition-recovery-probe','spatial-host-actor-native-runner','spatial-authority-launch-probe','spatial-authority-supervisor','spatial-security-auth','identityd','spatial-issuer-client','spatial-storage-broker-launch-probe','spatial-storage-owner','spatial-process-scope-probe','spatial-participant-runtime-probe','spatial-key','spatial-app-init','spatial-confined-xprobe','worldd','spatialwm','workspaced']
archives=[]
for profile in ('debug','asan'):
 build=out/('build-'+profile);files=[];deps={}
 for name in bins:
  p=build/name;assert p.is_file() and os.access(p,os.X_OK),p
  files.append((p,'bin/'+name));deps[name]=subprocess.check_output(['ldd',str(p)],text=True);assert 'not found' not in deps[name]
 files.append((build/'runtime.textproto','bin/runtime.textproto'))
 for p in (build/'python').rglob('*.py'):files.append((p,'bin/python/'+str(p.relative_to(build/'python'))))
 for p in paths:
  if p.relative_to(source).parts[0] in {'tests','harness','tools','config','native','proto'}:files.append((p,'source/'+str(p.relative_to(source))))
 files += [(out/'source-commit.txt','source-commit.txt'),(out/'source.sha256','source.sha256')]
 (out/(profile+'-dependencies.json')).write_text(json.dumps(deps,indent=2)+chr(10));files.append((out/(profile+'-dependencies.json'),'DEPENDENCIES.json'))
 manifest=out/(profile+'-files.sha256');manifest.write_text(''.join(sha(p)+'  '+n+chr(10) for p,n in files))
 archive=out/(profile+'-bundle.tar.gz')
 with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
  for p,n in files:tar.add(p,arcname=profile+'/'+n,recursive=False)
  tar.add(manifest,arcname=profile+'/SHA256SUMS',recursive=False)
 archives.append(archive)
(out/'archives.sha256').write_text(''.join(sha(p)+'  '+p.name+chr(10) for p in archives))
print((out/'archives.sha256').read_text())