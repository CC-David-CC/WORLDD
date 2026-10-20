#!/usr/bin/env python3
"""Build pinned XLibre in local directories. Never install packages or replace a system server."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--sanitize', action='store_true', help='Instrument the native server with ASan and UBSan')
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0].casefold()
    if host not in ('middle-child', 'rog-ally-x-rc72la'):
        parser.error('X server builds are restricted to middle-child and ROG Ally X')
    evidence = (ROOT / args.evidence_dir).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    lock_path = ROOT / 'server/base.lock.json'
    lock = json.loads(lock_path.read_text())
    local = ROOT / 'local'
    prefix = local / 'xlibre-deps'
    record = {'host': host, 'utc': datetime.now(timezone.utc).isoformat(), 'status': 'failed',
              'base_lock': lock, 'lock_sha256': digest(lock_path),
              'builder_sha256': digest(Path(__file__)), 'sanitizers': args.sanitize, 'commands': [], 'binaries': {}}

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

    def configure(label, path, options, env=None, build=None):
        build = build if build is not None else path / 'build'
        command = ['meson', 'setup']
        if (build / 'meson-private/coredata.dat').is_file():
            command += ['--reconfigure']
        run(label, [*command, build, path, *options], env)

    def patched(base):
        if not lock['patches'] and not lock.get('overlays'):
            return base
        names = [*lock['patches'], *lock.get('overlays', {})]
        inputs = {name: digest(ROOT / name) for name in names}
        spec = {'commit': lock['xserver']['commit'], 'files': inputs,
                'patches': lock['patches'], 'overlays': lock.get('overlays', {})}
        key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        folder = local / 'xlibre-patched' / key
        source = folder / 'source'
        manifest = folder / 'source.json'
        if not folder.exists():
            folder.mkdir(parents=True)
            run('server-worktree', ['git', '-C', base, 'worktree', 'add', '--detach', source,
                                    lock['xserver']['commit']])
            for index, name in enumerate(lock['patches']):
                run(f'server-patch-{index}', ['git', '-C', source, 'apply', '--check', ROOT / name])
                run(f'server-apply-{index}', ['git', '-C', source, 'apply', ROOT / name])
            for name, destination in spec['overlays'].items():
                output = (source / destination).resolve()
                if not output.is_relative_to(source) or output.exists():
                    raise ValueError('Overlay must create a new file inside the source tree')
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes((ROOT / name).read_bytes())
            diff = subprocess.check_output(['git', '-C', str(source), 'diff', '--binary', 'HEAD'])
            manifest.write_text(json.dumps({'spec': spec, 'diff': hashlib.sha256(diff).hexdigest()}, indent=2) + '\n')
        saved = json.loads(manifest.read_text())
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        diff = subprocess.check_output(['git', '-C', str(source), 'diff', '--binary', 'HEAD'])
        if saved['spec'] != spec or head != spec['commit'] or saved['diff'] != hashlib.sha256(diff).hexdigest():
            raise RuntimeError('Patched source changed outside the builder; no automatic reset')
        for name, destination in spec['overlays'].items():
            if digest(source / destination) != inputs[name]:
                raise RuntimeError('Overlay source changed outside the builder')
        record['extension_sources'] = inputs
        record['effective_source'] = str(source)
        return source

    try:
        proto = local / 'xorgproto'
        server = local / 'xlibre'
        checkout('xorgproto', proto)
        checkout('xserver', server)
        server = patched(server)
        configure('protocol-configure', proto, ['--prefix=' + str(prefix)])
        run('protocol-install', ['meson', 'install', '-C', proto / 'build'])
        env = {**os.environ, 'PKG_CONFIG_PATH': str(prefix / 'share/pkgconfig')}
        if shutil.which('ccache'):
            env.update(CC='ccache cc', CXX='ccache c++')
        (local / 'xkb-compiled').mkdir(exist_ok=True)
        options = ['--prefix=' + str(server / 'image'), *lock['meson_options'],
                   '-Dxkb_output_dir=' + str(local / 'xkb-compiled')]
        build = server / ('build-sanitize' if args.sanitize else 'build')
        options += ['-Db_sanitize=' + ('address,undefined' if args.sanitize else 'none')]
        configure('server-configure', server, options, env, build)
        run('server-build', ['ninja', '-C', build, '-j2'], env, timeout=900)
        run('server-unit', ['meson', 'test', '-C', build, '--no-rebuild',
                            '--print-errorlogs', 'unit', 'modesetting symbol test'], env)
        for name, relative in [('Xvfb', 'hw/vfb/Xvfb'), ('Xorg', 'hw/xfree86/Xorg')]:
            path = build / relative
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
