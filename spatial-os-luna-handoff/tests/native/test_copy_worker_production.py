"""Production storage-owner and ProcessScope copy-stage witness for approved world hosts."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import sys
import time
import uuid

assert os.getuid() == 0 and socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
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
record = {'host': socket.gethostname(), 'source': str(SOURCE), 'build': str(BUILD), 'commands': [], 'witnesses': []}
unit = 'spatial-m07-copy-'+uuid.uuid4().hex+'.service'
socket_dir = Path('/run') / ('s-m07-'+uuid.uuid4().hex[:12])
scope = None; owner = None; client = None; helper = Path('/usr/local/libexec/spatial-copy-tree-worker')
original_helper = None; installed_helper_hash = None; config_paths = []
backup = EVIDENCE / 'prior-copy-worker'
had_helper = os.path.lexists(helper)


def run(argv, timeout=120, check=True):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=timeout)
    record['commands'].append({'argv': list(map(str, argv)), 'exit': result.returncode,
                               'stdout': result.stdout, 'stderr': result.stderr})
    if check and result.returncode: raise RuntimeError(f'{argv}: {result.stdout}\n{result.stderr}')
    return result


def start_owner_client(mode):
    global owner, client
    socket_path = socket_dir / ('o-'+mode+'.sock')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path)); os.chmod(socket_path, 0o666); listener.listen(1); listener.settimeout(10)
    wrapper = ('import os,socket,sys; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(sys.argv[1]); '
               'fd=s.detach(); os.set_inheritable(fd,True); os.execv(sys.argv[2],[sys.argv[2],str(fd),*sys.argv[3:]])')
    def enter_scope():
        (scope/'cgroup.procs').write_text(str(os.getpid())+'\n')
        os.setgroups([]); os.setgid(gid); os.setuid(uid)
    client = subprocess.Popen([sys.executable, '-c', wrapper, str(socket_path), str(BUILD/'spatial-copy-worker-production-probe'),
                              str(volume), str(EVIDENCE/'resource.textproto'), mode],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             preexec_fn=enter_scope)
    connection, _ = listener.accept(); listener.close()
    configuration = storage.StorageOwnerConfig.FromString((entry/'owner.pb').read_bytes())
    configuration.broker_uid = uid; configuration.broker_pid = client.pid
    config_path = Path('/run')/('m07-copy-owner-'+uuid.uuid4().hex+'.pb')
    config_path.write_bytes(configuration.SerializeToString(deterministic=True)); config_path.chmod(0o600); config_paths.append(config_path)
    owner = subprocess.Popen([str(BUILD/'spatial-storage-owner'), '--channel-fd', str(connection.fileno()),
                              '--config', str(config_path)], pass_fds=[connection.fileno()],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    connection.close()
    started = time.monotonic()
    samples = []
    while client.poll() is None:
        for group in scope.glob('authority-*'):
            try:
                sample = {key:(group/key).read_text().strip() for key in ('memory.max','pids.max','cpu.max')}
                children = (group/'cgroup.procs').read_text().split()
                for child in children:
                    pid = int(child)
                    try:
                        executable = os.readlink(f'/proc/{pid}/exe')
                        if Path(executable).name != 'spatial-copy-tree-worker': continue
                        sample['worker_pid'] = pid
                        sample['worker_cgroup'] = str(group)
                        sample['private_netns'] = os.readlink(f'/proc/{pid}/ns/net') != os.readlink(f'/proc/{client.pid}/ns/net')
                        samples.append(sample)
                        break
                    except (FileNotFoundError, ProcessLookupError): pass
            except (FileNotFoundError, ProcessLookupError): pass
        if time.monotonic()-started > 180:
            client.kill(); raise TimeoutError('copy probe exceeded 180 seconds')
        time.sleep(.001)
    stdout, stderr = client.communicate(timeout=5)
    owner_stderr = owner.communicate(timeout=10)[1]
    result = {'mode':mode,'exit':client.returncode,'stdout':stdout,'stderr':stderr,'owner_stderr':owner_stderr,'scopes':samples}
    record['witnesses'].append(result)
    if client.returncode: raise RuntimeError(json.dumps(result, indent=2))
    if owner.returncode: raise RuntimeError('Storage owner failed: '+owner_stderr)
    if mode=='success':
        assert 'retry=identical inode=independent' in stdout
        assert any(sample.get('memory.max')==str(256*2**20) and sample.get('pids.max')=='16' and
                   sample.get('cpu.max')=='200000 100000' for sample in samples), samples
        assert any(sample.get('worker_pid') and sample.get('private_netns') for sample in samples), samples
        assert any(sample.get('worker_pid') and sample.get('worker_cgroup') for sample in samples), samples
    if mode=='symlink': assert 'SYMLINK_REJECTED UNCERTAIN' in stdout
    config_path.unlink(missing_ok=True); socket_path.unlink(missing_ok=True)


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
    record['package_sha256']={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*') if p.is_file()}
    template=broker.StorageVolumeDefinition(); text_format.Parse((SOURCE/'config/operator-storage.textproto').read_text(),template)
    template.name=name; policy=template.owner_policy; policy.broker_uid=uid
    policy.volume_bytes=512*2**20; policy.control_plane_bytes=64*2**20; policy.filesystem_reserve_bytes=64*2**20
    policy.default_policy.total_bytes=128*2**20; policy.default_policy.actor_state_default_bytes=4*2**20
    for profile in policy.default_policy.profiles: profile.state_bytes=4*2**20
    definition=EVIDENCE/'definition.textproto'; definition.write_text(text_format.MessageToString(template))
    run([sys.executable,SOURCE/'tools/provision_storage.py','create','--bindings-dir',BUILD/'python',
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
    scope=delegate/('broker-'+uuid.uuid4().hex); scope.mkdir(); os.chown(scope,uid,gid); os.chmod(scope,0o700)
    runtime.resources.delegated_cgroup=str(scope)
    resource_path=EVIDENCE/'resource.textproto'; resource_path.write_text(text_format.MessageToString(runtime.resources))
    os.chown(resource_path,uid,gid); resource_path.chmod(0o600)
    io_limits=list(runtime.resources.manager_budget.io)
    assert io_limits
    record.update(volume=str(volume),unit=unit,delegate=str(delegate),scope=str(scope),resource_config=str(resource_path))
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
    start_owner_client('success')
    start_owner_client('symlink')
    restart_evidence=EVIDENCE/'owner-recovery'; restart_evidence.mkdir(mode=0o700)
    restart_env=dict(os.environ,SPATIAL_SECURITY_EVIDENCE=str(restart_evidence))
    recovery=subprocess.run([sys.executable,SOURCE/'tests/core/test_storage_snapshot.py',BUILD/'spatial-storage-owner-probe',SOURCE,'-k','copy_stage_claim_is_digest_bound_and_restart_is_uncertain'],capture_output=True,text=True,timeout=300,env=restart_env)
    record['restart_recovery']={'exit':recovery.returncode,'stdout':recovery.stdout,'stderr':recovery.stderr}
    if recovery.returncode: raise RuntimeError('COPYING restart recovery regression failed: '+recovery.stdout+' '+recovery.stderr)
finally:
    if client and client.poll() is None: client.kill(); client.wait(timeout=5)
    for config in config_paths: config.unlink(missing_ok=True)
    if owner and owner.poll() is None: owner.kill(); owner.wait(timeout=5)
    try:
        cleanup=run([sys.executable,SOURCE/'tools/provision_storage.py','unmount','--bindings-dir',BUILD/'python','--name',name],check=False)
        if cleanup.returncode: record['cleanup_volume_error']='volume unmount command returned nonzero'
    except Exception as error: record['cleanup_volume_error']=repr(error)
    try:
        if scope and scope.exists(): scope.rmdir()
    except Exception as error: record['cleanup_scope_error']=repr(error)
    try: run(['systemctl','stop',unit],timeout=20,check=False)
    except Exception as error: record['cleanup_unit_error']=repr(error)
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
    (EVIDENCE/'evidence.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
cleanup_errors=[key for key in record if key.startswith('cleanup_') or key=='helper_restore_error']
if cleanup_errors: raise RuntimeError('fixture cleanup failed: '+json.dumps({key:record[key] for key in cleanup_errors}))
