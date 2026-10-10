"""Attach kernel timing to the exact live candidate02 test runner and its children."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0]=='sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
expected=[b'python3',str(base/'check-issuer-timeout-02.py').encode(),
    b'699626c6fd625608de3a40b222664bf2f6ec005b8f1fddda75378e5fd52900c4']
matches=[]
for path in Path('/proc').iterdir():
    if not path.name.isdigit():continue
    try:argv=(path/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    if argv==expected:matches.append(int(path.name))
assert len(matches)==1,('Expected one live candidate runner',matches)
pid=matches[0]
out=base/'m02-candidate02-kernel-timing-01';out.mkdir(exist_ok=False)
identities={}
pending=[pid]
while pending:
    current=pending.pop()
    try:
        stat=Path(f'/proc/{current}/stat').read_text()
        children=Path(f'/proc/{current}/task/{current}/children').read_text().split()
    except (FileNotFoundError,ProcessLookupError):continue
    identities[current]=dict(start_ticks=stat.rsplit(')',1)[1].split()[19])
    pending += [int(child) for child in children]
assert pid in identities
program=(base/'approval-timeout-timing.bt').read_text()
program=program.replace('BEGIN { @tracked[$1] = 1;',
    'BEGIN { @tracked[$1] = 1; '+''.join(f'@tracked[{child}] = 1; ' for child in identities if child!=pid),1)
(out/'kernel.bt').write_bytes(program.encode())
(out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
record=dict(status='observing',started=time.time(),runner_pid=pid,initial_process_identities=identities,
    scope='Exact live candidate02 runner, its observed children, and future descendants. Kernel timing only.',
    starts_mid_run=True,source_changed=False,deadlines_changed=False)
command=['sudo','-n','bpftrace','-q','-B','none',str(out/'kernel.bt'),str(pid)]
record['command']=command
(out/'review.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
with (out/'timing.log').open('xb') as log:
    result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=100)
record.update(exit_code=result.returncode,ended=time.time(),
    status='runner_exit_observed' if result.returncode==0 and 'ROOT_EXIT' in (out/'timing.log').read_text() else 'limited_or_failed_observation')
record['attachments']={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in out.iterdir()
    if path.is_file() and path.name!='review.json'}
(out/'review.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
print(record['status'],flush=True)
