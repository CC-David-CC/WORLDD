"""Remove only enumerated, inactive CMake test builds. Preserve reports and source snapshots."""
import hashlib,json,os,re,shutil,socket,stat,subprocess,sys,time
from pathlib import Path
assert os.getuid()==0 and socket.gethostname().split('.')[0] in ('sb16','sb16-2')
assert shutil.rmtree.avoids_symlink_attacks
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local').resolve(strict=True)
host=socket.gethostname().split('.')[0];apply=sys.argv[1:]==['--apply'];assert apply or sys.argv[1:]==['--plan']
current=base/'m03-git-storage-20261011'
latest=json.loads((current/'native-storage-session-06/review.json').read_text())
preserved=[current/'source',current/'sanitize',Path(latest['xserver']).resolve(strict=True),base/'m02-phase-gate-02',base/'xlibre-patched']
protected_roots=[current,base/'m03-storage-device-direct-03',base/'m03-storage-broker-launch-04']
candidates={}
def add(path,reason):
 assert path.exists() and not path.is_symlink()
 absolute=path.resolve(strict=True);assert absolute.is_relative_to(base) and absolute!=base
 assert not any(absolute==p or absolute.is_relative_to(p) or p.is_relative_to(absolute) for p in preserved)
 info=path.lstat();candidates[absolute]=dict(reason=reason,device=info.st_dev,inode=info.st_ino,directory=stat.S_ISDIR(info.st_mode))
for name in ('transport-diagnostic-run-05','issuer-sync-diagnostic-09','build-overlap-diagnostic-07','world-bootstrap-diagnostic-run-01','m02-startup-timing-03'):
 root=base/name
 if not root.is_dir() or root.is_symlink():continue
 if root in protected_roots or root==base/'m02-phase-gate-02':continue
 for child in sorted(root.iterdir()):
  if not child.is_dir() or child.is_symlink():continue
  if child.name not in ('build','workload-build','load-build') and not re.fullmatch(r'(?:debug|runtime-sanitize)-issuer-client(?:-defaults)?-native',child.name):continue
  build=child if (child/'CMakeCache.txt').is_file() else child/'build'
  cache=build/'CMakeCache.txt'
  if not cache.is_file() or build.is_symlink():continue
  contents=cache.read_text(errors='replace')
  project=re.search(r'^CMAKE_PROJECT_NAME:STATIC=(.*)$',contents,re.M)
  home=re.search(r'^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$',contents,re.M)
  if not project or project.group(1)!='spatial_runtime' or not home:continue
  home_path=Path(home.group(1)).resolve(strict=True)
  assert home_path.is_relative_to(base) and not home_path.is_relative_to(build.resolve())
  add(build,'Superseded milestone CMake output; exact source, cache and test evidence retained')
# Protect against live runtime references, mounts, and any retained loop association.
mountinfo=Path('/proc/self/mountinfo').read_text().splitlines()
mounts=[]
for line in mountinfo:
 target=line.split()[4]
 target=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m.group(1),8)),target)
 mounts.append(Path(target))
loops=json.loads(subprocess.check_output(['losetup','--json','--list','--output','NAME,BACK-FILE'],text=True)).get('loopdevices',[])
references=[]
for directory in Path('/proc').iterdir():
 if not directory.name.isdecimal() or int(directory.name)==os.getpid():continue
 try:
  arguments=(directory/'cmdline').read_bytes().split(b'\0')
 except OSError:arguments=[]
 for argument in arguments:
  value=argument.decode(errors='replace').split('=',1)[-1]
  if value.startswith(str(base)+'/'):references.append((int(directory.name),Path(value)))
 try:namespace_mounts=(directory/'mountinfo').read_text().splitlines()
 except OSError:namespace_mounts=[]
 for line in namespace_mounts:
  value=line.split()[3]
  value=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m.group(1),8)),value)
  if value.startswith(str(base)+'/'):references.append((int(directory.name),Path(value)))
 for link in [directory/'cwd',directory/'exe',*(directory/'fd').glob('*')]:
  try:value=os.readlink(link)
  except (OSError,FileNotFoundError):continue
  if value.startswith(str(base)+'/'):references.append((int(directory.name),Path(value.removesuffix(' (deleted)'))))
blocked=[]
for path in list(candidates):
 causes=[]
 for mount in mounts:
  if mount==path or mount.is_relative_to(path):causes.append('Mounted path')
 for loop in loops:
  backing=Path(loop.get('back-file',''))
  if backing==path or backing.is_relative_to(path):causes.append('Loop backing file')
 for pid,reference in references:
  if reference==path or reference.is_relative_to(path):causes.append('Live process reference: '+str(pid))
 if causes:blocked.append(dict(path=str(path),causes=sorted(set(causes))));del candidates[path]
# Preserve file evidence from state directories before discarding databases and payloads.
def evidence_files(path):
 return [p for p in path.rglob('*') if p.is_file() and not p.is_symlink() and p.suffix.lower() in ('.png','.ppm','.rgba','.json','.jsonl','.log','.pb','.txt')]
def allocated(path):
 if path.is_file():return path.stat().st_blocks*512
 return sum(p.lstat().st_blocks*512 for p in path.rglob('*') if p.is_file() and not p.is_symlink())
fs=os.statvfs(base);before=fs.f_bavail*fs.f_frsize
record=dict(host=host,status='planned',user_authorized=True,base=str(base),preserved=[str(p) for p in preserved],blocked=blocked,
 candidates=[dict(path=str(p),allocated_bytes=allocated(p),**detail) for p,detail in candidates.items()],deleted=[],retained_evidence=[],free_before_bytes=before)
record['protected_identities']={str(p):[p.stat().st_dev,p.stat().st_ino] for p in preserved}
record['xserver_sha256']=hashlib.sha256(preserved[2].read_bytes()).hexdigest()
receipt=base/('obsolete-diagnostic-builds-cleanup-'+('applied' if apply else 'plan')+'-20261011.json')
assert not receipt.exists()
if apply:
 plan=json.loads((base/'obsolete-diagnostic-builds-cleanup-plan-20261011.json').read_text())
 planned={item['path']:(item['device'],item['inode']) for item in plan['candidates']}
 assert all(str(path) in planned and planned[str(path)]==(detail['device'],detail['inode']) for path,detail in candidates.items())
def save():
 temporary=receipt.with_suffix('.tmp');temporary.write_text(json.dumps(record,indent=2)+'\n');os.chown(temporary,1000,1000);temporary.replace(receipt)
save()
if apply:
 record['status']='applying';save()
 journal_path=base/'obsolete-diagnostic-builds-cleanup-progress-20261011.jsonl'
 journal=journal_path.open('x');os.chown(journal_path,1000,1000)
 for path,detail in candidates.items():
  observed=path.lstat();assert observed.st_dev==detail['device'] and observed.st_ino==detail['inode']
  assert path.resolve(strict=True)==path and path.is_relative_to(base)
  if detail['directory']:
   evidence=path.with_name(path.name+'-retained-evidence');evidence.mkdir(exist_ok=False);os.chown(evidence,1000,1000)
   for original in evidence_files(path):
    target=evidence/original.relative_to(path);target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(original,target)
    os.chown(target,1000,1000)
    digest=hashlib.sha256(original.read_bytes()).hexdigest();assert hashlib.sha256(target.read_bytes()).hexdigest()==digest
    record['retained_evidence'].append(dict(source=str(original),target=str(target),sha256=digest))
  if detail['directory']:shutil.rmtree(path)
  else:path.unlink()
  record['deleted'].append(str(path));journal.write(json.dumps(dict(deleted=str(path)))+'\n');journal.flush()
  if len(record['deleted'])%250==0:
   os.fsync(journal.fileno());save();print(json.dumps(dict(host=host,deleted=len(record['deleted']),total=len(candidates))),flush=True)
 journal.flush();os.fsync(journal.fileno());journal.close()
 assert all([Path(p).stat().st_dev,Path(p).stat().st_ino]==identity for p,identity in record['protected_identities'].items())
 assert hashlib.sha256(preserved[2].read_bytes()).hexdigest()==record['xserver_sha256']
 fs=os.statvfs(base);record.update(status='completed',free_after_bytes=fs.f_bavail*fs.f_frsize)
 record['net_reclaimed_bytes']=record['free_after_bytes']-before;save()
print(json.dumps(dict(host=host,status=record['status'],candidates=len(candidates),planned_gib=sum(x['allocated_bytes'] for x in record['candidates'])/2**30,
 deleted=len(record['deleted']),blocked=blocked,receipt=str(receipt),net_reclaimed_gib=record.get('net_reclaimed_bytes',0)/2**30)),flush=True)
