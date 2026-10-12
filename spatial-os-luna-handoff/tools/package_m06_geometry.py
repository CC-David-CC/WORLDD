#!/usr/bin/env python3
"""Publish a verified M06 compile-only runtime bundle from one build profile."""
import argparse
import ast
import fcntl
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('build', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--off-build', required=True, type=Path,
                        help='Separate Debug BUILD_TESTING=OFF runtime and binding closure')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    build = args.build.resolve()
    output = args.output.resolve()
    off = args.off_build.resolve()
    cache = (off / 'CMakeCache.txt').read_text()
    if 'BUILD_TESTING:BOOL=OFF' not in cache or 'SPATIAL_SANITIZERS:BOOL=OFF' not in cache:
        raise SystemExit('OFF closure must be an explicitly identified non-sanitized production build')
    if not (build / 'worldd').is_file() or not (build / 'runtime.textproto').is_file():
        raise SystemExit('Missing built worldd or adjacent runtime.textproto')
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_suffix(output.suffix + '.lock')
    with lock.open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        required = {'worldd', 'identityd', 'spatial-issuer-client',
                    'spatial-actor-session-client-probe', 'spatial-actor-session-client-tls-probe',
                    'spatial-actor-transition-coordinator-probe',
                    'spatial-actor-transition-integration-driver', 'spatial-host-actor-controller-probe',
                    'spatialwm', 'spatial-xprobe', 'workspaced', 'spatial-app-init', 'spatial-test-host',
                    'spatial-authority-launch-probe', 'spatial-security-auth',
                    'spatial-authority-supervisor', 'spatial-session-application-probe',
                    'spatial-process-scope-probe',  # ResourceBroker's retained process-scope owner.
                    'spatial-storage-broker-launch-probe', 'spatial-confined-xprobe',
                    'spatial-participant-runtime-probe', 'spatial-session-world-probe'}
        if not all((build / name).is_file() and os.access(build / name, os.X_OK) for name in required):
            raise SystemExit('Missing required helper binaries')
        files = {'bin/' + name: build / name for name in required}
        files['bin/runtime.textproto'] = build / 'runtime.textproto'
        for path in (build / 'python').glob('*_pb2.py'):
            files['python/' + path.name] = path
        if not (build / 'python/world_session_pb2.py').is_file() or not (build / 'python/geometry_view_pb2.py').is_file():
            raise SystemExit('Missing generated geometry import closure')
        for name, path in list(files.items()):
            if not name.startswith('python/'):
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.endswith('_pb2') and not (build / 'python' / (alias.name + '.py')).is_file():
                            raise SystemExit(f'Unresolved generated import in {name}: {alias.name}')
                if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith('_pb2'):
                    if not (build / 'python' / (node.module + '.py')).is_file():
                        raise SystemExit(f'Unresolved generated import in {name}: {node.module}')
        for name in ('worldd', 'identityd', 'spatial-issuer-client', 'workspaced', 'spatialwm', 'spatial-app-init'):
            path = off / name
            if not path.is_file() or not os.access(path, os.X_OK):
                raise SystemExit('Missing OFF executable: ' + name)
            files['off-debug/bin/' + name] = path
        if not (off / 'runtime.textproto').is_file():
            raise SystemExit('Missing OFF runtime.textproto')
        files['off-debug/bin/runtime.textproto'] = off / 'runtime.textproto'
        for path in (off / 'python').glob('*_pb2.py'):
            files['off-debug/python/' + path.name] = path
            for node in ast.walk(ast.parse(path.read_text())):
                imports = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                           else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for module in imports:
                    if module.endswith('_pb2') and not (off / 'python' / (module + '.py')).is_file():
                        raise SystemExit('OFF generated closure missing: ' + module)
        for name in ('world_session_pb2.py', 'geometry_view_pb2.py', 'core_pb2.py'):
            if 'off-debug/python/' + name not in files:
                raise SystemExit('Missing OFF required binding: ' + name)
        for folder in ('tests/core', 'tests/native', 'harness', 'tools'):
            for path in (source / folder).rglob('*.py'):
                files[str(path.relative_to(source))] = path
        for path in (source / 'proto').rglob('*.proto'):
            files[str(path.relative_to(source))] = path
        for suffix in ('*.cpp', '*.hpp', '*.h'):
            for path in (source / 'src').rglob(suffix):
                files[str(path.relative_to(source))] = path
        files['CMakeLists.txt'] = source / 'CMakeLists.txt'
        files['docs/M06_GEOMETRY_VIEW_INCREMENT1.txt'] = source / 'docs/M06_GEOMETRY_VIEW_INCREMENT1.txt'
        needed = set()
        for name, path in list(files.items()):
            if not (name.startswith('bin/') or name.startswith('off-debug/bin/')) or not os.access(path, os.X_OK):
                continue
            result = subprocess.run(['ldd', str(path)], text=True, capture_output=True, check=True)
            if 'not found' in result.stdout:
                raise SystemExit('Unresolved dependency in ' + name + ': ' + result.stdout)
            for line in result.stdout.splitlines():
                match = re.search(r'(?:=>\s*)?(/[^\s]+)\s+\(', line)
                if match:
                    needed.add(('off-debug/lib/' if name.startswith('off-debug/') else 'lib/', Path(match.group(1))))
        for prefix, path in sorted(needed):
            files[prefix + path.name] = path
        # Static source pin includes dirty tracked and new files through the per-file hashes.
        base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
        manifest = ('base_commit ' + base + '\n' +
                    ''.join(f'{digest(path)}  {name}\n' for name, path in sorted(files.items())))
        if len(files) < 50:
            raise SystemExit('Suspiciously empty bundle')
        fd, temp_name = tempfile.mkstemp(prefix=output.name + '.tmp-', dir=output.parent)
        os.close(fd)
        temp = Path(temp_name)
        try:
            with tarfile.open(temp, 'w:gz', dereference=True) as archive:
                for name, path in sorted(files.items()):
                    archive.add(path, arcname=name, recursive=False)
                data = manifest.encode()
                with tempfile.TemporaryFile() as stream:
                    stream.write(data); stream.seek(0)
                    info = tarfile.TarInfo('SHA256SUMS'); info.size = len(data); info.mode = 0o644
                    archive.addfile(info, stream)
            names = set()
            with tarfile.open(temp, 'r|gz') as archive:
                for entry in archive:
                    names.add(entry.name)
                    packed = archive.extractfile(entry)
                    if packed is None:
                        raise RuntimeError('Archive entry is unreadable: ' + entry.name)
                    if entry.name == 'SHA256SUMS':
                        if packed.read().decode() != manifest:
                            raise RuntimeError('Manifest changed during packaging')
                    else:
                        expected = files.get(entry.name)
                        if expected is None:
                            raise RuntimeError('Unexpected archive entry: ' + entry.name)
                        actual = hashlib.sha256()
                        for block in iter(lambda: packed.read(1024 * 1024), b''):
                            actual.update(block)
                        if actual.hexdigest() != digest(expected):
                            raise RuntimeError('Archive hash mismatch: ' + entry.name)
            if names != set(files) | {'SHA256SUMS'}:
                raise RuntimeError('Archive contents differ from manifest')
            os.replace(temp, output)
            print(f'{output} sha256={digest(output)} files={len(files)}')
        finally:
            temp.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
