import hashlib, json, pathlib, socket, tarfile

r = pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
out = r / 'local/owned-startup-diagnostic-01'
run = json.loads((out / 'run.json').read_text())
assert run['host'] == host and run['production_or_tests_changed'] is False
assert len(run['results']) == 15 and len({x['name'] for x in run['results']}) == 15
source = r / 'local/m02-owned-participant-02/source'
manifest = json.loads((r / 'local/owned-participant-candidate-01.json').read_text())['files']
for files in (manifest, json.loads((source / 'acceptance.lock.json').read_text())['files']):
    for name, digest in files.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
files = [(out / 'run.json', 'run.json')]
for result in run['results']:
    folder = out / result['name']
    log = (folder / 'test.log').read_text()
    assert result['exit_code'] == 0 and 'Ran 1 test in ' in log and log.rstrip().endswith('OK')
    files.append((folder / 'test.log', result['name'] + '/test.log'))
    for path in sorted((folder / 'server').glob('*.log')):
        files.append((path, result['name'] + '/server/' + path.name))
summary = dict(host=host, checks=15, failed=0, unchanged_source_files=len(manifest),
               interpretation='Diagnostic repeats only. Original full sanitizer failures remain unresolved; timing cause is unproven.')
path = out / 'summary.json'
path.write_text(json.dumps(summary, indent=2) + '\n')
files.append((path, 'summary.json'))
with tarfile.open(r / ('local/owned-startup-diagnostic-' + host + '-01.tar.gz'), 'w:gz') as archive:
    for path, name in files:
        archive.add(path, arcname='startup-diagnostic-01/' + host + '/' + name)
print(json.dumps(summary), flush=True)
