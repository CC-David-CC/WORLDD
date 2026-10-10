"""Observe the missing administrative transport on the qualified real broker."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
out = r/'local/control-owner-startup-baseline-01'
out.mkdir(exist_ok=False)
build = r/'local/m02-supervisor-crash-fencing-01/debug/build'
broker = build/'workspaced'
admin, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
with tempfile.TemporaryDirectory(prefix='control-owner-baseline-') as state:
    command = [str(broker), '--state-dir', state, '--control-supervisor-fd', str(child.fileno()),
               '--control-supervisor-pid', str(os.getpid())]
    result = subprocess.run(command, input=b'', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            pass_fds=(child.fileno(),), timeout=10)
admin.close()
child.close()
(out/'stdout.bin').write_bytes(result.stdout)
(out/'stderr.txt').write_bytes(result.stderr)
record = dict(host=host, command=command, exit_code=result.returncode,
              binary_sha256=hashlib.sha256(broker.read_bytes()).hexdigest(),
              missing_interface_reproduced=result.returncode == 2 and
                  b'Unknown or incomplete workspaced argument' in result.stderr,
              acceptance_pass=False)
(out/'result.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record))
assert record['missing_interface_reproduced']
