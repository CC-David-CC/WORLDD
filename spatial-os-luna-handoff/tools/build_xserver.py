#!/usr/bin/env python3
"""Build pinned XLibre in local directories. Never install packages or replace a system server."""
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
    parser.add_argument('--evidence-dir', required=True)
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0]
    if host not in ('sb16', 'sb16-2'):
        parser.error('X server builds are restricted to sb16 and sb16-2')
    evidence = (ROOT / args.evidence_dir).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    lock_path = ROOT / 'server/base.lock.json'
    lock = json.loads(lock_path.read_text())
    if lock['patches']:
        parser.error('This baseline builder does not yet apply server patches')
    local = ROOT / 'local'
    prefix = local / 'xlibre-deps'
    record = {'host': host, 'utc': datetime.now(timezone.utc).isoformat(), 'status': 'failed',
              'base_lock': lock, 'lock_sha256': digest(lock_path),
              'builder_sha256': digest(Path(__file__)), 'commands': [], 'binaries': {}}

    def run(label, argv, env=None, timeout=300):
        command = [str(item) for item in argv]
        with (evidence / (label + '.log')).open('wb') as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout)
        record['commands'].append({'name': label, 'argv': command, 'exit_code': result.returncode})
        print(f'{label}: {result.returncode}', flush=True)
        if result.returncode:
            print('\n'.join((evidence / (label + '.log')).read_text().splitlines()[-60:]))
            raise RuntimeError(f'{label} failed')

    def checkout(name, path):
        source = lock[name]
        if not path.exists():
            ref = source['ref']
            if not ref.startswith('refs/tags/'):
                raise ValueError('A pinned release tag is required')
            run(name + '-clone', ['git', 'clone', '--depth', '1', '--branch', ref[len('refs/tags/'):],
                                 source['repository'], path])
        head = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
        if head != source['commit']:
            raise RuntimeError(f'{name} checkout does not match its pin; no automatic reset')
        run(name + '-pristine', ['git', '-C', path, 'diff', '--exit-code', 'HEAD'])

    def configure(label, path, options, env=None):
        build = path / 'build'
        command = ['meson', 'setup']
        if (build / 'meson-private/coredata.dat').is_file():
            command += ['--reconfigure']
        run(label, [*command, build, path, *options], env)

    try:
        proto = local / 'xorgproto'
        server = local / 'xlibre'
        checkout('xorgproto', proto)
        checkout('xserver', server)
        configure('protocol-configure', proto, ['--prefix=' + str(prefix)])
        run('protocol-install', ['meson', 'install', '-C', proto / 'build'])
        env = {**os.environ, 'PKG_CONFIG_PATH': str(prefix / 'share/pkgconfig')}
        (local / 'xkb-compiled').mkdir(exist_ok=True)
        options = ['--prefix=' + str(server / 'image'), *lock['meson_options'],
                   '-Dxkb_output_dir=' + str(local / 'xkb-compiled')]
        configure('server-configure', server, options, env)
        run('server-build', ['ninja', '-C', server / 'build', '-j2'], env, timeout=900)
        run('server-unit', ['meson', 'test', '-C', server / 'build', '--no-rebuild',
                            '--print-errorlogs', 'unit', 'modesetting symbol test'], env)
        for name, relative in [('Xvfb', 'hw/vfb/Xvfb'), ('Xorg', 'hw/xfree86/Xorg')]:
            path = server / 'build' / relative
            record['binaries'][name] = {'path': str(path), 'sha256': digest(path)}
        record['status'] = 'passed'
    except Exception as error:
        record['error'] = str(error)
        print(error, file=sys.stderr)
    finally:
        (evidence / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f'{record["status"]}: {evidence / "run.json"}')
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
