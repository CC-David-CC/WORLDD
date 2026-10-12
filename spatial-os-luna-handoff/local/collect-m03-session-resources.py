"""Collect terminal evidence only. Do not archive runtime databases or build products."""
import hashlib
import json
from pathlib import Path
import socket
import sys
import tarfile

host = socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
candidate = sys.argv[1]
assert candidate.startswith('m03-session-resources-') and candidate[-2:].isdigit()
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base/candidate
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
review = json.loads((root/'review.json').read_bytes())
assert 'ended' in review and review['status'] in ('failed','passed_review_pending')
assert all(sha(root/name)==value for name,value in review['attachments'].items())
output = base/(candidate+'-'+host+'-evidence.tar.gz')
assert not output.exists()
with tarfile.open(output,'w:gz') as archive:
    for name in ['review.json', *sorted(review['attachments'])]:
        path = root/name
        assert path.is_file() and path.suffix in ('.json','.log','.py','.textproto')
        archive.add(path,arcname=host+'/'+name)
print(json.dumps(dict(host=host,candidate=candidate,path=str(output),sha256=sha(output),files=1+len(review['attachments']))))
