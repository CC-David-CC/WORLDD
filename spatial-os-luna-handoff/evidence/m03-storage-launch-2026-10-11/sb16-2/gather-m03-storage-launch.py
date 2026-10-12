import hashlib,json,os,socket,subprocess,tarfile
from pathlib import Path
assert os.getuid()==0 and socket.gethostname().split('.')[0] in ('sb16','sb16-2')
host=socket.gethostname().split('.')[0]
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m03-git-storage-20261011'
regression=json.loads((root/'storage-launch-resource-regressions/review.json').read_text())
assert regression['status']=='passed'
for name,digest in regression['source_hashes'].items():
 assert hashlib.sha256((root/'source'/name).read_bytes()).hexdigest()==digest,name
attempt='m03-git-storage-integration-'+('15' if host=='sb16' else '14')
receipt=json.loads((base/attempt/'review.json').read_text())
assert receipt['status']=='passed' and len(receipt['results'])==5 and len(receipt['owner_kernel_witnesses'])==2
assert not receipt['mounted'] and receipt['unmount']['exit_code']==0 and receipt['detach']['exit_code']==0
assert all('ActiveState=inactive\n' in value for value in receipt['owner_unit_cleanup'].values())
assert not subprocess.check_output(['losetup','-j',str(base/attempt/'volume.img')])
assert hashlib.sha256((root/'test_storage_git_integration.py').read_bytes()).hexdigest()==receipt['runner_sha256']
install=json.loads((root/'storage-owner-install-01.json').read_text())
assert install['sha256']==hashlib.sha256(Path('/usr/local/libexec/spatial-storage-owner').read_bytes()).hexdigest()
selected=list(p for p in (root/'storage-launch-resource-regressions').rglob('*') if p.is_file() and p.suffix in ('.json','.log','.pb'))
selected.extend(root.glob('storage-launch-build-*.log'))
selected.extend([root/'storage-owner-install-01.json',root/'test_storage_git_integration.py',root/'bootstrap_storage.py'])
for number in range(10,16):
 directory=base/f'm03-git-storage-integration-{number:02}'
 if directory.exists(): selected.extend(p for p in directory.iterdir() if p.is_file() and p.suffix in ('.json','.log'))
selected.extend(p for p in base.glob('m03-storage-owner-runtime-*/volume-archive.json') if p.is_file())
for name in ('CMakeLists.txt','proto/spatial/workspace/v1/storage.proto','src/supervisor/storage_launch.cpp',
             'src/supervisor/storage_launch.hpp','src/testing/storage_launch_probe.cpp','src/workspace/resources.cpp',
             'src/workspace/storage_main.cpp','src/workspace/storage_channel.cpp','src/workspace/storage_channel.hpp'):
 selected.append(root/'source'/name)
selected.append(Path(__file__))
pins={p.relative_to(base).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(selected))}
manifest=root/'storage-launch-evidence-sha256.json';manifest.write_text(json.dumps(pins,indent=2)+'\n')
target=base/f'm03-storage-launch-{host}-evidence.tar.gz'
with tarfile.open(target,'w:gz') as tar:
 for p in sorted(set(selected+[manifest])):tar.add(p,arcname=p.relative_to(base).as_posix(),recursive=False)
os.chown(target,1000,1000)
print(json.dumps(dict(host=host,files=len(pins),sha256=hashlib.sha256(target.read_bytes()).hexdigest(),archive=str(target))))
