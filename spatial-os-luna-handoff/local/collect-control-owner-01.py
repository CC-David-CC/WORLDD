"""Preserve the completed first candidate, including the failed fixture checks."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
root = r/'local/m02-control-owner-01'
launch = json.loads((root/'launch.json').read_text())
path = Path('/proc')/str(launch['pid'])/'cmdline'
assert not path.exists() or b'qualify-control-owner-01.py' not in path.read_bytes(), 'Candidate01 is still live'
summary = json.loads((root/'qualification/summary.json').read_text())
assert summary['status'] == 'failed'
manifest = json.loads((root/'manifest.json').read_text())
for group in ('files','locked_files'):
    for name, digest in manifest[group].items():
        assert hashlib.sha256((root/'source'/name).read_bytes()).hexdigest() == digest, name
text = (root/'qualification/debug/focused.log').read_text()
failures = [line for line in text.splitlines() if '***Failed' in line]
assert len(failures) == 1 and 'control-owner-channels' in failures[0]
assert "TypeError: 'PosixPath' object is not callable" in text
assert len(re.findall(r'ERROR: test_', text)) == 12
passed = len(re.findall(r'^\d+: test_.* \.\.\. ok$', text, re.M))
review = dict(host=host, source_files=297, locked_files=41, status='failed',
              new_checks_errored=12, regression_checks_passed=passed, harness_passed=51,
              sanitizer_profile='unrun because Debug failed',
              failure='Inherited fixture self.owner path shadows the new owner() helper.',
              binaries={name:hashlib.sha256((root/'debug/build'/name).read_bytes()).hexdigest()
                        for name in ('workspaced','worldd','identityd','spatialwm','spatial-issuer-client','spatial-security-auth')})
(root/'results-review.json').write_text(json.dumps(review, indent=2)+'\n')
archive = r/'local'/('control-owner-01-'+host+'.tar.gz')
assert not archive.exists()
with tarfile.open(archive, 'w:gz') as output:
    for name in ('launch.json','manifest.json','driver.log','results-review.json','qualification'):
        output.add(root/name, arcname='owner-candidate-01/'+host+'/'+name)
print(json.dumps(review))
