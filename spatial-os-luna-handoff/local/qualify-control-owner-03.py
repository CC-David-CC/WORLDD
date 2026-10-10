"""Qualify exact candidate03 on both profiles without overlapping compiler work."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
root = r/'local/m02-control-owner-03'
source = root/'source'
previous = r/'local/m02-control-owner-02'
manifest = json.loads((root/'manifest.json').read_text())
out = root/'qualification'
out.mkdir(exist_ok=False)
record = dict(host=host, source_files=297, locked_files=41, source_manifest=manifest['files'],
              status='preparing', profiles={}, started=time.time(), compiler_overlap=[])
stop = threading.Event()
monitor = None

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save():
    (out/'summary.json').write_text(json.dumps(record, indent=2)+'\n')

def audit():
    for group in ('files', 'locked_files'):
        for name, digest in manifest[group].items():
            assert sha(source/name) == digest, name

def compilers():
    result = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try: name = (proc/'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        if name in ('cc1','cc1plus','clang','clang++','lto1'):
            result.append(dict(pid=int(proc.name), name=name))
    return result

def watch():
    with (out/'compiler-monitor.jsonl').open('x') as log:
        while not stop.is_set():
            found = compilers()
            entry = dict(time=time.time(), compilers=found)
            log.write(json.dumps(entry)+'\n'); log.flush()
            if found: record['compiler_overlap'].append(entry)
            stop.wait(1)

def run(name, command, target, env, results):
    audit()
    started = time.time()
    with (target/(name+'.log')).open('xb') as log:
        result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
    results.append(dict(name=name, command=command, started=started, ended=time.time(), exit_code=result.returncode))
    save()
    print(host, target.name, name, result.returncode, flush=True)
    if result.returncode:
        print((target/(name+'.log')).read_text()[-9000:], flush=True)
        raise RuntimeError(name+' failed')

try:
    audit()
    launch = json.loads((previous/'launch.json').read_text())
    marker = Path('/proc')/str(launch['pid'])/'cmdline'
    assert not marker.exists() or b'qualify-control-owner-02.py' not in marker.read_bytes(), 'Candidate02 remains live'
    prior = json.loads((previous/'qualification/summary.json').read_text())
    assert prior['status'] == 'failed'
    assert next(x for x in prior['profiles']['debug']['results'] if x['name']=='build')['exit_code'] == 0
    old = json.loads((previous/'manifest.json').read_text())['files']
    assert set(old) == set(manifest['files'])
    changes = [n for n,h in manifest['files'].items() if old[n] != h]
    assert changes == ['tests/core/test_control_owner_channels.py'], changes
    assert all(sha(previous/'source'/name) == digest for name,digest in old.items())
    record['debug_reuse'] = dict(reason='All production, schema, configuration, CMake, and other test files are byte-identical.',
        original_source_manifest=old, corrected_test=changes[0],
        execution='Exclude the original owner test from CTest; run all19 corrected checks explicitly in both profiles.')
    assert not compilers(), 'Another compiler is active'
    server = json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
    assert sha(Path(server['path'])) == server['sha256']
    record['server'] = server
    builds = {'debug':previous/'debug/build', 'runtime-sanitize':root/'runtime-sanitize/build'}
    environments = {}
    for profile, build in builds.items():
        target = out/profile
        target.mkdir()
        env = dict(os.environ, SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build/'spatial-xprobe'),
            SPATIAL_SECURITY_EVIDENCE=str(target/'native'),
            SPATIAL_EVIDENCE_DIR=str(target/'cases'), SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),
            PYTHONPATH=str(build/'python')+':'+str(source/'tests/core')+':'+str(source),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        env.pop('LSAN_OPTIONS', None)
        environments[profile] = env
        results = []
        record['profiles'][profile] = dict(build=str(build), results=results)
        if profile == 'runtime-sanitize':
            record['status'] = 'building-runtime-sanitize'; save()
            run('configure', ['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug',
                '-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS=ON','-DCMAKE_CXX_COMPILER_LAUNCHER=ccache',
                '-DCMAKE_C_COMPILER_LAUNCHER=ccache'], target, env, results)
            run('build', ['cmake','--build',str(build),'--parallel','2'], target, env, results)
            assert 'SPATIAL_SANITIZERS:BOOL=ON' in (build/'CMakeCache.txt').read_text()
        record['profiles'][profile]['binaries'] = {name:sha(build/name) for name in
            ('workspaced','worldd','identityd','spatialwm','spatial-test-host','spatial-issuer-client','spatial-security-auth','spatial-authority-supervisor')}
        record['profiles'][profile]['build_id_header'] = (build/'build_id.hpp').read_text()
        save()
    assert not compilers()
    monitor = threading.Thread(target=watch)
    monitor.start()
    record['status'] = 'qualifying'; save()
    for profile, build in builds.items():
        target = out/profile
        env = environments[profile]
        results = record['profiles'][profile]['results']
        commands = [
            ('harness', ['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
            ('owners', ['python3',str(source/'tests/core/test_control_owner_channels.py'),str(build/'identityd'),
                str(build/'spatial-issuer-client'),str(build/'worldd'),str(build/'spatial-security-auth')]),
            ('core', ['ctest','--test-dir',str(build),'-V','-E','^control-owner-channels$']),
            ('P10', ['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10',
                '--report',str(target/'P10.json')])]
        for name, command in commands:
            assert not compilers(), 'Compiler detected before qualification segment'
            run(name, command, target, env, results)
        assert all(sha(build/name)==digest for name,digest in record['profiles'][profile]['binaries'].items())
    audit()
    assert not record['compiler_overlap'], 'Compiler overlap invalidates qualification'
    record['status'] = 'passed'
except BaseException:
    record['status'] = 'failed'
    raise
finally:
    stop.set()
    if monitor is not None: monitor.join()
    record['ended'] = time.time()
    save()
