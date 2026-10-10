"""Compare unchanged failing scenarios during and after a bounded two-job build."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import threading
import time

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
prior = r/'local/transport-diagnostic-run-05'
source, tested_build = prior/'source', prior/'build'
manifest = json.loads((r/'local/transport-diagnostic-05.json').read_text())['files']
def audit():
    assert all(hashlib.sha256((source/n).read_bytes()).hexdigest() == v for n,v in manifest.items())
audit()
records05 = json.loads((prior/'run.json').read_text())['results']
assert len(records05) == 13 and all(x['exit_code'] == 0 for x in records05)
# Do not add this build alongside another compiler. Existing test jobs may continue.
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit(): continue
    try: name = (entry/'comm').read_text().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError): continue
    assert name not in ('cc1', 'cc1plus', 'clang', 'clang++'), (entry.name, name)
out = r/'local/build-overlap-diagnostic-07'
out.mkdir()
workload_build = out/'workload-build'
server = json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest() == server['sha256']
env = dict(os.environ, PYTHONPATH=str(tested_build/'python')+':'+str(source),
    SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(tested_build/'spatial-xprobe'),
    ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
listed = json.loads(subprocess.check_output(['ctest','--test-dir',str(tested_build),'--show-only=json-v1']))
commands = {x['name']:x['command'] for x in listed['tests']}
selected = [
    ('actor-pixels', 'ActorPixelTests.test_live_paint_changes_the_next_pixel_observation'),
    ('workspace-bootstrap', 'WorkspaceBootstrapTests.test_binding_revocation_blocks_new_recipients_but_preserves_existing_access'),
    ('world-bootstrap', 'WorldBootstrapTests.test_public_entry_reports_unbound_template_without_action_authority'),
    ('world-bootstrap-races', 'WorldBootstrapRaceTests.test_credential_revoked_during_profile_read_creates_no_receipt'),
    ('workspace-bootstrap-owner-idle', None),
    ('world-application-control', 'WorldApplicationControlTests.test_resized_aim_rejects_stale_pixels'),
]
configure = ['cmake','-S',str(source),'-B',str(workload_build),'-G','Ninja',
    '-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS=ON']
with (out/'configure.log').open('xb') as log:
    result = subprocess.run(configure, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
assert result.returncode == 0
build_command = ['cmake','--build',str(workload_build),'--parallel','2']
build_log = (out/'build.log').open('xb')
started = time.time()
build = subprocess.Popen(build_command, env=env, stdout=build_log, stderr=subprocess.STDOUT)
stop = threading.Event()
def sample():
    with (out/'resource-samples.jsonl').open('x') as stream:
        while not stop.is_set():
            value = dict(time=time.time(), monotonic=time.monotonic(), build_live=build.poll() is None)
            for name in ('pressure/cpu','pressure/io','pressure/memory','loadavg','meminfo','stat'):
                value[name] = (Path('/proc')/name).read_text()
            stream.write(json.dumps(value)+'\n'); stream.flush()
            stop.wait(1)
sampler = threading.Thread(target=sample)
sampler.start()
records = []
def run(name, method, phase):
    number = len(records)+1
    target = out/(str(number).zfill(2)+'-'+phase+'-'+name); target.mkdir()
    command = commands[name]+([method] if method else [])
    env['SPATIAL_SECURITY_EVIDENCE'] = str(target/'service-logs')
    record = dict(name=name, method=method, phase=phase, command=command, start=time.time(),
        build_live_at_start=build.poll() is None)
    with (target/'test.log').open('xb') as log:
        try:
            result = subprocess.run(command,cwd=tested_build,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
            record['exit_code'] = result.returncode
        except subprocess.TimeoutExpired:
            record['exit_code'] = 'timeout'
    record.update(end=time.time(), build_live_at_end=build.poll() is None)
    records.append(record)
    (out/'tests.json').write_text(json.dumps(records,indent=2)+'\n')
    print(host, phase, number, name, record['exit_code'], 'build_live',record['build_live_at_end'],flush=True)
    if record['exit_code'] != 0:
        print((target/'test.log').read_text()[-6000:],flush=True)
        for path in sorted((target/'service-logs').glob('*.log')):
            text = path.read_text(errors='replace')
            if 'diagnostic:' in text: print(path.name,text[-18000:],flush=True)
    return record['exit_code']
try:
    # At most 24 methods under load. Stop at the first diagnostic failure.
    for index in range(24):
        name, method = selected[index % len(selected)]
        if index and build.poll() is not None: break
        if run(name, method, 'build-overlap') != 0: break
    build_exit = build.wait(timeout=600)
    # Compare with the same immutable binaries after the compiler exits.
    for name, method in selected:
        if run(name, method, 'after-build') != 0: break
finally:
    if build.poll() is None:
        # CMake owns this test-only build. Let its bounded normal build complete.
        build.wait(timeout=600)
    build_log.close(); stop.set(); sampler.join()
audit()
summary = dict(host=host,diagnostic_only=True,qualification=False,production_changed=False,
    source_files=manifest,server=server,build_command=build_command,build_start=started,
    build_exit_code=build.returncode,tests=records,
    limitation='Other already-running qualification jobs remain active. This is not an isolated CPU/IO causal proof.')
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(r/('local/build-overlap-diagnostic-'+host+'-07.tar.gz'),'w:gz') as archive:
    for path in sorted(out.rglob('*')):
        relative=path.relative_to(out)
        if path.is_file() and 'workload-build' not in relative.parts and path.suffix in ('.log','.json','.jsonl'):
            archive.add(path,arcname='build-overlap-07/'+host+'/'+relative.as_posix())
print(host,'build overlap diagnostic complete; no qualification claim',flush=True)
