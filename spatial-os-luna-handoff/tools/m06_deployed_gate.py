#!/usr/bin/env python3
"""Build, package, deploy-verify, or run the artifact-only M06 gate."""
from __future__ import annotations
import argparse
import ast
import copy
import gzip
import shutil
import stat
import shlex
import hashlib
import io
import ipaddress
import json
import os
import pwd
from pathlib import Path
import re
import socket
import signal
import subprocess
import sys
import tarfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CODING_HOSTS = {"G164": "a5500", "a5500": "a5500", "middle-child": "middle-child"}
TEST_HOSTS = {"sb16", "sb16-2"}
EVIDENCE_KEYS = ("SPATIAL_SECURITY_EVIDENCE", "SPATIAL_SCOPE_EVIDENCE", "SPATIAL_RENDER_EVIDENCE")
PROFILES = ("Debug", "ASan-UBSan")
REQUIRED = {
    "native-parent-metadata", "native-topology-pixels", "native-device-administration",
    "native-global-isolation", "native-application-launch", "authority-supervisor",
    "world-shared-state", "actor-session-client", "actor-session-client-tls",
    "actor-transition-coordinator", "actor-transition-real-world", "actor-transition-recovery",
    "host-actor-controller", "host-actor-controller-native", "presentation-owner",
    "presentation-stream", "shared-render-client", "presentation-server",
    "presentation-input-bindings", "geometry-presentation-native",
    "geometry-native-crosshost", "geometry-process-identity-witness",
    "geometry-native-fixture-regressions",
}
DEDICATED_CROSSHOST = {"geometry-native-crosshost"}
STORAGE_LOCAL_EVIDENCE_TESTS = ("session-storage-owner", "session-storage-contention")
GEOMETRY_CASES = [f"test_{i:02d}_" for i in range(1, 10)]
SAN_RE = re.compile(r"AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|runtime error:")
INNER_SKIP_RE = re.compile(r'^(?:\d+: )?(?:OK|FAILED)\s*\([^\n)]*skipped\s*=|'
                          r'^(?:\d+: )?.*\.\.\. skipped(?:\s|$)|\[\s*SKIPPED\s*\]', re.M)

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def run(command, *, cwd=None, env=None, log=None, timeout=None):
    start = time.monotonic()
    try:
        if log:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            with Path(log).open("wb") as stream:
                p = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        else:
            p = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        p = subprocess.CompletedProcess(command, 124, stdout=b"TIMEOUT: deadline exceeded; process terminated\n")
        if log:
            with Path(log).open("ab") as stream: stream.write(p.stdout)
    return p, time.monotonic() - start

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

def source_pin():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True)
    if dirty:
        raise RuntimeError("tracked source is dirty; commit and freeze the candidate before packaging")
    locked = json.loads((ROOT / "acceptance.lock.json").read_text())["files"]
    for name, expected in locked.items():
        if sha(ROOT / name) != expected:
            raise RuntimeError("locked baseline mismatch: " + name)
    tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
    selected = {name for name in tracked if not name.startswith(("evidence/", "local/"))}
    # Detect untracked code that a commit pin would otherwise omit.
    for base in ("CMakeLists.txt", "acceptance.lock.json", "docs", "src", "proto", "tests", "tools", "config", "server", "harness", "native", "scripts", "cmake"):
        item = ROOT / base
        if item.is_file():
            selected.add(base)
        elif item.is_dir():
            selected.update(str(p.relative_to(ROOT)) for p in item.rglob("*")
                            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    untracked = sorted(selected - tracked)
    if untracked:
        raise RuntimeError("candidate source has untracked files: " + ", ".join(untracked))
    files = {name: sha(ROOT / name) for name in sorted(selected)}
    return commit, files, locked

def file_manifest(root: Path):
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("bundle input contains link: " + str(path))
        if path.is_file():
            result[str(path.relative_to(root))] = sha(path)
        elif not path.is_dir():
            raise RuntimeError("bundle input contains special file: " + str(path))
    return result


def _hash_member_stream(stream, destination=None, expected_size=None):
    digest = hashlib.sha256()
    size = 0
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
        size += len(block)
        if destination is not None:
            destination.write(block)
    if expected_size is not None and size != expected_size:
        raise RuntimeError('archive member size mismatch')
    return digest.hexdigest()


def stream_archive_hashes(bundle: Path):
    """Hash regular members in one forward-only gzip-tar pass."""
    files = {}
    with gzip.open(bundle, 'rb') as compressed:
        with tarfile.open(fileobj=compressed, mode='r|') as archive:
            for member in archive:
                if not member.isfile():
                    continue
                if member.name in files:
                    raise RuntimeError('duplicate archive file: ' + member.name)
                stream = archive.extractfile(member)
                if stream is None:
                    raise RuntimeError('archive member is unreadable: ' + member.name)
                files[member.name] = _hash_member_stream(stream, expected_size=member.size)
        while compressed.read(1024 * 1024):
            pass
    return files


def extract_verified_bundle(bundle: Path, out: Path, expected_files):
    """Validate, hash, and stage archive files in one sequential gzip-tar pass."""
    if out.is_symlink() or not out.is_dir() or any(out.iterdir()):
        raise RuntimeError('bundle destination must be a fresh empty directory')
    stage = out / ('.m06-extract-' + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    files, directories = {}, []
    try:
        root = stage.resolve(strict=True)
        with gzip.open(bundle, 'rb') as compressed:
            with tarfile.open(fileobj=compressed, mode='r|') as archive:
                for member in archive:
                    if not (member.isfile() or member.isdir()) or member.name.startswith('/') or '..' in Path(member.name).parts:
                        raise RuntimeError('unsafe bundle member: ' + member.name)
                    target = stage / member.name
                    resolved = target.resolve(strict=False)
                    if root not in resolved.parents:
                        raise RuntimeError('unsafe bundle path: ' + member.name)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                        directories.append((target, member.mode & 0o777))
                        continue
                    if member.name in files:
                        raise RuntimeError('duplicate archive file: ' + member.name)
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise RuntimeError('archive member is unreadable: ' + member.name)
                    with target.open('xb') as output:
                        files[member.name] = _hash_member_stream(stream, output, member.size)
                    os.chmod(target, member.mode & 0o777)
            while compressed.read(1024 * 1024):
                pass
        if files != expected_files:
            raise RuntimeError('archive file manifest mismatch')
        for directory, mode in reversed(directories):
            os.chmod(directory, mode)
        for child in stage.iterdir():
            os.replace(child, out / child.name)
        stage.rmdir()
        return files
    except BaseException:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        raise


def generated_closure(build: Path):
    folder = build / "python"
    files = sorted(folder.rglob("*_pb2.py")) if folder.exists() else []
    if not files:
        raise RuntimeError("generated protobuf Python bindings are absent")
    names = {p.stem for p in files}
    imports = {}
    for path in files:
        tree = ast.parse(path.read_text())
        refs = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names
                if alias.name.endswith("_pb2")}
        refs |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                 for alias in node.names if alias.name.endswith('_pb2')}
        missing = refs - names
        if missing:
            raise RuntimeError(f"generated binding closure missing {path.name}: {sorted(missing)}")
        imports[str(path.relative_to(build))] = sorted(refs)
    return imports


def verify_production_client(source, build):
    client = source / 'tools/host_session_client.py'
    if not client.is_file():
        raise RuntimeError('BLOCKED production host-session Python client missing')
    tree = ast.parse(client.read_text())
    imports = {alias.name.split('.')[-1] for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names if alias.name.endswith('_pb2')}
    imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                for alias in node.names if alias.name.endswith('_pb2')}
    available = {path.stem for path in (build / 'python').rglob('*_pb2.py')}
    if not imports or imports - available:
        raise RuntimeError('production host client binding closure missing: ' + repr(sorted(imports - available)))
    return dict(path=str(client.relative_to(source)), sha256=sha(client), bindings=sorted(imports))

def ctest_metadata(build: Path):
    paths = sorted(build.rglob("CTestTestfile.cmake"))
    if not paths:
        raise RuntimeError("CTest launch metadata absent")
    return paths

def replace_text(value, mapping):
    # Apply once, longest prefix first. Never rewrite a newly inserted destination.
    substitutions = dict((old, new) for old, new in mapping if old and old != new)
    if not substitutions:
        return value
    pattern = re.compile('|'.join(re.escape(old) for old in sorted(substitutions, key=len, reverse=True)))
    return pattern.sub(lambda match: substitutions[match.group()], value)


def replace_metadata(build, mapping):
    edits = []
    for path in ctest_metadata(build):
        before = path.read_bytes()
        data = replace_text(before.decode('utf-8'), mapping).encode('utf-8')
        if data != before:
            path.write_bytes(data)
            edits.append(dict(file=str(path.relative_to(build)), before=hashlib.sha256(before).hexdigest(), after=sha(path)))
    return edits

def map_inventory(value, mapping):
    if isinstance(value, str):
        return replace_text(value, mapping)
    if isinstance(value, list):
        return [map_inventory(x, mapping) for x in value]
    if isinstance(value, dict):
        return {k: map_inventory(v, mapping) for k, v in value.items()}
    return value


def require_inventory_equal(observed, original, mapping):
    if observed != map_inventory(original, mapping):
        raise RuntimeError('relocated CTest commands/properties differ from prepared inventory')


def _require_real_directory(path: Path, *, create=False):
    """Validate one trusted directory component without following a symlink."""
    created = False
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise RuntimeError('required evidence parent is missing: ' + str(path))
        path.mkdir(mode=0o700)
        created = True
        info = path.lstat()
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError('evidence path component is not a real directory: ' + str(path))
    if info.st_uid != os.getuid():
        raise RuntimeError('evidence path component has unexpected owner: ' + str(path))
    mode = info.st_mode & 0o777
    if created and mode != 0o700:
        raise RuntimeError('new evidence directory mode is not 0700: ' + str(path))
    return dict(path=str(path), uid=info.st_uid, mode=mode)


def create_storage_evidence_attempt(source: Path, host: str, attempt: str):
    """Reserve a new ignored source/local root for the two storage CTest fixtures."""
    _require_real_directory(source)
    local = source / 'local'
    local_receipt = _require_real_directory(local, create=True)
    container = local / 'm06-gate-storage'
    container_receipt = _require_real_directory(container, create=True)
    host_root = container / host
    host_receipt = _require_real_directory(host_root, create=True)
    token = hashlib.sha256((host + '\0' + attempt).encode()).hexdigest()[:24]
    attempt_root = host_root / token
    attempt_root.mkdir(mode=0o700, exist_ok=False)
    attempt_info = attempt_root.lstat()
    local_resolved = local.resolve(strict=True)
    resolved = attempt_root.resolve(strict=True)
    if (attempt_root.is_symlink() or local_resolved not in resolved.parents
            or attempt_info.st_uid != os.getuid() or attempt_info.st_mode & 0o777 != 0o700):
        raise RuntimeError('storage evidence attempt escaped source/local or has unsafe ownership/mode')
    return dict(root=attempt_root, parents=[local_receipt, container_receipt, host_receipt],
                attempt=dict(path=str(attempt_root), uid=attempt_info.st_uid,
                             mode=attempt_info.st_mode & 0o777), token=token)


def create_storage_evidence_profile(attempt_root: Path, source: Path, profile: str):
    leaf = attempt_root / profile
    leaf.mkdir(mode=0o700, exist_ok=False)
    info = leaf.lstat()
    local = (source / 'local').resolve(strict=True)
    resolved = leaf.resolve(strict=True)
    if leaf.is_symlink() or local not in resolved.parents:
        raise RuntimeError('storage evidence profile escaped source/local')
    if info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o700:
        raise RuntimeError('storage evidence profile has unexpected owner or mode')
    registrations = {}
    for name in STORAGE_LOCAL_EVIDENCE_TESTS:
        target = leaf / name
        target.mkdir(mode=0o700, exist_ok=False)
        target_info = target.lstat()
        if target.is_symlink() or target_info.st_uid != os.getuid() or target_info.st_mode & 0o777 != 0o700:
            raise RuntimeError('storage registration evidence leaf has unsafe owner or mode: ' + name)
        resolved_target = target.resolve(strict=True)
        if local not in resolved_target.parents:
            raise RuntimeError('storage registration evidence leaf escaped source/local')
        registrations[name] = dict(path=str(target), uid=target_info.st_uid, mode=target_info.st_mode & 0o777)
    return dict(path=str(leaf), uid=info.st_uid, mode=info.st_mode & 0o777, registrations=registrations)


def verify_storage_evidence_path(source: Path, path: Path):
    local = source / 'local'
    _require_real_directory(local)
    local_resolved = local.resolve(strict=True)
    try:
        relative = path.relative_to(local)
    except ValueError as error:
        raise RuntimeError('storage evidence path is outside source/local') from error
    current = local
    rows = []
    for part in relative.parts:
        current = current / part
        rows.append(_require_real_directory(current))
    resolved = path.resolve(strict=True)
    if local_resolved not in resolved.parents or not rows:
        raise RuntimeError('storage evidence path escaped source/local')
    if rows[-1]['mode'] != 0o700:
        raise RuntimeError('storage evidence leaf mode changed')
    return rows


def relocate_storage_evidence_inventory(inventory, from_root: Path, targets):
    """Change only the two declared storage tests' evidence environment values."""
    result = copy.deepcopy(inventory)
    seen = set()
    for test in result:
        if test.get('name') not in STORAGE_LOCAL_EVIDENCE_TESTS:
            continue
        name = test['name']
        if name in seen:
            raise RuntimeError('duplicate storage evidence registration: ' + name)
        seen.add(name)
        properties = [item for item in test.get('properties', []) if item.get('name') == 'ENVIRONMENT']
        if len(properties) != 1 or not isinstance(properties[0].get('value'), list):
            raise RuntimeError('unsupported storage test ENVIRONMENT metadata: ' + name)
        values = properties[0]['value']
        matches = [i for i, value in enumerate(values)
                   if isinstance(value, str) and value.startswith('SPATIAL_SECURITY_EVIDENCE=')]
        if len(matches) != 1:
            raise RuntimeError('storage test must declare one security evidence path: ' + name)
        index = matches[0]
        if values[index] != 'SPATIAL_SECURITY_EVIDENCE=' + str(from_root):
            raise RuntimeError('storage test evidence source differs from reviewed mapping: ' + name)
        target = targets.get(name)
        if not target:
            raise RuntimeError('storage test evidence target missing: ' + name)
        values[index] = 'SPATIAL_SECURITY_EVIDENCE=' + str(target)
    if seen != set(STORAGE_LOCAL_EVIDENCE_TESTS):
        raise RuntimeError('storage evidence registrations missing or duplicated')
    return result


def relocate_storage_evidence_metadata(build: Path, from_root: Path, targets):
    """Edit the evidence value only in the two named generated CTest property rows."""
    edits = []
    for name in STORAGE_LOCAL_EVIDENCE_TESTS:
        marker = 'set_tests_properties([=[' + name + ']=] PROPERTIES '
        found = []
        for path in ctest_metadata(build):
            original = path.read_text(encoding='utf-8')
            lines = original.splitlines(keepends=True)
            for index, line in enumerate(lines):
                if line.startswith(marker):
                    found.append((path, lines, index, original))
        if len(found) != 1:
            raise RuntimeError('expected one generated CTest property row for ' + name)
        path, lines, index, original = found[0]
        line = lines[index]
        old = 'SPATIAL_SECURITY_EVIDENCE=' + str(from_root)
        target = targets.get(name)
        if not target:
            raise RuntimeError('storage metadata target missing: ' + name)
        new = 'SPATIAL_SECURITY_EVIDENCE=' + str(target)
        if line.count(old) != 1 or line.count('SPATIAL_SECURITY_EVIDENCE=') != 1:
            raise RuntimeError('unsupported storage evidence CTest metadata row: ' + name)
        lines[index] = line.replace(old, new, 1)
        updated = ''.join(lines)
        path.write_text(updated, encoding='utf-8')
        edits.append(dict(test=name, file=str(path.relative_to(build)),
                          before=hashlib.sha256(original.encode()).hexdigest(), after=sha(path),
                          evidence_path=str(target)))
    return edits


def storage_evidence_inventory(root: Path):
    """Hash logs and structured receipts, excluding sparse images and cleaned volume mountpoints."""
    allowed = {'.log', '.json', '.pb', '.txt', '.textproto'}
    hashes, log_paths, cleanups, exclusions = {}, [], [], []
    def walk_error(error):
        raise RuntimeError('cannot inspect storage evidence tree: ' + str(error)) from error
    for current, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        here = Path(current)
        kept = []
        for name in directories:
            child = here / name
            if child.is_symlink():
                raise RuntimeError('storage evidence contains a directory symlink: ' + str(child))
            if name == 'volume':
                info = child.lstat()
                if not stat.S_ISDIR(info.st_mode) or os.path.ismount(child):
                    raise RuntimeError('storage volume exclusion is not an unmounted real directory: ' + str(child))
                exclusions.append(dict(path=str(child.relative_to(root)), kind='cleaned-volume-mountpoint'))
                continue
            kept.append(name)
        directories[:] = kept
        for name in filenames:
            path = here / name
            if path.is_symlink():
                raise RuntimeError('storage evidence contains a file symlink: ' + str(path))
            if name == 'volume.img':
                if not path.is_file():
                    raise RuntimeError('storage sparse image exclusion is not a regular file: ' + str(path))
                exclusions.append(dict(path=str(path.relative_to(root)), kind='sparse-volume-image'))
                continue
            if path.suffix.lower() not in allowed:
                continue
            if not path.is_file():
                raise RuntimeError('storage evidence receipt is not a regular file: ' + str(path))
            relative = str(path.relative_to(root))
            hashes[relative] = sha(path)
            if path.suffix.lower() == '.log':
                log_paths.append(path)
            if name == 'volume-setup.json':
                receipt = json.loads(path.read_text())
                volume, image = path.parent / 'volume', path.parent / 'volume.img'
                loop, commands = receipt.get('loop'), receipt.get('commands')
                cleanup_commands = commands if isinstance(commands, list) else []
                umount_ok = any(item.get('argv') == ['umount', str(volume)] and item.get('exit_code') == 0
                    for item in cleanup_commands if isinstance(item, dict))
                detach_ok = any(item.get('argv') == ['losetup', '-d', loop] and item.get('exit_code') == 0
                    for item in cleanup_commands if isinstance(item, dict))
                detached_check_ok = any(item.get('argv') == ['losetup', '-j', str(image)]
                    and item.get('exit_code') == 0 and item.get('stdout') == ''
                    for item in cleanup_commands if isinstance(item, dict))
                if (receipt.get('status') != 'prepared' or receipt.get('cleanup_confirmed') is not True
                        or receipt.get('mounted') is not False or receipt.get('loop_attached') is not False
                        or receipt.get('volume') != str(volume.resolve(strict=False))
                        or not isinstance(loop, str) or not re.fullmatch(r'/dev/loop[0-9]+', loop)
                        or not umount_ok or not detach_ok or not detached_check_ok
                        or not volume.is_dir() or volume.is_symlink() or os.path.ismount(volume)):
                    raise RuntimeError('storage volume cleanup receipt is incomplete or inconsistent: ' + str(path))
                cleanups.append(dict(path=relative, sha256=hashes[relative], loop=loop,
                    volume=str(volume.relative_to(root)), cleanup_commands_verified=True))
    if not cleanups:
        raise RuntimeError('storage registration has no successful volume cleanup receipt: ' + str(root))
    return dict(files=hashes, logs=log_paths, cleanups=cleanups, exclusions=exclusions)


def scan_storage_evidence(registrations):
    files, cleanups, exclusions = {}, {}, {}
    log_count = 0
    for name in STORAGE_LOCAL_EVIDENCE_TESTS:
        if name not in registrations:
            raise RuntimeError('storage evidence registration missing: ' + name)
        row = storage_evidence_inventory(registrations[name])
        files[name] = row['files']
        cleanups[name] = row['cleanups']
        exclusions[name] = row['exclusions']
        for path in row['logs']:
            log_count += 1
            if SAN_RE.search(path.read_text(errors='replace')):
                raise RuntimeError('sanitizer diagnostic in storage evidence log: ' + name + '/' + str(path))
    return dict(files=files, cleanups=cleanups, exclusions=exclusions, logs_scanned=log_count)


def reject_inner_skips(output):
    if INNER_SKIP_RE.search(output):
        raise RuntimeError('inner test reports a skipped case')


def startup_packaging_command(source, executable, bindings):
    return ['python3', str(source / 'tests/core/test_host_session_startup.py'),
            str(executable), str(bindings)]


def stage_environment(environment, label, production, source):
    selected = environment.copy()
    if label == 'production-off-startup':
        selected['PYTHONPATH'] = str(production / 'python') + ':' + str(source)
    return selected


def copy_runtime_closure(source, destination, hashes):
    for rel, expected in hashes.items():
        original = source / rel
        if sha(original) != expected:
            raise RuntimeError('runtime closure changed before packaging: ' + rel)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        if sha(target) != expected:
            raise RuntimeError('runtime closure copy mismatch: ' + rel)


def add_startup_bindings(build, inventory, bindings):
    """Record one explicit stronger invocation; do not edit test/CMake source."""
    result = copy.deepcopy(inventory)
    tests = [test for test in result if test['name'] == 'host-session-startup']
    if len(tests) != 1:
        raise RuntimeError('mandatory host-session-startup registration missing or duplicated')
    command = tests[0]['command']
    if (len(command) != 3 or Path(command[1]).name != 'test_host_session_startup.py'
            or Path(command[2]).name != 'spatial-host-session'):
        raise RuntimeError('host-session-startup CLI changed; review its packaging invocation')
    edits = []
    marker = 'add_test([=[host-session-startup]=] '
    # CMake's current generated form is inspected before this narrow addition.
    for path in ctest_metadata(build):
        original = path.read_text()
        lines = original.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.startswith(marker):
                if not line.rstrip().endswith(')'):
                    raise RuntimeError('unsupported multiline startup registration')
                argument = str(bindings)
                bracket = '='
                while ']' + bracket + ']' in argument:
                    bracket += '='
                lines[index] = line.rstrip()[:-1] + ' [' + bracket + '[' + argument + ']' + bracket + '])\n'
                edits.append(dict(file=str(path.relative_to(build)), before=sha(path), argument=argument))
        updated = ''.join(lines)
        if updated != original:
            path.write_text(updated, newline='\n')
    if len(edits) != 1:
        raise RuntimeError('startup metadata augmentation must affect exactly one command')
    command.append(str(bindings))
    return result, edits

def verify_inventory_paths(inventory, source: Path, build: Path, output_roots=()):
    roots = (source.as_posix(), build.as_posix())
    inputs = set()
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
    for value in strings(inventory):
        if os.name == 'nt':
            value = value.replace('\\', '/')
        for root in roots:
            for match in re.finditer(re.escape(root) + r"(?:/[^;:\s\"'\\]*)?", value):
                path = Path(match.group())
                if any(path == Path(root) or Path(root) in path.parents for root in output_roots):
                    continue
                if not path.exists():
                    raise RuntimeError("CTest source/build closure missing: " + str(path))
                if path.is_file():
                    inputs.add(path)
    return inputs

def runtime_files(runtime, host, profile):
    try:
        row = runtime["hosts"][host][profile]
        entries = [row["server_receipt"], row["xace_wrapper"], *row["egl_files"], *row["installed_helpers"]]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"BLOCKED runtime manifest incomplete for {host}/{profile}: {exc}") from exc
    if not row["egl_files"] or not row["installed_helpers"]:
        raise RuntimeError(f"BLOCKED EGL and installed helper lists required for {host}/{profile}")
    if not {'spatial-bwrap', 'spatial-storage-owner'} <= {Path(item['path']).name for item in row['installed_helpers']}:
        raise RuntimeError('both production root-owned helpers are required')
    for key, entries_key in (("required_egl_names", "egl_files"),
                             ("required_helper_names", "installed_helpers")):
        required_names = runtime.get(key)
        actual_names = {Path(item["path"]).name for item in row[entries_key] if isinstance(item, dict) and item.get("path")}
        if not isinstance(required_names, list) or not required_names or len(required_names) != len(set(required_names)) or not set(required_names) <= actual_names:
            raise RuntimeError(f"BLOCKED {key} inventory incomplete for {host}/{profile}")
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("path") or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
            raise RuntimeError(f"BLOCKED invalid runtime file entry for {host}/{profile}")
    return row, entries

def verify_helper_ownership(entries):
    if os.name == 'posix':
        for entry in entries:
            info = Path(entry['path']).stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('installed helper must be root-owned and not group/world writable')


def verify_runtime(runtime, host, profile, source):
    row, entries = runtime_files(runtime, host, profile)
    for entry in entries:
        path = Path(entry["path"])
        if not path.is_absolute() or not path.is_file() or sha(path) != entry["sha256"]:
            raise RuntimeError(f"BLOCKED runtime file missing or changed for {host}/{profile}: {path}")
    verify_helper_ownership(row['installed_helpers'])
    receipt = json.loads(Path(row["server_receipt"]["path"]).read_text())
    if receipt.get("status") != "passed" or receipt.get("sanitizers") != (profile == "ASan-UBSan"):
        raise RuntimeError("server receipt status/profile mismatch")
    if receipt.get("host") != host:
        raise RuntimeError("server receipt host mismatch")
    # Actual build_xserver.py receipts pin every project patch and overlay.
    # Reuse the compatible M06 XACE build only when those exact bytes still match.
    extensions = receipt.get("extension_sources", {})
    lock = json.loads((source / 'server/base.lock.json').read_text())
    required_extensions = set(lock['patches']) | set(lock.get('overlays', {}))
    if not extensions or set(extensions) != required_extensions:
        raise RuntimeError("server extension inventory differs from candidate lock")
    if receipt.get('base_lock') != lock or receipt.get('lock_sha256') != sha(source / 'server/base.lock.json'):
        raise RuntimeError("server base lock differs from candidate")
    for name, expected in extensions.items():
        if not (source / name).is_file() or sha(source / name) != expected:
            raise RuntimeError("server extension differs from candidate: " + name)
    if not receipt.get('commands') or any(command.get('exit_code') != 0 for command in receipt['commands']):
        raise RuntimeError("server build commands did not all succeed")
    binary = receipt.get("binaries", {}).get("Xvfb")
    if not binary or not Path(binary.get("path", "")).is_file() or sha(Path(binary["path"])) != binary.get("sha256"):
        raise RuntimeError("server binary receipt mismatch")
    wrapper_tree = ast.parse(Path(row['xace_wrapper']['path']).read_text())
    updates = [node for node in ast.walk(wrapper_tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
               and node.func.value.id == 'env' and node.func.attr == 'update']
    if len(updates) != 1 or len(updates[0].args) != 1:
        raise RuntimeError('wrapper must use the reviewed explicit environment template')
    graphics_env = ast.literal_eval(updates[0].args[0])
    expected_wrapper = ('import os, sys\nenv = dict(os.environ)\nenv.update(' + repr(graphics_env)
                        + ')\nserver = ' + repr(binary['path'])
                        + '\nos.execve(server, [server, *sys.argv[1:]], env)\n')
    if ast.dump(wrapper_tree) != ast.dump(ast.parse(expected_wrapper)):
        raise RuntimeError('wrapper does not execute the verified server with the reviewed template')
    pinned_graphics = {entry['path'] for entry in row['egl_files']}
    vendor = graphics_env.get('__EGL_VENDOR_LIBRARY_FILENAMES')
    if vendor not in pinned_graphics:
        raise RuntimeError('wrapper EGL vendor configuration is not pinned')
    library = json.loads(Path(vendor).read_text())['ICD']['library_path']
    if library not in pinned_graphics:
        raise RuntimeError('wrapper EGL vendor library is not pinned')
    return row


def retained_files(build):
    keep = set()
    for path in build.rglob('*'):
        if not path.is_file():
            continue
        rel = str(path.relative_to(build))
        if (path.name == 'CTestTestfile.cmake' or 'python' in path.relative_to(build).parts
                or path.suffix in ('.textproto', '.json', '.conf', '.cfg', '.ini', '.yaml', '.yml', '.toml')
                or '.so' in path.name or os.access(path, os.X_OK)):
            keep.add(rel)
    if 'runtime.textproto' not in keep:
        raise RuntimeError('generated runtime.textproto missing')
    return keep

def prepare(a):
    import fcntl
    actual_host = socket.gethostname().split(".")[0]
    host = CODING_HOSTS.get(actual_host)
    if host is None:
        raise RuntimeError("preparation is allowed only on a5500 (G164) or middle-child")
    commit, files, locked = source_pin()
    if a.candidate != commit:
        raise RuntimeError(f"candidate mismatch: expected {a.candidate}, checkout is {commit}")
    runtime = json.loads(a.runtime_manifest.read_text())
    if runtime.get("candidate") != commit:
        raise RuntimeError("runtime manifest candidate mismatch")
    for test_host in sorted(TEST_HOSTS):
        for profile in PROFILES:
            runtime_files(runtime, test_host, profile)
    attempt = a.attempt or f"m06-{commit[:12]}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    dest = a.output.resolve() / attempt
    dest.mkdir(parents=True, exist_ok=False)
    build_root = dest / "build"
    rec = dict(schema=2, attempt=attempt, status="building", candidate=commit, source_root=str(ROOT),
               source_sha256=files, locked_files_verified=len(locked), builder=host,
               builder_hostname=actual_host, profiles={}, stage_history=[], runtime_manifest=runtime)
    write_json(dest / "provenance.json", rec)
    try:
        for profile in PROFILES:
            build = build_root / profile
            logdir = dest / profile; logdir.mkdir()
            evidence = dest / "ctest-evidence" / profile
            xserver_placeholder = dest / "ctest-runtime" / profile / "xace-wrapper"
            env = os.environ.copy()
            env["SPATIAL_TEST_XSERVER"] = str(xserver_placeholder)
            for key in EVIDENCE_KEYS:
                env[key] = str(evidence / key.lower())
            config = ["cmake", "-S", str(ROOT), "-B", str(build), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Debug",
                      "-DBUILD_TESTING=ON", "-DSPATIAL_INPUT_TESTS=ON",
                      "-DSPATIAL_SANITIZERS=" + ("ON" if profile == "ASan-UBSan" else "OFF")]
            lock_path = dest / (profile + ".build.lock")
            with lock_path.open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for label, command, timeout in (("configure", config, 900),
                    ("build", ["cmake", "--build", str(build), "--parallel", "2"], 3600),
                    ("inventory", ["ctest", "--test-dir", str(build), "--show-only=json-v1"], 120)):
                    result, seconds = run(command, cwd=ROOT, env=env, log=logdir / (label + ".log"), timeout=timeout)
                    rec["stage_history"].append(dict(profile=profile, name=label, command=command,
                                                       exit_code=result.returncode, seconds=seconds))
                    write_json(dest / "provenance.json", rec)
                    if result.returncode:
                        raise RuntimeError(f"{profile} {label} failed; see raw log")
            if source_pin() != (commit, files, locked):
                raise RuntimeError("source pin changed during build")
            inventory = json.loads((logdir / "inventory.log").read_text())["tests"]
            inventory_inputs = verify_inventory_paths(inventory, ROOT, build, (evidence, xserver_placeholder))
            generated = {str(p.relative_to(build)): sha(p) for p in (build / "python").rglob("*") if p.is_file()}
            closure = generated_closure(build)
            executables = {rel: sha(build / rel) for rel in rec_executables(build)}
            dependencies = {}
            for rel in executables:
                proc = subprocess.run(["ldd", str(build / rel)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                if proc.returncode or "not found" in proc.stdout or "not a dynamic executable" in proc.stdout:
                    raise RuntimeError(f"{profile} unresolved runtime dependency: {rel}: {proc.stdout}")
                dependencies[rel] = proc.stdout.splitlines()
            keep = retained_files(build)
            keep |= {str(path.relative_to(build)) for path in inventory_inputs if build in path.parents}
            rec["profiles"][profile] = dict(build=str(build), evidence_root=str(evidence),
                xserver_placeholder=str(xserver_placeholder),
                inventory=inventory, inventory_sha256=sha(logdir / "inventory.log"),
                executables=executables, generated_python_sha256=generated,
                generated_binding_imports=closure, ldd_dependencies=dependencies,
                runtime_files={rel: sha(build / rel) for rel in sorted(keep)},
                ctest_metadata={str(p.relative_to(build)): sha(p) for p in ctest_metadata(build)})
            write_json(dest / "provenance.json", rec)
        if source_pin() != (commit, files, locked):
            raise RuntimeError("source pin changed after builds")
        # Build production closure separately. No test-only generator may supply it.
        rec['production_off'] = {}
        for profile in PROFILES:
            production = dest / 'production-off' / profile
            with (dest / ('production-off-' + profile + '.lock')).open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for label, command in (
                    ('configure-production-off', ['cmake', '-S', str(ROOT), '-B', str(production), '-G', 'Ninja',
                     '-DCMAKE_BUILD_TYPE=Debug', '-DBUILD_TESTING=OFF', '-DSPATIAL_SANITIZERS=' + ('ON' if profile == 'ASan-UBSan' else 'OFF')]),
                    ('build-production-off', ['cmake', '--build', str(production), '--parallel', '2'])):
                    result, seconds = run(command, cwd=ROOT, log=dest / (profile + '-' + label + '.log'), timeout=3600)
                    rec['stage_history'].append(dict(profile=profile, name=label, command=command, exit_code=result.returncode, seconds=seconds))
                    write_json(dest / 'provenance.json', rec)
                    if result.returncode:
                        raise RuntimeError(label + ' failed')
            if not (production / 'spatial-host-session').is_file():
                raise RuntimeError('BLOCKED production spatial-host-session missing from BUILD_TESTING=OFF')
            rec['production_off'][profile] = dict(generated_binding_imports=generated_closure(production),
                client=verify_production_client(ROOT, production),
                runtime_files={rel: sha(production / rel) for rel in sorted(retained_files(production))})
        if source_pin() != (commit, files, locked):
            raise RuntimeError('source changed during production build')
        # Copy runtime closure. Preserve original build trees for reproducibility.
        staged_build = dest / 'runtime-build'
        staged_off = dest / 'runtime-production-off'
        for profile in PROFILES:
            copy_runtime_closure(dest / 'production-off' / profile, staged_off / profile,
                                 rec['production_off'][profile]['runtime_files'])
            build = build_root / profile
            copy_runtime_closure(build, staged_build / profile, rec['profiles'][profile]['runtime_files'])
            verify_inventory_paths(map_inventory(rec['profiles'][profile]['inventory'],
                [(str(build), str(staged_build / profile))]), ROOT, staged_build / profile,
                (Path(rec['profiles'][profile]['evidence_root']), Path(rec['profiles'][profile]['xserver_placeholder'])))
        rec["status"] = "bundle_ready_needs_external_provisioning"
        write_json(dest / "provenance.json", rec)
        with tarfile.open(dest / "m06-artifacts.tar.gz", "w:gz") as tar:
            for name, path in (("source", ROOT), ("build", staged_build), ("production-off", staged_off), ("provenance.json", dest / "provenance.json")):
                if path.is_dir():
                    for member in path.rglob("*"):
                        if name == "source" and (not member.is_file() or str(member.relative_to(path)) not in files):
                            continue
                        if member.is_symlink() or not (member.is_file() or member.is_dir()):
                            raise RuntimeError("unsafe archive input: " + str(member))
                        if member.is_file():
                            tar.add(member, arcname=str(Path(name) / member.relative_to(path)), recursive=False)
                else:
                    tar.add(path, arcname=name, recursive=False)
        # Manifest is out of band because it also hashes provenance.json.
        rec["archive_files"] = stream_archive_hashes(dest / "m06-artifacts.tar.gz")
        rec["bundle_sha256"] = sha(dest / "m06-artifacts.tar.gz")
        write_json(dest / "archive-manifest.json", {"bundle_sha256": rec["bundle_sha256"], "files": rec["archive_files"]})
        write_json(dest / "provenance.json", rec)
        return 0
    except BaseException as exc:
        rec.update(status="failed", error=repr(exc))
        write_json(dest / "provenance.json", rec)
        raise

def verify_bundle(a):
    host = socket.gethostname().split(".")[0]
    if a.host not in TEST_HOSTS or host != a.host:
        raise RuntimeError("deployment host must be the actual sb16 or sb16-2")
    bundle = a.bundle.resolve()
    if sha(bundle) != a.sha256:
        raise RuntimeError("bundle hash mismatch")
    external = json.loads(a.archive_manifest.read_text())
    if external.get("bundle_sha256") != a.sha256 or not external.get("files"):
        raise RuntimeError("out-of-band archive manifest mismatch")
    out = a.destination.resolve()
    out.mkdir(parents=True, exist_ok=False)
    extract_verified_bundle(bundle, out, external["files"])
    rec = json.loads((out / "provenance.json").read_text())
    if rec.get("candidate") != rec["runtime_manifest"].get("candidate"):
        raise RuntimeError("runtime candidate mismatch")
    for name, expected in rec["source_sha256"].items():
        if sha(out / "source" / name) != expected:
            raise RuntimeError("source transfer mismatch: " + name)
    substitutions = {}
    deployed_inventory = {}
    metadata_baseline = {}
    for profile, row in rec["profiles"].items():
        build = out / "build" / profile
        for rel, expected in rec['production_off'][profile]['runtime_files'].items():
            if sha(out / 'production-off' / profile / rel) != expected:
                raise RuntimeError('production-off transfer mismatch: ' + rel)
        for rel, expected in row["runtime_files"].items():
            if sha(build / rel) != expected:
                raise RuntimeError("bundled runtime file transfer mismatch: " + rel)
        wrapper = rec["runtime_manifest"]["hosts"][host][profile]["xace_wrapper"]["path"]
        mapping = [(row["build"], str(build)), (rec["source_root"], str(out / "source")),
                   (row["xserver_placeholder"], wrapper)]
        substitutions[profile] = replace_metadata(build, mapping)
        proc, _ = run(["ctest", "--test-dir", str(build), "--show-only=json-v1"],
                      cwd=out / "source", timeout=120)
        if proc.returncode:
            raise RuntimeError("relocated CTest inventory command failed: " + profile)
        observed = json.loads(proc.stdout)["tests"]
        require_inventory_equal(observed, row['inventory'], mapping)
        verify_inventory_paths(observed, out / "source", build,
                               (Path(replace_text(row['evidence_root'], mapping)),))
        deployed_inventory[profile] = hashlib.sha256(proc.stdout).hexdigest()
        metadata_baseline[profile] = {}
        for path in ctest_metadata(build):
            rel = str(path.relative_to(build))
            saved = out / 'ctest-original' / profile / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
            metadata_baseline[profile][rel] = sha(saved)
        for rel, expected in row["generated_python_sha256"].items():
            if sha(build / rel) != expected:
                raise RuntimeError("generated bytes changed during relocation")
    # External per-host runtime is a separate, verified provisioning dependency.
    runtime_status = {}
    for profile in PROFILES:
        try:
            verify_runtime(rec["runtime_manifest"], host, profile, out / 'source')
            runtime_status[profile] = "verified"
        except (OSError, KeyError, ValueError, RuntimeError) as exc:
            runtime_status[profile] = "BLOCKED: " + str(exc)
    status = "verified" if all(v == "verified" for v in runtime_status.values()) else "blocked_external_runtime"
    write_json(out / "deployment-receipt.json", dict(host=host, status=status, runtime=runtime_status,
        bundle_sha256=a.sha256, candidate=rec["candidate"], source_sha256=rec["source_sha256"],
        substitutions=substitutions, inventory_sha256=deployed_inventory,
        metadata_baseline=metadata_baseline, verified_utc=time.time()))
    if status != "verified":
        raise RuntimeError("BLOCKED external runtime provisioning: " + repr(runtime_status))
    return 0

def audit_inventory(inventory, required_names):
    names = [t["name"] for t in inventory]
    if len(names) != len(set(names)):
        raise RuntimeError("duplicate CTest registrations")
    missing = sorted(set(required_names) - set(names))
    if missing:
        raise RuntimeError("BLOCKED missing required CTest registrations: " + ", ".join(missing))
    disabled = [t["name"] for t in inventory if any(p.get("name") == "DISABLED" and str(p.get("value")).upper() in ("1", "ON", "TRUE") for p in t.get("properties", []))]
    if disabled:
        raise RuntimeError("BLOCKED disabled CTest registrations: " + ", ".join(disabled))
    return names

def rec_executables(build):
    for p in build.rglob("*"):
        if p.is_file():
            try:
                if os.access(p, os.X_OK) and p.read_bytes()[:4] == b"\x7fELF":
                    yield str(p.relative_to(build))
            except OSError:
                pass

def gate(a):
    import fcntl
    host = socket.gethostname().split(".")[0]
    if host not in ("sb16", "sb16-2"):
        raise RuntimeError("execution is allowed only on sb16 and sb16-2")
    gate_lock = (Path('/run/user') / str(os.getuid()) / 'spatial-m06-gate.lock').open('w')
    fcntl.flock(gate_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    root = a.deployment.resolve()
    receipt = json.loads((root / "deployment-receipt.json").read_text())
    if receipt.get("host") != host or receipt.get("status") != "verified":
        raise RuntimeError("missing matching verified deployment receipt")
    rec = json.loads((root / "provenance.json").read_text())
    for name, expected in rec["source_sha256"].items():
        if sha(root / "source" / name) != expected:
            raise RuntimeError("deployed source pin mismatch: " + name)
    manifest = rec["runtime_manifest"]
    required_slots = (a.native_controller, a.real_world, a.recovery, a.host_session, a.held_input)
    if any(not x for x in required_slots):
        raise RuntimeError("BLOCKED: explicitly name native-controller, real-world, recovery, production host-session, and held-input suites")
    if a.native_controller != a.held_input:
        raise RuntimeError("held-input shares host-actor-controller-native and must not create a duplicate suite mapping")
    requirement_suites = dict(zip(('native_controller', 'real_world', 'recovery', 'host_session', 'held_input'), required_slots))
    requirement_suites.update(native_geometry_presentation="geometry-presentation-native", native_geometry_crosshost="geometry-native-crosshost")
    required = set(REQUIRED) | set(required_slots)
    out = root / "evidence" / (a.attempt or f"{host}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}")
    out.mkdir(parents=True, exist_ok=False)
    report = dict(status="running", phase_complete=False, host=host, candidate=rec["candidate"], attempt=out.name,
                  bundle_sha256=receipt["bundle_sha256"], requirement_suites=requirement_suites, stages=[], blockers=[], profiles={})
    write_json(out / "run.json", report)
    source = root / "source"
    builds = root / "build"
    try:
        storage_attempt = create_storage_evidence_attempt(source, host, out.name)
        for profile in PROFILES:
            build = builds / profile
            production = root / 'production-off' / profile
            for rel, expected in rec['production_off'][profile]['runtime_files'].items():
                if sha(production / rel) != expected:
                    raise RuntimeError('production-off runtime mismatch: ' + rel)
            target = out / profile; target.mkdir()
            storage_profile = create_storage_evidence_profile(storage_attempt["root"], source, profile)
            storage_evidence = {name: Path(row["path"])
                                for name, row in storage_profile["registrations"].items()}
            env = os.environ.copy()
            env.update(PYTHONPATH=str(build / "python") + ":" + str(source), SPATIAL_XPROBE=str(build / "spatial-xprobe"),
                SPATIAL_SECURITY_EVIDENCE=str(target / "native"), SPATIAL_SCOPE_EVIDENCE=str(target / "scopes"),
                SPATIAL_RENDER_EVIDENCE=str(target / "graphics"), ASAN_OPTIONS="detect_leaks=1:abort_on_error=1",
                UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
            for key in ("DISPLAY", "SPATIAL_TEST_DISPLAY", "SPATIAL_SECURITY_TEST_CORE_ONLY", "LD_LIBRARY_PATH", "__EGL_VENDOR_LIBRARY_FILENAMES"):
                env.pop(key, None)
            profile_runtime = verify_runtime(manifest, host, profile, source)
            wrapper_path = Path(profile_runtime["xace_wrapper"]["path"])
            env["SPATIAL_TEST_XSERVER"] = str(wrapper_path)
            for rel, expected in rec["profiles"][profile]["executables"].items():
                if sha(build / rel) != expected: raise RuntimeError(profile + " deployed executable hash mismatch: " + rel)
            for rel, expected in rec["profiles"][profile]["runtime_files"].items():
                if rel not in rec["profiles"][profile]["ctest_metadata"] and sha(build / rel) != expected:
                    raise RuntimeError(profile + " runtime file hash mismatch: " + rel)
            prepared = rec["profiles"][profile]
            for rel, expected in receipt['metadata_baseline'][profile].items():
                original = root / 'ctest-original' / profile / rel
                if sha(original) != expected:
                    raise RuntimeError('preserved CTest metadata changed')
                shutil.copy2(original, build / rel)
            deployment_mapping = [(prepared['build'], str(build)), (rec['source_root'], str(source)),
                                  (prepared['xserver_placeholder'], str(wrapper_path))]
            deployed_evidence = replace_text(prepared['evidence_root'], deployment_mapping)
            mapping = [(prepared["build"], str(build)), (rec["source_root"], str(source)),
                       (prepared["xserver_placeholder"], str(wrapper_path)),
                       (prepared["evidence_root"], str(target / "ctest"))]
            evidence_edits = replace_metadata(build, [(deployed_evidence, str(target / "ctest"))])
            expected_inventory, startup_edits = add_startup_bindings(build,
                map_inventory(prepared['inventory'], mapping), production / 'python')
            storage_source = target / 'ctest'
            expected_inventory = relocate_storage_evidence_inventory(
                expected_inventory, storage_source, storage_evidence)
            storage_edits = relocate_storage_evidence_metadata(build, storage_source, storage_evidence)
            row_metadata = {str(p.relative_to(build)): sha(p) for p in ctest_metadata(build)}
            write_json(target / "metadata-relocation.json", dict(
                edits=evidence_edits, startup_packaging_augmentation=startup_edits,
                storage_local_evidence_augmentation=dict(
                    tests=list(STORAGE_LOCAL_EVIDENCE_TESTS), source=str(storage_source),
                    destinations={name: str(path) for name, path in storage_evidence.items()},
                    parents=storage_attempt["parents"], attempt=storage_attempt["attempt"],
                    profile=storage_profile,
                    metadata_edits=storage_edits),
                after=row_metadata))
            invpath = target / "inventory.json"
            # CTest --show-only is read-only; compare normalized registration names against build-host inventory.
            p, sec = run(["ctest", "--test-dir", str(build), "--show-only=json-v1"], cwd=source, env=env, timeout=120)
            (target / "inventory.log").write_bytes(p.stdout)
            if p.returncode: raise RuntimeError(profile + " inventory command failed")
            deployed = json.loads(p.stdout)["tests"]
            names = audit_inventory(deployed, required)
            local_names = sorted(set(names) - DEDICATED_CROSSHOST)
            require_inventory_equal(deployed, expected_inventory, [])
            verify_inventory_paths(deployed, source, build,
                                   (target / 'ctest', *storage_evidence.values()))
            storage_names = sorted(test["name"] for test in deployed
                                   if any(prop.get("name") == "ENVIRONMENT"
                                          and any(value == "SPATIAL_SECURITY_EVIDENCE=" + str(storage_evidence.get(test["name"]))
                                                  for value in prop.get("value", []))
                                          for prop in test.get("properties", []))
                                   and test["name"] in STORAGE_LOCAL_EVIDENCE_TESTS)
            if storage_names != sorted(STORAGE_LOCAL_EVIDENCE_TESTS):
                raise RuntimeError("storage evidence augmentation escaped its two declared registrations")
            write_json(invpath, dict(names=sorted(names), count=len(names), dedicated_crosshost=sorted(DEDICATED_CROSSHOST),
                                     local_names=local_names, sha256=sha(target / "inventory.log"),
                                     storage_local_evidence=dict(tests=storage_names,
                                         paths={name: str(path) for name, path in storage_evidence.items()})))
            row = report["profiles"][profile] = dict(status="running", inventory_count=len(names), commands=[
                dict(name="inventory", command=["ctest", "--test-dir", str(build), "--show-only=json-v1"], exit_code=0, seconds=sec, assertions_passed=True)])
            commands = [
                ('production-off-startup', startup_packaging_command(source, production / 'spatial-host-session', production / 'python'), 120),
                ("harness", ["python3", "-m", "unittest", "discover", "-s", "tests/harness", "-t", ".", "-v"], 180),
                ("core", ["ctest", "--test-dir", str(build), "--parallel", "1", "-V", "-E", "^geometry-native-crosshost$", "--output-junit", str(target / "core.xml")], 14400),
                ("P10", ["python3", "tools/with_resource_delegate.py", "--native", "--", "python3", "tools/with_adapter_resources.py", "--", "python3", "tools/with_test_display.py", "--", "python3", "tools/gate.py", "--through", "P10", "--sut", json.dumps([str(build / "spatial-test-host")]), "--report", str(target / "P10.json")], 300),
                ("bounded-storage-git", ["sudo", "-n", "python3", "tests/native/test_storage_git_integration.py", "--production-owner", str(build), str(target / "bounded-storage-git")], 600),
            ]
            for label, command, timeout in commands:
                command_env = stage_environment(env, label, production, source)
                result, seconds = run(command, cwd=source, env=command_env, log=target / (label + ".log"), timeout=timeout)
                item = dict(name=label, command=command, exit_code=result.returncode, seconds=seconds)
                row["commands"].append(item); write_json(out / "run.json", report)
                output = (target / (label + '.log')).read_text(errors='replace')
                reject_inner_skips(output)
                if label == 'production-off-startup' and not re.search(r'Ran 5 tests in .*\n\nOK(?:\n|$)', output):
                    raise RuntimeError('production-off startup must pass all five checks')
                if label == "harness":
                    text = (target / "harness.log").read_text(errors="replace")
                    item["expected_51"] = bool(re.search(r"Ran 51 tests in .*\n\nOK(?:\n|$)", text)) and not re.search(r"\bskipped\b", text)
                if label == "core":
                    try:
                        import xml.etree.ElementTree as ET
                        cases = list(ET.parse(target / "core.xml").getroot().iter("testcase"))
                        item["junit_cases"] = len(cases)
                        item["all_registered_present"] = len({c.attrib.get("name") for c in cases}) == len(local_names) and {c.attrib.get("name") for c in cases} == set(local_names)
                        item["failed_or_skipped"] = any(c.find(x) is not None for c in cases for x in ("failure", "error", "skipped"))
                    except Exception as ex: item["assertion_error"] = repr(ex)
                if label == "P10" and (target / "P10.json").exists():
                    acceptance = json.loads((target / "P10.json").read_text())
                    item["acceptance"] = {k: acceptance.get(k) for k in ("status", "through", "selected", "executed", "total_baseline", "not_selected", "diagnostic_only")}
                    cases = acceptance.get("tests", [])
                    item["acceptance_rows_valid"] = (len(cases) == 151 and
                        len({c.get("test") for c in cases}) == 151 and
                        {c.get('test') for c in cases} == set(json.loads((source / 'acceptance.lock.json').read_text())['test_ids']) and
                        all(c.get("status") == "passed" and c.get("phase") <= "P10" for c in cases))
                if result.returncode:
                    row["status"] = "failed"; write_json(out / "run.json", report)
                    raise RuntimeError(f"{profile} {label} failed with status {result.returncode}; subsequent stages stopped")
                if label == "harness" and not item["expected_51"]: raise RuntimeError("harness inventory is not exactly 51 passing tests")
                if label == "core" and (item.get("junit_cases") != len(local_names) or not item.get("all_registered_present") or item.get("failed_or_skipped")): raise RuntimeError("CTest full-suite JUnit assertion failed")
                if label == "P10" and not (item.get("acceptance", {}).get("status") == "passed" and item["acceptance"].get("selected") == item["acceptance"].get("executed") == item["acceptance"].get("total_baseline") == 151 and item["acceptance"].get("not_selected") == 0 and not item["acceptance"].get("diagnostic_only") and item["acceptance"].get("through") == "P10" and item.get("acceptance_rows_valid")): raise RuntimeError("locked P10 inventory assertion failed")
                if label == "bounded-storage-git":
                    review = target / "bounded-storage-git/review.json"
                    if not review.is_file(): raise RuntimeError("storage owner review receipt missing")
                    data = json.loads(review.read_text())
                    expected_storage = {"large_import_retirement", "application_and_actor_quota", "read_only", "sibling_survival", "restart"}
                    if data.get("status") != "passed" or set(data.get("results", {})) != expected_storage: raise RuntimeError("five storage-owner scenario receipt invalid")
                    if not all(result.get("passed") for result in data["results"].values()): raise RuntimeError("storage-owner scenario failure")
                    if data.get("mounted") is not False or any(data.get(key, {}).get("exit_code") != 0 for key in ("service_stop", "unmount", "detach")):
                        raise RuntimeError("storage-owner cleanup receipt invalid")
                    if "ActiveState=inactive" not in data.get("service_after", ""):
                        raise RuntimeError("storage-owner service remains active")
                    owners = data.get("owner_unit_cleanup", {})
                    if len(owners) != 2 or not all("ActiveState=inactive" in state for state in owners.values()):
                        raise RuntimeError("storage owner units remain active")
                    scope = data.get("scope")
                    if not scope or Path(scope).exists() or os.path.ismount(target / "bounded-storage-git/volume"):
                        raise RuntimeError("storage owner scope or mount remains")
                row["commands"][-1]["assertions_passed"] = True
                write_json(out / "run.json", report)
            storage_receipt = scan_storage_evidence(storage_evidence)
            target_logs = list(target.rglob("*.log"))
            findings = [str(p.relative_to(target)) for p in target_logs if SAN_RE.search(p.read_text(errors="replace"))]
            row["sanitizer_logs_scanned"] = len(target_logs) + storage_receipt["logs_scanned"]
            row["sanitizer_findings"] = findings
            row["storage_evidence_sha256"] = storage_receipt["files"]
            row["storage_cleanup_receipts"] = storage_receipt["cleanups"]
            row["storage_evidence_exclusions"] = storage_receipt["exclusions"]
            if findings: raise RuntimeError(profile + " sanitizer findings: " + ", ".join(findings))
            row["status"] = "passed_needs_crosshost_and_review"
            row["artifact_sha256"] = {str(p.relative_to(target)): sha(p) for p in target.rglob("*") if p.is_file()}
            write_json(out / "run.json", report)
        report["status"] = "local_passed_crosshost_pending"
    except BaseException as e:
        report["status"] = "failed"; report["error"] = repr(e)
    finally:
        report["source_sha256_rechecked"] = all((root / "source" / name).is_file() and sha(root / "source" / name) == expected for name, expected in rec["source_sha256"].items())
        report["profile_artifacts_rechecked"] = {}
        report['production_off_rechecked'] = {}
        for profile, row in rec["profiles"].items():
            production = root / 'production-off' / profile
            report['production_off_rechecked'][profile] = all(
                (production / name).is_file() and sha(production / name) == expected
                for name, expected in rec['production_off'][profile]['runtime_files'].items())
            build = builds / profile
            report["profile_artifacts_rechecked"][profile] = all(
                (build / name).is_file() and sha(build / name) == expected
                for name, expected in row["runtime_files"].items() if name not in row["ctest_metadata"])
        report["metadata_rechecked"] = {}
        report["storage_evidence_rechecked"] = {}
        for profile in PROFILES:
            build = builds / profile
            metadata_record = out / profile / "metadata-relocation.json"
            if metadata_record.is_file():
                relocation = json.loads(metadata_record.read_text())
                expected_metadata = relocation.get("after", {})
                report["metadata_rechecked"][profile] = bool(expected_metadata) and all(
                    (build / rel).is_file() and sha(build / rel) == digest
                    for rel, digest in expected_metadata.items())
                try:
                    augmentation = relocation["storage_local_evidence_augmentation"]
                    if augmentation.get("tests") != list(STORAGE_LOCAL_EVIDENCE_TESTS):
                        raise RuntimeError("storage evidence registration set changed")
                    profile_path = Path(augmentation["profile"]["path"])
                    verify_storage_evidence_path(source, profile_path)
                    paths = augmentation["destinations"]
                    declared = augmentation["profile"]["registrations"]
                    if set(paths) != set(STORAGE_LOCAL_EVIDENCE_TESTS) or set(declared) != set(paths):
                        raise RuntimeError("storage evidence paths incomplete")
                    if any(paths[name] != declared[name]["path"] for name in paths):
                        raise RuntimeError("storage evidence registration map changed")
                    for value in paths.values():
                        verify_storage_evidence_path(source, Path(value))
                    report["storage_evidence_rechecked"][profile] = True
                except (KeyError, OSError, RuntimeError, ValueError):
                    report["storage_evidence_rechecked"][profile] = False
            else:
                report["metadata_rechecked"][profile] = False
                report["storage_evidence_rechecked"][profile] = False
        report["runtime_rechecked"] = {}
        for profile in PROFILES:
            try:
                verify_runtime(manifest, host, profile, source)
                report["runtime_rechecked"][profile] = True
            except (OSError, KeyError, ValueError, RuntimeError):
                report["runtime_rechecked"][profile] = False
        if (not report["source_sha256_rechecked"] or not all(report['production_off_rechecked'].values())
                or not all(report["profile_artifacts_rechecked"].values()) or not all(report["runtime_rechecked"].values())
                or not all(report["metadata_rechecked"].values())
                or not all(report["storage_evidence_rechecked"].values())):
            report["status"] = "failed"
            report["error"] = "postrun source, artifact, runtime, or storage evidence invariant failed"
        report["phase_complete"] = False
        write_json(out / "run.json", report)
    return 1 if report["status"] == "failed" else 0

def _read_json(path, label):
    if not path.is_file():
        raise RuntimeError(label + " receipt is missing")
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise RuntimeError(label + " receipt is unreadable") from error

def _run_on_host(host, argv, timeout=60, payload=None):
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, shlex.join(list(map(str, argv)))]
    result = subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError(host + " command failed: " + result.stderr.decode(errors="replace")[-1000:])
    return result.stdout

def _validate_remote_deployment(host, row, candidate):
    root = row["path"]
    script = ("import hashlib,json,pathlib,sys; r=pathlib.Path(sys.argv[1]); c=sys.argv[2]; "
              "p=json.loads((r/'provenance.json').read_text()); d=json.loads((r/'deployment-receipt.json').read_text()); "
              "bad=[n for n,h in p['source_sha256'].items() if hashlib.sha256((r/'source'/n).read_bytes()).hexdigest()!=h]; "
              "print(json.dumps({'candidate':p.get('candidate'),'receipt_candidate':d.get('candidate'),'host':d.get('host'),"
              "'status':d.get('status'),'source_ok':not bad,'bad':bad[:8]})); "
              "sys.exit(p.get('candidate')!=c or d.get('candidate')!=c or d.get('host')!=pathlib.Path('/etc/hostname').read_text().strip().split('.')[0] or d.get('status')!='verified' or bool(bad))")
    raw = _run_on_host(host, ["python3", "-c", script, root, candidate], timeout=120)
    record = json.loads(raw)
    if record.get("candidate") != candidate or record.get("receipt_candidate") != candidate or record.get("status") != "verified" or not record.get("source_ok"):
        raise RuntimeError(host + " deployment/source pin mismatch: " + repr(record))
    return record

def _validate_geometry_inventory(inventory, host, profile):
    names = inventory.get("names", [])
    if len(names) != len(set(names)) or not set(REQUIRED).issubset(set(names)):
        raise RuntimeError(host + " " + profile + " inventory misses mandatory M06 suites or contains duplicates")
    if "geometry-native-crosshost" not in inventory.get("dedicated_crosshost", []):
        raise RuntimeError("native cross-host CTest must be deferred to its dedicated provisioned stage")
    local_names = sorted(set(names) - DEDICATED_CROSSHOST)
    if inventory.get("local_names") != local_names:
        raise RuntimeError(host + " " + profile + " local CTest inventory mapping is incomplete")
    return sorted(names)

def _validate_requirement_suites(suites, host):
    if (suites.get("native_controller") != "host-actor-controller-native"
            or suites.get("held_input") != suites.get("native_controller")
            or suites.get("real_world") != "actor-transition-real-world"
            or suites.get("recovery") != "actor-transition-recovery"
            or suites.get("host_session") != "host-session-production-native"
            or suites.get("native_geometry_presentation") != "geometry-presentation-native"
            or suites.get("native_geometry_crosshost") != "geometry-native-crosshost"):
        raise RuntimeError(host + " local requirement mapping is incomplete or duplicates held-input")


def _validate_local_run(path, expected_host, candidate):
    report = _read_json(path, expected_host + " local gate")
    if report.get("host") != expected_host or report.get("candidate") != candidate or report.get("status") != "local_passed_crosshost_pending" or report.get("phase_complete") is not False:
        raise RuntimeError(expected_host + " local gate receipt does not match the pending candidate")
    _validate_requirement_suites(report.get("requirement_suites", {}), expected_host)
    inventories = {}
    for profile in PROFILES:
        profile_dir = path.parent / profile
        inventory = _read_json(profile_dir / "inventory.json", expected_host + " " + profile + " inventory")
        names = _validate_geometry_inventory(inventory, expected_host, profile)
        row = report.get("profiles", {}).get(profile, {})
        if row.get("status") not in ("passed_needs_crosshost_and_review", "passed"):
            raise RuntimeError(expected_host + " " + profile + " local profile did not pass")
        core = next((item for item in row.get("commands", []) if item.get("name") == "core"), {})
        if (core.get("exit_code") != 0 or core.get("junit_cases") != len(names) - len(DEDICATED_CROSSHOST)
                or core.get("all_registered_present") is not True or core.get("failed_or_skipped") is not False):
            raise RuntimeError(expected_host + " " + profile + " local CTest receipt is incomplete")
        inventories[profile] = dict(path=str(profile_dir / "inventory.json"), sha256=sha(profile_dir / "inventory.json"), names=names)
    return dict(path=str(path), sha256=sha(path), inventories=inventories)

def _validate_plan(plan, plan_path):
    if any(key in plan for key in ("credentials", "bearer_token", "private_key", "actor_secret")):
        raise RuntimeError("cross-host plan must not contain credentials or private keys")
    if plan.get("schema") != 1:
        raise RuntimeError("cross-host plan schema must be 1")
    candidate = plan.get("candidate")
    if not isinstance(candidate, str) or not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise RuntimeError("cross-host plan must pin one full candidate commit")
    if plan.get("runner") != "tools/run_geometry_native_crosshost.py":
        raise RuntimeError("cross-host plan must select the reviewed native geometry runner")
    if plan.get("directions") != ["sb16-to-sb16-2", "sb16-2-to-sb16"] or plan.get("profiles") != list(PROFILES):
        raise RuntimeError("cross-host plan must cover both directions and both profiles")
    expected_cases = [f"{case}:{direction}" for direction in plan["directions"]
                      for case in ("round-trip", "private-denial", "unavailable-destination")]
    if plan.get("semantic_cases") != expected_cases:
        raise RuntimeError("cross-host plan must enumerate the six semantic cases exactly once")
    if plan.get("provisioning") != "actor_transition_crosshost_fixture.py:production-private-two-world-issuer-mtls":
        raise RuntimeError("cross-host plan provisioning selector is not the production fixture")
    if set(plan.get("deployments", {})) != TEST_HOSTS or set(plan.get("local_runs", {})) != TEST_HOSTS:
        raise RuntimeError("cross-host plan must provide both deployed hosts and local gate receipts")
    for host, row in plan["deployments"].items():
        if not Path(row.get("path", "")).is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", row.get("provenance_sha256", "")) or not re.fullmatch(r"[0-9a-f]{64}", row.get("deployment_receipt_sha256", "")):
            raise RuntimeError(host + " deployment path and receipt hashes are required")
    if plan["deployments"]["sb16"]["path"] != plan["deployments"]["sb16-2"]["path"]:
        raise RuntimeError("both hosts must use the same final deployment path")
    for host, row in plan["local_runs"].items():
        if not Path(row.get("path", "")).is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")):
            raise RuntimeError(host + " local run requires an absolute path and SHA-256")
    output = Path(plan.get("evidence", {}).get("output", ""))
    if not output.is_absolute() or output.exists():
        raise RuntimeError("cross-host evidence output must be a new absolute path")
    source_root = (Path(plan["deployments"]["sb16"]["path"]) / "source").resolve()
    output = output.resolve()
    plan_path = plan_path.resolve()
    if plan_path.is_relative_to(source_root) or output.is_relative_to(source_root):
        raise RuntimeError("cross-host evidence must remain outside the deployed source tree")
    for profile in PROFILES:
        if profile not in plan.get("native_geometry", {}):
            raise RuntimeError("native geometry manifests must cover both build profiles")
        hosts = plan["native_geometry"][profile].get("hosts", {})
        if set(hosts) != TEST_HOSTS:
            raise RuntimeError("native geometry manifest must name both hosts")
        artifacts = Path(plan["deployments"]["sb16"]["path"])
        short = "debug" if profile == "Debug" else "asan"
        for host, row in hosts.items():
            expected = {"source": artifacts / "source", "bin": artifacts / "build" / profile,
                        "python": artifacts / "build" / profile / "python"}
            if any(Path(row.get(key, "")) != value for key, value in expected.items()):
                raise RuntimeError(profile + " " + host + " native manifest uses a wrong deployed source/profile layout")
            for key in ("xserver", "evidence", "command_runner"):
                if not Path(row.get(key, "")).is_absolute():
                    raise RuntimeError(profile + " " + host + " native manifest lacks absolute " + key)
            address = ipaddress.ip_address(row.get("address", ""))
            if address.is_loopback or address.is_unspecified:
                raise RuntimeError("native geometry needs reachable host addresses")
    return candidate, output

def _validate_semantic_receipt(output):
    status = (output / "runtime-status.txt").read_text().splitlines()[0]
    cleanup = _read_json(output / "cleanup.json", "semantic cleanup")
    summary = _read_json(output / "plan-summary.json", "semantic plan")
    harness_exit = (output / "harness.exit").read_text().strip()
    harness_output = (output / "harness.stdout").read_text(errors="replace")
    if status != "PASS" or harness_exit != "0" or "cross-host actor transition protobuf plan passed" not in harness_output:
        raise RuntimeError("semantic fixture lacks a successful terminal harness receipt")
    if cleanup.get("complete") is not True or cleanup.get("errors") != []:
        raise RuntimeError("semantic fixture cleanup is incomplete")
    expected = [f"{case}:{direction}" for direction in ("sb16-to-sb16-2", "sb16-2-to-sb16")
                for case in ("round-trip", "private-denial", "unavailable-destination")]
    if summary.get("cases") != 6 or summary.get("semantic_cases") != expected or set(summary.get("worlds", {})) != TEST_HOSTS:
        raise RuntimeError("semantic fixture did not execute the six expected cases across both persistent worlds")
    logs = [path for path in output.rglob("*") if path.is_file() and path.suffix in (".log", ".stdout", ".stderr")]
    findings = [str(path.relative_to(output)) for path in logs if SAN_RE.search(path.read_text(errors="replace"))]
    skips = [str(path.relative_to(output)) for path in logs if INNER_SKIP_RE.search(path.read_text(errors="replace"))]
    if findings or skips:
        raise RuntimeError("semantic logs contain sanitizer diagnostics or skipped checks")
    return dict(cleanup_sha256=sha(output / "cleanup.json"), plan_summary_sha256=sha(output / "plan-summary.json"), logs_scanned=len(logs))

def _validate_native_terminal(terminal, command_exit, wrapper_exit, stdout, stderr):
    if terminal != "COMPLETE" or command_exit != "0" or wrapper_exit != "0":
        raise RuntimeError("native geometry command, wrapper, or terminal receipt is incomplete")
    if not re.search(r"Ran 9 tests in .*\n\nOK(?:\n|$)", stdout):
        raise RuntimeError("native geometry does not report all nine passing cases")
    if SAN_RE.search(stdout + stderr) or INNER_SKIP_RE.search(stdout):
        raise RuntimeError("native geometry output contains sanitizer diagnostics or skips")

def _validate_semantic_fixture(deployment, profile, output):
    short = "debug" if profile == "Debug" else "asan"
    source = deployment / "source"
    account = pwd.getpwuid(os.getuid())
    environment = {"PATH": "/usr/bin:/bin", "HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
                   "PYTHONPATH": str(deployment / "build" / profile / "python") + ":" + str(source / "tests" / "core") + ":" + str(source),
                   "ASAN_OPTIONS": "detect_leaks=1:abort_on_error=1", "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"}
    command = [sys.executable, str(source / "tests/core/actor_transition_crosshost_fixture.py"),
               "--bundle", str(deployment), "--profile", short, "--output", str(output)]
    if output.exists():
        raise RuntimeError("semantic fixture output path must be new")
    launch_evidence = output.with_name(output.name + ".launch")
    launch_evidence.mkdir(parents=True, exist_ok=False)
    write_json(launch_evidence / "invocation.json", dict(argv=command, environment=environment))
    process = subprocess.Popen(command, cwd=source, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=1800)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.send_signal(signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=10)
    (launch_evidence / "stdout.log").write_bytes(stdout); (launch_evidence / "stderr.log").write_bytes(stderr)
    (launch_evidence / "command.exit").write_text(("TIMEOUT" if timed_out else str(process.returncode)) + "\n")
    if timed_out or process.returncode != 0:
        raise RuntimeError(profile + " semantic cross-host fixture failed or timed out; inspect its retained evidence")
    receipt = _validate_semantic_receipt(output)
    return dict(status="passed", output=str(output), launch_evidence=str(launch_evidence), **receipt)

def _run_semantic_fixture(deployment, profile, output):
    return _validate_semantic_fixture(deployment, profile, output)

def _receipt_text(host, path):
    if host == "sb16":
        return path.read_text(errors="replace") if path.is_file() else ""
    script = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); sys.stdout.write(p.read_text(errors='replace') if p.is_file() else '')"
    data = _run_on_host(host, ["python3", "-c", script, str(path)], timeout=20)
    return data.decode(errors="replace")

def _wait_native_run(host, run_dir, timeout=1860):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _receipt_text(host, run_dir / "terminal-status.txt").strip()
        command = _receipt_text(host, run_dir / "command.exit").strip()
        wrapper = _receipt_text(host, run_dir / "wrapper.exit").strip()
        if status == "COMPLETE" and command and wrapper:
            if command != "0" or wrapper != "0":
                raise RuntimeError(host + " native geometry command/wrapper failed: " + command + "/" + wrapper)
            return
        time.sleep(5)
    raise RuntimeError(host + " native geometry run has no terminal command/wrapper receipts")

def _fetch_native_case_tree(host, run_dir, destination):
    if host == "sb16":
        return Path(run_dir) / "geometry"
    archive = _run_on_host(host, ["tar", "-C", str(run_dir), "-cf", "-", "geometry"], timeout=90)
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            relative = Path(member.name)
            target = (destination / relative).resolve()
            if relative.is_absolute() or root not in target.parents or not (member.isfile() or member.isdir()):
                raise RuntimeError(host + " native authority archive contains an unsafe entry")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = bundle.extractfile(member)
            if stream is None:
                raise RuntimeError(host + " native authority archive entry is unreadable")
            target.write_bytes(stream.read())
    return destination / "geometry"


def _validate_local_native_cases(local_evidence, host):
    expected = [f"test_{i:02d}_" for i in range(1, 10)]
    directories = [path for path in local_evidence.iterdir() if path.is_dir()] if local_evidence.is_dir() else []
    names = [path.name for path in directories]
    if len(names) != 9 or any(not any(name.startswith(prefix) for name in names) for prefix in expected):
        raise RuntimeError(host + " native geometry case evidence is incomplete")
    local_rows = {}
    for directory in directories:
        terminal = _read_json(directory / "exact-unit-terminal.json", host + " exact-unit terminal")
        unit_name = terminal.get("unit", "")
        if (not re.fullmatch(r"spatial-m03-resources-m06-geometry-[0-9a-f]{32}\.service", unit_name)
                or "LoadState=not-found" not in terminal.get("state", "")
                or "ActiveState=inactive" not in terminal.get("state", "")
                or terminal.get("cgroup_absent") is not True):
            raise RuntimeError(host + " native geometry exact-unit cleanup is incomplete")
        peer_path = directory / "peer-resource-terminal.txt"
        if not peer_path.is_file():
            raise RuntimeError(host + " native geometry case lacks peer cleanup evidence")
        try:
            peer = [json.loads(line) for line in peer_path.read_text(errors="replace").splitlines() if line]
        except ValueError as error:
            raise RuntimeError(host + " native geometry peer cleanup evidence is malformed") from error
        adapter = [row for row in peer if "scope_removed" in row]
        unit = [row for row in peer if "unit_after" in row]
        if (len(adapter) != 1 or not adapter[0].get("scope_removed") or adapter[0].get("unexpected_population")
                or adapter[0].get("exit_code") != 0 or len(unit) != 1 or unit[0].get("unit") != unit_name
                or unit[0].get("cgroup_exists") or "LoadState=not-found" not in unit[0].get("unit_after", "")
                or "ActiveState=inactive" not in unit[0].get("unit_after", "")):
            raise RuntimeError(host + " native geometry peer cleanup failed")
        required_records = (directory / "exact-unit-terminal.json", peer_path)
        if any(not path.is_file() or path.stat().st_size == 0 for path in required_records):
            raise RuntimeError(host + " native geometry required cleanup record is empty")
        files = {path.name: sha(path) for path in directory.iterdir() if path.is_file()}
        if not files:
            raise RuntimeError(host + " native geometry case evidence is empty")
        for path in directory.rglob("*.log"):
            if SAN_RE.search(path.read_text(errors="replace")):
                raise RuntimeError(host + " native geometry case contains sanitizer diagnostics")
        local_rows[directory.name] = files
    return local_rows

def _validate_native_run(host, observer, run_dir, remote_evidence, expected_profile, evidence_cache):
    stdout = _receipt_text(host, Path(run_dir) / "stdout.log")
    stderr = _receipt_text(host, Path(run_dir) / "stderr.log")
    terminal = _receipt_text(host, Path(run_dir) / "terminal-status.txt").strip()
    command_exit = _receipt_text(host, Path(run_dir) / "command.exit").strip()
    wrapper_exit = _receipt_text(host, Path(run_dir) / "wrapper.exit").strip()
    _validate_native_terminal(terminal, command_exit, wrapper_exit, stdout, stderr)
    local_evidence = _fetch_native_case_tree(host, run_dir, evidence_cache)
    local_rows = _validate_local_native_cases(local_evidence, host)
    remote_rows = {}
    for case in local_rows:
        path = Path(remote_evidence) / case / "geometry-host-session.log"
        content = _receipt_text(observer, path)
        if not content or SAN_RE.search(content):
            raise RuntimeError(observer + " native observer evidence is missing or diagnostic-bearing: " + case)
        remote_rows[case] = hashlib.sha256(content.encode()).hexdigest()
    return dict(status="passed", authority=host, observer=observer, profile=expected_profile,
                command_exit="0", wrapper_exit="0", terminal="COMPLETE", tests=9,
                stdout_sha256=hashlib.sha256(stdout.encode()).hexdigest(), stderr_sha256=hashlib.sha256(stderr.encode()).hexdigest(),
                local_case_evidence=local_rows, remote_witness_sha256=remote_rows)

def _fresh_native_manifest(profile_manifest):
    return copy.deepcopy(profile_manifest)


def _install_timeout_runner(native, authority, observer, run_tag, output):
    source_runner = {host: Path(native["hosts"][host]["command_runner"]) for host in TEST_HOSTS}
    script_for = lambda path: ("#!/usr/bin/env bash\nset -u\ncheckout=$1\nrun_dir=$2\nshift 2\nexec bash " + shlex.quote(str(path)) + " \"$checkout\" \"$run_dir\" timeout --signal=TERM --kill-after=10 1800 \"$@\"\n")
    wrapper_dir = Path(output) / "timeout-runners"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    local_wrapper = wrapper_dir / (run_tag + ".sh")
    local_wrapper.write_text(script_for(source_runner["sb16"]), encoding="utf-8")
    native["hosts"]["sb16"]["command_runner"] = str(local_wrapper)
    authority_run = Path(native["hosts"][authority]["evidence"]) / run_tag / ("authority-" + authority)
    observer_run = Path(native["hosts"][observer]["evidence"]) / run_tag / ("observer-for-" + authority)
    for host, target in ((authority, authority_run), (observer, observer_run)):
        if host == "sb16":
            if target.exists():
                raise RuntimeError("refusing reused native authority or observer evidence path")
        else:
            _run_on_host(host, ["test", "!", "-e", str(target)], timeout=15)
    remote_path = Path(native["hosts"]["sb16-2"]["evidence"]) / run_tag / "run-command-timeout.sh"
    _run_on_host("sb16-2", ["mkdir", "-p", str(remote_path.parent)])
    remote_local = wrapper_dir / (run_tag + "-sb16-2.sh")
    remote_local.write_text(script_for(source_runner["sb16-2"]), encoding="utf-8")
    transfer = subprocess.run(["scp", "-q", str(remote_local), "sb16-2:" + str(remote_path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if transfer.returncode:
        raise RuntimeError("cannot transfer native timeout runner to sb16-2")
    digest = sha(remote_local)
    remote_digest = _run_on_host("sb16-2", ["sha256sum", str(remote_path)]).decode().split()[0]
    if digest != remote_digest:
        raise RuntimeError("native timeout runner hash differs across hosts")
    native["hosts"]["sb16-2"]["command_runner"] = str(remote_path)
    return dict(sb16_path=str(local_wrapper), sb16_2_path=str(remote_path), sha256=digest,
                timeout_seconds=1800, term_grace_seconds=10)

def _host_command_result(host, argv, timeout=20):
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, shlex.join(list(map(str, argv)))]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return dict(exit_code=result.returncode, stdout=result.stdout.decode(errors="replace"),
                    stderr=result.stderr.decode(errors="replace"))
    except Exception as error:
        return dict(exit_code=None, stdout="", stderr=repr(error))


def _recover_native_timeout(authority, observer, run_dir, output):
    pattern = re.compile(r"spatial-m03-resources-m06-geometry-[0-9a-f]{32}\.service")
    found = set()
    rows = []
    try:
        evidence = _fetch_native_case_tree(authority, run_dir, Path(output) / ("timeout-authority-" + authority))
        for path in evidence.rglob("*") if evidence.is_dir() else []:
            if path.is_file() and path.name in ("peer-resource-launch.json", "stdout.log", "stderr.log"):
                found.update(pattern.findall(path.read_text(errors="replace")))
    except BaseException as error:
        rows.append(dict(stage="read-authority-evidence", error=repr(error), complete=False))
    for unit in sorted(found):
        if not re.fullmatch(r"spatial-m03-resources-m06-geometry-[0-9a-f]{32}\.service", unit):
            continue
        stopped = _host_command_result(observer, ["sudo", "-n", "systemctl", "stop", unit])
        query = _host_command_result(observer, ["systemctl", "show", unit, "-p", "ActiveState", "-p", "LoadState"])
        cgroup = _host_command_result(observer, ["test", "!", "-e", "/sys/fs/cgroup/system.slice/" + unit])
        query_valid = query["exit_code"] in (0, 4) and "LoadState=not-found" in query["stdout"] and "ActiveState=inactive" in query["stdout"]
        complete = stopped["exit_code"] in (0, 5) and query_valid and cgroup["exit_code"] == 0
        rows.append(dict(unit=unit, stop=stopped, query=query, cgroup=cgroup, complete=complete))
    receipt = dict(status="recovered" if found and rows and all(row.get("complete") is True for row in rows) else "incomplete",
                   authority=authority, observer=observer, exact_units=rows, no_unit_observed=not bool(found), phase_complete=False)
    write_json(Path(output) / "timeout-recovery.json", receipt)
    return receipt

def _assert_runtime_gate_idle(host):
    lock = "/run/user/" + str(os.getuid()) + "/spatial-m06-gate.lock"
    if host == "sb16":
        import fcntl
        with open(lock, "a") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("local M06 runtime gate still owns sb16") from error
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    else:
        _run_on_host(host, ["flock", "-n", lock, "-c", "true"], timeout=20)

def _validate_crossstage_profile_artifacts(deployment, provenance, host, profile, run_path, expected_run_sha256):
    deployment = Path(deployment).resolve()
    run_path = Path(run_path)
    evidence_root = (deployment / 'evidence').resolve()
    resolved_run = run_path.resolve()
    if evidence_root not in resolved_run.parents:
        raise RuntimeError(host + ' local run receipt is outside deployment evidence')
    if not re.fullmatch(r'[0-9a-f]{64}', expected_run_sha256) or sha(resolved_run) != expected_run_sha256:
        raise RuntimeError(host + ' local run receipt hash changed')
    report = _read_json(resolved_run, host + ' local gate')
    if (report.get('host') != host or report.get('candidate') != provenance.get('candidate')
            or report.get('status') != 'local_passed_crosshost_pending' or report.get('phase_complete') is not False):
        raise RuntimeError(host + ' local gate receipt is stale or incomplete')
    profile_row = report.get('profiles', {}).get(profile, {})
    if profile_row.get('status') not in ('passed_needs_crosshost_and_review', 'passed'):
        raise RuntimeError(host + ' ' + profile + ' local profile did not pass')
    build = deployment / 'build' / profile
    production = deployment / 'production-off' / profile
    prepared = provenance.get('profiles', {}).get(profile, {})
    off = provenance.get('production_off', {}).get(profile, {})
    if not prepared or not off:
        raise RuntimeError(host + ' ' + profile + ' provenance closure is incomplete')
    generated_hashes = prepared.get('generated_python_sha256')
    if not isinstance(generated_hashes, dict) or not generated_hashes:
        raise RuntimeError(host + ' ' + profile + ' generated Python hashes are missing')
    for rel, expected in generated_hashes.items():
        rel_path = Path(rel)
        if rel_path.is_absolute() or '..' in rel_path.parts:
            raise RuntimeError(host + ' ' + profile + ' unsafe generated Python path: ' + rel)
        path = build / rel_path
        if not path.is_file() or sha(path) != expected:
            raise RuntimeError(host + ' ' + profile + ' generated binding artifact changed: ' + rel)
    for label, root, entries in (('profile', build, prepared.get('executables', {})),
                                 ('profile runtime', build, prepared.get('runtime_files', {})),
                                 ('production-off', production, off.get('runtime_files', {}))):
        if not isinstance(entries, dict) or not entries:
            raise RuntimeError(host + ' ' + profile + ' ' + label + ' closure is empty')
        for rel, expected in entries.items():
            rel_path = Path(rel)
            if rel_path.is_absolute() or '..' in rel_path.parts:
                raise RuntimeError(host + ' ' + profile + ' unsafe provenance path: ' + rel)
            # The gate relocates CTest metadata and pins the exact post-relocation bytes below.
            if label == 'profile runtime' and rel in prepared.get('ctest_metadata', []):
                continue
            path = root / rel_path
            if not path.is_file() or sha(path) != expected:
                raise RuntimeError(host + ' ' + profile + ' artifact changed: ' + label + '/' + rel)
    for build_root, expected_imports, label in (
            (build, prepared.get('generated_binding_imports'), 'profile'),
            (production, off.get('generated_binding_imports'), 'production-off')):
        if not isinstance(expected_imports, dict) or not expected_imports:
            raise RuntimeError(host + ' ' + profile + ' ' + label + ' generated binding closure missing')
        actual_imports = generated_closure(build_root)
        if actual_imports != expected_imports:
            raise RuntimeError(host + ' ' + profile + ' ' + label + ' generated binding closure changed')
    source = deployment / 'source'
    verify_production_client(source, production)
    runtime = provenance.get('runtime_manifest')
    verify_runtime(runtime, host, profile, source)
    profile_evidence = resolved_run.parent / profile
    metadata_path = profile_evidence / 'metadata-relocation.json'
    metadata_hash = profile_row.get('artifact_sha256', {}).get('metadata-relocation.json')
    if not metadata_path.is_file() or not metadata_hash or sha(metadata_path) != metadata_hash:
        raise RuntimeError(host + ' ' + profile + ' metadata relocation receipt changed')
    metadata = _read_json(metadata_path, host + ' ' + profile + ' metadata relocation')
    after = metadata.get('after')
    expected_metadata = prepared.get('ctest_metadata')
    if not isinstance(after, dict) or not isinstance(expected_metadata, dict) or set(after) != set(expected_metadata):
        raise RuntimeError(host + ' ' + profile + ' metadata relocation inventory changed')
    for rel, digest in after.items():
        rel_path = Path(rel)
        if rel_path.is_absolute() or '..' in rel_path.parts:
            raise RuntimeError(host + ' ' + profile + ' unsafe relocated metadata path: ' + rel)
        path = build / rel_path
        if not path.is_file() or not re.fullmatch(r'[0-9a-f]{64}', digest) or sha(path) != digest:
            raise RuntimeError(host + ' ' + profile + ' relocated CTest metadata changed: ' + rel)
    return dict(status='passed', host=host, profile=profile, run_sha256=expected_run_sha256,
                metadata_sha256=sha(metadata_path), generated_binding_files=len(expected_imports),
                runtime_files=len(runtime_files(runtime, host, profile)[1]))


def _crossstage_host_check_script():
    return r'''import hashlib, importlib.util, json, pathlib, socket, sys
host, deployment, candidate, provenance_hash, receipt_hash, run_path, run_hash = sys.argv[1:]
root = pathlib.Path(deployment).resolve()
def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()
actual_host = socket.gethostname().split('.')[0]
result = {'host': host, 'actual_host': actual_host, 'candidate': candidate, 'status': 'failed', 'profiles': {}, 'errors': []}
try:
    if actual_host != host:
        raise RuntimeError('actual host identity mismatch')
    provenance_path = root / 'provenance.json'
    receipt_path = root / 'deployment-receipt.json'
    if digest(provenance_path) != provenance_hash or digest(receipt_path) != receipt_hash:
        raise RuntimeError('deployment provenance or receipt pin changed')
    provenance = json.loads(provenance_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    if (provenance.get('candidate') != candidate or receipt.get('candidate') != candidate
            or receipt.get('status') != 'verified' or receipt.get('host') != host):
        raise RuntimeError('stale, wrong-host, or unverified candidate')
    source = root / 'source'
    for name, expected in provenance.get('source_sha256', {}).items():
        relative = pathlib.Path(name)
        path = source / relative
        if relative.is_absolute() or '..' in relative.parts or source.resolve() not in path.resolve().parents:
            raise RuntimeError('unsafe source pin path: ' + name)
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError('deployed source pin mismatch before checker import: ' + name)
    checker_name = 'tools/m06_deployed_gate.py'
    checker = source / checker_name
    if provenance.get('source_sha256', {}).get(checker_name) != digest(checker):
        raise RuntimeError('gate checker source is absent from or differs from the pinned source closure')
    spec = importlib.util.spec_from_file_location('pinned_m06_gate', checker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for profile in ('Debug', 'ASan-UBSan'):
        result['profiles'][profile] = module._validate_crossstage_profile_artifacts(
            root, provenance, host, profile, run_path, run_hash)
    result['status'] = 'passed'
except BaseException as error:
    result['errors'].append(repr(error))
print(json.dumps(result, sort_keys=True))
'''


def _crossstage_artifact_check(host, row, provenance, candidate, local_run, run_pin):
    args = [host, row['path'], candidate, row['provenance_sha256'],
            row['deployment_receipt_sha256'], local_run['path'], run_pin]
    script = _crossstage_host_check_script()
    if host == 'sb16':
        command = [sys.executable, '-c', script, *args]
        try:
            completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
            stdout = completed.stdout.decode(errors='replace')
            data = json.loads(stdout.splitlines()[-1]) if stdout.strip() else {}
            return dict(host=host, exit_code=completed.returncode, result=data,
                        stderr=completed.stderr.decode(errors='replace')[-2000:])
        except BaseException as error:
            return dict(host=host, exit_code=None, result={'status': 'failed', 'errors': [repr(error)]})
    command = ['python3', '-c', script, *args]
    try:
        completed = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', host,
                                    shlex.join(command)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
        stdout = completed.stdout.decode(errors='replace')
        data = json.loads(stdout.splitlines()[-1]) if stdout.strip() else {}
        return dict(host=host, exit_code=completed.returncode, result=data,
                    stderr=completed.stderr.decode(errors='replace')[-2000:])
    except BaseException as error:
        return dict(host=host, exit_code=None, result={'status': 'failed', 'errors': [repr(error)]})


def _record_crossstage_artifact_checks(report, stage, checks, reconciliation_path):
    report.setdefault('artifact_rechecks', {})[stage] = checks
    failures = []
    seen_hosts = []
    for row in checks:
        host = row.get('host')
        seen_hosts.append(host)
        result = row.get('result', {})
        profiles = result.get('profiles', {}) if isinstance(result, dict) else {}
        valid_profiles = (set(profiles) == set(PROFILES) and all(
            isinstance(profiles.get(profile), dict)
            and profiles[profile].get('status') == 'passed'
            and profiles[profile].get('host') == host
            and profiles[profile].get('profile') == profile
            for profile in PROFILES))
        if (host not in TEST_HOSTS or row.get('exit_code') != 0 or not isinstance(result, dict)
                or result.get('status') != 'passed' or result.get('host') != host
                or result.get('actual_host') != host or not valid_profiles):
            failures.append(row)
    if sorted(seen_hosts) != sorted(TEST_HOSTS) or len(seen_hosts) != len(set(seen_hosts)):
        failures.append(dict(error='artifact recheck must contain exactly one complete result for each qualification host',
                             observed_hosts=seen_hosts, expected_hosts=sorted(TEST_HOSTS)))
    if failures:
        report.setdefault('artifact_check_failures', []).append(dict(stage=stage, hosts=failures))
        report['status'] = 'failed_or_incomplete'
        report['phase_complete'] = False
    write_json(Path(reconciliation_path), report)
    return not failures


def crosshost(a):
    if socket.gethostname().split(".")[0] != "sb16":
        raise RuntimeError("cross-host workflow runs on sb16 after both local gates release")
    plan_path = a.plan.resolve()
    plan = _read_json(plan_path, "cross-host plan")
    candidate, output = _validate_plan(plan, plan_path)
    deployment = Path(plan["deployments"]["sb16"]["path"]).resolve()
    provenance_path = deployment / "provenance.json"
    deployment_receipt = deployment / "deployment-receipt.json"
    if sha(provenance_path) != plan["deployments"]["sb16"]["provenance_sha256"] or sha(deployment_receipt) != plan["deployments"]["sb16"]["deployment_receipt_sha256"]:
        raise RuntimeError("sb16 deployment receipt hashes do not match the reviewed plan")
    provenance = _read_json(provenance_path, "deployment provenance")
    receipt = _read_json(deployment_receipt, "deployment")
    if provenance.get("candidate") != candidate or receipt.get("candidate") != candidate or receipt.get("status") != "verified":
        raise RuntimeError("stale or unverified final candidate")
    deployments = {}
    for host, row in plan["deployments"].items():
        deployments[host] = _validate_remote_deployment(host, row, candidate)
        if host == "sb16-2" and (_run_on_host(host, ["sha256sum", str(Path(row["path"]) / "provenance.json")]).decode().split()[0] != row["provenance_sha256"] or
                                 _run_on_host(host, ["sha256sum", str(Path(row["path"]) / "deployment-receipt.json")]).decode().split()[0] != row["deployment_receipt_sha256"]):
            raise RuntimeError("sb16-2 deployment receipt hashes do not match the reviewed plan")
    for host in sorted(TEST_HOSTS):
        _assert_runtime_gate_idle(host)
    for profile in PROFILES:
        for host, row in plan["native_geometry"][profile]["hosts"].items():
            if host == "sb16":
                if not Path(row["xserver"]).is_file() or not Path(row["command_runner"]).is_file():
                    raise RuntimeError(profile + " sb16 native wrapper/command runner is missing")
            else:
                _run_on_host(host, ["test", "-f", row["xserver"]], timeout=20)
                _run_on_host(host, ["test", "-f", row["command_runner"]], timeout=20)
    local_runs = {}
    for host, row in plan["local_runs"].items():
        path = Path(row.get("path", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")):
            raise RuntimeError(host + " local gate receipt hash is required")
        if host == "sb16":
            if sha(path) != row["sha256"]:
                raise RuntimeError("sb16 local gate receipt hash mismatch")
            local_runs[host] = _validate_local_run(path, host, candidate)
        else:
            data = _run_on_host(host, ["cat", str(path)], timeout=20)
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise RuntimeError("sb16-2 local gate receipt hash mismatch")
            local_runs[host] = dict(path=str(path), sha256=row["sha256"], remote_receipt=json.loads(data))
            remote_report = local_runs[host]["remote_receipt"]
            if remote_report.get("host") != host or remote_report.get("candidate") != candidate or remote_report.get("status") != "local_passed_crosshost_pending" or remote_report.get("phase_complete") is not False:
                raise RuntimeError("sb16-2 local gate receipt is stale or incomplete")
            if any(remote_report.get("profiles", {}).get(profile, {}).get("status") not in ("passed_needs_crosshost_and_review", "passed") for profile in PROFILES):
                raise RuntimeError("sb16-2 local profile result is incomplete")
            _validate_requirement_suites(remote_report.get("requirement_suites", {}), host)
            remote_inventories = {}
            for profile in PROFILES:
                inventory_path = path.parent / profile / "inventory.json"
                raw_inventory = _run_on_host(host, ["cat", str(inventory_path)], timeout=20)
                data = json.loads(raw_inventory)
                names = _validate_geometry_inventory(data, host, profile)
                row = remote_report.get("profiles", {}).get(profile, {})
                core = next((item for item in row.get("commands", []) if item.get("name") == "core"), {})
                if (row.get("status") not in ("passed_needs_crosshost_and_review", "passed") or core.get("exit_code") != 0
                        or core.get("junit_cases") != len(names) - len(DEDICATED_CROSSHOST)
                        or core.get("all_registered_present") is not True or core.get("failed_or_skipped") is not False):
                    raise RuntimeError("sb16-2 local profile CTest receipt is incomplete")
                remote_inventories[profile] = dict(path=str(inventory_path), sha256=hashlib.sha256(raw_inventory).hexdigest(), names=names)
            local_runs[host]["inventories"] = remote_inventories
    if output.exists():
        raise RuntimeError("cross-host output path already exists")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", {key: value for key, value in plan.items() if key not in ("credentials", "private_roots")})
    report = dict(status="running", phase_complete=False, primary_review_required=True, candidate=candidate,
                  deployments=deployments, local_runs=local_runs, semantic={}, native_geometry={})
    write_json(output / "reconciliation.json", report)
    try:
        prechecks = [_crossstage_artifact_check(host, plan["deployments"][host], provenance,
                      candidate, plan["local_runs"][host], plan["local_runs"][host]["sha256"])
                     for host in sorted(TEST_HOSTS)]
        if not _record_crossstage_artifact_checks(report, "before", prechecks, output / "reconciliation.json"):
            raise RuntimeError("pre-crossstage artifact recheck failed")
        for profile in PROFILES:
            semantic_output = output / "semantic" / profile
            report["semantic"][profile] = _run_semantic_fixture(deployment, profile, semantic_output)
            write_json(output / "reconciliation.json", report)
        runner = deployment / "source" / "tools/run_geometry_native_crosshost.py"
        if not runner.is_file() or sha(runner) != provenance.get("source_sha256", {}).get("tools/run_geometry_native_crosshost.py"):
            raise RuntimeError("native geometry runner is missing from the frozen source closure")
        run_tag = "gate-" + time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
        for profile in PROFILES:
            short = "debug" if profile == "Debug" else "asan"
            manifest_path = output / ("native-" + short + "-manifest.json")
            for authority, observer in (("sb16", "sb16-2"), ("sb16-2", "sb16")):
                native = _fresh_native_manifest(plan["native_geometry"][profile])
                run_id = run_tag + "-" + short + "-" + authority
                manifest = dict(profile=short, source_commit=candidate, hosts=native["hosts"])
                manifest["timeout_runner"] = _install_timeout_runner(native, authority, observer, run_id, output)
                write_json(manifest_path, manifest)
                argv = [sys.executable, str(runner), "--manifest", str(manifest_path), "--authority", authority, "--run-id", run_id]
                launch_env = {"PATH": "/usr/bin:/bin", "HOME": pwd.getpwuid(os.getuid()).pw_dir}
                launch = subprocess.run(argv, cwd=deployment / "source", env=launch_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                (output / (run_id + ".launch.stdout")).write_bytes(launch.stdout)
                (output / (run_id + ".launch.stderr")).write_bytes(launch.stderr)
                if launch.returncode:
                    raise RuntimeError("native geometry runner refused " + run_id)
                started = json.loads(launch.stdout)
                if started.get("status") != "RUNNING_UNQUALIFIED" or started.get("source_commit") != candidate:
                    raise RuntimeError("native geometry runner did not pin the deployed candidate")
                authority_row = native["hosts"][authority]
                run_dir = Path(authority_row["evidence"]) / run_id / ("authority-" + authority)
                try:
                    _wait_native_run(authority, run_dir)
                except BaseException:
                    _recover_native_timeout(authority, observer, run_dir, output)
                    raise
                observer_row = native["hosts"][observer]
                remote_evidence = str(Path(observer_row["evidence"]) / run_id / ("observer-for-" + authority))
                cache = output / "native-authority" / authority / run_id
                evidence = _validate_native_run(authority, observer, run_dir, remote_evidence, profile, cache)
                report["native_geometry"][run_id] = evidence
                write_json(output / "reconciliation.json", report)
        report["status"] = "passed_needs_primary_review"
        report["phase_complete"] = False
        report["review_required"] = ["primary source review", "native process witness validation", "full M06 gate reconciliation"]
    except BaseException as error:
        report["execution_error"] = repr(error)
        report["error"] = repr(error)
        recovery = output / "timeout-recovery.json"
        if recovery.is_file(): report["timeout_recovery"] = _read_json(recovery, "native timeout recovery")
        postchecks = [_crossstage_artifact_check(host, plan["deployments"][host], provenance,
                       candidate, plan["local_runs"][host], plan["local_runs"][host]["sha256"])
                      for host in sorted(TEST_HOSTS)]
        _record_crossstage_artifact_checks(report, "after", postchecks, output / "reconciliation.json")
        report["status"] = "failed_or_incomplete"
        report["phase_complete"] = False
        write_json(output / "reconciliation.json", report)
        raise
    postchecks = [_crossstage_artifact_check(host, plan["deployments"][host], provenance,
                   candidate, plan["local_runs"][host], plan["local_runs"][host]["sha256"])
                  for host in sorted(TEST_HOSTS)]
    if not _record_crossstage_artifact_checks(report, "after", postchecks, output / "reconciliation.json"):
        report["status"] = "failed_or_incomplete"
        report["phase_complete"] = False
    write_json(output / "reconciliation.json", report)
    return 1 if report.get("status") == "failed_or_incomplete" else 0

def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="mode", required=True)
    b = sub.add_parser("prepare"); b.add_argument("--candidate", required=True); b.add_argument("--output", type=Path, required=True); b.add_argument("--attempt"); b.add_argument("--runtime-manifest", type=Path, required=True); b.set_defaults(fn=prepare)
    d = sub.add_parser("verify-deploy"); d.add_argument("--bundle", type=Path, required=True); d.add_argument("--sha256", required=True); d.add_argument("--archive-manifest", type=Path, required=True); d.add_argument("--host", required=True); d.add_argument("--destination", type=Path, required=True); d.set_defaults(fn=verify_bundle)
    g = sub.add_parser("run-local"); g.add_argument("--deployment", type=Path, required=True); g.add_argument("--attempt")
    g.add_argument("--native-controller", required=True); g.add_argument("--real-world", required=True)
    g.add_argument("--recovery", required=True); g.add_argument("--host-session", required=True); g.add_argument("--held-input", required=True)
    g.set_defaults(fn=gate)
    c = sub.add_parser("crosshost-handoff"); c.add_argument("--plan", type=Path, required=True); c.set_defaults(fn=crosshost)
    a = p.parse_args()
    try: return a.fn(a)
    except BaseException as e: print(f"BLOCKED/FAILED: {e}", file=sys.stderr); return 2
if __name__ == "__main__": raise SystemExit(main())
