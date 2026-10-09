import hashlib, json, pathlib, socket, tarfile

r = pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
out = r / 'local/m02-authority-supervisor-baseline-01'
source = out / 'source'
manifest = json.loads((r / 'local/authority-supervisor-baseline-01.json').read_text())['files']
locked = json.loads((source / 'acceptance.lock.json').read_text())['files']
for files in (manifest, locked):
    for name, digest in files.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
folder = out / 'debug'
run = json.loads((folder / 'run.json').read_text())
assert [x['name'] for x in run['results']] == ['configure', 'build', 'harness', 'baseline']
assert [x['exit_code'] for x in run['results']] == [0, 0, 0, 8]
log = (folder / 'baseline.log').read_text()
assert 'Missing production authority supervisor; a manually ticked test probe does not provide its event loop' in log
assert 'Ran 1 test in ' in log and 'FAILED (failures=1)' in log
harness = (folder / 'harness.log').read_text()
assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
summary = dict(host=host, source_files=len(manifest), locked_files=len(locked),
               harness_passed=51, expected_baseline_failures=1, acceptance_pass=False,
               interpretation='Missing executable confirmed. Runtime behavior remains unimplemented and untested.')
path = out / 'summary-baseline.json'
path.write_text(json.dumps(summary, indent=2) + '\n')
with tarfile.open(r / ('local/authority-supervisor-baseline-' + host + '-01.tar.gz'), 'w:gz') as archive:
    for name in ('run.json', 'configure.log', 'build.log', 'harness.log', 'baseline.log'):
        archive.add(folder / name, arcname='baseline-01/' + host + '/' + name)
    archive.add(path, arcname='baseline-01/' + host + '/summary.json')
print(json.dumps(summary), flush=True)
