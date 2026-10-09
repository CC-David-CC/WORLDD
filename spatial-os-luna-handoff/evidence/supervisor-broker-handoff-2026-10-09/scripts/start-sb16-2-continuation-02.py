import json
import os
from pathlib import Path
import socket
import subprocess
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] == 'sb16-2'
for pid in (701256, 708801, 716356, 785544):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        continue
    raise AssertionError('Original worker remains present: ' + str(pid))
out = r / 'local/durable-qualification-continuation-02'
out.mkdir()
with (out / 'driver.log').open('xb') as log:
    child = subprocess.Popen(['python3', str(r / 'local/continue-sb16-2-qualification-02.py')],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
record = dict(pid=child.pid, command=child.args,
    reason='Original full driver and queues disappeared after recorded successful core step. No P10 or baseline commands started. Preserve completed results and run remaining steps.')
(out / 'launch.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
