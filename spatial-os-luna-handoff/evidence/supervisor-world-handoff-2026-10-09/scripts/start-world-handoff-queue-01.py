import json
from pathlib import Path
import socket
import subprocess
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
out = r / 'local/after-supervisor-world-handoff-01'
out.mkdir()
with (out / 'driver.log').open('xb') as log:
    child = subprocess.Popen(['python3', str(r / 'local/after-supervisor-world-handoff-01.py')],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
record = dict(pid=child.pid, command=child.args)
(out / 'launch.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
