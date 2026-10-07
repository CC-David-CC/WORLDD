#!/usr/bin/env python3
"""Build and check the core on an authorized host. Never acquire dependencies."""
import argparse
import hashlib
import json
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', choices=['P00', 'P01', 'P02'], required=True)
    parser.add_argument('--build-dir', default='build')
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0]
    if host not in ('sb16', 'sb16-2'):
        parser.error('Linux builds and tests are restricted to sb16 and sb16-2')
    build = (ROOT / args.build_dir).resolve()
    evidence = (ROOT / args.evidence_dir).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    binary = build / 'spatial-test-host'
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join([str(build / 'python'), str(ROOT), env.get('PYTHONPATH', '')])
    commands = [
        ('configure', ['cmake', '-S', '.', '-B', str(build), '-G', 'Ninja',
                       '-DCMAKE_BUILD_TYPE=Debug', f'-DSPATIAL_SANITIZERS={"ON" if args.sanitize else "OFF"}']),
        ('build', ['cmake', '--build', str(build), '--parallel', '2']),
        ('harness', [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests/harness', '-t', '.', '-v']),
        ('core', ['ctest', '--test-dir', str(build), '--output-on-failure', '-V']),
        ('acceptance', [sys.executable, 'tools/gate.py', '--through', args.through,
                        '--sut', json.dumps([str(binary)]), '--report', str(evidence / 'acceptance.json')]),
    ]
    record = {'host': host, 'utc': datetime.now(timezone.utc).isoformat(), 'through': args.through,
              'sanitizers': args.sanitize, 'status': 'failed', 'commands': [], 'source_sha256': {}}
    locked = json.loads((ROOT / 'acceptance.lock.json').read_text())['files']
    inputs = {ROOT / name for name in locked}
    inputs.update([ROOT / 'CMakeLists.txt', *ROOT.glob('src/**/*'), *ROOT.glob('proto/**/*.proto'),
                   *ROOT.glob('tests/core/*.py'), ROOT / 'tools/qualify_core.py', ROOT / 'acceptance.lock.json'])
    for path in sorted(inputs):
        if path.is_file():
            record['source_sha256'][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for label, command in commands:
        with (evidence / f'{label}.log').open('wb') as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        record['commands'].append({'name': label, 'argv': command, 'exit_code': result.returncode})
        print(f'{label}: {result.returncode}', flush=True)
        if result.returncode:
            print('\n'.join((evidence / f'{label}.log').read_text().splitlines()[-80:]))
            break
    else:
        record['status'] = 'passed'
        sys.path[:0] = [str(build / 'python'), str(ROOT)]
        from harness.wire import frame, make_request, read_message, pb, result_value
        hello = subprocess.run([str(binary), '--state-dir', str(evidence / 'hello-state')],
                               input=frame(make_request('system.hello', {})),
                               stdout=subprocess.PIPE, check=True, timeout=5)
        record['hello'] = result_value(read_message(io.BytesIO(hello.stdout), pb.Reply))
    (evidence / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f'{record["status"]}: {evidence / "run.json"}')
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
