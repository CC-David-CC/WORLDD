"""Provision explicit test policy inside the delegated service owned by this run."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from google.protobuf import text_format
import broker_pb2 as broker
import process_scope_pb2 as process

assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
assert sys.argv[1:2] == ['--'] and len(sys.argv) > 2
relative = Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1]
manager = Path('/sys/fs/cgroup') / relative.lstrip('/')
assert manager.name == 'manager' and manager.parent.name.startswith('spatial-m03-resources-')
owner = manager.parent
assert owner.stat().st_uid == os.getuid() and not (owner/'cgroup.procs').read_text().strip()
(owner/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
scope = owner / ('adapter-' + uuid.uuid4().hex); scope.mkdir()
pin = scope.stat()
(scope/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
result = dict(scope=str(scope), device=pin.st_dev, inode=pin.st_ino, removed=[], unexpected_population=False)
code = 1
try:
    with tempfile.TemporaryDirectory(prefix='spatial-adapter-resources-') as temporary:
        directory = Path(temporary)
        device = (Path('/sys/dev/block') / f'{os.major(directory.stat().st_dev)}:{os.minor(directory.stat().st_dev)}').resolve(strict=True)
        if (device/'partition').exists(): device = device.parent
        major, minor = map(int, (device/'dev').read_text().split(':'))
        mib = 1024 * 1024
        def budget(memory, processes, cpu):
            return process.ProcessScopeBudget(memory_bytes=memory*mib, swap_bytes=0, processes=processes,
                cpu_microseconds=cpu, io=[dict(major=major, minor=minor,
                    read_bytes_per_second=64*mib, write_bytes_per_second=16*mib)])
        config = broker.BrokerProcessConfig(protocol_version=1, delegated_cgroup=str(scope),
            resources=broker.BrokerResourceConfig(protocol_version=2,
                broker_budget=budget(1024, 128, 400000),
                manager_budget=budget(256, 32, 200000),
                default_policy=broker.WorkspaceResourcePolicy(total=budget(512, 64, 200000),
                    actor_default=budget(256, 32, 100000), profiles=[
                        broker.ApplicationResourceProfile(name=name, maximum=budget(128, 16, 100000))
                        for name in ('development', 'read-only')])))
        path = directory/'broker.textproto'; path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
        resource_path = directory/'resources.textproto'
        resource_path.write_text(text_format.MessageToString(config.resources)); resource_path.chmod(0o600)
        result['configuration'] = text_format.MessageToString(config)
        env = dict(os.environ, SPATIAL_TEST_BROKER_CONFIG=str(path), SPATIAL_TEST_CGROUP_ROOT=str(scope),
            SPATIAL_TEST_RESOURCE_CONFIG=str(resource_path), SPATIAL_TEST_IO_DEVICE=f'{major}:{minor}')
        result['command'] = sys.argv[2:]
        child = subprocess.run(sys.argv[2:], env=env)
        code = result['exit_code'] = child.returncode
finally:
    assert scope.stat().st_ino == pin.st_ino and scope.stat().st_dev == pin.st_dev
    # Abrupt adapter deaths can leave empty groups. No live application may survive.
    deadline = time.monotonic() + 3
    while 'populated 1' in (scope/'cgroup.events').read_text() and time.monotonic() < deadline:
        time.sleep(.01)
    if 'populated 1' in (scope/'cgroup.events').read_text():
        result['unexpected_population'] = True; code = 1
        (scope/'cgroup.kill').write_text('1\n')
        deadline = time.monotonic() + 3
        while 'populated 1' in (scope/'cgroup.events').read_text():
            assert time.monotonic() < deadline, 'Owned adapter scope did not stop'
            time.sleep(.01)
    for path in sorted((p for p in scope.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        assert path.stat().st_uid == os.getuid()
        result['removed'].append(str(path.relative_to(scope))); path.rmdir()
    scope.rmdir(); result['scope_removed'] = not scope.exists()
    evidence = os.environ.get('SPATIAL_SCOPE_EVIDENCE')
    if evidence:
        target = Path(evidence); target.mkdir(parents=True, exist_ok=True)
        (target/('adapter-provisioning-' + scope.name + '.json')).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)
raise SystemExit(code)
