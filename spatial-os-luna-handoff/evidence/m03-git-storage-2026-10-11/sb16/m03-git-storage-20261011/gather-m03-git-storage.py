import hashlib,json,os,socket,subprocess,tarfile
from pathlib import Path
assert os.getuid()==0 and socket.gethostname().split('.')[0] in ('sb16','sb16-2')
host=socket.gethostname().split('.')[0]
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
names=['m03-retirement-identity-before','m03-git-output-before','m03-git-storage-focused-after']
names += [f'm03-git-storage-integration-{i:02}' for i in range(2,8)]
root=base/'m03-git-storage-20261011'
receipt=json.loads((root/'regressions/review.json').read_text())
assert receipt['status'] in ('passed','failed')
selected=[]
for name in names:
    directory=base/name
    if directory.exists():
        selected.extend(p for p in directory.iterdir() if p.is_file() and p.suffix in ('.json','.log'))
selected.extend(p for p in root.iterdir() if p.is_file() and p.suffix in ('.json','.log','.py'))
for name in ('initializer-evidence','regressions','regressions-02','io-device-before'):
    selected.extend(p for p in (root/name).rglob('*') if p.is_file() and p.suffix in ('.json','.log','.pb'))
kernel=subprocess.run(['journalctl','-k','--since=2026-10-11','--grep=spatial-m03-storage-integration|^file|^anon|^kernel|Memory cgroup out of memory','--no-pager'],capture_output=True,text=True)
path=root/'kernel-memory-evidence.log';path.write_text(kernel.stdout);selected.append(path)
state=subprocess.run(['findmnt','--json','--types','btrfs'],capture_output=True,text=True)
loops=subprocess.run(['losetup','--list','--json'],capture_output=True,text=True)
cleanup=root/'final-host-storage-state.json'
cleanup.write_text(json.dumps(dict(findmnt_exit=state.returncode,findmnt=state.stdout,loops=loops.stdout),indent=2)+'\n')
selected.append(cleanup)
pins={}
for path in sorted(set(selected)):
    pins[path.relative_to(base).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
manifest=root/'evidence-sha256.json';manifest.write_text(json.dumps(pins,indent=2)+'\n')
target=base/f'm03-git-storage-{host}-evidence.tar.gz'
with tarfile.open(target,'w:gz') as tar:
    for path in sorted(set(selected+[manifest])):
        tar.add(path,arcname=path.relative_to(base).as_posix(),recursive=False)
os.chown(target,1000,1000)
print(json.dumps(dict(host=host,files=len(pins),archive=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest())))
