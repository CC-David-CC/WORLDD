import hashlib, json, pathlib, re, socket, subprocess, sys, tarfile
from google.protobuf import descriptor_pb2

r = pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
mode = sys.argv[1]
assert host in ('sb16', 'sb16-2') and mode in ('debug', 'final', 'failure')
out = r / 'local/m02-authority-supervisor-01'
source = out / 'source'
manifest = json.loads((r / 'local/authority-supervisor-candidate-01.json').read_text())['files']
locked = json.loads((source / 'acceptance.lock.json').read_text())['files']
for files in (manifest, locked):
    for name, digest in files.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
summary = dict(host=host, candidate=1, source_files=len(manifest), locked_files=len(locked), integrated=False)
files = []
failed = False
for profile in ('debug', 'runtime-sanitize'):
    folder = out / profile
    if profile == 'runtime-sanitize' and (mode == 'debug' or not (folder / 'run.json').exists()):
        assert mode != 'final'
        continue
    run = json.loads((folder / 'run.json').read_text())
    failed = failed or any(result['exit_code'] for result in run['results'])
    if mode != 'failure':
        assert [x['name'] for x in run['results']] == ['configure', 'build', 'harness', 'supervisor', 'focused']
        assert all(x['exit_code'] == 0 for x in run['results'])
        for name, checks, suites in (('supervisor', 14, 1), ('focused', 366, 23)):
            log = (folder / (name + '.log')).read_text()
            counts = [int(x) for x in re.findall(r'^\d+: Ran (\d+) tests? in ', log, re.M)]
            assert len(counts) == suites and sum(counts) == checks
            assert f'100% tests passed, 0 tests failed out of {suites}' in log
            assert '... skipped' not in log and 'skipped=' not in log
        harness = (folder / 'harness.log').read_text()
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        summary[profile] = dict(checks=380, suites=24, new_checks=14, existing_checks=366, harness_checks=51, failed=0, skipped=0)
    else:
        summary[profile] = run
    for name in ('run.json', 'configure.log', 'build.log', 'harness.log', 'supervisor.log', 'focused.log'):
        if (folder / name).exists():
            files.append((folder / name, 'candidate-01/' + host + '/' + profile + '/' + name))
    for path in (folder / 'server').glob('AuthoritySupervisorTests-*.log'):
        files.append((path, 'candidate-01/' + host + '/' + profile + '/server/' + path.name))
if mode == 'failure':
    assert failed
else:
    assert not failed
audit = out / 'descriptor-audit'
audit.mkdir(exist_ok=True)
descriptors = []
commands = []
for label, tree in (('before', r / 'local/m02-participant-relay-01/source'), ('after', source)):
    target = audit / (label + '.descriptor')
    command = ['protoc', '--include_imports', '--descriptor_set_out=' + str(target)]
    command += ['-I' + str(path) for path in sorted((tree / 'proto').glob('spatial/*/v1'))]
    command += [str(tree / 'proto/spatial/supervisor/v1/launch.proto')]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    commands.append(dict(command=command, exit_code=result.returncode))
    descriptors.append({item.name: item for item in descriptor_pb2.FileDescriptorSet.FromString(target.read_bytes()).file})
before, after = descriptors
assert before.keys() == after.keys()
launch = after['launch.proto']
launch.message_type.remove(next(item for item in launch.message_type if item.name == 'AuthoritySupervisorConfig'))
for name in before:
    assert before[name].SerializeToString() == after[name].SerializeToString(), name
summary['schema'] = dict(existing_descriptors_unchanged=True, added_message='AuthoritySupervisorConfig')
path = audit / 'run.json'
path.write_text(json.dumps(dict(commands=commands, result=summary['schema']), indent=2) + '\n')
files.append((path, 'candidate-01/' + host + '/descriptor-audit.json'))
path = out / ('summary-' + mode + '.json')
path.write_text(json.dumps(summary, indent=2) + '\n')
files.append((path, 'candidate-01/' + host + '/summary-' + mode + '.json'))
with tarfile.open(r / ('local/authority-supervisor-' + mode + '-' + host + '-01.tar.gz'), 'w:gz') as archive:
    for path, name in files:
        archive.add(path, arcname=name)
print(json.dumps(summary), flush=True)
