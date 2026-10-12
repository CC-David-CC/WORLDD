#!/usr/bin/env python3
"""Run the complete local M04 gate on one frozen project checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--server-debug-receipt', type=Path, required=True)
    parser.add_argument('--server-sanitize-receipt', type=Path, required=True)
    parser.add_argument('--graphics-receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0]
    if host not in ('sb16', 'sb16-2'):
        parser.error('Only sb16 and sb16-2 are allowed')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if commit != args.candidate:
        parser.error('Checkout does not match the frozen candidate')
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', '.'], cwd=ROOT, check=True)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    graphics = json.loads(args.graphics_receipt.read_text())
    assert graphics['status'] == 'passed' and graphics['host'].split('.')[0] == host
    vendor = Path(graphics['vendor_path'])
    assert digest(vendor) == graphics['vendor_sha256']
    graphics_env = graphics['environment']
    vendor_config = Path(graphics_env['__EGL_VENDOR_LIBRARY_FILENAMES'])
    assert Path(json.loads(vendor_config.read_text())['ICD']['library_path']) == vendor
    names = ['CMakeLists.txt', 'acceptance.lock.json', 'docs/CONTRACT.md']
    for directory in ('src', 'proto', 'tests', 'tools', 'config', 'server', 'harness', 'native'):
        names.extend(str(p.relative_to(ROOT)) for p in (ROOT / directory).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    inputs = {name: digest(ROOT / name) for name in sorted(set(names))}
    locked = json.loads((ROOT / 'acceptance.lock.json').read_text())['files']
    for name, expected in locked.items():
        assert digest(ROOT / name) == expected, name
    record = dict(host=host, candidate=commit, status='running', phase_complete=False,
                  runner_pid=os.getpid(), started=time.time(), source_sha256=inputs,
                  locked_files_verified=len(locked), graphics=graphics_env,
                  vendor_sha256=digest(vendor), profiles={})
    record['installed_helpers'] = {str(p): digest(p) for p in (
        Path('/usr/local/libexec/spatial-bwrap'), Path('/usr/local/libexec/spatial-storage-owner'))}

    def save():
        (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')

    save()
    try:
        for profile, receipt_path, sanitize in (
                ('Debug', args.server_debug_receipt, False),
                ('ASan-UBSan', args.server_sanitize_receipt, True)):
            receipt = json.loads(receipt_path.read_text())
            assert receipt['status'] == 'passed' and receipt['host'] == host
            assert receipt['sanitizers'] == sanitize
            server = Path(receipt['binaries']['Xvfb']['path'])
            assert digest(server) == receipt['binaries']['Xvfb']['sha256']
            for name, expected in receipt['extension_sources'].items():
                assert digest(ROOT / name) == expected, name
            target = out / profile
            target.mkdir()
            launcher = target / 'server-launch' / 'Xvfb'
            launcher.parent.mkdir()
            launcher.write_text('#!/usr/bin/python3\nimport os, sys\n'
                                'env = dict(os.environ)\n'
                                'env.update(' + repr(graphics_env) + ')\n'
                                'server = ' + repr(str(server)) + '\n'
                                'os.execve(server, [server, *sys.argv[1:]], env)\n')
            launcher.chmod(0o755)
            build = ROOT / 'local' / ('m04-gate-' + out.name) / profile
            env = {**os.environ, 'PYTHONPATH': str(build / 'python') + ':' + str(ROOT),
                   'SPATIAL_TEST_XSERVER': str(launcher),
                   'SPATIAL_SECURITY_EVIDENCE': str(target / 'native'),
                   'SPATIAL_SCOPE_EVIDENCE': str(target / 'scopes'),
                   'SPATIAL_RENDER_EVIDENCE': str(target / 'graphics'),
                   'SPATIAL_XPROBE': str(build / 'spatial-xprobe'),
                   'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1',
                   'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}
            for key in ('DISPLAY', 'SPATIAL_TEST_DISPLAY', 'SPATIAL_SECURITY_TEST_CORE_ONLY',
                        'LD_LIBRARY_PATH', '__EGL_VENDOR_LIBRARY_FILENAMES'):
                env.pop(key, None)
            row = dict(status='running', server=receipt['binaries']['Xvfb'],
                       launcher=str(launcher), launcher_sha256=digest(launcher), results=[])
            record['profiles'][profile] = row
            save()
            commands = [
                ('configure', ['cmake', '-S', str(ROOT), '-B', str(build), '-G', 'Ninja',
                               '-DCMAKE_BUILD_TYPE=Debug', '-DSPATIAL_INPUT_TESTS=ON',
                               '-DSPATIAL_SANITIZERS=' + ('ON' if sanitize else 'OFF')], 180),
                ('build', ['cmake', '--build', str(build), '--parallel', '2'], 1200),
                ('inventory', ['ctest', '--test-dir', str(build), '--show-only=json-v1'], 60),
                ('harness', ['python3', '-m', 'unittest', 'discover', '-s', 'tests/harness', '-t', '.', '-v'], 180),
                ('core', ['ctest', '--test-dir', str(build), '--parallel', '1', '-V',
                          '--output-junit', str(target / 'core.xml')], 7200),
                ('P10', ['python3', 'tools/with_resource_delegate.py', '--native', '--',
                         'python3', 'tools/with_adapter_resources.py', '--',
                         'python3', 'tools/with_test_display.py', '--',
                         'python3', 'tools/gate.py', '--through', 'P10', '--sut',
                         json.dumps([str(build / 'spatial-test-host')]),
                         '--report', str(target / 'P10.json')], 300),
                ('bounded-storage-git', ['sudo', '-n', 'python3',
                                         'tests/native/test_storage_git_integration.py',
                                         '--production-owner', str(build), str(target / 'bounded-storage-git')], 600)]
            for label, command, timeout in commands:
                started = time.monotonic()
                with (target / (label + '.log')).open('wb') as log:
                    result = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                            stderr=subprocess.STDOUT, timeout=timeout)
                row['results'].append(dict(name=label, command=command, exit_code=result.returncode,
                                           seconds=time.monotonic() - started))
                save()
                print(profile, label, result.returncode, flush=True)
                if label in ('configure', 'build', 'inventory') and result.returncode:
                    raise RuntimeError(profile + ' ' + label + ' failed')
                if label == 'inventory':
                    inventory = json.loads((target / 'inventory.log').read_text())
                    registered = {test['name'] for test in inventory['tests']}
                    required = {'native-parent-metadata', 'native-topology-pixels',
                                'native-device-administration', 'native-global-isolation',
                                'native-application-launch', 'authority-supervisor'}
                    assert required <= registered, sorted(required - registered)
                    row['registered_suites'] = sorted(registered)
                    save()
            row['binary_sha256'] = {p.name: digest(p) for p in build.iterdir()
                                    if p.is_file() and os.access(p, os.X_OK) and p.read_bytes()[:4] == b'\x7fELF'}
            assert digest(server) == row['server']['sha256']
            assert digest(launcher) == row['launcher_sha256']
            row['status'] = 'commands_passed_needs_review' if all(
                item['exit_code'] == 0 for item in row['results']) else 'failed'
            save()
        assert digest(vendor) == record['vendor_sha256']
        assert all(digest(ROOT / name) == expected for name, expected in inputs.items())
        record['status'] = 'commands_passed_needs_review' if all(
            row['status'] == 'commands_passed_needs_review' for row in record['profiles'].values()) else 'failed'
    except BaseException as error:
        record.update(status='failed', error=repr(error))
        raise
    finally:
        record['ended'] = time.time()
        save()
    return 0 if record['status'] == 'commands_passed_needs_review' else 1


if __name__ == '__main__':
    raise SystemExit(main())
