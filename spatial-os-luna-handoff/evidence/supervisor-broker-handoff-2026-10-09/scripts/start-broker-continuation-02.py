"""Start one durable continuation after the original driver is confirmed absent."""
import json
import os
from pathlib import Path
import socket
import subprocess
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] == 'sb16'
try:
    os.kill(1006960, 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError('Original qualifier PID is still present')
out = r / 'local/broker-continuation-02'
out.mkdir()
with (out / 'driver.log').open('xb') as log:
    child = subprocess.Popen(['python3', str(r / 'local/finish-supervisor-broker-handoff-02.py')],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
record = dict(pid=child.pid, command=child.args,
    reason='Original qualifier PID1006960 disappeared after recorded successful Debug build. No test logs existed. Continue only unrun steps; preserve configure/build evidence.')
(out / 'launch.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
