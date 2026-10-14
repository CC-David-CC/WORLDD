"""Launch one production world on the second host; never implement world actions."""
import argparse
import os
from pathlib import Path
import shutil
import socket
import sys

from harness.wire import frame
import world_topology_pb2 as wire

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
parser.add_argument('--world')
parser.add_argument('--address')
parser.add_argument('--cleanup', action='store_true')
args = parser.parse_args()
host = socket.gethostname().split('.')[0]
if host not in ('sb16', 'sb16-2'):
    raise SystemExit('Unauthorized world host')
root = args.directory.resolve(strict=True)
if root.parent != Path('/tmp') or not root.name.startswith('spatial-m05-topology-'):
    raise SystemExit('Invalid fixture directory')
if root.is_symlink() or root.stat().st_uid != os.getuid():
    raise SystemExit('Fixture directory ownership mismatch')
if args.cleanup:
    shutil.rmtree(root)
    raise SystemExit(0)
if not args.world or not Path(args.world).is_absolute() or not args.address:
    raise SystemExit('World binary and listening address are required')
listener = socket.socket()
listener.bind((args.address, 0))
listener.listen(16)
listener.set_inheritable(True)
receipt = wire.WorldEndpoint(protocol_version=1, host=host, address=args.address,
                             port=listener.getsockname()[1], process_id=os.getpid())
sys.stdout.buffer.write(frame(receipt))
sys.stdout.buffer.flush()
os.execv(args.world, [args.world, '--state-dir', str(root / 'state'),
    '--identity-config', str(root / 'identity.textproto'), '--config', str(root / 'runtime.textproto'),
    '--actor-listen-fd', str(listener.fileno()), '--actor-tls-config', str(root / 'tls.textproto')])
