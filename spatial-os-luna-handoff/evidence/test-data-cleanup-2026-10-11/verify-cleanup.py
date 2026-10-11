import hashlib,json,os,subprocess,socket,gzip
from pathlib import Path
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
reports=[]
for prefix in ('obsolete-test-data-cleanup','obsolete-test-builds-cleanup','obsolete-diagnostic-builds-cleanup'):
 path=base/(prefix+'-applied-20261011.json')
 data=json.loads(path.read_text())
 assert data['status']=='completed' and len(data['deleted'])==len(data['candidates'])
 assert not any(Path(p).exists() or Path(p).is_symlink() for p in data['deleted'])
 for preserved,identity in data['protected_identities'].items():
  stat=Path(preserved).stat();assert [stat.st_dev,stat.st_ino]==identity
 assert hashlib.sha256(Path(data['preserved'][2]).read_bytes()).hexdigest()==data['xserver_sha256']
 for proof in data['retained_evidence']:
  assert hashlib.sha256(Path(proof['target']).read_bytes()).hexdigest()==proof['sha256']
 for suffix in ('plan','applied'):
  original=base/(prefix+'-'+suffix+'-20261011.json')
  packed=original.with_suffix('.json.gz')
  with gzip.open(packed,'wb') as output:output.write(original.read_bytes())
  os.chown(packed,1000,1000)
 reports.append({'receipt':str(path),'receipt_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'deleted':len(data['deleted']),'retained_evidence_verified':len(data['retained_evidence']),'reclaimed_gib':data['net_reclaimed_bytes']/2**30,'protected_paths_verified':len(data['protected_identities']),'blocked':data['blocked']})
fs=os.statvfs(base)
summary={'host':socket.gethostname().split('.')[0],'verification':'passed','passes':reports,'total_reclaimed_gib':sum(r['reclaimed_gib'] for r in reports),'free_gib':fs.f_bavail*fs.f_frsize/2**30}
path=base/'obsolete-test-data-cleanup-verified-20261011.json'
path.write_text(json.dumps(summary,indent=2)+'\n');os.chown(path,1000,1000)
print(json.dumps(summary))
result=subprocess.run(['du','-x','-B1','--max-depth=1',str(base)],capture_output=True,text=True)
rows=[]
for line in result.stdout.splitlines():
 amount,name=line.split('\t',1);rows.append((round(int(amount)/2**30,3),name))
print(json.dumps({'largest_remaining':sorted(rows,reverse=True)[:10],'du_status':result.returncode}))
