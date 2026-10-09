import json
from pathlib import Path
import subprocess

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=r/'local/supervisor-fencing-driver-01';out.mkdir()
with (out/'driver.log').open('xb') as log:
    child=subprocess.Popen(['python3',str(r/'local/qualify-supervisor-fencing-01.py')],
        cwd=r,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
record=dict(pid=child.pid,command=['python3',str(r/'local/qualify-supervisor-fencing-01.py')])
(out/'launch.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record))
