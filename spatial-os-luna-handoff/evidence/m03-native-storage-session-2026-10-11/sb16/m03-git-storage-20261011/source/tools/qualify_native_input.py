#!/usr/bin/env python3
"""Qualify the production native input bridge and pinned server extension."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-report', required=True)
    parser.add_argument('--evidence-dir', required=True)
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0]
    if host not in ('sb16', 'sb16-2'):
        parser.error('Native input qualification is restricted to sb16 and sb16-2')
    folder = (ROOT / args.evidence_dir).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    record = {'host': host, 'utc': datetime.now(timezone.utc).isoformat(), 'status': 'failed',
              'commands': [], 'source_sha256': {}}
    try:
        report_path = (ROOT / args.server_report).resolve()
        server = json.loads(report_path.read_text())
        binary = Path(server['binaries']['Xvfb']['path'])
        if server['host'] != host or server['status'] != 'passed' or digest(binary) != server['binaries']['Xvfb']['sha256']:
            raise ValueError('The server report does not identify a verified local binary')
        if server['lock_sha256'] != digest(ROOT / 'server/base.lock.json'):
            raise ValueError('Server build uses a different source lock')
        for name, expected in server['extension_sources'].items():
            if digest(ROOT / name) != expected:
                raise ValueError('Server build differs from current extension sources: ' + name)
        record['server_report'] = {'path': str(report_path), 'sha256': digest(report_path)}
        record['server'] = server['binaries']['Xvfb']
        inputs = [ROOT / 'CMakeLists.txt', *ROOT.glob('src/**/*'), *ROOT.glob('proto/**/*.proto'),
                  *ROOT.glob('server/**/*'), ROOT / 'native/xprobe.c', ROOT / 'harness/probe.py',
                  ROOT / 'tests/native/input_bridge.cpp', ROOT / 'tests/native/test_input_extension.py',
                  ROOT / 'tools/build_probe.py', ROOT / 'tools/build_xserver.py',
                  ROOT / 'tools/with_test_display.py', Path(__file__)]
        for path in sorted(set(inputs)):
            if path.is_file():
                record['source_sha256'][str(path.relative_to(ROOT))] = digest(path)
        env = {**os.environ, 'SPATIAL_TEST_XSERVER': str(binary),
               'SPATIAL_TEST_XSERVER_LOG': str(folder / 'server.log'),
               'SPATIAL_INPUT_EVIDENCE': str(folder / 'witness'),
               'SPATIAL_XPROBE': str(ROOT / 'build/xprobe'),
               'PYTHONPATH': str(ROOT / 'build/python') + os.pathsep + str(ROOT)}
        commands = [
            ('configure', ['cmake', '-S', '.', '-B', 'build', '-G', 'Ninja',
                           '-DCMAKE_BUILD_TYPE=Debug', '-DSPATIAL_SANITIZERS=OFF']),
            ('build', ['cmake', '--build', 'build', '--parallel', '2', '--target',
                       'spatial-input-bridge', 'input-python-protobuf']),
            ('witness-build', [sys.executable, 'tools/build_probe.py', '--xi2']),
            ('native', [sys.executable, 'tools/with_test_display.py', '--', sys.executable,
                        'tests/native/test_input_extension.py', 'build/spatial-input-bridge']),
        ]
        for label, argv in commands:
            with (folder / (label + '.log')).open('wb') as log:
                result = subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            record['commands'].append({'name': label, 'argv': argv, 'exit_code': result.returncode})
            print(f'{label}: {result.returncode}', flush=True)
            if result.returncode:
                print((folder / (label + '.log')).read_text()[-8000:])
                raise RuntimeError(label + ' failed')
        record['native'] = json.loads((folder / 'witness/summary.json').read_text())
        if not record['native']['passed']:
            raise RuntimeError('Native checks did not pass')
        record['bridge_sha256'] = digest(ROOT / 'build/spatial-input-bridge')
        record['witness_sha256'] = digest(ROOT / 'build/xprobe')
        record['status'] = 'passed'
    except Exception as error:
        record['error'] = str(error)
        print(error, file=sys.stderr)
    finally:
        (folder / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f'{record["status"]}: {folder / "run.json"}')
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
