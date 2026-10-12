"""Wait for the existing gate, then run isolated fixture corrections once."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time

host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
root = Path('/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff')
corrected = Path('/home/dflanag3/src/spacial_desktop-m04-fixture-20261011/spatial-os-luna-handoff')
gate_path = root / 'local/m04-full-gate-01/run.json'
out = root / 'local/m04-fixture-followups-01'
out.mkdir(exist_ok=False)
record = dict(status='waiting_for_existing_gate', host=host, runner_pid=os.getpid(),
              phase_complete=False, results=[], started=time.time())

def save():
    (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')

save()
try:
    deadline = time.monotonic() + 10800
    while True:
        try:
            gate = json.loads(gate_path.read_text())
        except json.JSONDecodeError:
            if time.monotonic() >= deadline:
                raise TimeoutError('Existing gate observation expired; do not restart it')
            time.sleep(2)
            continue
        assert gate['candidate'] == 'f74c6b579c6149f41e1cbd17b025eb92fd453009'
        process = Path('/proc') / str(gate['runner_pid']) / 'cmdline'
        try:
            command = process.read_bytes()
            gate_live = b'qualify_m04_gate.py' in command and str(root).encode() in command
        except FileNotFoundError:
            gate_live = False
        if gate['status'] != 'running' and not gate_live:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('Existing gate observation expired; do not restart it')
        time.sleep(2)
    record.update(status='running_followups', original_gate_status=gate['status'],
                  original_gate_pid=gate['runner_pid'])
    save()
    print(host, 'existing gate terminal:', gate['status'], flush=True)
    for name, script in (
        ('storage-fixture', corrected / 'local/rerun-storage-fixture.py'),
        ('setup-deadlines', root / 'local/rerun-setup-deadlines.py'),
    ):
        record['current'] = name
        save()
        with (out / (name + '.log')).open('wb') as log:
            result = subprocess.run(['python3', str(script)], stdout=log,
                                    stderr=subprocess.STDOUT, timeout=1800)
        record['results'].append(dict(name=name, exit_code=result.returncode))
        save()
        print(host, name, result.returncode, flush=True)
    record['status'] = ('commands_passed_needs_review' if all(r['exit_code'] == 0 for r in record['results'])
                        else 'failed_needs_review')
except BaseException as error:
    record.update(status='failed_or_observation_incomplete', error=repr(error))
    raise
finally:
    record['ended'] = time.time()
    save()
raise SystemExit(0 if record['status'] == 'commands_passed_needs_review' else 1)
