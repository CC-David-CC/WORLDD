#!/usr/bin/env python3
"""Prepare graphics dependencies on the two authorized hosts with a change record."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ['libx11-dev', 'libgl-dev', 'libglew-dev', 'libxcomposite-dev',
            'libxdamage-dev', 'libxfixes-dev', 'libxi-dev', 'libxtst-dev',
            'libxrandr-dev', 'xvfb', 'xauth', 'mesa-utils']


def installed():
    result = subprocess.run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${db:Status-Status}\n'],
                            text=True, capture_output=True, check=True)
    return {name: version for name, version, status in (line.split('\t') for line in result.stdout.splitlines())
            if status == 'installed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-deps', action='store_true')
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] not in ('sb16', 'sb16-2'):
        parser.error('Graphics preparation is restricted to sb16 and sb16-2')
    before = installed()
    missing = [name for name in PACKAGES if name not in before and name + ':amd64' not in before]
    if not missing:
        print('Graphics dependencies are installed.')
        return 0
    if not args.install_deps:
        parser.error('Missing dependencies: ' + ', '.join(missing) + '; use --install-deps')
    directory = ROOT / 'local' / ('graphics-bootstrap-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir(parents=True, exist_ok=False)
    command = ['apt-get', 'install', '-y', '--no-install-recommends', *PACKAGES]
    simulation = subprocess.run(['apt-get', '--simulate', *command[1:]], text=True, capture_output=True, check=True)
    (directory / 'simulation.log').write_text(simulation.stdout + simulation.stderr)
    if re.search(r'^Remv ', simulation.stdout, re.MULTILINE):
        raise RuntimeError('Dependency plan removes packages; no changes made')
    plan = [line.split()[1] for line in simulation.stdout.splitlines() if line.startswith('Inst ')]
    previous = {name: before.get(name, before.get(name + ':amd64')) for name in plan}
    new = [name for name, version in previous.items() if version is None]
    upgrade = [f'{name}={version}' for name, version in previous.items() if version is not None]
    manual = subprocess.check_output(['apt-mark', 'showmanual'], text=True).splitlines()
    record = {'utc': datetime.now(timezone.utc).isoformat(), 'previous_versions': previous,
              'manual_packages_before': manual, 'command': ['sudo', '-n', 'env', 'DEBIAN_FRONTEND=noninteractive',
                 'NEEDRESTART_MODE=l', *command], 'status': 'prepared',
              'recovery_remove_new': ['sudo', '-n', 'apt-get', 'remove', *new],
              'recovery_restore_versions': ['sudo', '-n', 'apt-get', 'install', '--allow-downgrades', *upgrade],
              'recovery_note': 'Review dependency removals first. Restoring old versions requires those versions from an apt source or retained package archive. Do not run autoremove blindly.'}
    (directory / 'change.json').write_text(json.dumps(record, indent=2) + '\n')
    with (ROOT / 'local/system-changes.md').open('a') as log:
        log.write('\n## ' + record['utc'] + ': graphics dependencies\n')
        log.write('Prior versions and recovery commands: ' + str(directory / 'change.json') + '\n')
        log.write('Command: ' + ' '.join(record['command']) + '\n')
        log.write('Automatic service restarts are disabled. No login or display configuration is requested.\n')
    with (directory / 'apt.log').open('wb') as log:
        result = subprocess.run(record['command'], stdout=log, stderr=subprocess.STDOUT)
    record['exit_code'] = result.returncode
    after = installed()
    record['installed_versions'] = {name: after.get(name, after.get(name + ':amd64')) for name in PACKAGES}
    record['status'] = 'passed' if result.returncode == 0 and all(record['installed_versions'].values()) else 'failed'
    (directory / 'change.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'status': record['status'], 'record': str(directory / 'change.json')}))
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
