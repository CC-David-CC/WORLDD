import hashlib, json, pathlib, socket, tarfile

r = pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
out = r / 'local/native-scope-config-baseline-01'
run = json.loads((out / 'run.json').read_text())
assert run['host'] == host and run['production_changed'] is False
assert [(x['profile'], x['name'], x['exit_code']) for x in run['results']] == [
    (profile, name, code) for profile in ('debug', 'runtime-sanitize')
    for name, code in (('compile', 0), ('link', 0), ('test', 1))]
source = r / 'local/m02-participant-relay-01/source'
manifest = json.loads((r / 'local/participant-relay-candidate-01.json').read_text())['files']
locked = json.loads((source / 'acceptance.lock.json').read_text())['files']
for files in (manifest, locked):
    for name, digest in files.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
files = [(out / 'run.json', 'run.json')]
for profile in ('debug', 'runtime-sanitize'):
    for name, digest in run['linked_inputs'][profile].items():
        assert hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() == digest, name
    folder = out / profile
    log = (folder / 'test.log').read_text()
    assert 'FAIL: NativeScope consumed or replaced the caller-owned configuration descriptor' in log
    assert 'Ran 3 tests in ' in log and 'FAILED (failures=2)' in log
    assert 'FAIL: NativeScope consumed the caller-owned rejected configuration descriptor' in log
    assert 'PASS: NativeLaunch rejection retains its consuming interface' in log
    for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
        assert marker not in log, marker
    for name in ('compile.log', 'link.log', 'test.log'):
        files.append((folder / name, profile + '/' + name))
    for path in (folder / 'server').glob('*.log'):
        files.append((path, profile + '/server/' + path.name))
summary = dict(host=host, source_files=len(manifest), locked_files=len(locked), production_changed=False,
    profiles=['debug', 'runtime-sanitize'], expected_failures_per_profile=2, passed_controls_per_profile=1, acceptance_pass=False,
    result='NativeScope consumes or replaces the configuration descriptor retained by its caller.',
    limitation='This proves ownership loss. It does not establish the cause of earlier world startup failures.')
path = out / 'summary.json'
path.write_text(json.dumps(summary, indent=2) + '\n')
files.append((path, 'summary.json'))
with tarfile.open(r / ('local/native-scope-config-baseline-' + host + '-01.tar.gz'), 'w:gz') as archive:
    for path, name in files:
        archive.add(path, arcname='baseline-01/' + host + '/' + name)
print(json.dumps(summary), flush=True)
