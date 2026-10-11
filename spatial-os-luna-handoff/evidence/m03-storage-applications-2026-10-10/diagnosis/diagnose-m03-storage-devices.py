import os,json,subprocess
from pathlib import Path
root=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m03-storage-applications-03')
image=root/'volume.img';volume=root/'volume';records=[];loop=None;mounted=False
assert os.getuid()==0
def run(command):
 p=subprocess.run(list(map(str,command)),capture_output=True,text=True,timeout=30);records.append(dict(command=list(map(str,command)),code=p.returncode,stdout=p.stdout,stderr=p.stderr));assert p.returncode==0,records[-1];return p
try:
 loop=run(['losetup','--find','--show',image]).stdout.strip()
 run(['mount','-t','btrfs','-o','ro,nodev,nosuid',loop,volume]);mounted=True
 run(['sudo','-n','-u','dflanag3',str(root.parent/'diagnose-m03-storage-devices'),str(volume)])
 for path in Path('/sys/devices/virtual/block').glob('loop*/loop/backing_file'):
  text=path.read_text().strip()
  if str(image) in text:
   records.append(dict(sysfs=str(path),backing_file=text,absolute=Path(text).is_absolute()))
finally:
 if mounted:run(['umount',volume])
 if loop:run(['losetup','-d',loop])
 target=root/'device-diagnosis.json';target.write_text(json.dumps(records,indent=2)+'\n');os.chown(target,1000,1000)
print(json.dumps(records))
