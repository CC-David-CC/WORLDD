import os,json,subprocess
from pathlib import Path
root=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m03-storage-applications-03')
image=root/'volume.img';volume=root/'volume';record=dict(commands=[],applications={},setup=[],units=[]);loop=None;mounted=False
assert os.getuid()==0
def run(command,expected=0):
 p=subprocess.run(list(map(str,command)),capture_output=True,text=True,timeout=30);entry=dict(command=list(map(str,command)),code=p.returncode,stdout=p.stdout,stderr=p.stderr);record['commands'].append(entry)
 if expected is not None:assert p.returncode==expected,entry
 return p
try:
 st=image.stat();record['image']=dict(bytes=st.st_size,uid=st.st_uid,mode=oct(st.st_mode & 0o777))
 run(['sudo','-n','-u','dflanag3','python3','-c','import os,sys; os.open(sys.argv[1], os.O_RDONLY)',image],expected=1)
 loop=run(['losetup','--find','--show',image]).stdout.strip();run(['mount','-t','btrfs','-o','ro,nodev,nosuid',loop,volume]);mounted=True
 for path in (volume/'applications').glob('*/*'):
  if path.is_file() and path.name in ('setup.log','stderr','stdout','readonly-witness.json'):
   record['applications'][str(path.relative_to(volume))]=path.read_text(errors='replace')
 for name in ('review.json','restart-review.json','fixed-review.json','regression-review.json'):
  review=json.loads((root/name).read_text());unit=review.get('unit')
  if unit:
   p=run(['systemctl','show',unit,'-p','LoadState','-p','ActiveState']);record['units'].append(dict(unit=unit,state=p.stdout))
finally:
 if mounted:run(['umount',volume])
 if loop:run(['losetup','-d',loop])
 target=root/'application-evidence.json';target.write_text(json.dumps(record,indent=2)+'\n');os.chown(target,1000,1000)
print(json.dumps(dict(applications=len(record['applications']),units=record['units'],image=record['image'])))
