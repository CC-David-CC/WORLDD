#!/usr/bin/env python3
"""Preflight the focused M07 broker-fence package without launching services."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROFILES = ('debug', 'asan')
TESTS = (
    'workspace-authorization-fence',
    'workspace-authorization-fence-scoped',
    'workspace-authorization-fence-registry',
    'workspace-authorization-fence-credential',
    'workspace-authorization-fence-no-authority',
    'workspace-authorization-fence-bootstrap',
)
BINARIES = ('identityd', 'spatial-issuer-client', 'worldd', 'workspaced', 'spatial-app-init', 'spatial-process-scope-probe')
PROTO_MODULES = ('broker_pb2', 'copy_pb2', 'core_pb2', 'grants_pb2', 'issuer_pb2', 'key_pb2', 'native_scope_pb2', 'process_scope_pb2', 'security_pb2', 'storage_pb2', 'geometry_view_pb2', 'world_session_pb2')
PROTO_TARGETS = ('broker-python-protobuf', 'workspace-copy-python-protobuf', 'python-protobuf', 'grants-python-protobuf', 'issuer-python-protobuf', 'key-python-protobuf', 'native-scope-python-protobuf', 'scope-python-protobuf', 'security-python-protobuf', 'storage-python-protobuf', 'geometry-view-python-protobuf', 'world-session-python-protobuf')
WRAPPERS = ('common/tools/with_resource_delegate.py', 'common/tools/with_adapter_resources.py')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def source_findings(source: Path) -> list[str]:
    findings: list[str] = []
    cmake = (source / 'CMakeLists.txt').read_text()
    broker_path = source / 'harness/resource_broker.py'
    world_path = source / 'src/world/main.cpp'
    config_path = source / 'config/conformance.textproto'
    if not broker_path.is_file() or "'spatial-process-scope-probe'" not in broker_path.read_text():
        findings.append('source closure: ResourceBroker sibling spatial-process-scope-probe launch not declared')
    if not world_path.is_file() or 'parent_path() / "runtime.textproto"' not in world_path.read_text():
        findings.append('source closure: worldd adjacent runtime.textproto read not declared')
    if not config_path.is_file() or 'configure_file(config/conformance.textproto runtime.textproto COPYONLY)' not in cmake:
        findings.append('source closure: frozen config/conformance.textproto runtime generation not declared')
    for name in TESTS:
        start = cmake.find(f'add_test(NAME {name} ')
        end = cmake.find('\n    add_test(', start + 1) if start >= 0 else -1
        block = cmake[start:end if end >= 0 else len(cmake)] if start >= 0 else ''
        for token in WRAPPERS:
            short = token.split('/')[-1]
            if short not in block:
                findings.append(f'source closure: {name} missing {short}')
        for target in ('identityd', 'spatial-issuer-client', 'worldd'):
            if f'TARGET_FILE:{target}' not in block:
                findings.append(f'source closure: {name} missing target {target}')
        properties = next((block for block in re.findall(r'set_tests_properties\((.*?)\)', cmake, re.S)
                           if name in block), '')
        if 'ENVIRONMENT "PYTHONPATH=' not in properties:
            findings.append(f'source closure: {name} missing declared Python environment')
    target_start = cmake.find('add_custom_target(spatial-m07-fence-runtime-fixture)')
    dependency_start = cmake.find('add_dependencies(spatial-m07-fence-runtime-fixture', target_start) if target_start >= 0 else -1
    target_end = cmake.find(')', dependency_start) if dependency_start >= 0 else -1
    target_block = cmake[target_start:target_end + 1] if target_start >= 0 and target_end >= 0 else ''
    for target in (*BINARIES, *PROTO_TARGETS):
        if target not in target_block:
            findings.append(f'source closure: spatial-m07-fence-runtime-fixture missing dependency {target}')
    return findings


def manifest_findings(package: Path, manifest: dict) -> list[str]:
    findings: list[str] = []
    closure = manifest.get('fence_runtime_closure')
    if not isinstance(closure, dict):
        return ['manifest missing fence_runtime_closure']
    if tuple(closure.get('profiles', ())) != PROFILES:
        findings.append('manifest fence_runtime_closure profiles differ from debug,asan')
    if tuple(closure.get('required_binaries', ())) != BINARIES:
        findings.append('manifest fence_runtime_closure required_binaries mismatch')
    if tuple(closure.get('protobuf_modules', ())) != PROTO_MODULES:
        findings.append('manifest fence_runtime_closure protobuf_modules mismatch')
    if tuple(closure.get('ctest_names', ())) != TESTS:
        findings.append('manifest fence_runtime_closure ctest_names mismatch')
    pins = closure.get('source_pins', {})
    if not pins.get('original_commit') or not pins.get('equivalent_commit') or not pins.get('equivalence_evidence_sha256'):
        findings.append('manifest source_pins must record original/equivalent commits and equivalence evidence SHA256')
    runtime_equivalence = closure.get('runtime_source_equivalence', {})
    if runtime_equivalence.get('equal') is not True or not runtime_equivalence.get('scope'):
        findings.append('manifest must separate unchanged runtime-source equivalence from build/tool overlays')
    cmake_overlay = closure.get('cmake_overlay', {})
    if len(cmake_overlay.get('sha256', '')) != 64 or not cmake_overlay.get('purpose'):
        findings.append('manifest must record the changed CMake overlay SHA256 and purpose')
    expected_config = closure.get('runtime_textproto_sha256')
    if not isinstance(expected_config, str) or len(expected_config) != 64:
        findings.append('manifest missing runtime_textproto_sha256')
    return findings


def profile_findings(package: Path, profile: str, expected_config: str) -> list[str]:
    findings: list[str] = []
    base = package / profile
    for relative in WRAPPERS:
        if not (package / relative).is_file():
            findings.append(relative)
    for binary in BINARIES:
        path = base / 'bin' / binary
        rel = f'{profile}/bin/{binary}'
        if not path.is_file() or not os.access(path, os.X_OK):
            findings.append(rel)
            continue
        result = subprocess.run(['ldd', '-r', str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode or any(marker in result.stdout.lower() for marker in ('not found', 'undefined symbol', 'symbol lookup error')):
            findings.append(f'{rel}: unresolved dynamic dependency: {result.stdout.strip()}')
    config = base / 'bin/runtime.textproto'
    if not config.is_file():
        findings.append(f'{profile}/bin/runtime.textproto')
    elif digest(config) != expected_config:
        findings.append(f'{profile}/bin/runtime.textproto: SHA256 differs from frozen conformance config')
    python_root = base / 'python'
    common_root = package / 'common'
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONPATH'] = f'{python_root}:{common_root}'
    code = 'import importlib; names=' + repr(PROTO_MODULES) + '; [importlib.import_module(n) for n in names]'
    result = subprocess.run([sys.executable, '-c', code], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        findings.append(f'{profile}/python protobuf import closure: {result.stdout.strip()}')
    return findings


def validate(package: Path, source: Path | None = None) -> tuple[list[str], dict]:
    findings: list[str] = []
    manifest_path = package / 'manifest.json'
    if not manifest_path.is_file():
        return ['manifest.json missing'], {}
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f'manifest.json invalid: {exc}'], {}
    findings.extend(manifest_findings(package, manifest))
    if source is not None:
        findings.extend(source_findings(source))
        closure = manifest.get('fence_runtime_closure', {})
        config = source / 'config/conformance.textproto'
        if config.is_file() and closure.get('runtime_textproto_sha256') != digest(config):
            findings.append('manifest runtime_textproto_sha256 differs from source config/conformance.textproto')
    expected = manifest.get('fence_runtime_closure', {}).get('runtime_textproto_sha256', '')
    for profile in PROFILES:
        findings.extend(profile_findings(package, profile, expected))
    return findings, manifest


def regression(package: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    expected = manifest['fence_runtime_closure']['runtime_textproto_sha256']
    def link_or_copy(src: str, dst: str) -> str:
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        return dst
    with tempfile.TemporaryDirectory(prefix='m07-fence-closure-regression-', dir=package.parent) as temporary:
        fixture = Path(temporary) / package.name
        shutil.copytree(package, fixture, copy_function=link_or_copy)
        for profile in PROFILES:
            helper = fixture / profile / 'bin/spatial-process-scope-probe'
            helper.unlink()
            observed = profile_findings(fixture, profile, expected)
            exact = f'{profile}/bin/spatial-process-scope-probe'
            if exact not in observed:
                errors.append(f'negative regression failed: omitted {exact} was not named')
            link_or_copy(str(package / profile / 'bin/spatial-process-scope-probe'), str(helper))
            config = fixture / profile / 'bin/runtime.textproto'
            config.unlink()
            observed = profile_findings(fixture, profile, expected)
            exact = f'{profile}/bin/runtime.textproto'
            if exact not in observed:
                errors.append(f'negative regression failed: omitted {exact} was not named')
            link_or_copy(str(package / profile / 'bin/runtime.textproto'), str(config))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root', type=Path)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--regression', action='store_true', help='run full positive control plus helper/config omission regressions')
    parser.add_argument('--source-only', action='store_true', help='inspect frozen source CMake, ResourceBroker, and worldd closure')
    args = parser.parse_args()
    if args.source_only:
        if not args.source_root:
            parser.error('--source-only requires --source-root')
        findings = source_findings(args.source_root.resolve())
        for finding in findings:
            print(f'FAIL: {finding}', file=sys.stderr)
        print(f'source_preflight={"FAIL" if findings else "PASS"} findings={len(findings)}')
        return bool(findings)
    if not args.package_root:
        parser.error('--package-root is required unless --source-only is used')
    findings, manifest = validate(args.package_root.resolve(), args.source_root.resolve() if args.source_root else None)
    if findings:
        for finding in findings:
            print(f'FAIL: {finding}', file=sys.stderr)
        print(f'preflight=FAIL findings={len(findings)}')
        return 1
    print(f'positive_control=PASS profiles={len(PROFILES)} binaries={len(BINARIES)} protobuf_imports={len(PROTO_MODULES)}')
    if args.regression:
        errors = regression(args.package_root.resolve(), manifest)
        if errors:
            for error in errors:
                print(f'FAIL: {error}', file=sys.stderr)
            return 1
        print('negative_regressions=PASS missing=debug/bin/spatial-process-scope-probe,debug/bin/runtime.textproto,asan/bin/spatial-process-scope-probe,asan/bin/runtime.textproto')
    print('preflight=PASS runtime_tests=UNRUN')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())