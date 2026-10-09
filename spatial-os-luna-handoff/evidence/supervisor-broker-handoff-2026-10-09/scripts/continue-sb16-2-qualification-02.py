"""Complete interrupted qualification, then baseline and the corrected candidate."""
import json
from pathlib import Path
import socket
import subprocess
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] == 'sb16-2'
out = r / 'local/durable-qualification-continuation-02'
commands = [
    ['python3', str(r / 'local/finish-supervisor-p10-02.py')],
    ['python3', str(r / 'local/collect-authority-supervisor-full-02.py'), 'final'],
    ['python3', str(r / 'local/diagnose-supervisor-broker-handoff-01.py')],
    ['python3', str(r / 'local/qualify-supervisor-broker-handoff-02.py')],
    ['python3', str(r / 'local/collect-supervisor-broker-handoff-02.py'), 'final'],
]
record = dict(results=[], status='running', skipped='Candidate01 broker build never started. Its sb16 observer defect is preserved. Qualify corrected candidate02 after unchanged-source baseline.')
for command in commands:
    record['active_command'] = command
    (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    result = subprocess.run(command)
    record['results'].append(dict(command=command, exit_code=result.returncode))
    if result.returncode:
        record['status'] = 'failed'
        (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
        raise SystemExit(result.returncode)
record['status'] = 'complete'
(out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
