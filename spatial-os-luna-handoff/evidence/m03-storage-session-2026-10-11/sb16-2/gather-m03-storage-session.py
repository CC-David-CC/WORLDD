import hashlib,json,os,socket,subprocess,tarfile
from pathlib import Path
assert os.getuid()==0 and socket.gethostname().split('.')[0] in ('sb16','sb16-2')
host=socket.gethostname().split('.')[0]
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m03-git-storage-20261011';source=root/'source'
current=json.loads((root/'storage-device-regressions-01/review.json').read_text())
assert current['status']=='passed'
for name,digest in current['source_hashes'].items():
 assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
native=json.loads((root/'storage-device-native-regressions-01/review.json').read_text())
assert native['status']=='passed' and native['source_hashes']==current['source_hashes']
integration=json.loads((base/'m03-storage-device-direct-03/review.json').read_text())
assert integration['status']=='passed' and len(integration['results'])==7
assert not integration['mounted'] and not integration['loop_attached']
assert all(value=='LoadState=not-found\nActiveState=inactive' for value in integration['owner_cleanup'].values())
assert not subprocess.check_output(['losetup','-j',str(base/'m03-storage-device-direct-03/volume.img')])
install=json.loads((root/'storage-owner-install-02.json').read_text())
assert install['sha256']==hashlib.sha256(Path('/usr/local/libexec/spatial-storage-owner').read_bytes()).hexdigest()
assert integration['runner_sha256']==hashlib.sha256((source/'tests/native/test_storage_broker_launch.py').read_bytes()).hexdigest()
selected=[]
for name in ['storage-session-regressions-01','storage-session-regressions-02','storage-session-native-regressions-03',
 'storage-session-native-regressions-04','storage-session-operator-regression-05','storage-session-operator-regression-06',
 'storage-device-regressions-01','storage-device-native-regressions-01','storage-device-build-01','storage-device-build-02']:
 p=root/name
 if p.exists():selected.extend(q for q in p.rglob('*') if q.is_file() and q.suffix in ('.json','.log','.txt'))
for pattern in ['storage-session-build-*.log','storage-device-direct-*.log','storage-owner-install-*.json']:
 selected.extend(root.glob(pattern))
for name in ['m03-storage-broker-launch-01','m03-storage-broker-launch-02','m03-storage-broker-launch-03',
 'm03-storage-broker-launch-04','m03-storage-device-negative-01','m03-storage-device-direct-02','m03-storage-device-direct-03']:
 p=base/name
 if p.exists():selected.extend(q for q in p.iterdir() if q.is_file() and q.suffix in ('.json','.log'))
selected.extend(base.glob('run-m03-storage-session-*.py'))
selected.extend(base.glob('run-m03-storage-device-*.py'))
selected.extend([base/'test-storage-device-negative-01.py',root/'bootstrap_storage_02.py'])
# Preserve current production inputs separately. Earlier reports retain their original source pins.
selected.extend(source/name for name in current['source_hashes'])
selected.append(Path(__file__))
pins={p.relative_to(base).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(selected))}
manifest=root/'storage-session-evidence-sha256.json';manifest.write_text(json.dumps(pins,indent=2)+'\n')
target=base/f'm03-storage-session-{host}-evidence.tar.gz'
assert not target.exists()
with tarfile.open(target,'w:gz') as tar:
 for p in sorted(set(selected+[manifest])):tar.add(p,arcname=p.relative_to(base).as_posix(),recursive=False)
os.chown(target,1000,1000)
print(json.dumps(dict(host=host,files=len(pins),sha256=hashlib.sha256(target.read_bytes()).hexdigest(),archive=str(target))))
