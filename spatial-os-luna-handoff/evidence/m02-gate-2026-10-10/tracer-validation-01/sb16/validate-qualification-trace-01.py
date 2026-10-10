"""Verify exact-executable capture for systemd children and exclusion of a sibling."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
out=base/'qualification-trace-validation-01';out.mkdir(exist_ok=False)
source=out/'probe.c'
source.write_text('''#include <fcntl.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>
int main(int argc,char**argv) {
 if(argc!=3)return 2;
 int fd=open(argv[2],O_WRONLY|O_CREAT|O_EXCL,0600);
 if(fd<0 || write(fd,"TIMING_PRIVATE_MARKER",21)!=21 || fsync(fd) || fdatasync(fd))return 3;
 struct timespec delay={0,180000000}; if(nanosleep(&delay,0))return 4;close(fd);
 printf("PROBE kind=%s pid=%d parent=%d fsync=%d fdatasync=%d sleep=%d\\n",argv[1],getpid(),getppid(),SYS_fsync,SYS_fdatasync,SYS_clock_nanosleep);
 return 0;
}
''')
binary=out/'trace-probe';other=out/'trace-other'
with (out/'compile.log').open('xb') as log:
    subprocess.run(['cc','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30)
shutil.copyfile(binary,other);other.chmod(0o700)
dispatcher=out/'dispatch.py'
dispatcher.write_text('''from pathlib import Path
import os,subprocess,sys
out=Path(sys.argv[1])
for kind,binary in [('selected','trace-probe'),('unselected','trace-other')]:
 cmd=['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-trace-proof-'+str(os.getpid())+'-'+kind,'--property=RuntimeMaxSec=10',str(out/binary),kind,str(out/(kind+'.data'))]
 subprocess.run(cmd,check=True,timeout=15)
''')
command=['python3',str(base/'trace-qualification-01.py'),'--output-dir',str(out/'trace'),
    '--template',str(base/'qualification-syscalls-01.bt'),'--timeout','45','--track-executable',str(binary)]
gate=base/'m02-phase-gate-01'
original=json.loads((gate/'summary.json').read_bytes())
for name in sorted(original['profiles']['debug']['binaries']):
    command+=['--track-executable',str(gate/'debug/build'/name)]
command+=['--','python3',str(dispatcher),str(out)]
with (out/'run.log').open('xb') as log:
    result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=80)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
record=dict(host=host,argv=command,exit_code=result.returncode,status='failed',ended=time.time())
try:
    assert result.returncode==0,(out/'run.log').read_text()
    trace=json.loads((out/'trace/record.json').read_bytes());assert trace['trace_complete']
    log=(out/'trace/timing.log').read_text();assert 'TIMING_PRIVATE_MARKER' not in log
    facts=re.findall(r'PROBE kind=(\w+) pid=(\d+) parent=(\d+) fsync=(\d+) fdatasync=(\d+) sleep=(\d+)',(out/'run.log').read_text())
    assert len(facts)==2
    selected,unselected=facts;assert selected[0]=='selected' and unselected[0]=='unselected'
    assert int(selected[2])!=trace['root_pid'] and int(unselected[2])!=trace['root_pid']
    assert re.search(r'EXEC_MATCH .*pid='+selected[1]+r' path=',log)
    assert not re.search(r'(?:pid|tid|child|parent)='+unselected[1]+r'(?:\D|$)',log)
    assert '@calls[trace-other,' not in log
    for syscall in selected[3:5]:assert re.search(r'@calls\[trace-probe, '+syscall+r'\]: 1\b',log)
    slow=re.search(r'SLOW .*pid='+selected[1]+r' .*syscall='+selected[5]+r' us=(\d+) result=0',log)
    assert slow and int(slow[1])>=150000
    record.update(status='scope_and_timing_verified',selected_pid=int(selected[1]),unselected_pid=int(unselected[1]),
        sleep_microseconds=int(slow[1]),outside_root_parent_pid=int(selected[2]),trace_root_pid=trace['root_pid'])
finally:
    record['attachments']={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file() and p.suffix in ('.py','.c','.bt','.json','.log')}
    (out/'review.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
    print(json.dumps({k:v for k,v in record.items() if k not in ('argv','attachments')}),flush=True)
