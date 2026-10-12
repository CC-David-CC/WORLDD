import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

root = Path('/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff')
old = Path('/home/dflanag3/src/spacial_desktop-m05-gate-20261012/spatial-os-luna-handoff')
out = root / 'local/m06-render-owner-01'
out.mkdir(parents=True, exist_ok=False)
names = ['CMakeLists.txt', *['src/platform/' + n for n in
         ('render_process.cpp', 'render_process.hpp', 'render_worker.cpp', 'render_worker.hpp')]]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
source = {n: sha(root / n) for n in names}
record = dict(host=socket.gethostname(), runner_pid=os.getpid(), status='running',
              source_sha256=source, selected=['render-observation-client', 'render-runtime',
              'worldd-lifecycle', 'actor-runtime'], profiles={}, phase_complete=False)
def save():
    (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
save()
try:
    for profile, sanitize in [('Debug', False), ('ASan-UBSan', True)]:
        target = out / profile
        target.mkdir()
        build = root / 'local/m06-build' / profile
        env = os.environ.copy()
        env.update(SPATIAL_TEST_XSERVER=str(old / 'local/m05-full-gate-01' / profile / 'server-launch/Xvfb'),
                   SPATIAL_RENDER_EVIDENCE=str(target / 'graphics'),
                   SPATIAL_SECURITY_EVIDENCE=str(target / 'native'),
                   SPATIAL_SCOPE_EVIDENCE=str(target / 'scopes'),
                   SPATIAL_XPROBE=str(build / 'spatial-xprobe'),
                   ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
                   UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        for key in ('DISPLAY', 'SPATIAL_TEST_DISPLAY', 'SPATIAL_SECURITY_TEST_CORE_ONLY',
                    'LD_LIBRARY_PATH', '__EGL_VENDOR_LIBRARY_FILENAMES'):
            env.pop(key, None)
        row = dict(status='running', results=[])
        record['profiles'][profile] = row
        save()
        commands = [
            ('configure', ['cmake', '-S', str(root), '-B', str(build), '-G', 'Ninja',
             '-DCMAKE_BUILD_TYPE=Debug', '-DSPATIAL_INPUT_TESTS=ON',
             '-DSPATIAL_SANITIZERS=' + ('ON' if sanitize else 'OFF')], 180),
            ('build', ['cmake', '--build', str(build), '--parallel', '2'], 1200),
            ('focused', ['ctest', '--test-dir', str(build), '--parallel', '1', '-V',
             '-R', '^(' + '|'.join(record['selected']) + ')$', '--output-junit', str(target / 'results.xml')], 600)]
        for name, command, timeout in commands:
            started = time.monotonic()
            with (target / (name + '.log')).open('wb') as log:
                result = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            row['results'].append(dict(name=name, command=command, exit_code=result.returncode,
                                       seconds=time.monotonic() - started))
            save()
            print(profile, name, result.returncode, flush=True)
            assert result.returncode == 0, (profile, name)
        row['status'] = 'commands_passed_needs_review'
        save()
    assert source == {n: sha(root / n) for n in names}
    record['status'] = 'commands_passed_needs_review'
except BaseException as error:
    record.update(status='failed', error=repr(error))
    raise
finally:
    save()
