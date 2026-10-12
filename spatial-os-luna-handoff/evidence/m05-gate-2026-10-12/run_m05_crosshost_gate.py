"""Run M05 cross-host cases only after both frozen local gates pass."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

ROOT = Path('/home/dflanag3/src/spacial_desktop-m05-gate-20261012/spatial-os-luna-handoff')
CANDIDATE = '6b9619e5b00be3011068b11c2c7d8ed48d5a0c47'
GATE = ROOT / 'local/m05-full-gate-01'
OUT = ROOT / 'local/m05-crosshost-gate-01'
HOST = socket.gethostname().split('.')[0]
assert HOST in ('sb16', 'sb16-2')
PEER = 'sb16-2' if HOST == 'sb16' else 'sb16'
ADDRESS = '10.0.7.49' if HOST == 'sb16' else '10.0.7.94'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


VERIFY = '''from pathlib import Path
import hashlib,json,subprocess
root=Path("/home/dflanag3/src/spacial_desktop-m05-gate-20261012/spatial-os-luna-handoff")
gate=root/"local/m05-full-gate-01"
a=json.loads((gate/"run.json").read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert a["status"]=="commands_passed_needs_review",a["status"]
assert a["candidate"]=="6b9619e5b00be3011068b11c2c7d8ed48d5a0c47"
assert subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()==a["candidate"]
subprocess.run(["git","diff","--exit-code","HEAD","--","."],cwd=root,check=True,stdout=subprocess.DEVNULL)
for name,expected in a["source_sha256"].items():assert sha(root/name)==expected,name
for profile,row in a["profiles"].items():
 assert row["status"]=="commands_passed_needs_review"
 build=root/"local/m05-gate-m05-full-gate-01"/profile
 for name,expected in row["binary_sha256"].items():assert sha(build/name)==expected,name
 assert sha(Path(row["launcher"]))==row["launcher_sha256"]
 assert sha(Path(row["server"]["path"]))==row["server"]["sha256"]
for name,expected in a["installed_helpers"].items():assert sha(Path(name))==expected,name
print(json.dumps(dict(host=a["host"],candidate=a["candidate"],gate_sha256=sha(gate/"run.json"),source_digest=hashlib.sha256(json.dumps(a["source_sha256"],sort_keys=True).encode()).hexdigest())))
'''


def verify(host):
    command = ['python3', '-'] if host == HOST else ['ssh', host, 'python3 -']
    result = subprocess.run(command, input=VERIFY, text=True, capture_output=True, check=True, timeout=30)
    receipt = json.loads(result.stdout)
    assert receipt['host'] == host
    return receipt


before = {host: verify(host) for host in (HOST, PEER)}
assert before[HOST]['source_digest'] == before[PEER]['source_digest']
OUT.mkdir(exist_ok=False)
record = dict(host=HOST, peer=PEER, candidate=CANDIDATE, status='running', phase_complete=False,
              runner_pid=os.getpid(), started=time.time(), prerequisites=before, results=[],
              runner_sha256=sha(Path(__file__)))


def save():
    (OUT / 'run.json').write_text(json.dumps(record, indent=2) + '\n')


save()
try:
    for profile in ('Debug', 'ASan-UBSan'):
        build = ROOT / 'local/m05-gate-m05-full-gate-01' / profile
        for suite, expected in (('world_remote_keys', 5), ('world_topology', 1), ('session_crosshost_recovery', 2)):
            target = OUT / profile / suite
            target.mkdir(parents=True)
            env = os.environ.copy()
            env.update(PYTHONPATH=str(build / 'python') + ':' + str(ROOT),
                SPATIAL_TEST_XSERVER=str(GATE / profile / 'server-launch/Xvfb'),
                SPATIAL_SECURITY_EVIDENCE=str(target / 'services'),
                SPATIAL_TEST_XSERVER_LOG=str(target / 'xserver.log'),
                ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
                UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
            for name in ('DISPLAY', 'SPATIAL_TEST_DISPLAY', 'LD_LIBRARY_PATH', '__EGL_VENDOR_LIBRARY_FILENAMES'):
                env.pop(name, None)
            peer_env = dict(SPATIAL_REMOTE_HOST=PEER, SPATIAL_WORLD_ADDRESS=ADDRESS,
                SPATIAL_REMOTE_PYTHONPATH=str(build / 'python'),
                SPATIAL_REMOTE_WITNESS=str(ROOT / 'tests/core/world_tls_witness.py'),
                SPATIAL_REMOTE_KEY_BINARY=str(build / 'spatial-key'))
            if suite == 'session_crosshost_recovery':
                command = ['python3', str(ROOT / 'tools/with_resource_delegate.py'), '--native', '--',
                    'python3', str(ROOT / 'tools/with_adapter_resources.py'), '--', 'env',
                    *[key + '=' + value for key, value in peer_env.items()],
                    'python3', str(ROOT / 'tests/native/test_session_crosshost_recovery.py'), str(ROOT),
                    str(build / 'spatial-authority-launch-probe'), str(build / 'spatial-security-auth'),
                    str(build / 'identityd'), str(build / 'spatial-issuer-client')]
            else:
                env.update(peer_env)
                command = ['python3', str(ROOT / 'tools/with_test_display.py'), '--', 'python3',
                    str(ROOT / ('tests/core/test_' + suite + '.py')), str(build / 'identityd'),
                    str(build / 'spatial-issuer-client'), str(build / 'worldd')]
            started = time.monotonic()
            with (target / 'output.log').open('wb') as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            output = (target / 'output.log').read_text(errors='replace')
            count = re.search(r'Ran (\d+) tests? in .*\n\nOK', output)
            row = dict(profile=profile, suite=suite, expected_cases=expected, exit_code=result.returncode,
                       cases=int(count.group(1)) if count else None, command=command,
                       peer_environment=peer_env, seconds=time.monotonic() - started)
            record['results'].append(row); save()
            assert result.returncode == 0 and row['cases'] == expected, row
            for path in target.rglob('*.log'):
                assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|'
                    r'SUMMARY: (AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:',
                    path.read_text(errors='replace')), str(path)
            print(profile, suite, 'passed', expected, flush=True)
    after = {host: verify(host) for host in (HOST, PEER)}
    assert before == after
    record['final_prerequisites'] = after
    record['artifacts'] = {str(p.relative_to(OUT)): sha(p) for p in OUT.rglob('*')
                           if p.is_file() and p != OUT / 'run.json'}
    record['status'] = 'commands_passed_needs_review'
except BaseException as error:
    record.update(status='failed', error=repr(error))
    raise
finally:
    record['ended'] = time.time(); save()
