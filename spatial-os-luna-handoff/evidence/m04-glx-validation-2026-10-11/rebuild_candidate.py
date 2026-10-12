"""Build the private cleanup candidate from the recorded source and local tools."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path('/home/dflanag3/src/spacial_desktop-m04-20261011/spatial-os-luna-handoff')
BUILD = ROOT / 'local/mesa-dril-diagnosis-20261011'
EVIDENCE = ROOT / 'evidence/m04-glx-validation-2026-10-11/rebuild-02'
EVIDENCE.mkdir(parents=True, exist_ok=False)


def bundle_filter(member, destination):
    if member.name == 'build-tools/bin/python3' and member.issym():
        if member.linkname != '/usr/bin/python3':
            raise ValueError('Unexpected Python interpreter link')
        return None
    return tarfile.data_filter(member, destination)


with tarfile.open(BUILD / 'rebuild-inputs.tar.gz') as bundle:
    bundle.extractall(BUILD, filter=bundle_filter)
(BUILD / 'build-tools/bin/python3').symlink_to('/usr/bin/python3')
archive = BUILD / 'mesa-25.2.8.tar.xz'
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
if digest != '097842f3e49d996868b38688db87b006f7d4541e93ce86d2f341d8b3e7be7c93':
    raise SystemExit('Mesa source archive differs from the reviewed input')
(EVIDENCE / 'source.json').write_text(json.dumps({
    'archive_sha256': digest,
    'recorded_source': json.loads((BUILD / 'source.json').read_text()),
    'bundle_sha256': hashlib.sha256((BUILD / 'rebuild-inputs.tar.gz').read_bytes()).hexdigest()}, indent=2))
with tarfile.open(archive) as source:
    source.extractall(BUILD, filter='data')
environment = {
    'PATH': str(BUILD / 'parser-tools/image/usr/bin') + ':' +
            str(BUILD / 'build-tools/bin') + ':' + os.environ['PATH'],
    'PKG_CONFIG_PATH': str(BUILD / 'glvnd-headers/image/usr/lib/x86_64-linux-gnu/pkgconfig'),
    'BISON_PKGDATADIR': str(BUILD / 'parser-tools/image/usr/share/bison'),
    'M4': str(BUILD / 'parser-tools/image/usr/bin/m4')}
env = {**os.environ, **environment}


def run(name, command, cwd=None):
    started = time.monotonic()
    with (EVIDENCE / (name + '.log')).open('wb') as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log,
                                stderr=subprocess.STDOUT)
    (EVIDENCE / (name + '.json')).write_text(json.dumps({
        'command': command, 'cwd': str(cwd) if cwd else None,
        'environment': environment, 'exit_code': result.returncode,
        'seconds': time.monotonic() - started}, indent=2))
    if result.returncode:
        raise SystemExit(result.returncode)


run('patch', ['patch', '-p1', '--batch', '-i', str(BUILD / 'mesa-egl-default-cleanup.patch')],
    BUILD / 'mesa-25.2.8')
run('configure', [str(BUILD / 'build-tools/bin/meson'), 'setup',
    str(BUILD / 'build-egl-candidate'), str(BUILD / 'mesa-25.2.8'),
    '--prefix=' + str(BUILD / 'image'), '--wrap-mode=nodownload',
    '-Dgallium-drivers=softpipe', '-Dvulkan-drivers=[]', '-Degl=enabled',
    '-Dgbm=enabled', '-Dglx=disabled', '-Dplatforms=[]', '-Dllvm=disabled',
    '-Dglvnd=enabled', '-Dgles1=disabled', '-Dgles2=enabled',
    '-Dvideo-codecs=[]', '-Dbuild-tests=false'])
run('compile', ['ninja', '-C', str(BUILD / 'build-egl-candidate'), '-j2',
                'src/egl/libEGL_mesa.so.0.0.0'])
library = BUILD / 'build-egl-candidate/src/egl/libEGL_mesa.so.0.0.0'
(EVIDENCE / 'complete.json').write_text(json.dumps({
    'library': str(library), 'sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
    'status': 'built; runtime qualification remains pending'}, indent=2))
