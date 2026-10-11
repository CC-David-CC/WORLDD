"""Real direct broker launch, bounded owner, and idle peer-death witnesses.

Run with sudo on an authorized host. Retain the dedicated image after detaching it.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import select
import socket
import struct
import subprocess
import sys
import time
import uuid
from google.protobuf import text_format

assert os.getuid() == 0 and socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
build, root = (Path(value).resolve() for value in sys.argv[1:])
assert build.is_dir() and not root.exists()
operator = pwd.getpwnam('dflanag3'); uid, gid = operator.pw_uid, operator.pw_gid
os.umask(0o077); root.mkdir(); os.chown(root, uid, gid)
sys.path.insert(0, str(build/'python'))
import broker_pb2 as broker
import core_pb2 as core
import storage_pb2 as storage
import process_scope_pb2 as process

mib = 2**20
volume, image = root/'volume', root/'volume.img'
volume.mkdir()
policy_root = Path('/run')/('spatial-storage-session-'+uuid.uuid4().hex)
policy_root.mkdir(mode=0o755); policy_root.chmod(0o755)
record = dict(status='running', phase_complete=False, host=socket.gethostname(), results={}, commands=[],
              runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
unit, loop, delegate = None, None, None
mounted = False; children = []; streams = []; owner_units = set()


def save(): (root/'review.json').write_text(json.dumps(record, indent=2)+'\n')


def run(argv):
    reply = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=30)
    record['commands'].append(dict(argv=list(map(str, argv)), exit_code=reply.returncode,
                                   stdout=reply.stdout, stderr=reply.stderr)); save()
    assert reply.returncode == 0, record['commands'][-1]
    return reply.stdout.strip()


def exact(stream, size):
    result = b''; deadline = time.monotonic()+15
    while len(result) < size:
        assert time.monotonic() < deadline, 'Direct broker witness reply deadline'
        if not select.select([stream], [], [], .05)[0]: continue
        chunk = os.read(stream.fileno(), size-len(result)); assert chunk, 'Direct broker witness EOF'
        result += chunk
    return result


def call(child, field, message):
    request = core.Request(protocol_version=1, request_id=17)
    getattr(request, field).CopyFrom(message)
    payload = request.SerializeToString(); child.stdin.write(struct.pack('!I', len(payload))+payload); child.stdin.flush()
    size = struct.unpack('!I', exact(child.stdout, 4))[0]; assert 0 < size <= mib
    reply = core.Reply.FromString(exact(child.stdout, size))
    assert reply.request_id == 17 and not reply.HasField('error'), reply
    return reply


def spawn(label, wrong_uid=False, wrong_volume=False, owner_devices=None):
    owner_policy.broker_uid = uid+1 if wrong_uid else uid
    policy = policy_root/(label+'.pb'); policy.write_bytes(owner_policy.SerializeToString())
    config = broker.BrokerProcessConfig(protocol_version=1, delegated_cgroup=str(delegate/'broker-root'),
        resources=resources, storage_owner=storage.StorageOwnerLaunchConfig(protocol_version=1,
            owner_configuration=str(policy), volume_root=str(root if wrong_volume else volume), budget=budget(256, 16, 100000)))
    if owner_devices is not None:
        config.storage_owner.budget.ClearField('io')
        for a, b in sorted(owner_devices):
            config.storage_owner.budget.io.add(major=a, minor=b, read_bytes_per_second=64*mib,
                                              write_bytes_per_second=16*mib)
    path = root/(label+'.textproto'); path.write_text(text_format.MessageToString(config)); os.chown(path, uid, gid)
    log = open(root/(label+'.log'), 'wb', buffering=0); streams.append(log)
    def drop():
        (delegate/'manager/cgroup.procs').write_text(str(os.getpid())+'\n')
        os.setgroups([]); os.setgid(gid); os.setuid(uid)
    child = subprocess.Popen([str(build/'spatial-storage-broker-launch-probe'), str(volume), str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, preexec_fn=drop,
        env=dict(os.environ, ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1'))
    children.append(child)
    return child, root/(label+'.log')


def owner_unit(log):
    matches = re.findall(r'Storage owner unit: (spatial-storage-owner-[a-f0-9]{32}\.service)', log.read_text())
    assert len(matches) == 1, log.read_text()
    owner_units.add(matches[0]); return matches[0]


def finish(child, expected_exit=0):
    child.stdin.close(); assert child.wait(timeout=15) == expected_exit
    child.stdout.close(); children.remove(child)
    assert not any(path.is_dir() for path in (delegate/'broker-root').iterdir()), 'Direct broker left a resource group'


try:
    with image.open('xb') as output: output.truncate(512*mib)
    loop = run(['losetup', '--find', '--show', image])
    run(['mkfs.btrfs', '-q', '-f', loop]); run(['mount', '-t', 'btrfs', '-o', 'rw,nodev,nosuid', loop, volume]); mounted = True
    owner_policy = storage.StorageOwnerConfig(protocol_version=1, volume_root=str(volume),
        filesystem_id=uuid.UUID(run(['blkid','-s','UUID','-o','value',loop])).bytes, broker_uid=uid,
        volume_bytes=512*mib, control_plane_bytes=64*mib, filesystem_reserve_bytes=64*mib)
    owner_policy.default_policy.total_bytes = 256*mib; owner_policy.default_policy.actor_state_default_bytes = 48*mib
    for name in ['development','read-only']: owner_policy.default_policy.profiles.add(name=name, state_bytes=32*mib)
    unit = 'spatial-m03-storage-launch-witness-'+uuid.uuid4().hex+'.service'
    run(['systemd-run','--unit='+unit,'--collect','--service-type=exec','--uid=dflanag3','-p',
        'Delegate=cpu memory pids io','-p','DelegateSubgroup=manager','-p','MemoryMax=1536M','-p','MemorySwapMax=0',
        '-p','TasksMax=256','-p','CPUQuota=400%','-p','RuntimeMaxSec=180','--','/usr/bin/sleep','180'])
    delegate = Path('/sys/fs/cgroup')/run(['systemctl','show',unit,'-p','ControlGroup','--value']).lstrip('/')
    assert delegate.name == unit and delegate.stat().st_uid == uid
    (delegate/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
    (delegate/'broker-root').mkdir(); os.chown(delegate/'broker-root',uid,gid)
    (delegate/'broker-root/cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
    backing = (Path('/sys/dev/block')/f'{os.major(root.stat().st_dev)}:{os.minor(root.stat().st_dev)}').resolve()
    if (backing/'partition').exists(): backing = backing.parent
    block = Path(loop).stat()
    devices = {tuple(map(int,(backing/'dev').read_text().split(':'))), (os.major(block.st_rdev),os.minor(block.st_rdev))}
    def budget(memory, processes, cpu):
        return process.ProcessScopeBudget(memory_bytes=memory*mib, swap_bytes=0, processes=processes,
            cpu_microseconds=cpu, io=[dict(major=a,minor=b,read_bytes_per_second=64*mib,write_bytes_per_second=16*mib)
                                     for a,b in sorted(devices)])
    resources = broker.BrokerResourceConfig(protocol_version=2, broker_budget=budget(1024,128,400000),
        manager_budget=budget(256,32,200000),default_policy=broker.WorkspaceResourcePolicy(total=budget(512,64,200000),
            actor_default=budget(256,32,100000), profiles=[broker.ApplicationResourceProfile(name=name,
                maximum=budget(128,16,100000)) for name in ['development','read-only']]))
    # Reject incomplete owner budgets before any catalog or quota mutation.
    loop_device = (os.major(block.st_rdev), os.minor(block.st_rdev))
    backing_device = tuple(map(int, (backing/'dev').read_text().split(':')))
    assert loop_device != backing_device
    for label, selected in [('owner-wrong-device', {backing_device}),
                             ('owner-missing-backing', {loop_device})]:
        assert not (volume/'.storage-owner').exists()
        child, log = spawn(label, owner_devices=selected)
        try:
            reply = call(child, 'hello', core.HelloRequest())
        except AssertionError:
            pass
        else:
            raise AssertionError('Storage owner accepted an uncovered volume: '+label+' '+str(reply))
        assert child.wait(timeout=15) == 2, log.read_text()
        child.stdin.close(); child.stdout.close(); children.remove(child)
        assert 'Workspace storage device has no I/O budget' in log.read_text(), log.read_text()
        owner_unit(log)
        assert not (volume/'.storage-owner').exists(), 'Rejected owner changed its catalog'
        assert not any(path.is_dir() for path in (delegate/'broker-root').iterdir())
        record['results'][label+'_rejected_before_catalog'] = dict(passed=True)
    child, log = spawn('normal')
    assert call(child,'hello',core.HelloRequest()).hello.implementation == 'production'
    identities = call(child,'runtime_identities',core.RuntimeIdentitiesRequest()).identities
    actual_broker = identities.workspaced_pid; assert actual_broker != child.pid
    actual_owner = owner_unit(log)
    owner_pid = int(run(['systemctl','show',actual_owner,'-p','MainPID','--value']))
    handles = [p.read_text() for p in Path(f'/proc/{owner_pid}/fdinfo').iterdir() if 'Pid:' in p.read_text()]
    assert any(f'Pid:\t{actual_broker}\n' in item for item in handles)
    finish(child)
    record['results']['normal_retained_direct_launch_and_cleanup'] = dict(passed=True,broker_pid=actual_broker,owner_pid=owner_pid)
    # Cancel at the real readiness boundary after the service is active.
    owner_policy.broker_uid = uid
    policy = policy_root/'cancel-ready.pb'; policy.write_bytes(owner_policy.SerializeToString())
    launch_policy = storage.StorageOwnerLaunchConfig(protocol_version=1, owner_configuration=str(policy),
        volume_root=str(volume), budget=budget(256,16,100000))
    launch_file = root/'cancel-ready-launch.pb'; launch_file.write_bytes(launch_policy.SerializeToString()); os.chown(launch_file,uid,gid)
    def unprivileged():
        (delegate/'manager/cgroup.procs').write_text(str(os.getpid())+'\n')
        os.setgroups([]); os.setgid(gid); os.setuid(uid)
    dummy = subprocess.Popen(['/usr/bin/sleep','30'],preexec_fn=unprivileged)
    pair = socket.socketpair()
    for end in pair: end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    pin = os.pidfd_open(dummy.pid)
    try:
        log = open(root/'cancel-ready.log','wb',buffering=0); streams.append(log)
        cancellation = subprocess.Popen([str(build/'spatial-storage-launch-probe'),str(launch_file),
            str(pair[0].fileno()),str(pin),'--interrupt-ready'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=log,pass_fds=[pair[0].fileno(),pin],preexec_fn=unprivileged)
        children.append(cancellation)
        size = struct.unpack('!I',exact(cancellation.stdout,4))[0]; assert 0 < size <= mib
        result = storage.StorageLaunchResult.FromString(exact(cancellation.stdout,size))
        assert result.protocol_version == 1 and result.HasField('startup_cleanup_confirmed') and result.startup_cleanup_confirmed
        assert result.error == 'Injected cancellation before storage readiness'
        assert re.fullmatch(r'spatial-storage-owner-[a-f0-9]{32}\.service',result.unit); owner_units.add(result.unit)
        assert cancellation.wait(timeout=15) == 2
        cancellation.stdin.close(); cancellation.stdout.close(); children.remove(cancellation)
        state = run(['systemctl','show',result.unit,'-p','LoadState','-p','ActiveState'])
        assert 'ActiveState=inactive' in state or 'ActiveState=failed' in state
        record['results']['cancelled_startup_confirms_owner_cleanup'] = dict(passed=True,unit=result.unit,state=state)
    finally:
        os.close(pin); pair[0].close(); pair[1].close()
        dummy.kill(); dummy.wait(timeout=5)
    child, log = spawn('idle-owner-death')
    actual_broker = call(child,'runtime_identities',core.RuntimeIdentitiesRequest()).identities.workspaced_pid
    pin = os.pidfd_open(actual_broker)
    try:
        run(['systemctl','stop',owner_unit(log)])
        assert select.select([pin],[],[],5)[0] == [pin], 'Idle broker survived owner death'
    finally: os.close(pin)
    finish(child, expected_exit=2)
    assert 'Storage owner unavailable' in log.read_text()
    record['results']['idle_owner_death_stops_actual_broker'] = dict(passed=True)
    for label, options, expected in [('wrong-uid',dict(wrong_uid=True),'Retained storage broker UID rejected'),
                                      ('wrong-volume',dict(wrong_volume=True),'Storage launch requires one explicit canonical volume')]:
        child, log = spawn(label, **options)
        assert child.wait(timeout=15) == 2
        assert expected in log.read_text(), log.read_text()
        if label == 'wrong-uid': owner_unit(log)
        else: assert 'Storage owner unit:' not in log.read_text()
        child.stdin.close(); child.stdout.close(); children.remove(child)
        assert not any(path.is_dir() for path in (delegate/'broker-root').iterdir())
        record['results'][label+'_rejected_with_checked_cleanup'] = dict(passed=True)
    record['status'] = 'passed'; save()
except BaseException as error:
    record.update(status='failed',failure=repr(error)); save(); raise
finally:
    for child in children:
        if child.poll() is None: child.kill()
        child.wait(timeout=10)
        child.stdin.close(); child.stdout.close()
    for log in streams: log.close()
    # Preserve every printed exact unit before removing the private policies.
    for log in root.glob('*.log'):
        owner_units.update(re.findall(r'Storage owner unit: (spatial-storage-owner-[a-f0-9]{32}\.service)',log.read_text(errors='replace')))
    record['owner_cleanup'] = {}
    for name in owner_units:
        subprocess.run(['systemctl','stop',name],capture_output=True,text=True,timeout=15)
        record['owner_cleanup'][name] = run(['systemctl','show',name,'-p','LoadState','-p','ActiveState'])
    if unit:
        run(['systemctl','stop',unit]); record['delegate_after'] = run(['systemctl','show',unit,'-p','LoadState','-p','ActiveState'])
    if mounted: run(['umount',volume]); mounted = False
    if loop: run(['losetup','-d',loop])
    for path in policy_root.iterdir():
        assert path.is_file() and path.suffix == '.pb'; path.unlink()
    policy_root.rmdir()
    record['mounted'] = mounted; record['loop_attached'] = bool(loop and run(['losetup','-j',image])); save()
    os.chown(root/'review.json',uid,gid)
    for path in root.glob('*.log'): os.chown(path,uid,gid)
print(json.dumps(dict(status=record['status'],results=record['results'])))
