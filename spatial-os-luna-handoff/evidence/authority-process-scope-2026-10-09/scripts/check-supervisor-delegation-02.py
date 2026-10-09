import json
import os
from pathlib import Path
import socket

assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
relative = Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1]
assert relative != '/' and 'spatial-supervisor-delegation-check-02.service' in relative
root = Path('/sys/fs/cgroup') / relative.lstrip('/')
child = root / 'owned-probe'
child.mkdir()
try:
    print(json.dumps(dict(host=socket.gethostname(), path=str(root), uid=root.stat().st_uid,
        child_uid=child.stat().st_uid, kill_writable=os.access(child/'cgroup.kill', os.W_OK),
        events=(child/'cgroup.events').read_text(), type=(child/'cgroup.type').read_text())), flush=True)
finally:
    child.rmdir()
