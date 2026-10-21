"""Real quota-volume, Git-worker, and confined-application integration witnesses.

Run with sudo on an approved host: python3 THIS_FILE BUILD EMPTY_EVIDENCE_DIRECTORY.
Creates one dedicated 2 GiB image. Retains the image and logs after detaching it.
This focused check does not provision the operator's production spatial session.
"""
import errno
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import pwd
import select
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import uuid

assert os.getuid() == 0 and socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
production_owner = '--production-owner' in sys.argv
if production_owner: sys.argv.remove('--production-owner')
build, root = (Path(value).resolve() for value in sys.argv[1:3])
assert len(sys.argv) in (3,4) and build.is_dir() and not root.exists()
fixture_source = Path(sys.argv[3]).resolve() if len(sys.argv)==4 else None
if fixture_source:
    assert fixture_source.name == 'repository' and fixture_source.parent.parent == root.parent
    assert fixture_source.parent.name.startswith('m03-git-storage-integration-')
operator = pwd.getpwnam('dflanag3')
uid, gid = operator.pw_uid, operator.pw_gid
os.umask(0o077)
root.mkdir(mode=0o700); os.chown(root,uid,gid)
sys.path.insert(0, str(build/'python'))
import broker_pb2 as broker
import core_pb2 as core
import storage_pb2 as storage
import process_scope_pb2 as process_scope
from google.protobuf import text_format

mib = 2**20
volume, image = root/'volume', root/'volume.img'
volume.mkdir()
record = dict(status='running', phase_complete=False, host=socket.gethostname(), results={}, commands=[],
              runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), started=time.time())
record['production_owner_launch'] = production_owner
policy_root = Path('/run')/('spatial-storage-policy-'+uuid.uuid4().hex) if production_owner else None
if policy_root:
    policy_root.mkdir(mode=0o755)
    policy_root.chmod(0o755)
live, logs = [], []
loop, unit, scope = None, None, None
mounted = False
sequence = 0
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
           UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',
           GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')


def save():
    (root/'review.json').write_text(json.dumps(record, indent=2)+'\n')


def drop():
    os.setgroups([]); os.setgid(gid); os.setuid(uid)


def run(command, actor=False, timeout=90):
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True, env=env,
                            preexec_fn=drop if actor else None, timeout=timeout)
    item = dict(argv=list(map(str, command)), exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    record['commands'].append(item); save()
    assert result.returncode == 0, item
    return result.stdout.strip()


def snapshot_workers():
    if not scope or not scope.exists():
        return
    for group in scope.rglob('authority-*'):
        try:
            sample = {name: (group/name).read_text().strip() for name in
                      ('memory.max', 'memory.high', 'memory.peak', 'memory.events', 'pids.max', 'cpu.max', 'io.max', 'io.stat')}
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ENODEV):
                continue # The broker removes completed job scopes concurrently.
            raise
        if sample['memory.max'] != str(256*mib) or sample['pids.max'] != '16' or sample['memory.high'] == 'max':
            continue # Application groups and scopes that are still being configured.
        record.setdefault('git_workers', {})[str(group.relative_to(scope))] = sample
        assert sample['memory.high'] == str(192*mib), sample
        assert sample['cpu.max'] == '200000 100000', sample
        assert 'oom_kill 0' in sample['memory.events'], sample


def exact(stream, size, deadline):
    result = b''
    while len(result) < size:
        snapshot_workers()
        assert time.monotonic() < deadline, 'broker reply deadline'
        if not select.select([stream], [], [], min(.05, max(0, deadline-time.monotonic())))[0]:
            continue
        data = os.read(stream.fileno(), size-len(result))
        assert data, 'broker response EOF'
        result += data
    return result


def exchange(request, timeout=65):
    global sequence
    sequence += 1; request.protocol_version = 1; request.request_id = sequence
    if request.HasField('management'):
        request.management.protocol_version = 1; request.management.request_id = sequence
    payload = request.SerializeToString()
    actor.stdin.write(struct.pack('!I', len(payload))+payload); actor.stdin.flush()
    deadline = time.monotonic()+timeout
    size = struct.unpack('!I', exact(actor.stdout, 4, deadline))[0]
    assert 0 < size <= mib
    reply = broker.Reply.FromString(exact(actor.stdout, size, deadline))
    record.setdefault('broker_memory', []).append(dict(request=sequence,
        operation=request.management.WhichOneof('operation') if request.HasField('management') else 'launch',
        **{name:(scope/name).read_text().strip() for name in ('memory.current','memory.stat','memory.events')}))
    save()
    assert reply.request_id == sequence and not reply.HasField('error'), reply
    if reply.HasField('management'):
        assert not reply.management.HasField('error'), reply
    return reply


def management(field, message, timeout=65):
    request = broker.Request(); getattr(request.management, field).CopyFrom(message)
    return exchange(request, timeout).management


def launch(workspace, identity, operation, code, profile='development'):
    request = broker.Request()
    request.launch.workspace = workspace; request.launch.actor = identity
    request.launch.command.operation_id = operation; request.launch.command.profile = profile
    request.launch.command.argv.extend(['/usr/bin/python3', '-c', code])
    reply = exchange(request); assert reply.HasField('launched'), reply
    completion = management('process_wait', core.ProcessWaitRequest(id=reply.launched.process_id, timeout_seconds=5))
    assert completion.process_wait.exit_code == 0, completion
    return reply.launched.process_id


def spawn(label):
    pair = socket.socketpair()
    for end in pair:
        end.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    actor_log = open(root/(label+'-broker.log'), 'wb', buffering=0)
    owner_log = open(root/(label+'-owner.log'), 'wb', buffering=0)
    logs.extend([actor_log, owner_log])
    wrapper = "import os,sys; assert os.read(0,1)==b'!'; os.execv(sys.argv[1],sys.argv[1:])"
    child = subprocess.Popen([sys.executable, '-c', wrapper, str(build/'workspaced'), '--state-dir', str(volume),
                              '--storage-fd', str(pair[1].fileno()), '--resource-config', str(root/'resource.textproto')],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=actor_log,
                             pass_fds=[pair[1].fileno()], preexec_fn=drop, env=env)
    live.append(child); (scope/'cgroup.procs').write_text(str(child.pid)+'\n')
    if production_owner:
        config.ClearField('broker_pid')
        path = policy_root/(label+'-operator.pb'); path.write_bytes(config.SerializeToString())
        launch_policy = storage.StorageOwnerLaunchConfig(protocol_version=1, owner_configuration=str(path),
            volume_root=str(volume), budget=budget(256,16,100000))
        public = root/(label+'-launch.pb'); public.write_bytes(launch_policy.SerializeToString()); os.chown(public,uid,gid)
        pidfd = os.pidfd_open(child.pid)
        try:
            owner = subprocess.Popen([str(build/'spatial-storage-launch-probe'), str(public),
                str(pair[0].fileno()), str(pidfd)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=owner_log, pass_fds=[pair[0].fileno(),pidfd], env=env, preexec_fn=drop)
        finally: os.close(pidfd)
        live.append(owner)
        command = storage.StorageLaunchControl(protocol_version=1,request_id=1)
        payload = command.SerializeToString(); owner.stdin.write(struct.pack('!I',len(payload))+payload); owner.stdin.flush()
        assert select.select([owner.stdout],[],[],8)[0], 'Production owner launch reply deadline'
        deadline = time.monotonic()+8
        size = struct.unpack('!I',exact(owner.stdout,4,deadline))[0]
        assert 0<size<=mib
        reply = storage.StorageLaunchResult.FromString(exact(owner.stdout,size,deadline))
        assert reply.protocol_version==1 and reply.request_id==1 and reply.running, reply
        owner.spatial_unit = reply.unit
        record.setdefault('storage_owner_units',[]).append(reply.unit); save()
    else:
        config.broker_pid = child.pid
        path = root/(label+'-operator.pb'); path.write_bytes(config.SerializeToString())
        owner = subprocess.Popen([str(build/'spatial-storage-owner'), '--channel-fd', str(pair[0].fileno()),
                                  '--config', str(path)], stdout=subprocess.DEVNULL, stderr=owner_log,
                                 pass_fds=[pair[0].fileno()], env=env)
        live.append(owner)
    pair[0].close(); pair[1].close()
    child.stdin.write(b'!'); child.stdin.flush()
    if production_owner:
        owner_pid = int(run(['systemctl','show',owner.spatial_unit,'-p','MainPID','--value']))
        pin = os.pidfd_open(owner_pid)
        try:
            assert not select.select([pin],[],[],0)[0]
            owner_group = run(['systemctl','show',owner.spatial_unit,'-p','ControlGroup','--value'])
            location = Path('/sys/fs/cgroup')/owner_group.lstrip('/')
            controls = {name:(location/name).read_text().strip() for name in
                ('memory.max','memory.high','memory.swap.max','pids.max','cpu.max','io.max')}
            assert controls['memory.max']==str(256*mib) and controls['memory.high']==str(192*mib)
            assert controls['memory.swap.max']=='0' and controls['pids.max']=='16' and controls['cpu.max']=='100000 100000'
            for a,b in devices: assert f'{a}:{b} rbps={64*mib} wbps={16*mib}' in controls['io.max']
            private_network = os.readlink(f'/proc/{owner_pid}/ns/net') != os.readlink('/proc/self/ns/net')
            assert private_network
            handles = {p.name:p.read_text() for p in Path(f'/proc/{owner_pid}/fdinfo').iterdir() if 'Pid:' in p.read_text()}
            assert any(f'Pid:\t{child.pid}\n' in data for data in handles.values())
            assert not select.select([pin],[],[],0)[0]
            record.setdefault('owner_kernel_witnesses',[]).append(dict(pid=owner_pid,unit=owner.spatial_unit,
                controls=controls,retained_broker_handles=handles,private_network=private_network))
            save()
        finally: os.close(pin)
    return child, owner


def stop(child, owner):
    child.stdin.close(); assert child.wait(timeout=20) == 0
    if production_owner:
        command = storage.StorageLaunchControl(protocol_version=1,request_id=2,stop=True)
        payload = command.SerializeToString(); owner.stdin.write(struct.pack('!I',len(payload))+payload); owner.stdin.flush()
        assert select.select([owner.stdout],[],[],12)[0], 'Production owner cleanup deadline'
        deadline = time.monotonic()+12
        size = struct.unpack('!I',exact(owner.stdout,4,deadline))[0]; assert 0<size<=mib
        reply = storage.StorageLaunchResult.FromString(exact(owner.stdout,size,deadline))
        assert reply.protocol_version==1 and reply.request_id==2 and reply.stopped, reply
        owner.stdin.close(); owner.stdout.close()
    assert owner.wait(timeout=20) == 0
    child.stdout.close(); live.remove(child); live.remove(owner)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


try:
    assert int(run(['df', '--output=avail', '-B1', root]).splitlines()[-1]) > 4*2**30
    run(['fallocate', '-l', 2*2**30, image]); run(['mkfs.btrfs', '-f', '-d', 'single', '-m', 'single', image])
    loop = run(['losetup', '--find', '--show', image]); assert loop.startswith('/dev/loop')
    record['recovery'] = [['sudo', '-n', 'umount', str(volume)], ['sudo', '-n', 'losetup', '-d', loop]]; save()
    run(['mount', '-t', 'btrfs', '-o', 'rw,nodev,nosuid,compress=no', loop, volume]); mounted = True
    config = storage.StorageOwnerConfig(protocol_version=1, volume_root=str(volume),
        filesystem_id=uuid.UUID(run(['blkid', '-s', 'UUID', '-o', 'value', loop])).bytes,
        broker_uid=uid, volume_bytes=2*2**30, control_plane_bytes=64*mib, filesystem_reserve_bytes=64*mib)
    policy = config.default_policy; policy.total_bytes = 896*mib; policy.actor_state_default_bytes = 48*mib
    for name in ('development', 'read-only'):
        policy.profiles.add(name=name, state_bytes=32*mib)

    unit = 'spatial-m03-storage-integration-'+uuid.uuid4().hex+'.service'
    run(['systemd-run', '--unit='+unit, '--collect', '--service-type=exec', '--uid=dflanag3', '-p',
         'Delegate=cpu memory pids io', '-p', 'DelegateSubgroup=manager', '-p', 'MemoryMax=1536M', '-p',
         'MemorySwapMax=0', '-p', 'TasksMax=256', '-p', 'CPUQuota=400%', '-p', 'RuntimeMaxSec=600', '-p',
         'TimeoutStopSec=5s', '--', '/usr/bin/sleep', '600'])
    group = run(['systemctl', 'show', unit, '-p', 'ControlGroup', '--value'])
    assert group.startswith('/system.slice/') and group.endswith('/'+unit)
    delegate = Path('/sys/fs/cgroup')/group.lstrip('/'); assert delegate.stat().st_uid == uid
    (delegate/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
    scope = delegate/('broker-'+uuid.uuid4().hex)
    run([sys.executable,'-c','from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()',scope],actor=True)
    backing = (Path('/sys/dev/block')/f'{os.major(root.stat().st_dev)}:{os.minor(root.stat().st_dev)}').resolve()
    if (backing/'partition').exists(): backing = backing.parent
    block = Path(loop).stat()
    devices = {tuple(map(int, (backing/'dev').read_text().split(':'))), (os.major(block.st_rdev), os.minor(block.st_rdev))}
    def budget(memory, processes, cpu):
        return process_scope.ProcessScopeBudget(memory_bytes=memory*mib, swap_bytes=0, processes=processes,
            cpu_microseconds=cpu, io=[dict(major=a, minor=b, read_bytes_per_second=64*mib,
                                          write_bytes_per_second=16*mib) for a,b in sorted(devices)])
    resources = broker.BrokerResourceConfig(protocol_version=2, delegated_cgroup=str(scope),
        broker_budget=budget(1024,128,400000), manager_budget=budget(256,32,200000),
        default_policy=broker.WorkspaceResourcePolicy(total=budget(512,64,200000), actor_default=budget(256,32,100000),
            profiles=[broker.ApplicationResourceProfile(name=name, maximum=budget(128,16,100000))
                      for name in ('development','read-only')]))
    path = root/'resource.textproto'; path.write_text(text_format.MessageToString(resources)); os.chown(path,uid,gid)
    record.update(unit=unit, scope=str(scope), resources=text_format.MessageToString(resources)); save()

    source = fixture_source or root/'repository'
    def git(*args): return run(['git','-C',source,*args],actor=True,timeout=120)
    if not fixture_source:
        source.mkdir(); os.chown(source,uid,gid)
        path = source/'README'; path.write_text('storage integration\n'); os.chown(path,uid,gid)
        git('init','-b','main'); git('add','README')
        git('-c','user.name=Storage witness','-c','user.email=witness@localhost','commit','-m','Initial')
    initial = git('rev-list','--max-parents=0','main')
    blobs = {}
    for index in range(18):
        path = source/f'blob-{index:02}.bin'
        data = hashlib.shake_256(f'spatial-large-repository-{index}'.encode()).digest(16*mib)
        blobs[path.name] = hashlib.sha256(data).hexdigest()
        if fixture_source:
            assert digest(path) == blobs[path.name]
        else:
            path.write_bytes(data); os.chown(path,uid,gid)
    del data
    if not fixture_source:
        git('add','--all'); git('-c','user.name=Storage witness','-c','user.email=witness@localhost','commit','-m','Large files')
        git('repack','-ad','--window=0','--depth=0','--threads=2')
    before = git('show-ref'); record['source_blobs'] = blobs; save()
    actor, owner = spawn('first')
    started = time.monotonic()
    repository = management('git_repo_import',core.RepositoryImportRequest(id='large',path=str(source)),timeout=305).repository
    imported_seconds = time.monotonic()-started
    tree = management('git_worktree_create',core.WorktreeCreateRequest(repo='large',id='large-tree',branch='large-work',
        start='main',operation_id='create-large')).worktree
    assert all(digest(Path(tree.path)/name)==value for name,value in blobs.items())
    assert management('git_status',core.WorktreeStatusRequest(worktree='large-tree')).git_status.clean
    old_inode = (Path(tree.path)/'README').stat().st_ino
    operation = '../opaque/\nretirement'
    with (Path(tree.path)/'README').open('a') as held:
        assert management('git_worktree_remove',core.WorktreeRemoveRequest(id='large-tree',operation_id=operation)).worktree_removed.removed
        with closing(sqlite3.connect(volume/'execution.db')) as database:
            retired = Path(database.execute('SELECT retired FROM removals WHERE id=?',(operation,)).fetchone()[0])
        retained = retired/'data/project/README'
        assert retained.stat().st_ino == old_inode
        held.write('late write\n'); held.flush(); os.fsync(held.fileno())
        assert retained.read_text() == 'storage integration\nlate write\n'
    assert all(digest(retired/'data/project'/name)==value for name,value in blobs.items())
    assert git('show-ref') == before
    assert record.get('git_workers'), 'No kernel Git-worker witness captured'
    record['results']['large_import_retirement'] = dict(passed=True, bytes=288*mib,
        import_seconds=imported_seconds, source_unchanged=True, retained_inodes=True); save()
    project = management('git_worktree_create',core.WorktreeCreateRequest(repo='large',id='active-tree',branch='active',
        start=initial,operation_id='create-active')).worktree
    rights = [core.WORKSPACE_SELECT,core.FILES_READ,core.FILES_WRITE,core.PROCESS_LAUNCH]
    management('workspace_create',core.WorkspaceCreateRequest(id='workspace-a',worktree='active-tree',policy=rights))
    management('workspace_member_set',core.WorkspaceMemberRequest(id='workspace-a',actor='writer',rights=rights))
    stage_root=Path(project.path)/'.spatial-copy-staging'
    stage_root.mkdir(mode=0o700,exist_ok=True)
    os.chown(stage_root,uid,gid); stage_root.chmod(0o700)
    project_rootid=run(['btrfs','inspect-internal','rootid',project.path],actor=True).strip()
    stage_rootid=run(['btrfs','inspect-internal','rootid',stage_root],actor=True).strip()
    assert project_rootid==stage_rootid,(project_rootid,stage_rootid)
    stage_stat=stage_root.stat()
    assert (stage_stat.st_uid,stage_stat.st_gid,stage_stat.st_mode&0o777)==(uid,gid,0o700),stage_stat
    marker=stage_root/'prepared-marker'; marker.write_bytes(b'broker-owned-stage')
    os.chown(marker,uid,gid); marker.chmod(0o600)
    marker_stat=marker.stat()
    assert (marker_stat.st_uid,marker_stat.st_gid,marker_stat.st_mode&0o777)==(uid,gid,0o600),marker_stat
    mask_code=f'''import json,os
from pathlib import Path
roots=[Path('/project'),Path({str(project.path)!r}),Path('.')]
result={{'paths':{{}},'inherited_project_fds':[]}}
for fd_name in os.listdir('/proc/self/fd'):
 try:
  target=os.readlink('/proc/self/fd/'+fd_name)
  if target=='/project' or target.startswith({str(project.path)!r}):
   roots.append(Path('/proc/self/fd')/fd_name); result['inherited_project_fds'].append([fd_name,target])
 except OSError: pass
for index,root_path in enumerate(roots):
 stage=root_path/'.spatial-copy-staging'; probe=stage/f'probe-{{index}}'; moved=stage/f'moved-{{index}}'
 marker=stage/'prepared-marker'; witness={{'entries':None,'probe_target':str(probe),
  'unlink_target':str(marker),'rename_source':str(marker),'rename_target':str(moved)}}
 try: witness['entries']=sorted(os.listdir(stage))
 except OSError as error: witness['entries_error']=error.errno
 try: witness['read']=(stage/'prepared-marker').read_bytes().decode()
 except OSError as error: witness['read_errno']=error.errno
 try:
  with probe.open('wb') as stream: stream.write(b'forbidden')
  witness['write_errno']=0
 except OSError as error: witness['write_errno']=error.errno
 try: os.unlink(marker); witness['unlink_errno']=0
 except OSError as error: witness['unlink_errno']=error.errno
 try: os.rename(marker,moved); witness['rename_errno']=0
 except OSError as error: witness['rename_errno']=error.errno
 witness['probe_exists_after']=probe.exists()
 witness['marker_visible_after']=marker.exists()
 witness['moved_exists_after']=moved.exists()
 result['paths'][str(root_path)]=witness
Path('/project/mask-witness.json').write_text(json.dumps(result))
Path('/project/ordinary-write-after-mask').write_bytes(b'allowed')
'''
    mask_app=launch('workspace-a','writer','copy-stage-mask',mask_code)
    witness=json.loads((Path(project.path)/'mask-witness.json').read_text())
    import errno
    assert witness['inherited_project_fds']==[],witness
    assert witness['paths'] and all(item.get('entries')==[] and item.get('read_errno')==errno.ENOENT and
        item.get('write_errno')==errno.EROFS and item.get('unlink_errno')==errno.EROFS and
        item.get('rename_errno')==errno.EROFS and item.get('probe_exists_after') is False and
        item.get('marker_visible_after') is False and item.get('moved_exists_after') is False
        for item in witness['paths'].values()),witness
    for index,(alias,item) in enumerate(witness['paths'].items()):
        alias_stage=Path(alias)/'.spatial-copy-staging'
        assert item.get('probe_target')==str(alias_stage/f'probe-{index}'),(alias,item)
        assert item.get('unlink_target')==str(alias_stage/'prepared-marker'),(alias,item)
        assert item.get('rename_source')==str(alias_stage/'prepared-marker'),(alias,item)
        assert item.get('rename_target')==str(alias_stage/f'moved-{index}'),(alias,item)
        assert not (stage_root/f'probe-{index}').exists(),(alias,item)
        assert not (stage_root/f'moved-{index}').exists(),(alias,item)
    after_marker_stat=marker.stat()
    assert marker.read_bytes()==b'broker-owned-stage'
    assert (after_marker_stat.st_dev,after_marker_stat.st_ino,after_marker_stat.st_uid,
            after_marker_stat.st_gid,after_marker_stat.st_mode)==(
        marker_stat.st_dev,marker_stat.st_ino,marker_stat.st_uid,marker_stat.st_gid,marker_stat.st_mode)
    assert sorted(path.name for path in stage_root.iterdir())==['prepared-marker']
    assert (Path(project.path)/'ordinary-write-after-mask').read_bytes()==b'allowed'
    record['results']['production_application_copy_stage_mask']=dict(passed=True,application=mask_app,
        witness=witness,project_subvolume_rootid=project_rootid,stage_subvolume_rootid=stage_rootid); save()
    fill = """import errno,json,os
written=0; error=0
with open('/state/payload.dat','wb',buffering=0) as target:
    try:
        for i in range(64): written+=target.write(b'x'*2**20)
        os.fsync(target.fileno())
    except OSError as failure: error=failure.errno
with open('/project/REPORT','w') as report: json.dump(dict(bytes=written,errno=error),report)
"""
    first_app = launch('workspace-a','writer','fill-first',fill.replace('REPORT','first-quota.json'))
    first = json.loads((Path(project.path)/'first-quota.json').read_text())
    assert first['errno'] == 122 and 0 < first['bytes'] < 32*mib, first
    second_app = launch('workspace-a','writer','fill-second',fill.replace('REPORT','second-quota.json'))
    second = json.loads((Path(project.path)/'second-quota.json').read_text())
    assert second['errno'] == 122 and 0 < second['bytes'] < 24*mib, second
    record['results']['application_and_actor_quota'] = dict(passed=True, first=first,second=second,
                                                         applications=[first_app,second_app]); save()
    management('workspace_member_set',core.WorkspaceMemberRequest(id='workspace-a',actor='reader',
        rights=[core.WORKSPACE_SELECT,core.FILES_READ,core.PROCESS_LAUNCH]))
    read_app = launch('workspace-a','reader','read-only',"""import json
from pathlib import Path
result=dict(read=Path('/project/README').read_text())
try: Path('/project/forbidden').write_text('bad'); result['write_errno']=0
except OSError as error: result['write_errno']=error.errno
Path('/state/witness.json').write_text(json.dumps(result))
""")
    result = json.loads((volume/'applications'/read_app/'witness.json').read_text())
    assert result == dict(read='storage integration\n',write_errno=30), result
    record['results']['read_only'] = dict(passed=True,witness=result); save()
    management('git_repo_import',core.RepositoryImportRequest(id='sibling',path=str(source)),timeout=305)
    sibling = management('git_worktree_create',core.WorktreeCreateRequest(repo='sibling',id='sibling-tree',branch='sibling',
        start=initial,operation_id='create-sibling')).worktree
    management('workspace_create',core.WorkspaceCreateRequest(id='workspace-b',worktree='sibling-tree',policy=rights))
    management('workspace_member_set',core.WorkspaceMemberRequest(id='workspace-b',actor='writer',rights=rights))
    launch('workspace-b','writer','sibling-survives',"from pathlib import Path; Path('/project/survives').write_bytes(b's'*4*2**20)")
    assert (Path(sibling.path)/'survives').stat().st_size == 4*mib
    record['results']['sibling_survival'] = dict(passed=True, bytes=4*mib); save()
    stop(actor,owner)
    # The owner catalog, Git registry, quota groups, and uncommitted files survive restart.
    assert 'populated 0' in (scope/'cgroup.events').read_text()
    assert not any(path.is_dir() for path in scope.iterdir())
    scope.rmdir()
    run([sys.executable,'-c','from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()',scope],actor=True)
    actor,owner = spawn('restart')
    assert management('workspace_get',core.WorkspaceIdRequest(id='workspace-a')).workspace.id == 'workspace-a'
    assert management('git_worktree_remove',core.WorktreeRemoveRequest(id='large-tree',operation_id=operation)).worktree_removed.removed
    assert (Path(sibling.path)/'survives').stat().st_size == 4*mib
    stop(actor,owner)
    record['results']['restart'] = dict(passed=True); save()
    record['application_diagnostics'] = {}
    for path in (volume/'applications').glob('*/*'):
        if path.is_file() and path.name in ('setup.log','stdout','stderr'):
            data = path.read_text(errors='replace')
            record['application_diagnostics'][str(path.relative_to(volume))] = data
            assert not any(word in data for word in ('AddressSanitizer','LeakSanitizer','runtime error:')), (path,data)
    for path in root.glob('*.log'):
        data = path.read_text(errors='replace')
        assert not any(word in data for word in ('AddressSanitizer','LeakSanitizer','runtime error:')), (path,data)
    record['status'] = 'passed'; save()
except BaseException as error:
    record.update(status='failed',failure=repr(error)); save(); raise
finally:
    for child in reversed(live):
        if child.poll() is None: child.terminate()
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=10)
        if child.stdin: child.stdin.close()
        if child.stdout: child.stdout.close()
    for log in logs: log.close()
    if production_owner:
        record['owner_unit_cleanup'] = {}
        for owner_unit in record.get('storage_owner_units',[]):
            subprocess.run(['systemctl','stop',owner_unit],capture_output=True,text=True)
            state = subprocess.run(['systemctl','show',owner_unit,'-p','ActiveState','-p','LoadState'],capture_output=True,text=True).stdout
            record['owner_unit_cleanup'][owner_unit] = state
        if policy_root:
            for path in policy_root.iterdir():
                assert path.is_file() and path.name.endswith('-operator.pb')
                path.unlink()
            policy_root.rmdir()
    # Preserve initializer and application diagnostics even when an earlier assertion fails.
    record['application_diagnostics'] = {str(path.relative_to(volume)):path.read_text(errors='replace')
        for path in (volume/'applications').glob('*/*')
        if path.is_file() and path.name in ('setup.log','stdout','stderr')}
    if unit:
        stopped = subprocess.run(['systemctl','stop',unit],capture_output=True,text=True)
        record['service_stop'] = dict(exit_code=stopped.returncode,stderr=stopped.stderr)
        record['service_after'] = subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','LoadState'],capture_output=True,text=True).stdout
    if mounted:
        result = subprocess.run(['umount',str(volume)],capture_output=True,text=True)
        mounted = result.returncode != 0
        record['unmount'] = dict(exit_code=result.returncode,stderr=result.stderr)
    if loop and not mounted:
        result = subprocess.run(['losetup','-d',loop],capture_output=True,text=True)
        record['detach'] = dict(exit_code=result.returncode,stderr=result.stderr)
    record.update(mounted=mounted,elapsed=time.time()-record['started']); save()
    os.chown(root/'review.json',uid,gid)
    for path in root.glob('*.log'): os.chown(path,uid,gid)
print(json.dumps(dict(status=record['status'],results=list(record['results']),elapsed=record['elapsed'])))
