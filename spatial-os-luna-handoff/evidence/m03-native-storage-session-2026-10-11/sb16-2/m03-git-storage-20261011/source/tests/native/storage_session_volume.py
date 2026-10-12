"""Root fixture setup for one retained native storage session. No world implementation."""
import json
import os
from pathlib import Path
import pwd
import re
import socket
import subprocess
import sys
import uuid
from google.protobuf import text_format

assert os.getuid() == 0 and socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
operation, build, root = sys.argv[1:4]
build, root = Path(build).resolve(strict=True), Path(root).resolve(strict=True)
assert root.name.startswith('test_native_storage_')
assert root.is_relative_to(Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local'))
assert not root.is_symlink()
sys.path.insert(0, str(build/'python'))
import broker_pb2 as broker
import storage_pb2 as storage
import process_scope_pb2 as process

operator = pwd.getpwnam('dflanag3'); mib = 2**20
image, volume, receipt = root/'volume.img', root/'volume', root/'volume-setup.json'
os.umask(0o077)


def run(argv):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=30)
    record['commands'].append(dict(argv=list(map(str, argv)), exit_code=result.returncode,
                                  stdout=result.stdout, stderr=result.stderr))
    save()
    assert result.returncode == 0, record['commands'][-1]
    return result.stdout.strip()


def save():
    receipt.write_text(json.dumps(record, indent=2)+'\n'); receipt.chmod(0o644)


if operation == 'prepare':
    assert len(sys.argv) == 5 and not receipt.exists() and not image.exists() and not volume.exists()
    record = dict(status='preparing', mounted=False, loop=None, policy_directory=None, commands=[])
    save()
    original = Path(sys.argv[4]).resolve(strict=True)
    assert original.stat().st_uid == operator.pw_uid and original.stat().st_size <= 65536
    config = broker.BrokerProcessConfig()
    text_format.Parse(original.read_text(), config)
    assert config.protocol_version == 1 and config.HasField('resources') and not config.HasField('storage_owner')
    assert not config.resources.delegated_cgroup
    try:
        volume.mkdir()
        with image.open('xb') as file: file.truncate(512*mib)
        record['loop'] = run(['losetup', '--find', '--show', image]); save()
        run(['mkfs.btrfs', '-q', '-f', record['loop']])
        run(['mount', '-t', 'btrfs', '-o', 'rw,nodev,nosuid', record['loop'], volume])
        record['mounted'] = True; save()
        policy_dir = Path('/run')/('spatial-m03-native-storage-'+uuid.uuid4().hex)
        policy_dir.mkdir(mode=0o755); policy_dir.chmod(0o755)
        record['policy_directory'] = str(policy_dir); save()
        owner = storage.StorageOwnerConfig(protocol_version=1, volume_root=str(volume),
            filesystem_id=uuid.UUID(run(['blkid', '-s', 'UUID', '-o', 'value', record['loop']])).bytes,
            broker_uid=operator.pw_uid, volume_bytes=512*mib, control_plane_bytes=64*mib,
            filesystem_reserve_bytes=64*mib)
        owner.default_policy.total_bytes = 256*mib; owner.default_policy.actor_state_default_bytes = 48*mib
        for name in ['development', 'read-only']: owner.default_policy.profiles.add(name=name, state_bytes=32*mib)
        policy = policy_dir/'owner.pb'; policy.write_bytes(owner.SerializeToString())
        backing = (Path('/sys/dev/block')/f'{os.major(root.stat().st_dev)}:{os.minor(root.stat().st_dev)}').resolve(strict=True)
        if (backing/'partition').exists(): backing = backing.parent
        backing_device = tuple(map(int, (backing/'dev').read_text().split(':')))
        number = Path(record['loop']).stat().st_rdev
        loop_device = (os.major(number), os.minor(number))
        assert backing_device != loop_device

        def add_loop(message):
            if message.DESCRIPTOR.full_name == process.ProcessScopeBudget.DESCRIPTOR.full_name:
                existing = [io for io in message.io if (io.major, io.minor) == backing_device]
                assert len(existing) == 1 and len(message.io) == 1
                io = message.io.add(); io.CopyFrom(existing[0]); io.major, io.minor = loop_device
            else:
                for field, value in message.ListFields():
                    if field.message_type is not None:
                        if field.label == field.LABEL_REPEATED:
                            for child in value: add_loop(child)
                        else: add_loop(value)
        add_loop(config.resources)
        launch = config.storage_owner
        launch.protocol_version = 1; launch.owner_configuration = str(policy); launch.volume_root = str(volume)
        launch.budget.CopyFrom(process.ProcessScopeBudget(memory_bytes=256*mib, swap_bytes=0,
            processes=16, cpu_microseconds=100000, io=[dict(major=a, minor=b,
                read_bytes_per_second=64*mib, write_bytes_per_second=16*mib)
                for a, b in sorted([backing_device, loop_device])]))
        path = root/'broker.textproto'; path.write_text(text_format.MessageToString(config)); os.chown(path, operator.pw_uid, operator.pw_gid)
        record.update(status='prepared', configuration=str(path), volume=str(volume), devices=[backing_device, loop_device])
        save()
    except BaseException as error:
        record.update(status='failed', failure=repr(error)); save(); raise
elif operation == 'inspect':
    assert len(sys.argv) == 6 and receipt.stat().st_uid == 0 and receipt.stat().st_size <= 65536
    record = json.loads(receipt.read_text()); unit, broker_pid = sys.argv[4:6]
    assert re.fullmatch(r'spatial-storage-owner-[a-f0-9]{32}\.service', unit)
    assert broker_pid.isdecimal() and int(broker_pid) > 1
    state = run(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState', '-p', 'ExecStart'])
    assert 'ActiveState=active' in state and str(Path(record['policy_directory'])/'owner.pb') in state
    pid = int(re.search(r'MainPID=([0-9]+)', state).group(1)); assert pid > 1
    handles = [p.read_text() for p in Path(f'/proc/{pid}/fdinfo').iterdir() if 'Pid:' in p.read_text()]
    assert any(f'Pid:\t{broker_pid}\n' in text for text in handles)
    record['owner_witness'] = dict(unit=unit, owner_pid=pid, broker_pid=int(broker_pid), broker_pidfd=handles)
    save()
elif operation == 'cleanup':
    assert len(sys.argv) == 4 and receipt.stat().st_uid == 0 and receipt.stat().st_size <= 65536
    record = json.loads(receipt.read_text())
    source = subprocess.run(['findmnt', '--mountpoint', str(volume), '-n', '-o', 'SOURCE'], capture_output=True, text=True)
    if source.returncode == 0:
        assert re.fullmatch(r'/dev/loop[0-9]+', record['loop']) and source.stdout.strip() == record['loop']
        association = run(['losetup', '--noheadings', '--output', 'BACK-FILE', record['loop']])
        assert Path(association).resolve(strict=True) == image
        run(['umount', volume])
    record['mounted'] = False; save()
    if record['loop']:
        assert re.fullmatch(r'/dev/loop[0-9]+', record['loop'])
        association = run(['losetup', '-j', image])
        if association:
            assert association.startswith(record['loop']+':')
            run(['losetup', '-d', record['loop']])
        assert not run(['losetup', '-j', image])
    directory = record['policy_directory']
    if directory:
        directory = Path(directory)
        assert directory.parent == Path('/run') and re.fullmatch(r'spatial-m03-native-storage-[a-f0-9]{32}', directory.name)
        assert directory.stat().st_uid == 0 and not directory.is_symlink()
        for item in directory.iterdir():
            assert item.name == 'owner.pb' and item.is_file() and not item.is_symlink(); item.unlink()
        directory.rmdir()
    record['loop_attached'] = False; record['cleanup_confirmed'] = True; save()
else:
    raise AssertionError('Unknown volume fixture operation')
print(json.dumps(record))
