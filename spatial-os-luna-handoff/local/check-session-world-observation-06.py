import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] == 'sb16'
root = r / 'local/m02-connected-recovery-03'
original = json.loads((root / 'checks/review.json').read_bytes())
assert original['status'] == 'passed', 'Wait for the original focused run to finish'
source = root / 'source'; manifest = json.loads((root / 'manifest.json').read_bytes())
fixture = r / 'local/test_session_world_06.py'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
fixture_hash = sha(fixture)
out = root / 'checks-observation06'; out.mkdir(exist_ok=False)
record = dict(status='running', host='sb16', fixture_sha256=fixture_hash,
    purpose='Independent saved world record and live descriptor inventories on the same production binaries.',
    original_review_sha256=sha(root / 'checks/review.json'), results={})
def save(): (out / 'review.json').write_text(json.dumps(record, indent=2) + '\n')
def audit():
    assert sha(fixture) == fixture_hash
    assert all(sha(source / n) == h for n,h in manifest['files'].items())
    for profile, data in original['profiles'].items():
        assert all(sha(root / profile / 'build' / n) == h for n,h in data['binaries'].items())
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try: name = (p/'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        assert name not in ('cc1','cc1plus','clang','clang++','ctest'), (p,name)
save()
try:
    for profile in ('debug','runtime-sanitize'):
        audit(); build = root / profile / 'build'
        env = dict(os.environ, PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/native'),
            SPATIAL_TEST_XSERVER=original['server']['path'],
            SPATIAL_SECURITY_EVIDENCE=str(out/profile/'native'),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        command=['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-world-observation06-'+profile,'--property=Delegate=yes']
        for key in ('PYTHONPATH','SPATIAL_TEST_XSERVER','SPATIAL_SECURITY_EVIDENCE','ASAN_OPTIONS','UBSAN_OPTIONS'):
            command.append('--setenv='+key+'='+env[key])
        command+=['python3',str(fixture),str(source),str(build/'spatial-authority-launch-probe'),
            str(build/'spatial-security-auth'),str(build/'identityd'),str(build/'spatial-issuer-client')]
        entry=dict(command=command,started=time.time());record['results'][profile]=entry;save()
        log=out/(profile+'.log')
        with log.open('xb') as stream:
            result=subprocess.run(command,env=env,stdout=stream,stderr=subprocess.STDOUT)
        entry.update(exit_code=result.returncode,ended=time.time(),log_sha256=sha(log));save()
        print(profile,result.returncode,flush=True)
        if result.returncode: print(log.read_text()[-10000:]);raise RuntimeError('Independent observations failed')
        audit()
    record['status']='passed'
except BaseException:
    record['status']='failed';raise
finally:
    save()
