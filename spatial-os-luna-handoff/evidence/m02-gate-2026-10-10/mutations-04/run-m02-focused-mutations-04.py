"""Check M02 regression sensitivity in a disposable, detached Git worktree."""
import hashlib
import fcntl
import shutil
import json
import os
from pathlib import Path
import re
import socket
import sys
import subprocess
import time

host = socket.gethostname().split('.')[0]
assert host == 'sb16'
repository = Path('/home/dflanag3/src/spacial_desktop')
base = repository/'spatial-os-luna-handoff/local'
gate = base/'m02-phase-gate-01'
summary = json.loads((gate/'summary.json').read_bytes())
assert summary['status'] == 'failed' and 'ended' in summary, 'Preserve the terminal gate first'
lock=(base/'m02-phase-qualification.lock').open('a')
fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
assert shutil.disk_usage(base).free > 12*2**30
assert not summary['compiler_overlap'] and not summary['monitor_errors']
review = json.loads((gate/'primary-review.json').read_bytes())
manifest = json.loads((gate/'manifest.json').read_bytes())
assert review['host'] == host and review['commit'] == manifest['commit'] == summary['commit']
# This is an independent focused witness check. It cannot complete the failed phase gate.
selected_suites={'authority-store','world-authority','workspace-authority','identity-participant-barrier'}
passed={item['name'] for item in review['profiles']['debug']['core']['suite_results'] if item['status']=='passed'}
assert selected_suites <= passed
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert all(sha(gate/'source'/name) == digest for name, digest in manifest['files'].items())
assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())

def idle():
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            name = (process/'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        assert name not in ('ctest', 'ninja', 'cc1', 'cc1plus', 'clang', 'clang++', 'lto1'), (process, name)

idle()
out = base/'m02-focused-mutations-04'
out.mkdir(exist_ok=False)
worktree = out/'worktree'
source = worktree/'spatial-os-luna-handoff'
build = out/'build'
server = summary['server']
assert sha(Path(server['path'])) == server['sha256']
record = dict(host=host, source_commit=manifest['commit'], status='preparing', commands=[], mutations=[],
    source_files=351, locked_files_unchanged=41, server=server, started=time.time(),
    phase_complete=False, original_gate_status=summary['status'],
    purpose='Independent focused mutation witnesses on unchanged production inputs. Full phase qualification remains pending.')

def save():
    path = out/'summary.json.tmp'
    path.write_bytes((json.dumps(record, indent=2)+'\n').encode())
    path.replace(out/'summary.json')

environment = {name:value for name,value in os.environ.items()
    if name not in ('DISPLAY', 'SPATIAL_TEST_DISPLAY', 'LSAN_OPTIONS')}
environment.update(GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=10', PYTHONDONTWRITEBYTECODE='1',
    CCACHE_BASEDIR=str(out), PYTHONPATH=str(build/'python')+':'+str(source),
    SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build/'spatial-xprobe'))

def run(label, command, cwd, *, expected=0, input_data=None, timeout=600, env=None):
    idle()
    item = dict(label=label, argv=command, started=time.time())
    record['commands'].append(item)
    save()
    with (out/(label+'.log')).open('xb') as log:
        result = subprocess.run(command, cwd=cwd, env=env or environment, input=input_data,
            stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    item.update(exit_code=result.returncode, ended=time.time(), sha256=sha(out/(label+'.log')))
    save()
    assert result.returncode == expected, (label, result.returncode, expected)
    print(host, label, result.returncode, flush=True)
    return (out/(label+'.log')).read_text()

def audit_clean():
    assert all(sha(source/name) == digest for name,digest in manifest['files'].items())
    assert sha(Path(server['path'])) == server['sha256']
    assert not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=worktree)

mutations = [
    dict(name='parent-revocation', file='src/authority/store.cpp', suite='authority-store',
        test='AuthorityTests.test_parent_revocation_invalidates_all_descendants',
        old='if (record.revoked_revision() || !identity(record.terms().subject())) valid = false;',
        new='if (!identity(record.terms().subject())) valid = false;'),
    dict(name='object-scope', file='src/world/objects.cpp', suite='world-authority',
        test='WorldAuthorityTests.test_object_scope_and_visibility_are_enforced_in_actor_engine',
        old='if (!granted(actor, "world.write", {object.room(), object.id()})) throw',
        new='if (false && !granted(actor, "world.write", {object.room(), object.id()})) throw'),
    dict(name='partial-write', file='src/workspace/authority.cpp', suite='workspace-authority',
        test='WorkspaceAuthorityTests.test_partial_file_write_grants_do_not_mount_entire_project_writable',
        old='if (std::all_of(mutation_rights.begin(), mutation_rights.end(), has)) mask |= 1u << wire::FILES_WRITE;',
        new='if (has(mutation_rights[1])) mask |= 1u << wire::FILES_WRITE;'),
    dict(name='premature-completion', file='src/identity/issuer.cpp', suite='identity-participant-barrier',
        test='BarrierTests.test_revocation_stays_pending_until_both_generations_apply',
        old='result.set_complete(count.integer(1) == 0);', new='result.set_complete(true);'),
]
record['selection'] = mutations
save()
try:
    # Fetch reviewed history only. Do not checkout or alter the host's main worktree.
    bundle=base/'m02-mutation-source-01.bundle'
    assert sha(bundle)==sys.argv[1]
    record['bundle_sha256']=sys.argv[1];save()
    run('verify-bundle',['git','bundle','verify',str(bundle)],repository)
    run('fetch', ['git', 'fetch', str(bundle), 'm02/control-recovery'], repository, timeout=300)
    run('worktree', ['git', 'worktree', 'add', '--detach', '--no-checkout', str(worktree), manifest['commit']], repository)
    names = ['AGENTS.md', 'spatial-os-luna-handoff/AGENTS.md'] + ['spatial-os-luna-handoff/'+name for name in manifest['files']]
    assert not any(any(character in name for character in '*?[]!\\') for name in names)
    patterns = ''.join('/'+name+'\n' for name in names).encode()
    run('sparse-checkout', ['git', 'sparse-checkout', 'set', '--no-cone', '--stdin'], worktree, input_data=patterns)
    run('populate-checkout', ['git','read-tree','-mu','HEAD'], worktree)
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=worktree, text=True).strip() == manifest['commit']
    audit_clean()
    run('configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Debug',
        '-DBUILD_TESTING=ON', '-DSPATIAL_INPUT_TESTS=ON', '-DSPATIAL_SANITIZERS=OFF',
        '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache'], source)
    run('clean-build', ['cmake', '--build', str(build), '--parallel', '2'], source, timeout=7200)
    inventory = json.loads(run('inventory', ['ctest', '--test-dir', str(build), '--show-only=json-v1'], source))
    suites = {item['name']:item for item in inventory['tests']}

    def witness(mutation, phase, expected):
        suite = suites[mutation['suite']]
        env = dict(environment, SPATIAL_SECURITY_EVIDENCE=str(out/(mutation['name']+'-'+phase+'-native')))
        for item in suite['properties']:
            if item['name'] == 'ENVIRONMENT':
                for value in item['value']:
                    key, content = value.split('=', 1)
                    env[key] = content
        log = run(mutation['name']+'-'+phase, suite['command']+[mutation['test']], source,
            expected=expected, env=env, timeout=180)
        assert re.search(r'^Ran 1 test in ', log, re.M), mutation['name']
        if expected:
            method = mutation['test'].split('.')[-1]
            assert 'FAIL: '+method in log and 'FAILED (failures=1)' in log, mutation['name']
            assert 'ERROR:' not in log, mutation['name']
        else:
            assert log.rstrip().endswith('OK') and 'skipped' not in log

    for mutation in mutations:
        witness(mutation, 'baseline', 0)
    record['status'] = 'checking_mutations'
    save()
    for mutation in mutations:
        audit_clean()
        target = source/mutation['file']
        before = target.read_bytes()
        old = mutation['old'].encode()
        assert before.count(old) == 1
        after = before.replace(old, mutation['new'].encode())
        try:
            target.write_bytes(after)
            (out/(mutation['name']+'.patch')).write_bytes(subprocess.check_output(['git', 'diff', '--',
                'spatial-os-luna-handoff/'+mutation['file']], cwd=worktree))
            run(mutation['name']+'-build', ['cmake', '--build', str(build), '--parallel', '2'], source, timeout=1800)
            witness(mutation, 'mutant', 1)
            record['mutations'].append(dict(name=mutation['name'], test=mutation['test'], status='detected',
                file=mutation['file'], before_sha256=hashlib.sha256(before).hexdigest(),
                after_sha256=hashlib.sha256(after).hexdigest()))
            save()
        finally:
            target.write_bytes(before)
        audit_clean()
    run('restored-build', ['cmake', '--build', str(build), '--parallel', '2'], source, timeout=1800)
    for mutation in mutations:
        witness(mutation, 'restored', 0)
    audit_clean()
    record['status'] = 'four_mutations_detected_clean_source_restored'
except BaseException as error:
    record.update(status='failed', error=type(error).__name__+': '+str(error))
    raise
finally:
    record['ended'] = time.time()
    save()
