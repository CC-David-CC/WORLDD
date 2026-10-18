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
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid
from google.protobuf import text_format
from cgroup_events import populated_value

assert os.getuid() == 0 and socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
build, root = (Path(value).resolve() for value in sys.argv[1:])
assert build.is_dir() and not root.exists()
operator = pwd.getpwnam('dflanag3'); uid, gid = operator.pw_uid, operator.pw_gid
instance_registry=Path('/var/lib')/('spatial-broker-instances-'+str(uid))
try:
    registry_stat=instance_registry.lstat()
except FileNotFoundError as error:
    raise RuntimeError(f'Missing {instance_registry}; run the supported setup: sudo python3 tools/bootstrap_execution.py --apply') from error
if not stat.S_ISDIR(registry_stat.st_mode) or registry_stat.st_uid!=uid or stat.S_IMODE(registry_stat.st_mode)!=0o700:
    raise RuntimeError(f'Unsafe {instance_registry}; expected a real directory owned by uid {uid} with mode 0700')
os.umask(0o077); root.mkdir(); os.chown(root, uid, gid)
sys.path.insert(0, str(build/'python'))
import broker_pb2 as broker
import core_pb2 as core
import storage_pb2 as storage
import process_scope_pb2 as process

mib = 2**20
volume = root/'volume'
state_a=root/'crash-state-a'; state_b=root/'crash-state-b'; clone_state=root/'crash-state-clone'
parent_state=root/'parent-binding-state'; replacement_state=root/'replaced-state'
policy_root = Path('/run')/('spatial-storage-session-'+uuid.uuid4().hex)
policy_root.mkdir(mode=0o755); policy_root.chmod(0o755)
record = dict(status='running', phase_complete=False, host=socket.gethostname(), results={}, commands=[],
              runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
unit, delegate = None, None
volume_info = {}; mounted_volumes = []; loop_devices = []; children = []; streams = []; owner_units = set()


def save(): (root/'review.json').write_text(json.dumps(record, indent=2)+'\n')


def run(argv):
    reply = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=30)
    record['commands'].append(dict(argv=list(map(str, argv)), exit_code=reply.returncode,
                                   stdout=reply.stdout, stderr=reply.stderr)); save()
    assert reply.returncode == 0, record['commands'][-1]
    return reply.stdout.strip()


def create_volume(path):
    path = Path(path)
    image = root/(path.name+'.img')
    path.mkdir()
    with image.open('xb') as output: output.truncate(512*mib)
    loop = run(['losetup', '--find', '--show', image])
    # Register the loop before mkfs/mount/blkid can fail. The finalizer can then
    # detach it even when setup stops before a usable volume is published.
    loop_devices.append(loop)
    volume_info[path.resolve()] = dict(image=image, loop=loop, filesystem_id=None)
    run(['mkfs.btrfs', '-q', '-f', loop])
    if os.path.ismount(path): raise RuntimeError('volume mountpoint was already mounted')
    # Track mount intent before the command. Cleanup checks the live mount table,
    # so a failed mount is harmless while a partial successful mount is removed.
    mounted_volumes.append(path)
    run(['mount', '-t', 'btrfs', '-o', 'rw,nodev,nosuid', loop, path])
    filesystem_id = uuid.UUID(run(['blkid','-s','UUID','-o','value',loop])).bytes
    volume_info[path.resolve()]['filesystem_id'] = filesystem_id
    return path


def owner_policy_for(state, broker_uid=uid):
    info = volume_info[Path(state).resolve()]
    value = storage.StorageOwnerConfig(protocol_version=1, volume_root=str(state),
        filesystem_id=info['filesystem_id'], broker_uid=broker_uid,
        volume_bytes=512*mib, control_plane_bytes=64*mib, filesystem_reserve_bytes=64*mib)
    value.default_policy.total_bytes = 256*mib
    value.default_policy.actor_state_default_bytes = 48*mib
    for name in ['development','read-only']:
        value.default_policy.profiles.add(name=name, state_bytes=32*mib)
    return value


def detach_volume(path):
    path = Path(path)
    if path in mounted_volumes:
        if os.path.ismount(path): run(['umount', path])
        mounted_volumes.remove(path)
    info = volume_info.get(path.resolve())
    if info and info['loop'] in loop_devices:
        run(['losetup', '-d', info['loop']])
        loop_devices.remove(info['loop'])


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


def owned_scope(state):
    identity = (Path(state)/'.broker-instance-id').read_text()
    assert re.fullmatch(r'[a-f0-9]{32}', identity), identity
    return delegate/'broker-root'/('authority-broker-'+identity)


def assert_owned_scopes_empty_for_state(state):
    scope=owned_scope(state)
    assert populated_value((scope/'cgroup.events').read_text()) == 0, scope
    assert not (scope/'cgroup.procs').read_text().strip(), scope
    assert not any(path.is_dir() for path in scope.rglob('*')), scope


def assert_owned_scopes_empty():
    for scope in (delegate/'broker-root').iterdir():
        if not scope.is_dir(): continue
        assert populated_value((scope/'cgroup.events').read_text()) == 0, scope
        assert not (scope/'cgroup.procs').read_text().strip(), scope
        assert not any(path.is_dir() for path in scope.rglob('*')), scope


def spawn(label, wrong_uid=False, wrong_volume=False, owner_devices=None, state=None, delegated=None):
    state = Path(state) if state is not None else volume
    policy = policy_root/(label+'.pb')
    policy.write_bytes(owner_policy_for(state, uid+1 if wrong_uid else uid).SerializeToString())
    configured_volume = root if wrong_volume else state
    if not wrong_volume:
        assert Path(configured_volume).resolve() == state.resolve(), 'Broker state and storage volume roots must match'
    config = broker.BrokerProcessConfig(protocol_version=1, delegated_cgroup=str(delegated or (delegate/'broker-root')),
        resources=resources, storage_owner=storage.StorageOwnerLaunchConfig(protocol_version=1,
            owner_configuration=str(policy), volume_root=str(configured_volume), budget=budget(256, 16, 100000)))
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
    os.chown(state, uid, gid)
    child = subprocess.Popen([str(build/'spatial-storage-broker-launch-probe'), str(state), str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, preexec_fn=drop,
        env=dict(os.environ, ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1'))
    children.append(child)
    return child, root/(label+'.log')


def owner_unit(log):
    matches = re.findall(r'Storage owner unit: (spatial-storage-owner-[a-f0-9]{32}\.service)', log.read_text())
    assert len(matches) == 1, log.read_text()
    owner_units.add(matches[0]); return matches[0]


def finish(child, expected_exit=0, state=None):
    child.stdin.close(); assert child.wait(timeout=15) == expected_exit
    child.stdout.close(); children.remove(child)
    if state is not None: assert_owned_scopes_empty_for_state(state)



try:
    for state_path in (volume, state_a, state_b, clone_state, parent_state, replacement_state):
        create_volume(state_path)
    owner_policy = owner_policy_for(volume)
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
    devices = {tuple(map(int,(backing/'dev').read_text().split(':')))}
    devices.update((os.major(Path(loop_path).stat().st_rdev),os.minor(Path(loop_path).stat().st_rdev)) for loop_path in loop_devices)
    def budget(memory, processes, cpu):
        return process.ProcessScopeBudget(memory_bytes=memory*mib, swap_bytes=0, processes=processes,
            cpu_microseconds=cpu, io=[dict(major=a,minor=b,read_bytes_per_second=64*mib,write_bytes_per_second=16*mib)
                                     for a,b in sorted(devices)])
    resources = broker.BrokerResourceConfig(protocol_version=2, broker_budget=budget(1024,128,400000),
        manager_budget=budget(256,32,200000),default_policy=broker.WorkspaceResourcePolicy(total=budget(512,64,200000),
            actor_default=budget(256,32,100000), profiles=[broker.ApplicationResourceProfile(name=name,
                maximum=budget(128,16,100000)) for name in ['development','read-only']]))
    # Reject incomplete owner budgets before any catalog or quota mutation.
    primary_block = Path(volume_info[volume.resolve()]['loop']).stat()
    loop_device = (os.major(primary_block.st_rdev), os.minor(primary_block.st_rdev))
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
        assert_owned_scopes_empty()
        record['results'][label+'_rejected_before_catalog'] = dict(passed=True)
    child, log = spawn('normal')
    assert call(child,'hello',core.HelloRequest()).hello.implementation == 'production'
    identities = call(child,'runtime_identities',core.RuntimeIdentitiesRequest()).identities
    actual_broker = identities.workspaced_pid; assert actual_broker != child.pid
    actual_owner = owner_unit(log)
    owner_pid = int(run(['systemctl','show',actual_owner,'-p','MainPID','--value']))
    handles = [p.read_text() for p in Path(f'/proc/{owner_pid}/fdinfo').iterdir() if 'Pid:' in p.read_text()]
    assert any(f'Pid:\t{actual_broker}\n' in item for item in handles)
    membership = Path(f'/proc/{owner_pid}/cgroup').read_text()
    assert membership == '0::/system.slice/'+actual_owner+'\n'
    owner_group = Path('/sys/fs/cgroup/system.slice')/actual_owner
    controls = {name: (owner_group/name).read_text() for name in
                ['io.max', 'memory.max', 'memory.swap.max', 'pids.max', 'cpu.max']}
    observed = {}
    for line in controls['io.max'].splitlines():
        device, *limits = line.split()
        observed[tuple(map(int, device.split(':')))] = dict(item.split('=') for item in limits)
    for device in devices:
        assert observed[device]['rbps'] == str(64*mib) and observed[device]['wbps'] == str(16*mib)
    assert controls['memory.max'] == str(256*mib)+'\n'
    assert controls['memory.swap.max'] == '0\n' and controls['pids.max'] == '16\n'
    assert controls['cpu.max'] == '100000 100000\n'
    record['owner_kernel_witness'] = dict(cgroup=membership, controls=controls, broker_pidfd=handles,
        executable_sha256=hashlib.sha256(Path('/usr/local/libexec/spatial-storage-owner').read_bytes()).hexdigest())
    finish(child, state=volume)
    record['results']['normal_retained_direct_launch_and_cleanup'] = dict(passed=True,broker_pid=actual_broker,owner_pid=owner_pid)

    # SIGKILL the BrokerProcess owner to bypass destructors. Keep a real detached
    # process in its stable subtree, then restart that same state beside a peer.
    first, first_log=spawn('scope-crash-first',state=state_a)
    assert call(first,'hello',core.HelloRequest()).hello.implementation == 'production'
    first_scope=owned_scope(state_a)
    first_scope_inode=(first_scope.stat().st_dev,first_scope.stat().st_ino)
    duplicate, duplicate_log=spawn('scope-crash-duplicate',state=state_a)
    assert duplicate.wait(timeout=15)==2, duplicate_log.read_text(errors='replace')
    duplicate.stdin.close();duplicate.stdout.close();children.remove(duplicate)
    assert 'already owned' in duplicate_log.read_text(errors='replace')
    record['results']['same_state_concurrent_start_rejected'] = dict(passed=True)
    assert call(first,'hello',core.HelloRequest()).hello.implementation == 'production'
    assert (first_scope.stat().st_dev,first_scope.stat().st_ino)==first_scope_inode
    shutil.copyfile(state_a/'.broker-instance-id', clone_state/'.broker-instance-id')
    os.chown(clone_state/'.broker-instance-id', uid, gid); (clone_state/'.broker-instance-id').chmod(0o600)
    clone, clone_log=spawn('scope-crash-clone',state=clone_state)
    assert call(clone,'hello',core.HelloRequest()).hello.implementation == 'production'
    clone_scope=owned_scope(clone_state)
    assert clone_scope != first_scope, 'Copied state reused the original host-local broker scope'
    assert (first_scope.stat().st_dev,first_scope.stat().st_ino)==first_scope_inode
    finish(clone,state=clone_state)
    record['results']['copied_state_gets_distinct_host_local_identity'] = dict(passed=True, cloned_scope=str(clone_scope))
    assert call(first,'hello',core.HelloRequest()).hello.implementation == 'production'
    second, second_log=spawn('scope-crash-independent',state=state_b)
    assert call(second,'hello',core.HelloRequest()).hello.implementation == 'production'
    marker=root/'detached-app-ready'
    def move_app_to_manager():
        (delegate/'manager/cgroup.procs').write_text(str(os.getpid())+'\n')
        os.setgroups([]); os.setgid(gid); os.setuid(uid)
    app=subprocess.Popen(['/usr/bin/python3','-c',
        f"import pathlib,sys,time;sys.stdin.readline();pathlib.Path({str(marker)!r}).touch();time.sleep(300)"],
        stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        start_new_session=True,preexec_fn=move_app_to_manager)
    app_pin=os.pidfd_open(app.pid)
    try:
        app.stdin.write(b'ready\n');app.stdin.flush()
        deadline=time.monotonic()+5
        while not marker.exists():
            assert time.monotonic()<deadline, 'Detached app readiness deadline';time.sleep(.01)
        # Keep the witness in a distinct sub-cgroup so restart must drain descendants.
        app_group=first_scope/'detached-app';app_group.mkdir()
        (app_group/'cgroup.procs').write_text(str(app.pid)+'\n')
        assert (app_group/'cgroup.procs').read_text().strip()==str(app.pid)
        first.kill(); assert first.wait(timeout=10)==-9
        first.stdin.close();first.stdout.close();children.remove(first)
        run(['systemctl','stop',owner_unit(first_log)])
        restarted,restart_log=spawn('scope-crash-restarted',state=state_a)
        deadline=time.monotonic()+15
        while 'Storage owner unit:' not in restart_log.read_text(errors='replace'):
            assert restarted.poll() is None, restart_log.read_text(errors='replace')
            assert time.monotonic()<deadline, 'Restarted BrokerProcess readiness deadline'
            time.sleep(.01)
        owner_unit(restart_log)
        assert select.select([app_pin],[],[],0)[0]==[app_pin], 'Stale descendant survived before first broker request'
        assert populated_value((first_scope/'cgroup.events').read_text()) == 1
        assert not app_group.exists(), 'Stale child cgroup survived restart drain'
        assert populated_value((owned_scope(state_b)/'cgroup.events').read_text()) == 1
        # The independent broker must remain usable while the restarted broker accepts work.
        assert call(second,'hello',core.HelloRequest()).hello.implementation == 'production'
        assert call(restarted,'hello',core.HelloRequest()).hello.implementation == 'production'
        finish(restarted,state=state_a)
        finish(second,state=state_b)
        record['results']['crash_restart_drains_stable_scope_preserves_peer'] = dict(
            passed=True, stale_app_pid=app.pid, stale_scope=str(first_scope), independent_scope=str(owned_scope(state_b)))
    finally:
        os.close(app_pin)
        if app.poll() is None: app.kill()
        app.wait(timeout=5); app.stdin.close()
    # A broker identity remains bound to its delegated parent for this boot.
    parent_owner,parent_log=spawn('parent-binding-original',state=parent_state)
    assert call(parent_owner,'hello',core.HelloRequest()).hello.implementation == 'production'
    finish(parent_owner,state=parent_state)
    alternate=delegate/'alternate-broker-root'; alternate.mkdir(); os.chown(alternate,uid,gid)
    (alternate/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
    changed,changed_log=spawn('parent-binding-changed',state=parent_state,delegated=alternate)
    assert changed.wait(timeout=15)==2, changed_log.read_text(errors='replace')
    changed.stdin.close();changed.stdout.close();children.remove(changed)
    assert 'Delegated cgroup parent changed within the current boot' in changed_log.read_text(errors='replace')
    assert_owned_scopes_empty_for_state(parent_state)
    assert not any(path.name.startswith('authority-broker-') for path in alternate.iterdir())
    record['results']['same_boot_parent_change_fails_closed'] = dict(passed=True)
    original,original_log=spawn('replacement-original',state=replacement_state)
    assert call(original,'hello',core.HelloRequest()).hello.implementation == 'production'
    replacement_scope=owned_scope(replacement_state)
    finish(original,state=replacement_state)
    displaced=root/'replaced-state-old'
    saved_identity=root/'replaced-state-identity.copy'
    shutil.copyfile(replacement_state/'.broker-instance-id', saved_identity)
    old_image=volume_info[replacement_state.resolve()]['image']
    detach_volume(replacement_state)
    replacement_state.rename(displaced)
    old_image.rename(root/'replaced-state-old.img')
    create_volume(replacement_state)
    shutil.copyfile(saved_identity, replacement_state/'.broker-instance-id')
    os.chown(replacement_state/'.broker-instance-id',uid,gid); (replacement_state/'.broker-instance-id').chmod(0o600)
    replaced,replaced_log=spawn('replacement-rejected',state=replacement_state)
    assert replaced.wait(timeout=15)==2, replaced_log.read_text(errors='replace')
    replaced.stdin.close();replaced.stdout.close();children.remove(replaced)
    assert 'Broker state directory was replaced at its registered path' in replaced_log.read_text(errors='replace')
    assert populated_value((replacement_scope/'cgroup.events').read_text()) == 0
    record['results']['same_path_replaced_state_inode_fails_closed'] = dict(passed=True)
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
        assert_owned_scopes_empty()
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
    for volume_path in list(reversed(list(volume_info))):
        detach_volume(volume_path)
    # A loop can be attached even when mkfs or mount failed before the path was
    # published as a mounted volume. Always drain any remaining tracked loops.
    for loop_path in list(reversed(loop_devices)):
        run(['losetup','-d',loop_path])
        loop_devices.remove(loop_path)
    for path in policy_root.iterdir():
        assert path.is_file() and path.suffix == '.pb'; path.unlink()
    policy_root.rmdir()
    record['mounted_volumes'] = [str(path) for path in mounted_volumes]
    record['loop_devices_attached'] = [loop_path for loop_path in loop_devices]
    save()
    os.chown(root/'review.json',uid,gid)
    for path in root.glob('*.log'): os.chown(path,uid,gid)
print(json.dumps(dict(status=record['status'],results=record['results'])))
