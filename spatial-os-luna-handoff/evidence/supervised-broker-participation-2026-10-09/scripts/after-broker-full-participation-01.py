"""Observe the exact full qualification process before the next integration checks."""
import json
import os
from pathlib import Path
import select
import socket
import subprocess
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
pid = {'sb16': 1011953, 'sb16-2': 807992}[host]
out = r / 'local/after-broker-full-participation-01'
record = dict(host=host, dependency_pid=pid, status='checking_dependency')
def save():
    (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
try:
    pin = os.pidfd_open(pid)
except ProcessLookupError:
    pin = None
if pin is not None:
    try:
        args = (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')
        assert len(args) >= 2 and args[1] == str(r / 'local/after-broker-focused-02.py').encode()
        record['status'] = 'waiting_for_pinned_full_driver'; save()
        poll = select.poll(); poll.register(pin, select.POLLIN)
        while not poll.poll(1000):
            pass
    finally:
        os.close(pin)
command = ['python3', str(r / 'local/diagnose-supervised-broker-participation-01.py')]
record['status'] = 'checking_full_evidence_and_running_integration'; save()
result = subprocess.run(command)
record['command'] = command
record['exit_code'] = result.returncode
record['status'] = 'complete' if result.returncode == 0 else 'failed'
save()
raise SystemExit(result.returncode)
