"""Production storage-owner and ProcessScope copy-stage witness for approved world hosts."""
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid

assert os.getuid() == 0 and socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
NATIVE_MODES = ('readonly-destination','writable-source','receipt-control','receipt-binding','success','symlink')
BUILD, SOURCE, EVIDENCE = (Path(arg).resolve() for arg in sys.argv[1:4])
assert BUILD.is_dir() and SOURCE.is_dir() and not EVIDENCE.exists()
EVIDENCE.mkdir(mode=0o700, parents=True)
lock_fd = os.open('/run/lock/spatial-m07-copy-worker.lock', os.O_CREAT|os.O_RDWR|os.O_CLOEXEC, 0o600)
fcntl.flock(lock_fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
operator = pwd.getpwnam('dflanag3'); uid, gid = operator.pw_uid, operator.pw_gid
name = 'm07-copy-' + uuid.uuid4().hex[:20]
entry = Path('/var/lib/spatial/volumes') / name
volume = entry / 'mount'
package = EVIDENCE / 'package'; package.mkdir(mode=0o700)
record = {'host': socket.gethostname(), 'source': str(SOURCE), 'build': str(BUILD), 'commands': [], 'witnesses': [], 'case_scopes': []}
control = EVIDENCE.parent / 'copy-fixture-process.json'
control_fd = os.open(control, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW, 0o600)
with os.fdopen(control_fd, 'w') as stream:
    os.fchown(stream.fileno(), uid, gid)
    process = {'pid': os.getpid(), 'start_ticks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
               'argv': Path('/proc/self/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')}
    json.dump(process, stream, sort_keys=True)
    stream.write('\n')


def request_cancellation(signum, _frame):
    record['cancelled_signal'] = signum
    raise RuntimeError('copy fixture cancellation requested')


signal.signal(signal.SIGTERM, request_cancellation)
signal.signal(signal.SIGINT, request_cancellation)
unit = 'spatial-m07-copy-'+uuid.uuid4().hex+'.service'
socket_dir = Path('/run') / ('s-m07-'+uuid.uuid4().hex[:12])
scope = None; owner = None; client = None; helper = Path('/usr/local/libexec/spatial-copy-tree-worker')
listeners = []; socket_paths = []; connections = []
original_helper = None; installed_helper_hash = None; config_paths = []
backup = EVIDENCE / 'prior-copy-worker'
had_helper = os.path.lexists(helper)


def owned_cgroup_descendants(root, maximum_nodes=512, maximum_depth=12):
    root = root.resolve(strict=True)
    pending = [(root, 0)]
    seen = []
    while pending:
        current, depth = pending.pop()
        resolved = current.resolve(strict=True)
        if resolved != root and root not in resolved.parents:
            raise RuntimeError('cgroup descendant escaped owned scope')
        seen.append(resolved)
        if len(seen) > maximum_nodes:
            raise RuntimeError('owned cgroup scan exceeded node bound')
        if depth >= maximum_depth:
            if any(child.is_dir() for child in resolved.iterdir()):
                raise RuntimeError('owned cgroup scan exceeded depth bound')
            continue
        pending.extend((child, depth + 1) for child in resolved.iterdir() if child.is_dir())
    return seen


def process_cgroup_path(pid):
    entries = Path(f'/proc/{pid}/cgroup').read_text().splitlines()
    unified = [line.split(':', 2)[2] for line in entries if line.startswith('0::')]
    if len(unified) != 1 or not unified[0].startswith('/'):
        raise RuntimeError(f'process {pid} has no unique unified cgroup')
    return (Path('/sys/fs/cgroup') / unified[0].lstrip('/')).resolve(strict=True)


def verify_empty_owned_scope(root):
    groups = owned_cgroup_descendants(root)
    for group in groups:
        members = (group/'cgroup.procs').read_text().split()
        events = dict(line.split() for line in (group/'cgroup.events').read_text().splitlines())
        if members or events.get('populated') != '0':
            raise RuntimeError(f'owned copy cgroup remains populated: {group}')
    return groups


def stop_unit_and_verify():
    stop = run(['systemctl','stop',unit],timeout=20,check=False)
    state = run(['systemctl','is-active',unit],timeout=10,check=False)
    current = state.stdout.strip()
    record['unit_stop'] = {'stop_exit':stop.returncode,'is_active_exit':state.returncode,'state':current}
    if current not in ('inactive','unknown') or state.returncode not in (3,4):
        raise RuntimeError(f'temporary unit did not become inactive: {current!r}')


def reject_sanitizer_diagnostics(*outputs):
    for output in outputs:
        if any(marker in output for marker in ('AddressSanitizer','LeakSanitizer','UndefinedBehaviorSanitizer','runtime error:')):
            raise RuntimeError('sanitizer diagnostics in fixture output: '+output)


def run(argv, timeout=120, check=True):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=timeout)
    record['commands'].append({'argv': list(map(str, argv)), 'exit': result.returncode,
                               'stdout': result.stdout, 'stderr': result.stderr})
    reject_sanitizer_diagnostics(result.stdout, result.stderr)
    if check and result.returncode: raise RuntimeError(f'{argv}: {result.stdout}\n{result.stderr}')
    return result


def drop_to_operator():
    os.setgroups([]); os.setgid(gid); os.setuid(uid)


def prepare_scope_as_operator(path):
    # Create the cgroup as its delegated owner so its control files inherit that UID.
    script = """import json,os,stat,sys
from pathlib import Path
scope=Path(sys.argv[1]);scope.mkdir(mode=0o700)
assert scope.stat().st_uid==os.getuid() and stat.S_IMODE(scope.stat().st_mode)==0o700
files={}
for name in ('cgroup.procs','cgroup.subtree_control','cgroup.threads'):
    path=scope/name;info=path.stat()
    assert info.st_uid==os.getuid(),name
    descriptor=os.open(path,os.O_WRONLY|os.O_CLOEXEC);os.close(descriptor)
    files[name]={'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode),'writable_as_uid':True}
limits={name:(scope/name).read_text() for name in ('memory.max','memory.swap.max','pids.max','cpu.max','io.max')}
assert limits=={'memory.max':'max\\n','memory.swap.max':'max\\n','pids.max':'max\\n','cpu.max':'max 100000\\n','io.max':''},limits
print(json.dumps({'uid':os.getuid(),'scope':str(scope),'files':files,'initial_limits':limits},sort_keys=True))
"""
    result=subprocess.run([sys.executable,'-B','-c',script,str(path)],preexec_fn=drop_to_operator,
                          capture_output=True,text=True,timeout=10)
    delegation={'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
    if result.returncode:
        raise RuntimeError('nonroot cgroup creation or control-file delegation failed: '+result.stderr)
    delegation['verified']=json.loads(result.stdout)
    return delegation


def cleanup_case_scope(case):
    path=Path(case['scope'])
    if not path.exists():
        assert case.get('removed'), 'case scope disappeared before verified removal'
        return
    groups=verify_empty_owned_scope(path)
    case['empty_before_removal']=[str(group) for group in groups]
    for group in sorted(groups,key=lambda entry:len(entry.parts),reverse=True):
        if group.stat().st_uid!=uid:
            raise RuntimeError('refusing to remove unowned case cgroup: '+str(group))
        group.rmdir()
    case['removed']=not path.exists()
    assert case['removed']


def start_owner_client(mode):
    global owner, client, scope
    scope=delegate/('broker-'+mode+'-'+uuid.uuid4().hex)
    case={'mode':mode,'scope':str(scope),'removed':False}
    record['case_scopes'].append(case)
    case['delegation']=prepare_scope_as_operator(scope)
    runtime.resources.delegated_cgroup=str(scope)
    resource_path=EVIDENCE/('resource-'+mode+'.textproto')
    resource_path.write_text(text_format.MessageToString(runtime.resources))
    os.chown(resource_path,uid,gid); resource_path.chmod(0o600)
    case['resource_config']=str(resource_path)
    case['resource_config_sha256']=hashlib.sha256(resource_path.read_bytes()).hexdigest()
    socket_path = socket_dir / ('o-'+mode+'.sock')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listeners.append(listener); socket_paths.append(socket_path)
    listener.bind(str(socket_path)); os.chmod(socket_path, 0o666); listener.listen(1); listener.settimeout(10)
    wrapper = ('import os,socket,sys; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(sys.argv[1]); '
               'fd=s.detach(); os.set_inheritable(fd,True); os.execv(sys.argv[2],[sys.argv[2],str(fd),*sys.argv[3:]])')
    def enter_scope():
        (scope/'cgroup.procs').write_text(str(os.getpid())+'\n')
        drop_to_operator()
    client = subprocess.Popen([sys.executable,'-B', '-c', wrapper, str(socket_path), str(BUILD/'spatial-copy-worker-production-probe'),
                              str(volume), str(resource_path), mode],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             preexec_fn=enter_scope)
    connection, _ = listener.accept(); connections.append(connection)
    listener.close(); listeners.remove(listener)
    socket_path.unlink(missing_ok=True); socket_paths.remove(socket_path)
    configuration = storage.StorageOwnerConfig.FromString((entry/'owner.pb').read_bytes())
    configuration.broker_uid = uid; configuration.broker_pid = client.pid
    config_path = Path('/run')/('m07-copy-owner-'+uuid.uuid4().hex+'.pb')
    config_path.write_bytes(configuration.SerializeToString(deterministic=True)); config_path.chmod(0o600); config_paths.append(config_path)
    relay_mode = mode in ('receipt-binding', 'receipt-control')
    audit_path = EVIDENCE / (mode+'-relay.json')
    if relay_mode:
        relay_command = [sys.executable,'-B', str(Path(__file__).with_name('storage_owner_receipt_relay.py')),
                         '--channel-fd', str(connection.fileno()), '--build', str(BUILD),
                         '--config', str(config_path), '--audit', str(audit_path)]
        if mode == 'receipt-binding': relay_command.append('--mutate')
        owner = subprocess.Popen(relay_command, pass_fds=[connection.fileno()],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True)
    else:
        owner = subprocess.Popen([str(BUILD/'spatial-storage-owner'), '--channel-fd', str(connection.fileno()),
                                  '--config', str(config_path)], pass_fds=[connection.fileno()],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True)
    connection.close(); connections.remove(connection)
    started = time.monotonic()
    samples = []
    while client.poll() is None:
        try:
            groups = owned_cgroup_descendants(scope)
        except FileNotFoundError:
            groups = []
        for group in groups:
            try:
                children = (group/'cgroup.procs').read_text().split()
                for child in children:
                    pid = int(child)
                    try:
                        executable = os.readlink(f'/proc/{pid}/exe')
                        if executable != str(helper): continue
                        actual_group = process_cgroup_path(pid)
                        if actual_group != group or (actual_group != scope.resolve() and scope.resolve() not in actual_group.parents):
                            raise RuntimeError('worker PID is outside the operation-owned scope')
                        sample = {key:(actual_group/key).read_text().strip()
                                  for key in ('memory.max','pids.max','cpu.max')}
                        sample['worker_pid'] = pid
                        sample['worker_cgroup'] = str(actual_group)
                        sample['private_netns'] = os.readlink(f'/proc/{pid}/ns/net') != os.readlink(f'/proc/{client.pid}/ns/net')
                        samples.append(sample)
                        break
                    except (FileNotFoundError, ProcessLookupError): pass
            except OSError as error:
                if error.errno in (errno.ENOENT, errno.ESRCH):
                    continue
                # cgroupfs can return ENODEV after a listed worker group is removed.
                # The operation root must remain readable; all other errors stay fatal.
                if error.errno != errno.ENODEV or group == scope:
                    raise
                races = case.setdefault('sampler_teardown_races', [])
                if len(races) >= 64:
                    raise RuntimeError('copy sampler teardown race bound exceeded') from error
                races.append({'group':str(group), 'errno':error.errno})
        if time.monotonic()-started > 180:
            client.kill(); raise TimeoutError('copy probe exceeded 180 seconds')
        time.sleep(.001)
    stdout, stderr = client.communicate(timeout=5)
    owner_stderr = owner.communicate(timeout=10)[1]
    test_id = {'receipt-binding':'copy_stage_begin_receipt_destination_binding_fences_before_worker',
               'receipt-control':'copy_stage_authorized_receipt_relay_reaches_worker',
               'writable-source':'copy_worker_rejects_writable_source_before_launch',
               'readonly-destination':'copy_worker_rejects_readonly_destination_before_launch'}.get(mode, mode)
    result = {'test_id':test_id,'mode':mode,'exit':client.returncode,'stdout':stdout,'stderr':stderr,'owner_stderr':owner_stderr,'broker_scope':case['scope'],'scopes':samples}
    record['witnesses'].append(result)
    reject_sanitizer_diagnostics(stdout, stderr, owner_stderr)
    if client.returncode: raise RuntimeError(json.dumps(result, indent=2))
    if owner.returncode: raise RuntimeError('Storage owner failed: '+owner_stderr)
    if relay_mode:
        audit = json.loads(audit_path.read_text())
        result['relay'] = audit
        reject_sanitizer_diagnostics(audit.get('owner_stderr',''))
        assert audit['peer_uid'] == 0 and audit['broker_uid'] == uid
        assert audit['broker_exit'] == 0 and audit['owner_exit'] == 0 and audit['client_eof']
        assert audit['requests'].count('copy_stage_begin') >= 1, audit
        if mode == 'receipt-binding':
            assert 'BEGIN_BINDING_REJECTED UNAVAILABLE exact-cause channel=fenced destination=unchanged worker=unprepared' in stdout
            assert len(audit['mutations']) == 1 and audit['mutations'][0]['field'] == 'destination_subvolume_id'
            assert audit['requests'].count('copy_stage_begin') == 1
            assert audit['requests'][-1] == 'copy_stage_begin', audit
            assert not any(method in audit['requests'] for method in ('copy_stage_start','copy_stage_seal','copy_stage_uncertain','copy_stage_inspect'))
            assert not samples, samples
        else:
            assert not audit['mutations']
            assert audit['requests'].count('copy_stage_start') == 1 and audit['requests'].count('copy_stage_seal') == 1
    if mode in ('success','receipt-control'):
        assert 'retry=identical inode=independent' in stdout
        assert any(sample.get('memory.max')==str(256*2**20) and sample.get('pids.max')=='16' and
                   sample.get('cpu.max')=='200000 100000' for sample in samples), samples
        assert any(sample.get('worker_pid') and sample.get('private_netns') for sample in samples), samples
        assert any(sample.get('worker_pid') and sample.get('worker_cgroup') for sample in samples), samples
    if mode=='symlink': assert 'SYMLINK_REJECTED UNCERTAIN' in stdout
    if mode=='readonly-destination':
        assert 'READONLY_DESTINATION_REJECTED exact-cause destination=unchanged worker=unprepared channel=alive' in stdout
        assert not samples, samples
    if mode=='writable-source':
        assert 'WRITABLE_SOURCE_REJECTED exact-cause destination=unchanged worker=unprepared false-readonly=fenced' in stdout
        assert not samples, samples
    config_path.unlink(missing_ok=True); socket_path.unlink(missing_ok=True)
    cleanup_case_scope(case)


try:
    sys.path.insert(0, str(BUILD/'python'))
    import broker_pb2 as broker
    import storage_pb2 as storage
    import process_scope_pb2
    from google.protobuf import text_format
    socket_dir.mkdir(mode=0o711)
    socket_info=socket_dir.stat(); assert socket_info.st_uid==0 and stat.S_IMODE(socket_info.st_mode)==0o711
    for name_file in ('spatial-copy-worker-production-probe','spatial-copy-tree-worker','spatial-storage-owner','spatial-storage-owner-probe'):
        source_file=BUILD/name_file; dest=package/name_file
        dest.write_bytes(source_file.read_bytes()); dest.chmod(0o700)
    python_package=package/'python'; python_package.mkdir()
    for binding in (BUILD/'python').rglob('*_pb2.py'):
        packaged=python_package/binding.relative_to(BUILD/'python'); target=python_package/packaged
        target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(binding.read_bytes())
    (package/'operator-storage.textproto').write_bytes((SOURCE/'config/operator-storage.textproto').read_bytes())
    (package/'test_storage_snapshot.py').write_bytes((SOURCE/'tests/core/test_storage_snapshot.py').read_bytes())
    (package/'test_copy_worker_production.py').write_bytes(Path(__file__).read_bytes())
    (package/'storage_owner_receipt_relay.py').write_bytes(Path(__file__).with_name('storage_owner_receipt_relay.py').read_bytes())
    record['package_sha256']={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*') if p.is_file()}
    template=broker.StorageVolumeDefinition(); text_format.Parse((SOURCE/'config/operator-storage.textproto').read_text(),template)
    template.name=name; policy=template.owner_policy; policy.broker_uid=uid
    policy.volume_bytes=1024*2**20; policy.control_plane_bytes=64*2**20; policy.filesystem_reserve_bytes=64*2**20
    policy.default_policy.total_bytes=128*2**20; policy.default_policy.actor_state_default_bytes=4*2**20
    domain_bytes=len(NATIVE_MODES)*policy.default_policy.total_bytes
    pool_bytes=policy.volume_bytes-policy.control_plane_bytes-policy.filesystem_reserve_bytes
    assert pool_bytes-domain_bytes==128*2**20
    record['capacity_plan']={'volume_bytes':policy.volume_bytes,'control_plane_bytes':policy.control_plane_bytes,
        'filesystem_reserve_bytes':policy.filesystem_reserve_bytes,'native_repositories':len(NATIVE_MODES),
        'domain_maximum_bytes':policy.default_policy.total_bytes,'planned_domain_bytes':domain_bytes,
        'available_pool_bytes':pool_bytes,'headroom_bytes':pool_bytes-domain_bytes}
    for profile in policy.default_policy.profiles: profile.state_bytes=4*2**20
    definition=EVIDENCE/'definition.textproto'; definition.write_text(text_format.MessageToString(template))
    run([sys.executable,'-B',SOURCE/'tools/provision_storage.py','create','--bindings-dir',BUILD/'python',
         '--definition',definition,'--apply'])
    config=storage.StorageOwnerConfig.FromString((entry/'owner.pb').read_bytes())
    config.broker_uid=uid; config.broker_pid=os.getpid()
    (EVIDENCE/'owner-base.pb').write_bytes(config.SerializeToString(deterministic=True))
    (EVIDENCE/'owner-base.pb').chmod(0o600)
    runtime=broker.StorageVolumeRuntime.FromString((entry/'runtime.pb').read_bytes())
    run(['systemd-run','--unit='+unit,'--collect','--service-type=exec','--uid=dflanag3','-p',
         'Delegate=cpu memory pids io','-p','DelegateSubgroup=manager','-p','MemoryMax=1536M','-p',
         'MemorySwapMax=0','-p','TasksMax=256','-p','CPUQuota=400%','-p','RuntimeMaxSec=600','-p',
         'TimeoutStopSec=5s','--','/usr/bin/sleep','600'])
    group=run(['systemctl','show',unit,'-p','ControlGroup','--value']).stdout.strip()
    delegate=Path('/sys/fs/cgroup')/group.lstrip('/')
    controllers=set((delegate/'cgroup.controllers').read_text().split())
    assert {'cpu','memory','pids','io'} <= controllers, controllers
    (delegate/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
    assert {'cpu','memory','pids','io'} <= set((delegate/'cgroup.subtree_control').read_text().split())
    resource_path=EVIDENCE/'resource-template.textproto'
    resource_path.write_text(text_format.MessageToString(runtime.resources))
    os.chown(resource_path,uid,gid); resource_path.chmod(0o600)
    io_limits=list(runtime.resources.manager_budget.io)
    assert io_limits
    record.update(volume=str(volume),unit=unit,delegate=str(delegate),resource_template=str(resource_path))
    if had_helper:
        info=helper.lstat(); assert stat.S_ISREG(info.st_mode) and info.st_uid==0 and not helper.is_symlink()
        original_helper={'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode),'sha256':hashlib.sha256(helper.read_bytes()).hexdigest()}
        backup.write_bytes(helper.read_bytes()); backup.chmod(0o600)
    helper.parent.mkdir(parents=True,exist_ok=True)
    staged_helper=helper.with_name(helper.name+'.m07-'+uuid.uuid4().hex)
    staged_bytes=(BUILD/'spatial-copy-tree-worker').read_bytes(); staged_helper.write_bytes(staged_bytes); staged_helper.chmod(0o755); os.chown(staged_helper,0,0); installed_helper_hash=hashlib.sha256(staged_bytes).hexdigest(); os.replace(staged_helper,helper)
    assert helper.lstat().st_uid==0 and stat.S_ISREG(helper.lstat().st_mode) and not helper.lstat().st_mode&0o022
    bwrap=Path('/usr/local/libexec/spatial-bwrap'); bwrap_info=bwrap.lstat()
    assert stat.S_ISREG(bwrap_info.st_mode) and bwrap_info.st_uid==0 and not bwrap_info.st_mode&0o022
    record['bwrap_sha256']=hashlib.sha256(bwrap.read_bytes()).hexdigest()
    os.chown(EVIDENCE,uid,gid); EVIDENCE.chmod(0o700)
    leak=run([BUILD/'spatial-copy-worker-production-probe','-1',volume,resource_path,'fd-leak'])
    assert 'MALFORMED_OWNER_FD_STABLE 32' in leak.stdout
    record['malformed_owner_fd_regression']=leak.stdout.strip()
    for mode in NATIVE_MODES:
        start_owner_client(mode)
    restart_evidence=EVIDENCE/'owner-recovery'; restart_evidence.mkdir(mode=0o700)
    os.chown(restart_evidence,uid,gid)
    restart_env=dict(os.environ,SPATIAL_SECURITY_EVIDENCE=str(restart_evidence))
    recovery=subprocess.run([sys.executable,'-B',SOURCE/'tests/core/test_storage_snapshot.py',BUILD/'spatial-storage-owner-probe',SOURCE,'-k','copy_stage_claim_is_digest_bound_and_restart_is_uncertain'],capture_output=True,text=True,timeout=300,env=restart_env,cwd=SOURCE,preexec_fn=drop_to_operator)
    record['restart_recovery']={'uid':uid,'exit':recovery.returncode,'stdout':recovery.stdout,'stderr':recovery.stderr}
    reject_sanitizer_diagnostics(recovery.stdout, recovery.stderr)
    record['restart_owner_stderr_scans']=[]
    for diagnostics_path in sorted(restart_evidence.rglob('owner-*.stderr.log')):
        assert diagnostics_path.resolve().is_relative_to(restart_evidence)
        descriptor=os.open(diagnostics_path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        with os.fdopen(descriptor,'rb') as stream:
            info=os.fstat(stream.fileno())
            assert stat.S_ISREG(info.st_mode) and info.st_uid==uid
            contents=stream.read(8*2**20+1)
        assert len(contents)<=8*2**20, 'recovery owner diagnostics exceeded8MiB'
        scan={'path':str(diagnostics_path),'bytes':len(contents),'sha256':hashlib.sha256(contents).hexdigest(),
              'sanitizer_free':False}
        record['restart_owner_stderr_scans'].append(scan)
        reject_sanitizer_diagnostics(contents.decode(errors='replace'))
        scan['sanitizer_free']=True
    if recovery.returncode: raise RuntimeError('COPYING restart recovery regression failed: '+recovery.stdout+' '+recovery.stderr)
    assert {Path(scan['path']).name for scan in record['restart_owner_stderr_scans']}=={'owner-1.stderr.log','owner-2.stderr.log'}
finally:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if client and client.poll() is None: client.kill(); client.wait(timeout=5)
    for listener in listeners:
        try: listener.close()
        except OSError: pass
    for connection in connections:
        try: connection.close()
        except OSError: pass
    for socket_path in socket_paths:
        try:
            if os.path.lexists(socket_path):
                info=socket_path.lstat()
                if socket_path.parent!=socket_dir or not stat.S_ISSOCK(info.st_mode) or info.st_uid!=0:
                    raise RuntimeError('refusing to remove an unexpected socket path')
                socket_path.unlink()
        except Exception as error: record['cleanup_socket_error']=repr(error)
    for config in config_paths: config.unlink(missing_ok=True)
    if owner and owner.poll() is None:
        os.killpg(owner.pid, signal.SIGKILL); owner.wait(timeout=5)
    try:
        cleanup=run([sys.executable,'-B',SOURCE/'tools/provision_storage.py','unmount','--bindings-dir',BUILD/'python','--name',name,'--apply'],check=False)
        volume_cleanup=json.loads(cleanup.stdout)
        record['volume_cleanup']=volume_cleanup
        if (cleanup.returncode or not volume_cleanup.get('applied') or volume_cleanup.get('mounted') is not False or
            volume_cleanup.get('status')!='unmounted' or volume_cleanup.get('loop') is not None):
            record['cleanup_volume_error']='volume unmount did not confirm applied cleanup'
    except Exception as error: record['cleanup_volume_error']=repr(error)
    try:
        remaining=[]
        for case in record['case_scopes']:
            path=Path(case['scope'])
            if path.exists():
                remaining.extend(str(group) for group in verify_empty_owned_scope(path))
            else:
                assert case.get('removed'), 'case scope disappeared without a removal receipt'
        record['empty_scope_before_unit_stop']=remaining
        record['owned_scopes_empty_before_unit_stop']=True
        for case in record['case_scopes']:
            cleanup_case_scope(case)
    except Exception as error:
        record['cleanup_scope_error']=repr(error)
    try:
        stop_unit_and_verify()
    except Exception as error: record['cleanup_unit_error']=repr(error)
    try:
        for case in record['case_scopes']:
            if Path(case['scope']).exists():
                cleanup_case_scope(case)
            elif not case.get('removed'):
                raise RuntimeError('case scope disappeared without verified removal')
        record['scope_removed']=all(case.get('removed') and not Path(case['scope']).exists()
                                    for case in record['case_scopes'])
    except Exception as error:
        record['cleanup_scope_error']=repr(error)
    try:
        if socket_dir.exists():
            children=list(socket_dir.iterdir())
            if children: raise RuntimeError(f'owned socket directory still contains entries: {children}')
            info=socket_dir.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o711:
                raise RuntimeError('refusing to remove unexpected socket directory')
            socket_dir.rmdir()
        record['socket_directory_removed'] = not socket_dir.exists()
    except Exception as error:
        record['cleanup_socket_error']=repr(error)
    try:
        if installed_helper_hash is not None:
            if os.path.lexists(helper):
                current=helper.lstat()
                if not stat.S_ISREG(current.st_mode) or current.st_uid!=0 or hashlib.sha256(helper.read_bytes()).hexdigest()!=installed_helper_hash:
                    raise RuntimeError('installed helper changed during fixture; refusing to overwrite unexpected bytes')
            if had_helper:
                restore=helper.with_name(helper.name+'.restore-'+uuid.uuid4().hex)
                restore.write_bytes(backup.read_bytes()); os.chown(restore,original_helper['uid'],original_helper['gid'])
                restore.chmod(original_helper['mode']); os.replace(restore,helper)
                restored=helper.lstat()
                if (hashlib.sha256(helper.read_bytes()).hexdigest()!=original_helper['sha256'] or
                    restored.st_uid!=original_helper['uid'] or restored.st_gid!=original_helper['gid'] or
                    stat.S_IMODE(restored.st_mode)!=original_helper['mode']):
                    raise RuntimeError('restored helper differs from the recorded original')
            elif os.path.lexists(helper):
                helper.unlink()
    except Exception as error: record['helper_restore_error']=repr(error)
    record['finished']=time.time()
    try:
        (EVIDENCE/'evidence.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
    finally:
        print('SPATIAL_COPY_PRODUCTION_RECEIPT '+json.dumps(record,sort_keys=True,separators=(',',':')),flush=True)
cleanup_errors=[key for key in record if key.startswith('cleanup_') or key=='helper_restore_error']
if cleanup_errors: raise RuntimeError('fixture cleanup failed: '+json.dumps({key:record[key] for key in cleanup_errors}))
