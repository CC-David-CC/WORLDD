"""Verify the exact comment-only integration delta on a world host."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
name = 'proto/spatial/supervisor/v1/launch.proto'
before_tree = r / 'local/m02-supervisor-world-handoff-02/source'
after_tree = r / 'local/m02-authority-fencing-01/source'
before = (before_tree / name).read_bytes()
after = (after_tree / name).read_bytes()
assert hashlib.sha256(before).hexdigest() == 'd06b323a4db82451417b51a0344cebf3b5ba1455049c9e6f274f1a916aa175d1'
assert hashlib.sha256(after).hexdigest() == '4de366bccc234c4f4f3d1371f01d34bfa3dbada5b7926c2e7d70a2348decaa6b'
def uncomment(data):
    return b'\n'.join(line.split(b'//', 1)[0].rstrip() for line in data.splitlines())
assert uncomment(before) == uncomment(after)
out = r / 'local/world-comment-equivalence-03'
out.mkdir(exist_ok=True)
commands = []
for label, tree in (('before', before_tree), ('after', after_tree)):
    imports = sorted({p.parent for p in (tree / 'proto').rglob('*.proto')})
    command = ['protoc'] + [arg for folder in imports for arg in ('-I', str(folder))]
    command += ['-I', '/usr/include', '--descriptor_set_out=' + str(out / (label + '.pb')),
                str(tree / name)]
    subprocess.run(command, check=True)
    commands.append(command)
descriptor = (out / 'before.pb').read_bytes()
assert descriptor and descriptor == (out / 'after.pb').read_bytes()
record = dict(host=host, file=name, before_sha256=hashlib.sha256(before).hexdigest(),
              after_sha256=hashlib.sha256(after).hexdigest(), comments_only=True,
              descriptor_equal=True, descriptor_sha256=hashlib.sha256(descriptor).hexdigest(),
              commands=commands, frozen_candidate_changed=False)
(out / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
