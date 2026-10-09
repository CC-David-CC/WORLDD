import os
from pathlib import Path
import subprocess
import sys
import process_scope_pb2 as pb
from harness.wire import frame
root=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=root/'local/scope-filter-diagnostic-01';out.mkdir()
scope=Path('/sys/fs/cgroup')/Path('/proc/self/cgroup').read_text().strip().split('::',1)[1].lstrip('/')
assert scope.stat().st_uid==os.getuid()
launch=pb.ProcessScopeRequest(protocol_version=1,request_id=1,launch=pb.AuthorityProcessLaunch(executable='/usr/bin/python3',arguments=['-c','import time;time.sleep(30)']))
stop=pb.ProcessScopeRequest(protocol_version=1,request_id=2,stop=pb.ProcessScopeQuery())
run=subprocess.run([sys.argv[1],str(scope),'deny-write'],input=frame(launch)+frame(stop),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
(out/'stderr.log').write_bytes(run.stderr);(out/'stdout.pb').write_bytes(run.stdout)
print('exit',run.returncode,'stdout bytes',len(run.stdout));print(run.stderr.decode(errors='replace'))
