"""Provision explicit test policy inside the delegated service owned by this run."""
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from copy_fixture_profiles import (consume_profile, manager_memory_mib, broker_memory_mib,
                                    build_adapter_resources)
from google.protobuf import text_format
import broker_pb2 as broker
import process_scope_pb2 as process


def provisioned_storage_resources(volume, resource_profile, broker_module):
    """Load the exact provisioned Btrfs budgets for an explicit volume opt-in."""
    volume = Path(volume)
    if not volume.is_absolute() or volume.resolve(strict=True) != volume:
        raise RuntimeError('Storage volume path must be an existing canonical absolute path')
    runtime_path = volume.parent / 'runtime.pb'
    metadata = runtime_path.lstat()
    if (metadata.st_uid != 0 or metadata.st_mode & 0o022 or
            not stat.S_ISREG(metadata.st_mode)):
        raise RuntimeError('Storage runtime record must be a root-owned protected regular file')
    runtime = broker_module.StorageVolumeRuntime.FromString(runtime_path.read_bytes())
    if runtime.protocol_version != 1 or Path(runtime.volume_root) != volume or not runtime.require_confinement:
        raise RuntimeError('Storage runtime record does not match the selected provisioned volume')
    if (not runtime.HasField('resources') or not runtime.HasField('storage_owner') or
            not runtime.storage_owner.HasField('budget')):
        raise RuntimeError('Storage runtime record has no provisioned resource budgets')
    resources = runtime.resources
    if (not resources.HasField('broker_budget') or not resources.HasField('manager_budget') or
            not resources.HasField('default_policy')):
        raise RuntimeError('Storage runtime record has incomplete resource budgets')
    policy = resources.default_policy
    if not policy.HasField('total') or not policy.HasField('actor_default') or any(
            not profile.HasField('maximum') for profile in policy.profiles):
        raise RuntimeError('Storage runtime record has incomplete resource budgets')
    minimum_broker_memory = broker_memory_mib(resource_profile) * 1024 * 1024
    if resources.broker_budget.memory_bytes < minimum_broker_memory:
        raise RuntimeError('Storage runtime broker budget is below the selected resource profile minimum')
    expected_memory = manager_memory_mib(resource_profile) * 1024 * 1024
    if resources.manager_budget.memory_bytes != expected_memory:
        raise RuntimeError('Storage runtime manager budget does not match the selected resource profile')
    devices = {(item.major, item.minor) for item in runtime.storage_owner.budget.io}
    budgets = (resources.broker_budget, resources.manager_budget, policy.total, policy.actor_default,
        *(profile.maximum for profile in policy.profiles))
    if not devices or any(not budget.io or {(item.major, item.minor) for item in budget.io} != devices
                          for budget in budgets):
        raise RuntimeError('Storage runtime resource budgets do not bind the provisioned device set')
    # The storage probe supports one accounting device. Provisioning sorts loop and
    # backing devices; the first identity is the actual mounted loop device.
    first = resources.manager_budget.io[0]
    return resources, f'{first.major}:{first.minor}'


assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
resource_profile = 'Debug'
if sys.argv[1:2] and sys.argv[1].startswith('--copy-profile='):
    _, resource_profile = consume_profile([sys.argv.pop(1)], '--copy-profile=')
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
result = dict(resource_profile=resource_profile, manager_memory_bytes=manager_memory_mib(resource_profile)*2**20, scope=str(scope), device=pin.st_dev, inode=pin.st_ino, removed=[], unexpected_population=False)
code = 1
try:
    with tempfile.TemporaryDirectory(prefix='spatial-adapter-resources-') as temporary:
        directory = Path(temporary)
        device = (Path('/sys/dev/block') / f'{os.major(directory.stat().st_dev)}:{os.minor(directory.stat().st_dev)}').resolve(strict=True)
        if (device/'partition').exists(): device = device.parent
        major, minor = map(int, (device/'dev').read_text().split(':'))
        mib = 1024 * 1024
        storage_volume = os.environ.get('SPATIAL_TEST_STORAGE_VOLUME')
        if storage_volume:
            resources, io_device = provisioned_storage_resources(storage_volume, resource_profile, broker)
        else:
            resources = build_adapter_resources(resource_profile, major, minor, broker, process)
            io_device = f'{major}:{minor}'
        config = broker.BrokerProcessConfig(protocol_version=1, delegated_cgroup=str(scope), resources=resources)
        path = directory/'broker.textproto'; path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
        resource_path = directory/'resources.textproto'
        resource_path.write_text(text_format.MessageToString(config.resources)); resource_path.chmod(0o600)
        result['configuration'] = text_format.MessageToString(config)
        env = dict(os.environ, SPATIAL_TEST_BROKER_CONFIG=str(path), SPATIAL_TEST_CGROUP_ROOT=str(scope),
            SPATIAL_TEST_RESOURCE_CONFIG=str(resource_path), SPATIAL_TEST_IO_DEVICE=io_device)
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
