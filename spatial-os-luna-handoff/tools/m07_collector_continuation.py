#!/usr/bin/env python3
"""One collector-only Debug-to-sanitizer continuation for the retained M07 candidate."""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import time
import xml.etree.ElementTree as ET

CANDIDATE = '1e58e65dec132a0a16f004ca4527034dca081f77'
DEPLOYMENT = Path('/home/dflanag3/spatial-artifacts/deployments/m07-1e58e65d/runtime')
ARTIFACT_ROOT = Path('/home/dflanag3/spatial-artifacts')
SCHEMA = 'm07-collector-continuation-v1'
TOOLS = ('m06_deployed_gate.py', 'm07_deployed_gate.py', 'm07_collector_continuation.py')
CHECKS = ('profile_artifacts_rechecked', 'production_off_rechecked', 'runtime_rechecked',
          'metadata_rechecked', 'storage_evidence_rechecked')
COMMANDS = ('inventory', 'production-off-startup', 'harness', 'core', 'P10', 'bounded-storage-git')
RUNNER_FILES = ('argv.nul', 'command.exit', 'wrapper.exit', 'terminal-status.txt',
                'command-receipt.txt', 'stdout.log', 'stderr.log')


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def raw_file(path):
    """Read regular files without following links. Ordinary hardlinks remain permitted."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute contained path required')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        require(not stat.S_ISLNK(current.lstat().st_mode), 'symlinked evidence component')
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), 'evidence is not a regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read()
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(identity(before) == identity(opened) == identity(after) == identity(path.lstat()),
            'evidence changed during read')
    return data


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pinned_json(path, expected):
    require(isinstance(expected, str) and re.fullmatch(r'[0-9a-f]{64}', expected), 'invalid input pin')
    raw = raw_file(path)
    require(digest(raw) == expected, 'pinned input changed: ' + str(path))
    return json.loads(raw)


def real_directory(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'invalid evidence directory')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        require(stat.S_ISDIR(current.lstat().st_mode), 'evidence parent is linked or not a directory')
    return path


def write_exclusive(gate, path, value):
    real_directory(Path(path).parent)
    raw = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return digest(raw)


def selected_profiles(gate, value):
    selected = tuple(gate.PROFILES if value is None else value)
    require(selected in (tuple(gate.PROFILES), ('ASan-UBSan',)),
            'select both ordered profiles or the sanitizer singleton')
    return selected


def _pid_absent(pid):
    require(type(pid) is int and pid > 1, 'exact numeric pane PID required')
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except PermissionError as error:
        raise RuntimeError('cannot prove pane PID absent') from error
    raise RuntimeError('pane PID remains live')


def _tools(gate, context):
    expected = context.get('tools')
    require(isinstance(expected, dict) and set(expected) == set(TOOLS), 'exact amended tool closure required')
    here = Path(__file__).resolve().parent
    for name in TOOLS:
        require(digest(raw_file(here / name)) == expected[name], 'amended tool changed: ' + name)
    require(digest(raw_file(Path(gate.__file__).resolve())) == expected['m06_deployed_gate.py'],
            'loaded gate differs from amended tool closure')


def _binding(context):
    return digest(json.dumps({k: v for k, v in context.items() if k != 'debug_audit'},
                             sort_keys=True, separators=(',', ':')).encode())


def load_context(gate, deployment, path, pin):
    root = real_directory(deployment)
    require(root == DEPLOYMENT, 'continuation is limited to the frozen 1e58 deployment')
    context = pinned_json(path, pin)
    host = socket.gethostname().split('.')[0]
    require(context.get('schema') == SCHEMA and context.get('host') == host
            and host in ('sb16', 'sb16-2') and context.get('candidate') == CANDIDATE,
            'continuation host/candidate/schema mismatch')
    require(context.get('deployment') == str(root), 'continuation deployment mismatch')
    batch = context.get('batch_id', '')
    require(isinstance(batch, str) and re.fullmatch(
        r'1e58e65d-' + re.escape(host) + r'-san-collector-[A-Za-z0-9][A-Za-z0-9._-]{0,63}', batch),
        'invalid unique sanitizer batch identity')
    require(type(context.get('capacity_floor_bytes')) is int and context['capacity_floor_bytes'] > 0,
            'reviewed incremental capacity floor required')
    _tools(gate, context)
    return context


def runner_directory(host, attempt):
    require(host in ('sb16', 'sb16-2') and isinstance(attempt, str)
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', attempt), 'invalid canonical runner identity')
    return ARTIFACT_ROOT / 'm07-fixture-repaired-full-gate-plan-20261016/full-gate-runtime-runs' / host / attempt


def _batch_runner(gate, row, root, context, inputs, inputs_sha):
    expected = runner_directory(context['host'], context['batch_id'])
    require(Path(row['path']) == expected, 'sanitizer canonical runner path mismatch')
    require(expected != runner_directory(context['host'], '1e58e65d-' + context['host'] + '-local-01'),
            'sanitizer runner must differ from failed origin')
    launch = pinned_json(row['launch']['path'], row['launch']['sha256'])
    argv = _batch_argv(root, inputs, inputs_sha)
    require(launch.get('schema') == 'm07-collector-batch-launch-v1'
            and launch.get('host') == context['host'] and launch.get('candidate') == CANDIDATE
            and launch.get('batch_id') == context['batch_id'] and launch.get('context_sha256') == inputs_sha
            and launch.get('runner_path') == str(expected) and launch.get('pane_pid') == row['pid']
            and launch.get('argv') == argv, 'actual primary sanitizer launch receipt mismatch')
    result = _runner(gate, row, launch['pane_pid'], 0, argv)
    result['launch'] = dict(row['launch'])
    return result


def _runner(gate, row, expected_pid, expected_exit, expected_argv=None):
    directory = real_directory(row['path'])
    require(directory.is_relative_to(ARTIFACT_ROOT), 'runner outside project artifacts')
    require(row['pid'] == expected_pid, 'runner PID differs from retained launch')
    _pid_absent(row['pid'])
    require(set(row['files']) == set(RUNNER_FILES), 'canonical runner file pins incomplete')
    values = {}
    for name in RUNNER_FILES:
        values[name] = raw_file(directory / name)
        require(digest(values[name]) == row['files'][name], 'actual canonical runner pin changed: ' + name)
    require(values['command.exit'].strip() == str(expected_exit).encode()
            and values['wrapper.exit'].strip() == str(expected_exit).encode()
            and values['terminal-status.txt'].strip() == b'COMPLETE', 'canonical runner terminal/exit mismatch')
    if expected_argv is not None:
        require(values['argv.nul'] == b''.join(os.fsencode(x) + b'\0' for x in expected_argv),
                'canonical runner argv differs')
    return dict(path=str(directory), pid=row['pid'], files=dict(row['files']),
                command_exit=expected_exit, wrapper_exit=expected_exit, terminal='COMPLETE')


def _origin_argv(root, host):
    return ['env', 'PATH=/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE=1', 'python3', '-B',
            'tools/m07_deployed_gate.py', 'run-local', '--deployment', str(root),
            '--attempt', '1e58e65d-' + host + '-local-01', '--native-controller', 'host-actor-controller-native',
            '--real-world', 'actor-transition-real-world', '--recovery', 'actor-transition-recovery',
            '--host-session', 'host-session-production-native', '--held-input', 'host-actor-controller-native']


def _source_closure(gate, root, context):
    provenance = json.loads(raw_file(root / 'provenance.json'))
    receipt = json.loads(raw_file(root / 'deployment-receipt.json'))
    require(provenance.get('candidate') == receipt.get('candidate') == CANDIDATE
            and receipt.get('host') == context['host'] and receipt.get('status') == 'verified',
            'frozen deployment identity changed')
    for key, name in (('provenance_sha256', 'provenance.json'),
                      ('deployment_receipt_sha256', 'deployment-receipt.json')):
        require(gate.sha(root / name) == context[key], 'deployment provenance/receipt pin changed')
    source = real_directory(root / 'source')
    actual = {}
    for path in source.rglob('*'):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not info.st_mode & 0o222, 'deployed source linked or writable')
        if stat.S_ISREG(info.st_mode):
            actual[path.relative_to(source).as_posix()] = digest(raw_file(path))
        else:
            require(stat.S_ISDIR(info.st_mode), 'special deployed source artifact')
    require(len(actual) == 1040 and actual == provenance.get('source_sha256'), 'frozen source closure changed')
    return provenance, receipt


def _origin(gate, root, context):
    retained = pinned_json(context['retention']['path'], context['retention']['sha256'])
    host = context['host']
    out = real_directory(root / 'evidence' / ('1e58e65d-' + host + '-local-01'))
    run_path = out / 'run.json'
    require(retained.get('host') == host and retained.get('pane_retired') is True
            and retained.get('original_run_path') == str(run_path), 'wrong retained Debug origin')
    report = pinned_json(run_path, retained['original_run_sha256'])
    require(report == retained.get('original_run') and report.get('candidate') == CANDIDATE
            and report.get('host') == host and report.get('attempt') == out.name
            and report.get('status') == 'failed' and report.get('phase_complete') is False,
            'original failed report changed')
    links = retained.get('symlinks', [])
    require(len(links) == 1, 'retained collector failure must have one known marker')
    expected_message = 'gate artifact tree contains a file symlink: ' + links[0]['path']
    require(report.get('execution_error', {}).get('type') == 'RuntimeError'
            and report['execution_error'].get('message') == expected_message,
            'original failure is not the pinned postexecution collector failure')
    require(report.get('source_sha256_rechecked') is True and set(report.get('profiles', {})) == {'Debug'},
            'original Debug execution/source was incomplete')
    for item in report.get('postrun_invariant_failures', []):
        require(item.get('profile') == 'ASan-UBSan' and item.get('reason') == 'profile-not-run'
                and item.get('check') in ('metadata_rechecked', 'storage_evidence_rechecked'),
                'original failure includes an executed-profile invariant')
    for check in CHECKS:
        require(report.get(check, {}).get('Debug') is True, 'original Debug postcheck failed: ' + check)
    require(Path(context['origin_runner']['path']) == runner_directory(host, out.name), 'original canonical runner path mismatch')
    _runner(gate, context['origin_runner'], retained['pane_pid'], 1, _origin_argv(root, host))
    for relative, entry in retained['command_evidence_pins'].items():
        require(relative in ('inventory.log', 'inventory.json', 'metadata-relocation.json',
                            'production-off-startup.log', 'harness.log', 'core.log', 'core.xml',
                            'P10.log', 'P10.json', 'bounded-storage-git.log', 'bounded-storage-git/review.json'),
                'unexpected original command anchor')
        raw = raw_file(out / 'Debug' / relative)
        require(len(raw) == entry['size'] and digest(raw) == entry['sha256'], 'original command artifact changed')
    require(len(retained['command_evidence_pins']) == 11, 'all eleven original command anchors required')
    return out, report, retained


def _runtime_profile(gate, root, provenance, host, profile, metadata):
    source = root / 'source'
    build = root / 'build' / profile
    production = root / 'production-off' / profile
    prepared = provenance['profiles'][profile]
    off = provenance['production_off'][profile]
    for base, rows in ((build, prepared['executables']),
                       (build, {k: v for k, v in prepared['runtime_files'].items()
                                if k not in prepared['ctest_metadata']}),
                       (production, off['runtime_files']), (build, prepared['generated_python_sha256'])):
        require(bool(rows), 'empty runtime closure')
        for relative, pin in rows.items():
            require(not Path(relative).is_absolute() and '..' not in Path(relative).parts, 'unsafe runtime relative path')
            require(digest(raw_file(base / relative)) == pin, 'runtime/generated artifact changed: ' + relative)
    for base, row in ((build, prepared), (production, off)):
        require(gate.generated_closure(base) == row['generated_binding_imports'], 'generated import closure changed')
    gate.verify_production_client(source, production)
    gate.verify_runtime(provenance['runtime_manifest'], host, profile, source)
    after = metadata.get('after', {})
    require(bool(after) and set(after) == set(prepared['ctest_metadata']), 'relocated metadata closure incomplete')
    for relative, pin in after.items():
        require(not Path(relative).is_absolute() and '..' not in Path(relative).parts, 'unsafe metadata path')
        require(digest(raw_file(build / relative)) == pin, 'current CTest metadata changed')


def audit_profile(gate, root, provenance, report, profile, expected_manifest=None):
    target = real_directory(root / 'evidence' / report['attempt'] / profile)
    row = copy.deepcopy(report['profiles'][profile])
    commands = row.get('commands', [])
    require([r.get('name') for r in commands] == list(COMMANDS), 'complete ordered profile commands required')
    require(all(c.get('exit_code') == 0 and c.get('assertions_passed') is True for c in commands),
            'profile command failed or lacks assertions')
    inventory = json.loads(raw_file(target / 'inventory.json'))
    query = raw_file(target / 'inventory.log')
    require(digest(query) == inventory.get('sha256'), 'inventory output pin changed')
    inventory_env = gate.profile_environment(os.environ, root / 'source', root / 'build' / profile, target)
    runtime = gate.verify_runtime(provenance['runtime_manifest'], report['host'], profile, root / 'source')
    inventory_env['SPATIAL_TEST_XSERVER'] = runtime['xace_wrapper']['path']
    current, _ = gate.run(['ctest', '--test-dir', str(root / 'build' / profile), '--show-only=json-v1'],
        cwd=root / 'source', env=inventory_env, timeout=120)
    require(current.returncode == 0 and current.stdout == query, 'current CTest registrations differ from retained inventory')
    query_rows = json.loads(query)['tests']
    names = [r['name'] for r in query_rows]
    dedicated = ['geometry-native-crosshost', 'workspace-geometry-native-crosshost']
    require(len(names) == len(set(names)) == inventory.get('count') == 199
            and inventory.get('names') == sorted(names)
            and inventory.get('dedicated_crosshost') == dedicated
            and inventory.get('local_names') == sorted(set(names) - set(dedicated)), 'profile inventory reduced or changed')
    gate._validate_geometry_inventory(inventory, report['host'], profile,
        set(gate.REQUIRED) | set(report['required_tests']), set(dedicated))
    expected_commands = [('inventory', ['ctest', '--test-dir', str(root / 'build' / profile), '--show-only=json-v1'])]
    expected_commands += [(name, argv) for name, argv, timeout in gate.profile_stage_commands(
        root / 'source', root / 'build' / profile, root / 'production-off' / profile, target, set(dedicated), profile=profile)]
    require([(c['name'], c.get('command')) for c in commands] == expected_commands, 'profile actual stage argv differs')
    for name in COMMANDS[1:]:
        text = raw_file(target / (name + '.log')).decode(errors='replace')
        gate.reject_inner_skips(text)
        require(not gate.SAN_RE.search(text), 'sanitizer diagnostic in stage output')
        if name in ('production-off-startup', 'harness'):
            count = 5 if name == 'production-off-startup' else 51
            require(re.search(r'Ran ' + str(count) + r' tests in .*\n\nOK(?:\n|$)', text), 'startup/harness output incomplete')
    cases = list(ET.fromstring(raw_file(target / 'core.xml')).iter('testcase'))
    require(len(cases) == 197 and {c.get('name') for c in cases} == set(inventory['local_names'])
            and not any(node.tag in ('failure', 'error', 'skipped') for c in cases for node in c.iter()),
            'actual core JUnit is incomplete or failed')
    core = commands[3]
    require(core.get('junit_cases') == 197 and core.get('all_registered_present') is True
            and core.get('failed_or_skipped') is False and commands[2].get('expected_51') is True,
            'core/harness stored assertions incomplete')
    p10 = json.loads(raw_file(target / 'P10.json'))
    ids = json.loads(raw_file(root / 'source' / 'acceptance.lock.json'))['test_ids']
    require(p10.get('status') == 'passed' and p10.get('through') == 'P10'
            and p10.get('selected') == p10.get('executed') == p10.get('total_baseline') == 151
            and p10.get('not_selected') == 0 and p10.get('diagnostic_only') is False
            and len(p10.get('tests', [])) == len(set(ids)) == 151
            and {c.get('test') for c in p10['tests']} == set(ids)
            and all(c.get('status') == 'passed' and c.get('phase', 'P99') <= 'P10' for c in p10['tests'])
            and commands[4].get('acceptance_rows_valid') is True
            and commands[4].get('acceptance') == {k: p10.get(k) for k in ('status', 'through', 'selected', 'executed', 'total_baseline', 'not_selected', 'diagnostic_only')}, 'actual locked P10 is reduced or failed')
    manifest = gate.artifact_evidence_manifest(target)
    gate.validate_artifact_links(target, manifest['artifact_links'])
    if expected_manifest is not None:
        require(manifest == expected_manifest, 'full regular/link/exclusion artifact manifest changed')
    logs = [p for p in target.rglob('*.log')]
    require(not any(gate.SAN_RE.search(raw_file(p).decode(errors='replace')) for p in logs), 'artifact sanitizer diagnostic')
    metadata = json.loads(raw_file(target / 'metadata-relocation.json'))
    _runtime_profile(gate, root, provenance, report['host'], profile, metadata)
    augmentation = metadata['storage_local_evidence_augmentation']
    require(augmentation['tests'] == list(gate.STORAGE_LOCAL_EVIDENCE_TESTS), 'storage registration set changed')
    destinations = augmentation['destinations']
    require(set(destinations) == set(gate.STORAGE_LOCAL_EVIDENCE_TESTS)
            and set(augmentation['profile']['registrations']) == set(destinations), 'external storage evidence missing')
    for name, value in destinations.items():
        gate.verify_storage_evidence_path(root / 'source', Path(value))
        require(value == augmentation['profile']['registrations'][name]['path'], 'storage destination map changed')
    storage = gate.scan_storage_evidence({k: Path(v) for k, v in destinations.items()})
    for key, actual in (('storage_evidence_sha256', storage['files']),
                        ('storage_cleanup_receipts', storage['cleanups']),
                        ('storage_evidence_exclusions', storage['exclusions'])):
        require(row.get(key) == actual, 'external storage evidence/cleanup changed')
    row.update(status='passed_needs_crosshost_and_review', artifact_sha256=manifest['sha256'],
               artifact_exclusions=manifest['exclusions'], artifact_links=manifest['artifact_links'],
               sanitizer_findings=[], sanitizer_logs_scanned=len(logs) + storage['logs_scanned'])
    return row, manifest


def audit_debug(gate, deployment, inputs, inputs_sha):
    root = real_directory(deployment)
    context = load_context(gate, root, inputs, inputs_sha)
    provenance, receipt = _source_closure(gate, root, context)
    out, original, retained = _origin(gate, root, context)
    row, manifest = audit_profile(gate, root, provenance, original, 'Debug')
    expected_links = [dict(path=e['path'], kind='completed-execution-retirement-marker',
                          target_bytes_hex=os.fsencode(e['target']).hex(), target_sha256=e['target_sha256'])
                      for e in retained['symlinks']]
    require(manifest['artifact_links'] == expected_links, 'Debug retirement marker differs from retained target')
    result = dict(schema=SCHEMA, status='retained_debug_collector_passed', host=context['host'],
                  candidate=CANDIDATE, context_binding=_binding(context), original_run_sha256=retained['original_run_sha256'],
                  profile='Debug', row=row, manifest=manifest, audited_unix=time.time(),
                  production_source_sha256=provenance['source_sha256'], original_runner=context['origin_runner'])
    write_exclusive(gate, out / 'debug-collector-audit.json', result)
    return result


def prepare_batch(gate, deployment, inputs, inputs_sha):
    root = real_directory(deployment)
    context = load_context(gate, root, inputs, inputs_sha)
    provenance, receipt = _source_closure(gate, root, context)
    out, original, retained = _origin(gate, root, context)
    entry = context.get('debug_audit', {})
    require(entry.get('path') == str(out / 'debug-collector-audit.json'), 'closed accepted Debug audit path required')
    audit = pinned_json(entry['path'], entry['sha256'])
    require(audit.get('status') == 'retained_debug_collector_passed' and audit.get('context_binding') == _binding(context)
            and audit.get('original_run_sha256') == retained['original_run_sha256'], 'accepted Debug audit is stale or unrelated')
    audit_profile(gate, root, provenance, original, 'Debug', audit['manifest'])
    for path in (out / 'ASan-UBSan', out / 'sanitizer-batch.json', out / 'combined-local.json'):
        require(not path.exists() and not path.is_symlink(), 'continuation output already exists: ' + str(path))
    token = hashlib.sha256((context['host'] + '\0' + context['batch_id']).encode()).hexdigest()[:24]
    storage = gate.STORAGE_EVIDENCE_ROOT / context['host'] / token
    require(not storage.exists() and not storage.is_symlink(), 'sanitizer storage batch already exists')
    require(shutil.disk_usage(root).free >= context['capacity_floor_bytes'], 'incremental sanitizer capacity not admitted')
    return dict(out=out, report_path=out / 'sanitizer-batch.json', storage_attempt=context['batch_id'],
                context=context, context_path=str(Path(inputs).absolute()), context_sha256=inputs_sha,
                original_sha256=retained['original_run_sha256'], debug_audit_sha256=entry['sha256'])


def _batch_argv(root, inputs, inputs_sha):
    argv = _origin_argv(root, socket.gethostname().split('.')[0])
    argv[5] = str(Path(__file__).resolve().with_name('m07_deployed_gate.py'))
    return argv + ['--selected-profile', 'ASan-UBSan', '--collector-continuation', str(Path(inputs).absolute()),
                   '--collector-continuation-sha256', inputs_sha]


def compose(gate, deployment, inputs, inputs_sha, runner):
    root = real_directory(deployment)
    context = load_context(gate, root, inputs, inputs_sha)
    provenance, receipt = _source_closure(gate, root, context)
    out, original, retained = _origin(gate, root, context)
    audit = pinned_json(context['debug_audit']['path'], context['debug_audit']['sha256'])
    require(audit.get('status') == 'retained_debug_collector_passed' and audit.get('context_binding') == _binding(context),
            'accepted Debug audit changed')
    batch_path = out / 'sanitizer-batch.json'
    batch = json.loads(raw_file(batch_path))
    require(batch.get('status') == 'profile_batch_passed' and batch.get('report_kind') == 'profile_batch'
            and batch.get('selected_profiles') == ['ASan-UBSan'] and set(batch.get('profiles', {})) == {'ASan-UBSan'}
            and batch.get('batch_id') == context['batch_id'] and batch.get('attempt') == out.name
            and batch.get('candidate') == CANDIDATE and batch.get('host') == context['host']
            and batch.get('phase_complete') is False and batch.get('continuation', {}).get('context_sha256') == inputs_sha
            and batch.get('postrun_invariant_failures') == [], 'sanitizer batch incomplete or unrelated')
    require(batch.get('source_sha256_rechecked') is True and all(
        batch.get(key, {}).get('ASan-UBSan') is True for key in CHECKS), 'sanitizer batch integrity failed')
    require(batch.get('requirement_suites') == original['requirement_suites']
            and batch.get('required_tests') == original['required_tests']
            and batch.get('dedicated_crosshost') == original['dedicated_crosshost']
            and batch.get('bundle_sha256') == original['bundle_sha256'] == receipt['bundle_sha256'],
            'batch requirement/bundle closure changed')
    terminal = _batch_runner(gate, runner, root, context, inputs, inputs_sha)
    debug_row, debug_manifest = audit_profile(gate, root, provenance, original, 'Debug', audit['manifest'])
    san_row, san_manifest = audit_profile(gate, root, provenance, batch, 'ASan-UBSan', dict(
        sha256=batch['profiles']['ASan-UBSan']['artifact_sha256'],
        exclusions=batch['profiles']['ASan-UBSan']['artifact_exclusions'],
        artifact_links=batch['profiles']['ASan-UBSan']['artifact_links']))
    combined = copy.deepcopy(original)
    for key in ('error', 'execution_error'):
        combined.pop(key, None)
    combined.update(schema=SCHEMA, report_kind='combined_collector_amendment',
                    status='local_passed_crosshost_pending', profiles={'Debug': debug_row, 'ASan-UBSan': san_row},
                    selected_profiles=list(gate.PROFILES), source_sha256_rechecked=True, phase_complete=False,
                    postrun_invariant_failures=[], collector_amendment=dict(
                        context_path=str(Path(inputs).absolute()), context_sha256=inputs_sha,
                        original_failed_run=str(out / 'run.json'), original_failed_run_sha256=retained['original_run_sha256'],
                        debug_audit_sha256=context['debug_audit']['sha256'],
                        sanitizer_batch_sha256=digest(raw_file(batch_path)), batch_id=context['batch_id'],
                        batch_runner=terminal, tools=context['tools']))
    for key in CHECKS:
        combined[key] = {profile: True for profile in gate.PROFILES}
    # The flags above derive from both actual profile audits, never inherited success guesses.
    require(digest(raw_file(out / 'run.json')) == retained['original_run_sha256'], 'original failed run changed during composition')
    write_exclusive(gate, out / 'combined-local.json', combined)
    return combined


def audit_artifacts(gate, deployment, inputs, inputs_sha, combined_sha, phase):
    require(phase in ('before-crosshost', 'after-crosshost'), 'fixed cross-host artifact audit phase required')
    root = real_directory(deployment)
    context = load_context(gate, root, inputs, inputs_sha)
    provenance, receipt = _source_closure(gate, root, context)
    out, original, retained = _origin(gate, root, context)
    combined = pinned_json(out / 'combined-local.json', combined_sha)
    amendment = combined.get('collector_amendment', {})
    require(combined.get('status') == 'local_passed_crosshost_pending'
            and combined.get('report_kind') == 'combined_collector_amendment'
            and combined.get('candidate') == CANDIDATE and combined.get('host') == context['host']
            and combined.get('phase_complete') is False and amendment.get('context_sha256') == inputs_sha
            and amendment.get('original_failed_run_sha256') == retained['original_run_sha256']
            and amendment.get('tools') == context['tools'], 'combined local proof mismatch')
    require(combined.get('source_sha256_rechecked') is True
            and combined.get('postrun_invariant_failures') == []
            and combined.get('selected_profiles') == list(gate.PROFILES)
            and set(combined.get('profiles', {})) == set(gate.PROFILES)
            and all(combined.get(key, {}).get(profile) is True for key in CHECKS for profile in gate.PROFILES)
            and all(combined['profiles'][profile].get('status') == 'passed_needs_crosshost_and_review' for profile in gate.PROFILES)
            and amendment.get('original_failed_run') == str(out / 'run.json')
            and amendment.get('batch_id') == context['batch_id'], 'combined local integrity contradicts success')
    require(context['debug_audit']['path'] == str(out / 'debug-collector-audit.json')
            and context['debug_audit']['sha256'] == amendment['debug_audit_sha256'], 'combined Debug audit pin mismatch')
    audit = pinned_json(context['debug_audit']['path'], amendment['debug_audit_sha256'])
    require(audit.get('status') == 'retained_debug_collector_passed'
            and audit.get('context_binding') == _binding(context)
            and audit.get('original_run_sha256') == retained['original_run_sha256'], 'accepted Debug audit changed')
    batch = pinned_json(out / 'sanitizer-batch.json', amendment['sanitizer_batch_sha256'])
    require(batch.get('status') == 'profile_batch_passed' and batch.get('report_kind') == 'profile_batch'
            and batch.get('selected_profiles') == ['ASan-UBSan'] and set(batch.get('profiles', {})) == {'ASan-UBSan'}
            and batch.get('host') == context['host'] and batch.get('candidate') == CANDIDATE
            and batch.get('attempt') == out.name and batch.get('batch_id') == context['batch_id']
            and batch.get('phase_complete') is False and batch.get('source_sha256_rechecked') is True
            and batch.get('postrun_invariant_failures') == []
            and batch.get('continuation', {}).get('context_sha256') == inputs_sha
            and all(batch.get(key, {}).get('ASan-UBSan') is True for key in CHECKS), 'sanitizer batch integrity changed')
    _batch_runner(gate, amendment['batch_runner'], root, context, inputs, inputs_sha)
    manifests = {}
    for profile in gate.PROFILES:
        row = combined['profiles'][profile]
        expected = dict(sha256=row['artifact_sha256'], exclusions=row['artifact_exclusions'], artifact_links=row['artifact_links'])
        _, actual = audit_profile(gate, root, provenance, combined, profile, expected)
        manifests[profile] = dict(regular_files=len(actual['sha256']), artifact_links=actual['artifact_links'],
                                  manifest_sha256=digest(json.dumps(actual, sort_keys=True).encode()))
    result = dict(schema=SCHEMA, status='primary_artifact_recheck_passed', phase=phase, host=context['host'],
                  candidate=CANDIDATE, combined_local_sha256=combined_sha, context_sha256=inputs_sha,
                  tools=context['tools'], profiles=manifests, checked_unix=time.time(), phase_complete=False)
    write_exclusive(gate, out / (phase + '-artifact-audit.json'), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('audit-debug', 'compose', 'audit-artifacts'))
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--inputs-sha256', required=True)
    parser.add_argument('--batch-runner', type=Path)
    parser.add_argument('--batch-runner-sha256')
    parser.add_argument('--combined-sha256')
    parser.add_argument('--phase', choices=('before-crosshost', 'after-crosshost'))
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('fixed_collector_gate', Path(__file__).with_name('m06_deployed_gate.py'))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    import fcntl
    lock = (Path('/run/user') / str(os.getuid()) / 'spatial-m06-gate.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.mode == 'audit-debug':
        result = audit_debug(gate, args.deployment, args.inputs, args.inputs_sha256)
    elif args.mode == 'compose':
        runner = pinned_json(args.batch_runner, args.batch_runner_sha256)
        result = compose(gate, args.deployment, args.inputs, args.inputs_sha256, runner)
    else:
        result = audit_artifacts(gate, args.deployment, args.inputs, args.inputs_sha256, args.combined_sha256, args.phase)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
