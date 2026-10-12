#!/usr/bin/env python3
"""Build, package, deploy-verify, or run the artifact-only M06 gate."""
from __future__ import annotations
import argparse
import ast
import copy
import shutil
import hashlib
import json
import os
from pathlib import Path
import re
import socket
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
    "presentation-input-bindings",
}
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
        with tarfile.open(dest / "m06-artifacts.tar.gz", "r:gz") as archive:
            rec["archive_files"] = {m.name: hashlib.sha256(archive.extractfile(m).read()).hexdigest()
                                    for m in archive.getmembers() if m.isfile()}
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
    with tarfile.open(bundle, "r:gz") as tar:
        members = tar.getmembers()
        files = {}
        for member in members:
            if not (member.isfile() or member.isdir()) or member.name.startswith("/") or ".." in Path(member.name).parts:
                raise RuntimeError("unsafe bundle member: " + member.name)
            target = (out / member.name).resolve()
            if out not in target.parents:
                raise RuntimeError("unsafe bundle path: " + member.name)
            if member.isfile():
                if member.name in files:
                    raise RuntimeError("duplicate archive file: " + member.name)
                files[member.name] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
        if files != external["files"]:
            raise RuntimeError("archive file manifest mismatch")
        tar.extractall(out)
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
    requirement_suites = dict(zip(('native_controller', 'real_world', 'recovery', 'host_session', 'held_input'), required_slots))
    required = set(REQUIRED) | set(required_slots)
    out = root / "evidence" / (a.attempt or f"{host}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}")
    out.mkdir(parents=True, exist_ok=False)
    report = dict(status="running", phase_complete=False, host=host, candidate=rec["candidate"], attempt=out.name,
                  bundle_sha256=receipt["bundle_sha256"], requirement_suites=requirement_suites, stages=[], blockers=[], profiles={})
    write_json(out / "run.json", report)
    source = root / "source"
    builds = root / "build"
    try:
        for profile in PROFILES:
            build = builds / profile
            production = root / 'production-off' / profile
            for rel, expected in rec['production_off'][profile]['runtime_files'].items():
                if sha(production / rel) != expected:
                    raise RuntimeError('production-off runtime mismatch: ' + rel)
            target = out / profile; target.mkdir()
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
            row_metadata = {str(p.relative_to(build)): sha(p) for p in ctest_metadata(build)}
            write_json(target / "metadata-relocation.json", dict(edits=evidence_edits, startup_packaging_augmentation=startup_edits, after=row_metadata))
            invpath = target / "inventory.json"
            # CTest --show-only is read-only; compare normalized registration names against build-host inventory.
            p, sec = run(["ctest", "--test-dir", str(build), "--show-only=json-v1"], cwd=source, env=env, timeout=120)
            (target / "inventory.log").write_bytes(p.stdout)
            if p.returncode: raise RuntimeError(profile + " inventory command failed")
            deployed = json.loads(p.stdout)["tests"]
            names = audit_inventory(deployed, required)
            require_inventory_equal(deployed, expected_inventory, [])
            verify_inventory_paths(deployed, source, build, (target / 'ctest',))
            write_json(invpath, dict(names=sorted(names), count=len(names), sha256=sha(target / "inventory.log")))
            row = report["profiles"][profile] = dict(status="running", inventory_count=len(names), commands=[
                dict(name="inventory", command=["ctest", "--test-dir", str(build), "--show-only=json-v1"], exit_code=0, seconds=sec, assertions_passed=True)])
            commands = [
                ('production-off-startup', startup_packaging_command(source, production / 'spatial-host-session', production / 'python'), 120),
                ("harness", ["python3", "-m", "unittest", "discover", "-s", "tests/harness", "-t", ".", "-v"], 180),
                ("core", ["ctest", "--test-dir", str(build), "--parallel", "1", "-V", "--output-junit", str(target / "core.xml")], 14400),
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
                        item["all_registered_present"] = len({c.attrib.get("name") for c in cases}) == len(names) and {c.attrib.get("name") for c in cases} == set(names)
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
                if label == "core" and (item.get("junit_cases") != len(names) or not item.get("all_registered_present") or item.get("failed_or_skipped")): raise RuntimeError("CTest full-suite JUnit assertion failed")
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
            findings = [str(p.relative_to(target)) for p in target.rglob("*.log") if SAN_RE.search(p.read_text(errors="replace"))]
            row["sanitizer_logs_scanned"] = len(list(target.rglob("*.log"))); row["sanitizer_findings"] = findings
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
        for profile in PROFILES:
            build = builds / profile
            metadata_record = out / profile / "metadata-relocation.json"
            if metadata_record.is_file():
                expected_metadata = json.loads(metadata_record.read_text()).get("after", {})
                report["metadata_rechecked"][profile] = bool(expected_metadata) and all(
                    (build / rel).is_file() and sha(build / rel) == digest
                    for rel, digest in expected_metadata.items())
            else:
                report["metadata_rechecked"][profile] = False
        report["runtime_rechecked"] = {}
        for profile in PROFILES:
            try:
                verify_runtime(manifest, host, profile, source)
                report["runtime_rechecked"][profile] = True
            except (OSError, KeyError, ValueError, RuntimeError):
                report["runtime_rechecked"][profile] = False
        if not report["source_sha256_rechecked"] or not all(report['production_off_rechecked'].values()) or not all(report["profile_artifacts_rechecked"].values()) or not all(report["runtime_rechecked"].values()) or not all(report["metadata_rechecked"].values()):
            report["status"] = "failed"
            report["error"] = "postrun source, artifact, or runtime invariant failed"
        report["phase_complete"] = False
        write_json(out / "run.json", report)
    return 1 if report["status"] == "failed" else 0

def crosshost(a):
    if not a.plan:
        raise RuntimeError("cross-host workflow requires a separately reviewed final M06 runner plan; unknown CLI is not inferred")
    plan = json.loads(a.plan.read_text())
    if plan.get("runner") is None or plan.get("directions") != ["sb16-to-sb16-2", "sb16-2-to-sb16"] or plan.get("profiles") != list(PROFILES):
        raise RuntimeError("cross-host plan must name reviewed runner, both directions, and both profiles")
    if not plan.get("cases") or not plan.get("provisioning"):
        raise RuntimeError("cross-host plan must enumerate expected cases and provisioning")
    print("BLOCKED: plan validated; runner execution requires reviewed final M06 integration artifacts")
    return 2

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
