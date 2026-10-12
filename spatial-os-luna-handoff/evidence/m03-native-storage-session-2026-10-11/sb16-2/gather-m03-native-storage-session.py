import hashlib,json,os,socket,subprocess,tarfile
from pathlib import Path
assert os.getuid()==0 and socket.gethostname().split('.')[0] in ('sb16','sb16-2')
host=socket.gethostname().split('.')[0]
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local');root=base/'m03-git-storage-20261011';source=root/'source'
latest=root/'native-storage-session-06';report=json.loads((latest/'review.json').read_text());assert report['status']=='passed'
for name,sha in report['source_hashes'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==sha,name
import sys
sys.path.insert(0,str(root/'sanitize/python'))
import launch_pb2 as launch
cases={}
for directory in sorted((latest/'native').glob('test_native_storage_*')):
 if not directory.is_dir():continue
 setup=json.loads((directory/'volume-setup.json').read_text())
 assert setup['cleanup_confirmed'] and not setup['mounted'] and not setup['loop_attached']
 assert not subprocess.check_output(['losetup','-j',str(directory/'volume.img')])
 assert subprocess.run(['findmnt','--mountpoint',str(directory/'volume')],capture_output=True).returncode==1
 records={}
 for name in ['broker-cleanup','world-cleanup']:
  saved=launch.AuthorityLaunchRecord.FromString((directory/(name+'.pb')).read_bytes())
  assert saved.stage==launch.OWNED_SCOPES_STOPPED and saved.HasField('process_stopped') and saved.HasField('native_stopped')
  assert saved.process==saved.process_stopped.identity and saved.native==saved.native_stopped.identity
  records[name]=dict(stage=saved.stage,launch_id=saved.process.launch_id.hex(),scope=saved.process.scope_name)
 witness=json.loads((directory/'owner-kernel.json').read_text())
 state=subprocess.check_output(['systemctl','show',witness['unit'],'-p','MainPID','-p','ActiveState'],text=True)
 assert 'MainPID=0\n' in state and 'ActiveState=inactive\n' in state
 assert witness['executable_sha256']==hashlib.sha256(Path('/usr/local/libexec/spatial-storage-owner').read_bytes()).hexdigest()
 cases[directory.name]=dict(records=records,owner_after=state,volume_cleanup_confirmed=True,
  read_only_recovery=['sudo','mount','-t','btrfs','-o','loop,ro,nodev,nosuid',str(directory/'volume.img'),str(directory/'volume')],
  recovery_unmount=['sudo','umount',str(directory/'volume')])
assert len(cases)==2
selected=[]
for number in range(1,7):
 directory=root/f'native-storage-session-{number:02}'
 selected.extend(p for p in directory.rglob('*') if p.is_file() and p.suffix in ('.json','.log','.txt','.pb'))
selected.extend(source/name for name in report['source_hashes'])
selected.extend(base.glob('run-m03-native-storage-session-*.py'))
selected.append(Path(__file__))
status=root/'native-storage-session-final-host.json';status.write_text(json.dumps(dict(cases=cases,phase_complete=False),indent=2)+'\n');selected.append(status)
pins={p.relative_to(base).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(selected))}
manifest=root/'native-storage-session-evidence-sha256.json';manifest.write_text(json.dumps(pins,indent=2)+'\n')
archive=base/f'm03-native-storage-session-{host}-evidence.tar.gz';assert not archive.exists()
with tarfile.open(archive,'w:gz') as tar:
 for p in sorted(set(selected+[manifest])):tar.add(p,arcname=p.relative_to(base).as_posix(),recursive=False)
os.chown(archive,1000,1000)
print(json.dumps(dict(host=host,attachments=len(pins),sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),archive=str(archive))))
