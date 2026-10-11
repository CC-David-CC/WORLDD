import hashlib,json,os,socket,subprocess,tarfile
from pathlib import Path
assert os.getuid()==0 and socket.gethostname().split('.')[0] in ('sb16','sb16-2')
host=socket.gethostname().split('.')[0]
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m03-git-storage-20261011'
regression=json.loads((root/'io-device-regressions-after/review.json').read_text())
assert regression['status']=='passed'
integration='m03-git-storage-integration-'+('09' if host=='sb16' else '08')
receipt=json.loads((base/integration/'review.json').read_text());assert receipt['status']=='passed'
selected=[]
for name in ('io-device-resume-before','io-device-regressions-after'):
 selected.extend(p for p in (root/name).rglob('*') if p.is_file() and p.suffix in ('.json','.log','.pb'))
selected.extend(root/name for name in ('io-device-build-after.log','io-device-build-after-02.log','io-device-build-after-03.log'))
for name in ('m03-git-storage-integration-08',integration):
 if (base/name).exists():selected.extend(p for p in (base/name).iterdir() if p.is_file() and p.suffix in ('.json','.log'))
for name in ('m03-git-storage-integration-07','m03-storage-backend-01','m03-storage-backend-02'):
 p=base/name/'volume-archive.json'
 if p.is_file():selected.append(p)
for name in ('CMakeLists.txt','src/workspace/resources.cpp','src/workspace/resources.hpp','src/workspace/git_command.cpp','src/workspace/git_command.hpp','src/workspace/store.cpp'):
 selected.append(root/'source'/name)
selected.append(Path(__file__))
target=base/f'm03-git-io-device-{host}-evidence.tar.gz'
pins={p.relative_to(base).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(selected))}
manifest=root/'io-correction-evidence-sha256.json';manifest.write_text(json.dumps(pins,indent=2)+'\n')
with tarfile.open(target,'w:gz') as tar:
 for p in sorted(set(selected+[manifest])):tar.add(p,arcname=p.relative_to(base).as_posix(),recursive=False)
os.chown(target,1000,1000)
print(json.dumps(dict(host=host,files=len(pins),sha256=hashlib.sha256(target.read_bytes()).hexdigest(),archive=str(target))))
