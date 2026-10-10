"""Preserve the completed candidate02 without treating inaccessible procfs as a pass."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
root = r/'local/m02-control-owner-02'
launch = json.loads((root/'launch.json').read_text())
path = Path('/proc')/str(launch['pid'])/'cmdline'
assert not path.exists() or b'qualify-control-owner-02.py' not in path.read_bytes()
summary = json.loads((root/'qualification/summary.json').read_text())
assert summary['status'] == 'failed'
manifest = json.loads((root/'manifest.json').read_text())
for group in ('files','locked_files'):
    for name,digest in manifest[group].items():
        assert hashlib.sha256((root/'source'/name).read_bytes()).hexdigest()==digest, name
text = (root/'qualification/debug/owners.log').read_text()
assert 'PermissionError: [Errno 13] Permission denied:' in text
assert len(re.findall(r'ERROR: test_', text)) == 1
passed = len(re.findall(r'^\d+: test_.* \.\.\. ok$', text, re.M))
assert passed == 18
review = dict(host=host, source_files=297, locked_files=41, status='failed',
    new_checks_passed=18, new_checks_errored=1, harness_passed=51,
    remaining_regressions='unrun after owner-suite failure', sanitizer_profile='unrun',
    failure='The independent descriptor-count witness cannot read the hardened broker procfs directory.',
    correction='Use read-only sudo enumeration of this exact unreaped fixture child; keep broker protections and assertions.',
    production_changed_by_correction=False,
    binaries={name:hashlib.sha256((root/'debug/build'/name).read_bytes()).hexdigest() for name in
        ('workspaced','worldd','identityd','spatialwm','spatial-issuer-client','spatial-security-auth')})
(root/'results-review.json').write_text(json.dumps(review,indent=2)+'\n')
archive = r/'local'/('control-owner-02-'+host+'.tar.gz')
assert not archive.exists()
with tarfile.open(archive,'w:gz') as output:
    for name in ('launch.json','manifest.json','driver.log','results-review.json','qualification'):
        output.add(root/name,arcname='owner-candidate-02/'+host+'/'+name)
print(json.dumps(review))
