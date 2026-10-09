"""Wait for the exact running driver, validate focused results, then run full gates."""
import json
import os
from pathlib import Path
import select
import socket
import subprocess
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
pid, name = {'sb16': (1008236, 'finish-supervisor-broker-handoff-02.py'),
             'sb16-2': (801583, 'continue-sb16-2-qualification-02.py')}[host]
out = r / 'local/after-broker-focused-02'
record = dict(host=host, dependency_pid=pid, dependency=name, status='checking_dependency', results=[])
def save():
    (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
try:
    pin = os.pidfd_open(pid)
except ProcessLookupError:
    pin = None
if pin is not None:
    try:
        args = (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')
        assert len(args) >= 2 and args[1] == str(r / 'local' / name).encode()
        record['status'] = 'waiting_for_pinned_driver'; save()
        poll = select.poll(); poll.register(pin, select.POLLIN)
        while not poll.poll(1000):
            pass
    finally:
        os.close(pin)
commands = [
    ['python3', str(r / 'local/collect-supervisor-broker-handoff-02.py'), 'final'],
    ['python3', str(r / 'local/finish-supervisor-broker-full-02.py')],
    ['python3', str(r / 'local/collect-supervisor-broker-full-02.py'), 'final'],
]
for command in commands:
    record['status'] = 'running'; record['active_command'] = command; save()
    result = subprocess.run(command)
    record['results'].append(dict(command=command, exit_code=result.returncode)); save()
    if result.returncode:
        record['status'] = 'failed'; save()
        raise SystemExit(result.returncode)
record['status'] = 'complete'; save()
