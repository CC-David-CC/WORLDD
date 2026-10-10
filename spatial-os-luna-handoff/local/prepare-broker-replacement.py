"""Freeze a candidate and generate its verified unpack command."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

root = Path(__file__).resolve().parents[1]
tag = sys.argv[1]
assert tag in ('01', '02', '03', '04')
prior = json.loads((root / 'local/session-replacement-source-03.json').read_bytes())
names = list(prior['files']) + ['tests/native/test_broker_replacement.py']
files = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in names}
sha = lambda data: hashlib.sha256(data).hexdigest()
assert all(sha(files[name]) == digest for name, digest in prior['locked_files'].items())
changes = {name for name in names if sha(files[name]) != prior['files'].get(name)}
assert changes <= {'tests/native/test_broker_replacement.py', 'CMakeLists.txt'}, changes
manifest = dict(files={name: sha(data) for name, data in files.items()}, locked_files=prior['locked_files'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    changed_from_qualified=sorted(changes), purpose='Actual broker replacement focused qualification')
encoded = (json.dumps(manifest, indent=2) + '\n').encode()
prefix = 'broker-replacement-source-' + tag
with (root / 'local' / (prefix + '.json')).open('xb') as out: out.write(encoded)
archive = root / 'local' / (prefix + '.tar')
with tarfile.open(archive, 'x') as tar:
    for name, data in [('manifest.json', encoded)] + [('source/' + name, data) for name, data in files.items()]:
        member = tarfile.TarInfo(name); member.size = len(data); member.mode = 0o644
        tar.addfile(member, io.BytesIO(data))
digest = sha(archive.read_bytes())
unpack = (root / 'local/unpack-session-replacement-03.py').read_text()
unpack = unpack.replace('session-replacement-source-03', prefix)
unpack = unpack.replace('m02-session-replacement-03', 'm02-broker-replacement-' + tag)
unpack = unpack.replace('2562f67dcf0972e48b416ce10fa389207a65db2bf575b1e3264f8be1804ffac7', digest)
with (root / 'local' / ('unpack-broker-replacement-' + tag + '.py')).open('xb') as out: out.write(unpack.encode())
state_path = root / 'local/driver-state.json'
state = json.loads(state_path.read_text())
state['broker_replacement_check'] = dict(status='frozen_unrun', host='sb16', candidate='m02-broker-replacement-' + tag,
    source_files=len(files), locked_files=len(prior['locked_files']), source_archive_sha256=digest,
    selection='docs/BROKER_REPLACEMENT_CHECKS.txt', full_phase_gate='pending on both hosts')
state_path.write_text(json.dumps(state, indent=2) + '\n')
print(json.dumps(dict(files=len(files), changes=sorted(changes), archive_sha256=digest)))
