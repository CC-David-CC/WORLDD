import json
from pathlib import Path
import socket
import subprocess
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/world-handoff-driver-02';out.mkdir()
command=['python3',str(r/'local/qualify-world-handoff-02.py')]
with (out/'driver.log').open('xb') as log:
    proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
record=dict(host=host,pid=proc.pid,command=command)
(out/'launch.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
