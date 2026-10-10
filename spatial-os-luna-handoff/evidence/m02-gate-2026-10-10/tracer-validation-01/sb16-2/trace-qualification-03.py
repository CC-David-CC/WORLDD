"""Retain syscall timing for an owned command and exact selected executable paths."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

if len(sys.argv)>1 and sys.argv[1]=='--worker':
    ready=Path(sys.argv[2]);command=json.loads(sys.argv[3]);deadline=time.monotonic()+30
    while not ready.exists():
        if time.monotonic()>deadline:raise RuntimeError('Timing tracer did not become ready')
        time.sleep(.01)
    os.execvpe(command[0],command,os.environ)

parser=argparse.ArgumentParser()
parser.add_argument('--output-dir',type=Path,required=True)
parser.add_argument('--template',type=Path,required=True)
parser.add_argument('--timeout',type=int,required=True)
parser.add_argument('--track-executable',action='append',default=[])
parser.add_argument('command',nargs=argparse.REMAINDER)
args=parser.parse_args()
command=args.command[1:] if args.command[:1]==['--'] else args.command
assert command and 1<=args.timeout<=18000
out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
paths=sorted(set(args.track_executable))
assert len(paths)<=96
for name in paths:
    p=Path(name)
    assert p.is_absolute() and p.is_file() and os.access(p,os.X_OK) and len(os.fsencode(name))<190
template=args.template.read_text()
assert template.count('__EXECUTABLE_PROBES__')==1
probes=[]
for path in paths:
    probes.append('tracepoint:sched:sched_process_exec /str(args->filename) == '+json.dumps(path)+'/ { '+
        '@tracked[tid] = 1; printf("EXEC_MATCH ns=%llu pid=%d\\n", nsecs, pid); }')
program=template.replace('__EXECUTABLE_PROBES__','\n'.join(probes))
bt=out/'trace.bt';bt.write_bytes(program.encode())
record=dict(status='preparing',argv=command,started=time.time(),wrapper_sha256=sha(Path(__file__)),
    template_sha256=sha(args.template),program_sha256=sha(bt),tracked_executables={p:sha(Path(p)) for p in paths},
    scope='Command descendants and exact selected executable paths, including systemd launches.',
    capture='Syscall numbers, durations, return codes, process metadata, and scheduler waits. No argument buffers.')
def save():
    temp=out/'record.json.tmp';temp.write_bytes((json.dumps(record,indent=2)+'\n').encode());temp.replace(out/'record.json')
proc=tracer=None;exit_code=125
save()
try:
    ready=out/'trace-ready'
    proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker',str(ready),json.dumps(command)],
        start_new_session=True)
    record['root_pid']=proc.pid;save()
    trace_command=['sudo','-n','env','BPFTRACE_MAX_STRLEN=200','BPFTRACE_MAX_MAP_KEYS=16384',
        'BPFTRACE_PERF_RB_PAGES=64','bpftrace','-q','-B','none',str(bt),str(proc.pid),str(args.timeout+30)]
    record['trace_argv']=trace_command;save()
    with (out/'timing.log').open('xb') as stream:
        tracer=subprocess.Popen(trace_command,stdout=stream,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+25
        while b'READY root=' not in (out/'timing.log').read_bytes():
            assert tracer.poll() is None and proc.poll() is None and time.monotonic()<deadline, 'Tracer startup failed; inspect timing.log'
            time.sleep(.05)
        ready.touch();record['status']='observing';record['command_started_ns']=time.monotonic_ns();save()
        record['command_exit_code']=proc.wait(timeout=args.timeout)
        record['command_ended_ns']=time.monotonic_ns();save()
        record['trace_exit_code']=tracer.wait(timeout=10)
    text=(out/'timing.log').read_text(errors='replace')
    record['timing_sha256']=sha(out/'timing.log')
    valid=record['trace_exit_code']==0 and 'ROOT_EXIT' in text and 'TRACE_DEADLINE' not in text
    valid=valid and not re.search(r'(?:Lost|lost) \d+ events|ERROR:|Error:',text)
    record['trace_complete']=bool(valid)
    assert valid, 'Timing trace is incomplete; command results remain preserved'
    exit_code=record['command_exit_code']
    record['status']='command_passed_trace_complete' if exit_code==0 else 'command_failed_trace_complete'
except BaseException as error:
    record.update(status='instrumentation_or_command_error',error=type(error).__name__+': '+str(error))
    print(record['error'],file=sys.stderr,flush=True)
    if proc and proc.poll() is None:
        assert os.getpgid(proc.pid)==proc.pid
        os.killpg(proc.pid,signal.SIGTERM)
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
    if tracer and tracer.poll() is None:
        # Kill only this retained sudo/bpftrace process tree if the root-exit event was unavailable.
        try:tracer.wait(timeout=10)
        except subprocess.TimeoutExpired:
            subprocess.run(['sudo','-n','kill','-INT',str(tracer.pid)],check=False,timeout=5)
            tracer.wait(timeout=10)
finally:
    record['ended']=time.time();save()
raise SystemExit(exit_code)
