"""Freeze current source bytes locally. Perform all compilation and tests on Linux."""
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

root = Path(__file__).resolve().parents[1]
candidate = sys.argv[1]
assert candidate.startswith('m03-session-resources-') and candidate[-2:].isdigit()
runner = root/'local'/('qualify-'+candidate+'.py')
ast.parse(runner.read_bytes())
ast.parse((root/'tests/core/test_broker_resources.py').read_bytes())
old = json.loads((root/'evidence/m03-resource-boundary-2026-10-10/candidate-02/sb16/manifest.json').read_bytes())
names = sorted(set(old['files']) | {'src/workspace/resources.cpp', 'src/workspace/resources.hpp',
    'tests/core/test_broker_resources.py', 'docs/M03_BROKER_RESOURCE_CHECKS.txt','docs/M03_SESSION_RESOURCE_CHECKS.txt','tests/native/test_session_resources.py'})
sha = lambda data: hashlib.sha256(data).hexdigest()
manifest = dict(base_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=root, text=True).strip(),
    files={name:sha((root/name).read_bytes()) for name in names}, locked_files=old['locked_files'],
    selection='docs/M03_SESSION_RESOURCE_CHECKS.txt', candidate=candidate)
assert all(manifest['files'][name]==value for name,value in manifest['locked_files'].items())
output = root/'local'/(candidate+'-source.tar.gz')
assert not output.exists()
with tarfile.open(output, 'w:gz') as archive:
    def add(name, data):
        info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
        archive.addfile(info, io.BytesIO(data))
    for name in names: add(candidate+'/source/'+name, (root/name).read_bytes())
    add(candidate+'/manifest.json', (json.dumps(manifest, indent=2)+'\n').encode())
    add(candidate+'/runner.py', runner.read_bytes())
print(json.dumps(dict(archive=str(output), sha256=sha(output.read_bytes()), files=len(names),
    locked_files=len(manifest['locked_files']))))
