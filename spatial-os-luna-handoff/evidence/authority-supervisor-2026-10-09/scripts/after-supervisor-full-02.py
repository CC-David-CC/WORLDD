"""Continue cumulative qualification only after the exact supervisor qualifier exits and its checks pass."""
import json, os, pathlib, select, socket, subprocess

r = pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
target = str(r / 'local/qualify-authority-supervisor-02.py')
out = r / 'local/after-supervisor-full-02'
out.mkdir()
matches = []
for path in pathlib.Path('/proc').iterdir():
    if not path.name.isdigit():
        continue
    try:
        if path.stat().st_uid != os.geteuid():
            continue
        args = (path / 'cmdline').read_bytes().split(b'\0')
        if len(args) >= 2 and args[1] == target.encode() and pathlib.Path(os.fsdecode(args[0])).name.startswith('python'):
            matches.append(int(path.name))
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        continue
assert len(matches) <= 1, matches
record = dict(host=host, target=target, qualifier_pid=matches[0] if matches else None, status='checking_dependency')
def save():
    (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
if matches:
    pid = matches[0]
    try:
        pin = os.pidfd_open(pid)
    except ProcessLookupError:
        pin = None
    if pin is not None:
        try:
            try:
                args = (pathlib.Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')
            except FileNotFoundError:
                args = []
            assert not args or (len(args) >= 2 and args[1] == target.encode()), 'Pinned process identity changed'
            record['status'] = 'waiting_for_exact_qualifier_process'; save()
            print(host, 'waiting for pinned supervisor qualifier', pid, flush=True)
            poll = select.poll(); poll.register(pin, select.POLLIN)
            while not poll.poll(1000):
                pass
        finally:
            os.close(pin)
record['status'] = 'qualifier_process_exited'; save()
print(host, 'supervisor qualifier exited; checking terminal records before cumulative qualification', flush=True)
# The collector requires both completed focused profiles, exact source hashes, and unchanged schemas.
commands = [
    ['python3', str(r / 'local/collect-authority-supervisor-02.py'), 'final'],
    ['python3', str(r / 'local/finish-authority-supervisor-full-02.py')],
    ['python3', str(r / 'local/collect-authority-supervisor-full-02.py'), 'final'],
]
for command in commands:
    record['status'] = 'running_' + pathlib.Path(command[1]).name; save()
    run = subprocess.run(command)
    record.setdefault('results', []).append(dict(command=command, exit_code=run.returncode)); save()
    if run.returncode:
        record['status'] = 'failed'; save()
        raise SystemExit(run.returncode)
record['status'] = 'full_qualification_collected'; save()
print(host, 'full supervisor qualification archived; integration remains a separate review', flush=True)
