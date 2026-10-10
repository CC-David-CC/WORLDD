"""Read sanitizer state only from exact task-owned runtime binaries. Requires read-only sudo."""
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import sys
import time

candidate=sys.argv[1]
assert candidate in ('03','04') and os.geteuid()==0
host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
root=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')/('m02-control-owner-'+candidate)
summary=json.loads((root/'qualification/summary.json').read_text())
profile=summary['profiles']['runtime-sanitize']
build=Path(profile['build'])
expected={str(build/name):digest for name,digest in profile['binaries'].items()
          if name in ('workspaced','worldd','identityd','spatialwm')}
samples=[]
for directory in Path('/proc').iterdir():
    if not directory.name.isdigit():continue
    descriptor=None
    try:
        executable=os.readlink(directory/'exe')
        if executable not in expected:continue
        descriptor=os.pidfd_open(int(directory.name))
        if select.select([descriptor],[],[],0)[0]:continue
        before=(directory/'stat').read_text().rsplit(')',1)[1].split()[19]
        assert hashlib.sha256(Path(executable).read_bytes()).hexdigest()==expected[executable]
        environment={}
        for field in (directory/'environ').read_bytes().split(b'\0'):
            key,separator,value=field.partition(b'=')
            if separator and key in (b'ASAN_OPTIONS',b'UBSAN_OPTIONS',b'LSAN_OPTIONS'):
                environment[key.decode()]=value.decode()
        mappings=(directory/'maps').read_text()
        libraries=sorted({line.split()[-1] for line in mappings.splitlines() if 'libasan.so' in line or 'libubsan.so' in line})
        tracer=next(line.split(':',1)[1].strip() for line in (directory/'status').read_text().splitlines() if line.startswith('TracerPid:'))
        after=(directory/'stat').read_text().rsplit(')',1)[1].split()[19]
        if before!=after or select.select([descriptor],[],[],0)[0]:continue
        confirmed=(environment.get('ASAN_OPTIONS')=='detect_leaks=1:abort_on_error=1' and
            environment.get('UBSAN_OPTIONS')=='halt_on_error=1:print_stacktrace=1' and
            not environment.get('LSAN_OPTIONS') and tracer=='0' and
            any('libasan.so' in name for name in libraries) and any('libubsan.so' in name for name in libraries))
        samples.append(dict(pid=int(directory.name),start_ticks=before,binary=executable,binary_sha256=expected[executable],
            environment=environment,libraries=libraries,tracer_pid=int(tracer),confirmed=confirmed))
    except (FileNotFoundError,ProcessLookupError,PermissionError):
        pass
    finally:
        if descriptor is not None:os.close(descriptor)
print(json.dumps(dict(host=host,candidate=candidate,profile='runtime-sanitize',observed=time.time(),
    read_only=True,ptrace_used=False,samples=samples),indent=2))
