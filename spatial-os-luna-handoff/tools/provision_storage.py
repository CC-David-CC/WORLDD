#!/usr/bin/env python3
"""Provision a persistent, dedicated storage volume on an authorized world host."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import uuid

ROOT = Path('/var/lib/spatial/volumes')
MIB = 2**20


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_definition(value):
    require(value.protocol_version == 1 and re.fullmatch(r'[a-z][a-z0-9-]{0,47}', value.name),
            'Expected a versioned volume definition and a simple volume name')
    copy = type(value)(); copy.CopyFrom(value); copy.DiscardUnknownFields()
    require(copy.SerializeToString(deterministic=True) == value.SerializeToString(deterministic=True),
            'Unknown definition fields are forbidden')
    require(value.HasField('owner_policy') and value.HasField('resources') and value.HasField('owner_budget'),
            'Storage, resource, and owner budgets are mandatory')
    policy = value.owner_policy
    require(policy.protocol_version == 1 and not policy.volume_root and not policy.filesystem_id and
            not policy.HasField('broker_pid'), 'Provisioning supplies the fixed filesystem and live broker identity')
    require(policy.HasField('broker_uid') and 0 < policy.broker_uid < 2**31, 'Expected an unprivileged broker UID')
    for name in ('volume_bytes', 'control_plane_bytes', 'filesystem_reserve_bytes'):
        require(policy.HasField(name), 'Storage capacity reservations are mandatory')
    require(256*MIB <= policy.volume_bytes <= 512*2**30, 'Volume capacity must be between 256 MiB and 512 GiB')
    capacity = policy.volume_bytes - policy.control_plane_bytes - policy.filesystem_reserve_bytes
    require(capacity >= 4*MIB, 'Storage reservations exhaust the volume')
    require(policy.HasField('default_policy') and not policy.default_policy.workspace and len(policy.workspaces) <= 64,
            'Expected a bounded default storage policy')
    names = set()
    for index, selected in enumerate([policy.default_policy, *policy.workspaces]):
        require(index == 0 or bool(selected.workspace), 'Workspace policy needs an identity')
        require(selected.workspace not in names, 'Duplicate workspace storage policy'); names.add(selected.workspace)
        require(selected.HasField('total_bytes') and selected.HasField('actor_state_default_bytes') and
                selected.total_bytes <= capacity and 0 < len(selected.profiles) <= 32 and len(selected.actors) <= 128,
                'Storage policy needs bounded capacities and application profiles')
        profiles = set()
        for profile in selected.profiles:
            require(profile.name and profile.name not in profiles and profile.HasField('state_bytes'),
                    'Duplicate or incomplete storage application profile'); profiles.add(profile.name)
    resources = value.resources
    require(resources.protocol_version == 2 and not resources.delegated_cgroup and
            resources.HasField('broker_budget') and resources.HasField('manager_budget') and
            resources.HasField('default_policy'), 'Expected an unbound aggregate resource policy')
    require(not resources.default_policy.workspace and len(resources.workspaces) <= 64,
            'Resource policies require one default and bounded workspace overrides')
    names = set()
    for index, selected in enumerate([resources.default_policy, *resources.workspaces]):
        require((index == 0 or bool(selected.workspace)) and selected.workspace not in names and
                selected.HasField('total') and selected.HasField('actor_default') and
                0 < len(selected.profiles) <= 32 and len(selected.actors) <= 128,
                'Resource policies need explicit budgets and application profiles')
        names.add(selected.workspace)
        require(len({p.name for p in selected.profiles}) == len(selected.profiles) and
                all(p.name and p.HasField('maximum') for p in selected.profiles), 'Invalid resource application profiles')
    require(0 < value.read_bytes_per_second <= 2**40 and 0 < value.write_bytes_per_second <= 2**40,
            'Expected finite read and write ceilings')

    def walk(message):
        if message.DESCRIPTOR.full_name == 'spatial.supervisor.v1.ProcessScopeBudget':
            require(all(message.HasField(field) for field in ('memory_bytes', 'swap_bytes', 'processes', 'cpu_microseconds')),
                    'Every process budget needs explicit memory, swap, process, and CPU ceilings')
            require(message.memory_bytes > 0 and message.swap_bytes == 0 and message.processes > 0 and
                    0 < message.cpu_microseconds <= 6400000 and not message.io,
                    'Provisioning requires positive budgets, zero swap, and no fixed I/O devices')
        if message.DESCRIPTOR.full_name in ('spatial.workspace.v1.ActorResourceLimit', 'spatial.workspace.v1.ActorStorageLimit'):
            require(message.WhichOneof('subject') == 'principal', 'Operator actor overrides require cryptographic identity')
        for field, item in message.ListFields():
            if field.type == field.TYPE_UINT64 and field.name.endswith('_bytes'):
                require(field.name in ('memory_bytes', 'swap_bytes') or
                        4*MIB <= item <= 2**50 and item % 4096 == 0, 'Storage ceilings need positive block multiples')
            if field.message_type:
                for child in item if field.label == field.LABEL_REPEATED else [item]: walk(child)
    walk(value)
    require(value.owner_budget.cpu_microseconds % 10 == 0, 'Owner CPU quota needs systemd precision')


def secure_directory(path):
    require(path.is_absolute(), 'Expected an absolute operator directory')
    if not path.exists():
        secure_directory(path.parent); path.mkdir(mode=0o755); path.chmod(0o755)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022 and
            path.resolve(strict=True) == path, 'Operator directory must have immutable root-owned parents')
    if path != Path('/'): secure_directory(path.parent)


def protected_file(path):
    secure_directory(path.parent)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077,
            'Expected a private, singly linked root-owned file')
    return info


def atomic_write(path, data, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if Path(temporary).exists(): Path(temporary).unlink()


def assign_volume_root_owner(volume, broker_uid, verify_mount):
    """Assign only the verified mounted root to its recorded broker UID."""
    require(0 < broker_uid < 2**31, 'Expected an unprivileged broker UID')
    fd = os.open(volume, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISDIR(before.st_mode), 'Mounted volume root is not a directory')
        path_before = volume.lstat()
        require(stat.S_ISDIR(path_before.st_mode) and
                (path_before.st_dev, path_before.st_ino) == (before.st_dev, before.st_ino),
                'Mounted volume root changed while opening it')
        require(before.st_uid in (0, broker_uid), 'Mounted volume root has an unexpected owner')
        verify_mount()
        if before.st_uid != broker_uid:
            os.fchown(fd, broker_uid, -1)
        os.fchmod(fd, 0o700)
        after = os.fstat(fd)
        path_after = volume.lstat()
        require(stat.S_ISDIR(after.st_mode) and after.st_uid == broker_uid and
                after.st_gid == before.st_gid and stat.S_IMODE(after.st_mode) == 0o700 and
                (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino) and
                stat.S_ISDIR(path_after.st_mode) and
                (path_after.st_dev, path_after.st_ino) == (after.st_dev, after.st_ino),
                'Mounted volume root ownership or identity did not persist')
        verify_mount()
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('create', 'mount', 'unmount', 'inspect'))
    parser.add_argument('--bindings-dir', required=True, type=Path)
    parser.add_argument('--definition', type=Path)
    parser.add_argument('--name')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(socket.gethostname().split('.')[0] in ('sb16', 'sb16-2'), 'Unauthorized host')
    sys.path.insert(0, str(args.bindings_dir.resolve(strict=True)))
    from google.protobuf import text_format
    import broker_pb2 as broker
    require((args.operation == 'create') == bool(args.definition) and
            (args.operation == 'create') == (args.name is None), 'Create uses a definition; other operations use a name')
    definition = broker.StorageVolumeDefinition()
    if args.definition:
        data = args.definition.read_bytes(); require(len(data) <= 65536, 'Definition exceeds size limit')
        text_format.Parse(data.decode('utf-8'), definition); validate_definition(definition)
        name = definition.name
    else:
        name = args.name
    require(re.fullmatch(r'[a-z][a-z0-9-]{0,47}', name or ''), 'Invalid volume name')
    entry = ROOT/name
    image, volume, record_path = entry/'volume.img', entry/'mount', entry/'record.pb'
    report = dict(host=socket.gethostname(), operation=args.operation, name=name, image=str(image),
                  volume=str(volume), applied=False, commands=[])

    def run(command):
        result = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=60,
                                env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin', 'LANG':'C'})
        report['commands'].append(dict(argv=list(map(str, command)), returncode=result.returncode,
                                       stdout=result.stdout, stderr=result.stderr))
        require(result.returncode == 0, 'Command failed: '+str(command)+'\n'+result.stderr)
        return result.stdout.strip()

    if args.operation != 'inspect' and not args.apply:
        report['status'] = 'planned'; print(json.dumps(report)); return
    require(os.getuid() == 0, 'Volume administration requires operator privilege')
    if args.operation != 'create': require(entry.is_dir(), 'Volume has not been provisioned')
    os.umask(0o077); secure_directory(ROOT)
    if args.operation == 'create':
        require(not entry.exists() and not entry.is_symlink(), 'Existing volume must never be overwritten')
        entry.mkdir(mode=0o755); entry.chmod(0o755)
    secure_directory(entry)
    lock = os.open(entry/'.provision.lock', os.O_RDWR | (os.O_CREAT if args.operation == 'create' else 0) |
                   os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        protected_file(entry/'.provision.lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.operation == 'create':
            with image.open('xb') as stream:
                os.posix_fallocate(stream.fileno(), 0, definition.owner_policy.volume_bytes)
                stream.flush(); os.fsync(stream.fileno())
            run(['mkfs.btrfs', '-q', str(image)])
            filesystem = uuid.UUID(run(['blkid', '-p', '-s', 'UUID', '-o', 'value', str(image)])).bytes
            info = protected_file(image)
            record = broker.StorageVolumeRecord(protocol_version=1, definition=definition, filesystem_id=filesystem,
                                                image_device=info.st_dev, image_inode=info.st_ino)
            atomic_write(record_path, record.SerializeToString(deterministic=True)); volume.mkdir(mode=0o700)
        protected_file(record_path)
        require(record_path.stat().st_size <= 65536, 'Volume record exceeds size limit')
        record = broker.StorageVolumeRecord.FromString(record_path.read_bytes())
        copy = broker.StorageVolumeRecord(); copy.CopyFrom(record); copy.DiscardUnknownFields()
        require(copy == record and record.protocol_version == 1 and record.definition.name == name and
                len(record.filesystem_id) == 16, 'Invalid persistent volume record')
        validate_definition(record.definition)
        info = protected_file(image)
        require((info.st_dev, info.st_ino, info.st_size) ==
                (record.image_device, record.image_inode, record.definition.owner_policy.volume_bytes),
                'Persistent image identity or capacity changed')
        if args.operation != 'unmount':
            require(uuid.UUID(run(['blkid', '-p', '-s', 'UUID', '-o', 'value', str(image)])).bytes == record.filesystem_id,
                    'Filesystem identity differs from the persistent record')
        require(volume.is_dir() and not volume.is_symlink() and volume.resolve(strict=True) == volume,
                'Volume mountpoint must be canonical')
        mounted = subprocess.run(['findmnt', '--mountpoint', str(volume), '-n', '-o', 'SOURCE'],
                                 capture_output=True, text=True, timeout=10)
        require(mounted.returncode in (0, 1), 'Cannot inspect exact mountpoint')
        loops = json.loads(run(['losetup', '--json', '--list', '--output', 'NAME,BACK-FILE']))['loopdevices']
        matches = [item['name'] for item in loops if item['back-file'] == str(image)]
        require(len(matches) <= 1, 'Image has ambiguous loop associations')
        loop = matches[0] if matches else None
        if mounted.returncode == 0:
            require(loop and mounted.stdout.strip() == loop, 'Mountpoint belongs to another storage device')
        report.update(filesystem_id=uuid.UUID(bytes=record.filesystem_id).hex, mounted=mounted.returncode == 0, loop=loop)
        if args.operation == 'inspect':
            report['status'] = 'verified'
        elif args.operation == 'unmount':
            if mounted.returncode == 0: run(['umount', str(volume)])
            if loop: run(['losetup', '-d', loop])
            require(not run(['losetup', '-j', str(image)]), 'Loop cleanup remains unconfirmed')
            volume.chmod(0o700); os.chown(volume, 0, 0)
            if (entry/'runtime.pb').exists(): (entry/'runtime.pb').unlink()
            report.update(status='unmounted', mounted=False, loop=None, applied=True)
        else:
            # Never format an existing image. Verify the filesystem before exposing it.
            if not loop: loop = run(['losetup', '--find', '--show', str(image)])
            require(re.fullmatch(r'/dev/loop[0-9]+', loop), 'Unexpected loop device')
            if mounted.returncode != 0: run(['mount', '-t', 'btrfs', '-o', 'rw,nodev,nosuid,subvolid=5', loop, str(volume)])
            description = json.loads(run(['findmnt', '--json', '--mountpoint', str(volume), '-o', 'SOURCE,FSTYPE,OPTIONS']))['filesystems']
            require(len(description) == 1 and description[0]['source'] == loop and description[0]['fstype'] == 'btrfs' and
                    {'rw','nodev','nosuid','subvolid=5'} <= set(description[0]['options'].split(',')), 'Unexpected volume mount policy')

            def verify_volume_mount():
                current = json.loads(run(['findmnt', '--json', '--mountpoint', str(volume),
                                          '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS']))['filesystems']
                require(len(current) == 1 and current[0]['target'] == str(volume) and
                        current[0]['source'] == loop and current[0]['fstype'] == 'btrfs' and
                        {'rw','nodev','nosuid','subvolid=5'} <= set(current[0]['options'].split(',')),
                        'Volume mount identity changed during root ownership update')

            assign_volume_root_owner(volume, record.definition.owner_policy.broker_uid, verify_volume_mount)
            device = Path('/sys/dev/block')/f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
            backing = device.resolve(strict=True)
            if (backing/'partition').exists(): backing = backing.parent
            require(not list((backing/'slaves').iterdir()), 'Stacked backing devices need explicit provisioning support')
            queues = {tuple(map(int, (backing/'dev').read_text().split(':')))}
            number = Path(loop).stat().st_rdev; queues.add((os.major(number), os.minor(number)))
            runtime = broker.StorageVolumeRuntime(protocol_version=1, volume_root=str(volume), require_confinement=True)
            runtime.resources.CopyFrom(record.definition.resources)
            runtime.storage_owner.protocol_version = 1; runtime.storage_owner.volume_root = str(volume)
            runtime.storage_owner.owner_configuration = str(entry/'owner.pb')
            runtime.storage_owner.budget.CopyFrom(record.definition.owner_budget)

            def bind_devices(message):
                if message.DESCRIPTOR.full_name == 'spatial.supervisor.v1.ProcessScopeBudget':
                    require(not message.io, 'Runtime devices must come from the actual image and backing queue')
                    for major, minor in sorted(queues):
                        message.io.add(major=major, minor=minor,
                                       read_bytes_per_second=record.definition.read_bytes_per_second,
                                       write_bytes_per_second=record.definition.write_bytes_per_second)
                for field, item in message.ListFields():
                    if field.message_type:
                        for child in item if field.label == field.LABEL_REPEATED else [item]: bind_devices(child)
            bind_devices(runtime)
            owner = type(record.definition.owner_policy)(); owner.CopyFrom(record.definition.owner_policy)
            owner.volume_root = str(volume); owner.filesystem_id = record.filesystem_id
            atomic_write(entry/'owner.pb', owner.SerializeToString(deterministic=True))
            atomic_write(entry/'runtime.pb', runtime.SerializeToString(deterministic=True), mode=0o644)
            report.update(status='mounted', mounted=True, loop=loop, applied=True, runtime=str(entry/'runtime.pb'),
                          devices=sorted(queues))
        print(json.dumps(report, indent=2))
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        print(json.dumps(report, indent=2), file=sys.stderr)
        raise
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
