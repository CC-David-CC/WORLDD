import ast, difflib, hashlib, json, pathlib, shutil, subprocess, tarfile, tempfile

r = pathlib.Path(__file__).resolve().parents[1]
local = r / 'local'
source = local / 'authority-supervisor-draft-01'
evidence = r / 'evidence/authority-supervisor-2026-10-09'
base = json.loads((local / 'participant-relay-candidate-01.json').read_text())['files']
files = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
         for name in sorted([*base, 'tests/native/test_authority_supervisor.py', 'src/supervisor/main.cpp'])}
assert len(files) == 279
for name, digest in json.loads((source / 'acceptance.lock.json').read_text())['files'].items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
for name in base:
    if name.startswith('tests/'):
        assert files[name] == base[name], name
tree = ast.parse((source / 'tests/native/test_authority_supervisor.py').read_text())
tests = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')]
assert len(tests) == 14, tests
target = local / 'authority-supervisor-candidate-01'
assert not target.with_suffix('.json').exists()
manifest = dict(base='participant-relay-candidate-01', files=files, new_tests=tests, qualified=False, integrated=False)
target.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8', newline='\n')
with tarfile.open(target.with_suffix('.tar'), 'w') as archive:
    for name in files:
        archive.add(source / name, arcname=name, recursive=False)
with tempfile.TemporaryDirectory(dir=local, prefix='supervisor-reconstruction-') as temporary:
    before = pathlib.Path(temporary)
    with tarfile.open(local / 'participant-relay-candidate-01.tar') as archive:
        archive.extractall(before, filter='data')
    patches = ''
    changed = []
    for name, digest in files.items():
        if digest == base.get(name):
            continue
        changed.append(name)
        old = (before / name).read_text().splitlines(keepends=True) if name in base else []
        new = (source / name).read_text().splitlines(keepends=True)
        patches += ''.join(difflib.unified_diff(old, new, fromfile='a/' + name if old else '/dev/null', tofile='b/' + name))
    patch = evidence / 'candidate-01.patch'
    patch.write_text(patches, encoding='utf-8', newline='\n')
    subprocess.run(['git', 'apply', '--unsafe-paths', str(patch)], cwd=before, check=True)
    for name, digest in files.items():
        assert hashlib.sha256((before / name).read_bytes()).hexdigest() == digest, name
shutil.copyfile(target.with_suffix('.json'), evidence / 'candidate-source-01.json')
qualifier = (local / 'qualify-participant-relay-01.py').read_text()
qualifier = qualifier.replace('m02-participant-relay-01', 'm02-authority-supervisor-01').replace('participant-relay-candidate-01', 'authority-supervisor-candidate-01')
qualifier = qualifier.replace("out=r/'local/m02-authority-supervisor-01';", """# Avoid overlapping builds with the timed relay matrix. Do not restart that matrix here.
for profile in ('debug', 'runtime-sanitize'):
    completed=json.loads((r/'local/m02-participant-relay-01'/profile/'run-full.json').read_text())
    assert len(completed['results'])==6 and all(x['exit_code']==0 for x in completed['results'])
out=r/'local/m02-authority-supervisor-01';""")
qualifier = qualifier.replace("('focused',['ctest'", "('supervisor',['ctest','--test-dir',str(build),'-V','-R','^authority-supervisor$']),\n        ('focused',['ctest'")
qualifier = qualifier.replace('supervisor participant relay focused qualification complete', 'production authority supervisor focused qualification complete')
(local / 'qualify-authority-supervisor-01.py').write_text(qualifier, encoding='utf-8', newline='\n')
review = dict(status='Primary-agent static review complete; Linux build and runtime qualification pending.',
    source_files=279, new_tests=tests, changed_files=changed, existing_tests_unchanged=True,
    locked_files_unchanged=41, patch_reconstruction='All 279 hashes match after applying candidate patch to frozen relay source.',
    native_handoff='Pending: worldd/workspaced still need supervised launch and native credentials.',
    checks=['Private configuration and direct-parent management peer validation.',
            'No child execution before exact durable approval and exclusive configuration persistence.',
            'Reject privileged descriptor numbers and inode aliases before creating ownership.',
            'Automatic bounded relay; private management only inspects or stops.',
            'Successful completion requires explicit release and retained direct-child exit status zero.',
            'Cleanup records local effects without claiming issuer generation retirement.'],
    integrated=False, qualified=False)
(evidence / 'source-review.json').write_text(json.dumps(review, indent=2) + '\n', encoding='utf-8', newline='\n')
for name in ('freeze-authority-supervisor-01.py', 'qualify-authority-supervisor-01.py'):
    shutil.copyfile(local / name, evidence / 'scripts' / name)
print(json.dumps(dict(source_files=len(files), changed=changed, new_tests=len(tests), build='not started', patch_reconstruction='passed')))
