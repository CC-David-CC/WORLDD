"""Run the prepared ownership witness after the exact live relay driver exits."""
import json, os, pathlib, select, socket, subprocess

r = pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
target = str(r / 'local/finish-participant-relay-full-01.py')
out = r / 'local/after-relay-native-config-01'
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
record = dict(host=host, target=target, relay_pid=matches[0] if matches else None, status='checking_dependency')
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
            record['status'] = 'waiting_for_exact_relay_process'; save()
            print(host, 'waiting for pinned relay driver', pid, flush=True)
            poll = select.poll(); poll.register(pin, select.POLLIN)
            while not poll.poll(1000):
                pass
        finally:
            os.close(pin)
record['status'] = 'relay_process_exited'; save()
print(host, 'relay driver exited; checking terminal records before ownership witness', flush=True)
# The diagnostic verifies terminal full-run records and every frozen source hash.
for name in ('diagnose-native-scope-config-01.py', 'collect-native-scope-config-baseline-01.py'):
    record['status'] = 'running_' + name; save()
    command = ['python3', str(r / 'local' / name)]
    run = subprocess.run(command)
    record.setdefault('results', []).append(dict(command=command, exit_code=run.returncode)); save()
    if run.returncode:
        record['status'] = 'failed'; save()
        raise SystemExit(run.returncode)
record['status'] = 'baseline_collected'; save()
print(host, 'ownership baseline completed and archived; correction remains pending review', flush=True)
