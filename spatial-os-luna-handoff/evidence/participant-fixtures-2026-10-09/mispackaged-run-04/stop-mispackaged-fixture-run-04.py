import os,pathlib,signal
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
script=str(r/'local/qualify-participant-fixtures-04.py').encode()
matches=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:args=p.read_bytes().split(b'\0')
    except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    if args[:2]==[b'python3',script] and len([x for x in args if x])==2:matches.append(int(p.parent.name))
assert len(matches)<=1,matches
handles=[]
def freeze(pid):
    try:
        assert pathlib.Path('/proc/'+str(pid)).stat().st_uid==os.getuid()
        fd=os.pidfd_open(pid);signal.pidfd_send_signal(fd,signal.SIGSTOP);handles.append((pid,fd))
        children=pathlib.Path('/proc/'+str(pid)+'/task/'+str(pid)+'/children').read_text().split()
        for child in children:freeze(int(child))
    except (FileNotFoundError,ProcessLookupError):pass
for pid in matches:freeze(pid)
for pid,fd in handles:
    try:signal.pidfd_send_signal(fd,signal.SIGKILL)
    except ProcessLookupError:pass
    finally:os.close(fd)
print('Stopped only the mispackaged qualification and its pinned descendants:',[pid for pid,fd in handles])
