"""Wait for the frozen prior matrix, then qualify the corrected candidate independently."""
import json,os,pathlib,sys,time
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
record=root/'local/m01-adapters-queue-04.json'
assert not record.exists(),'Queue was already started; inspect it instead of restarting'
def previous():
    found=[]
    for entry in pathlib.Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:args=(entry/'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError,PermissionError,ProcessLookupError):continue
        if any(arg.endswith(b'/qualify-bootstrap-adapters-03.py') for arg in args):found.append(int(entry.name))
    return found
before=previous();record.write_text(json.dumps({'status':'waiting','prior_runner_pids':before,'queue_pid':os.getpid()},indent=2)+'\n')
print('Corrected candidate queued behind prior frozen runner',before,flush=True)
while previous():time.sleep(1)
record.write_text(json.dumps({'status':'started','prior_runner_pids':before,'runner_pid':os.getpid()},indent=2)+'\n')
os.execv(sys.executable,[sys.executable,str(root/'local/qualify-bootstrap-adapters-04.py')])
