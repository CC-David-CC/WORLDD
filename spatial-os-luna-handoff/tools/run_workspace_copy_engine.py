"""Run the frozen M07 actor-copy checks with reviewed, temporary helper binaries."""
import ast
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
# File-based preflight importers need the adjacent, frozen profile helper too.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from copy_fixture_profiles import consume_profile, instrumentation_preflight
from with_private_test_evidence import create_private_evidence_children
from copy_fixture_image_cleanup import cleanup_engine_group, engine_group_passed, require_group_capacity, verify_previous_cleanup


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def selected_method_passed(output,method,fixture_class='WorkspaceCopyEngineTests'):
    pattern=r'^'+re.escape(method)+r' \(.+'+re.escape(fixture_class)+r'(?:\.'+re.escape(method)+r')?\) \.\.\. ok$'
    return re.search(pattern,output,re.M) is not None
def runtime_config_preflight(build):
    config=build/'runtime.textproto'
    assert config.is_file() and not config.is_symlink(),'Required runtime.textproto is missing or not a regular file'
    assert config.stat().st_size<=16384,'runtime.textproto exceeds the runtime loader limit'
    raw=config.read_bytes()
    sys.path.insert(0,str(build/'python'))
    from google.protobuf import text_format
    import core_pb2
    parsed=core_pb2.RuntimeConfig()
    text_format.Parse(raw.decode('utf-8'),parsed)
    helper_paths={name:build/name for name in ('spatial-copy-tree-worker','spatial-copy-finalize-worker')}
    for name,path in helper_paths.items():
        assert path.is_file() and not path.is_symlink() and path.stat().st_mode&0o111, 'Required copy helper is missing or not executable: '+name
    helpers={name:digest(path) for name,path in helper_paths.items()}
    return dict(path='runtime.textproto',size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),
        parsed_message='spatial.core.v1.RuntimeConfig',referenced_assets=[],helpers=helpers)
def helper_state(path):
    if not os.path.lexists(path):return None
    info=path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
    return dict(sha256=digest(path),uid=info.st_uid,gid=info.st_gid,mode=stat.S_IMODE(info.st_mode))

def main(*,fixture_spec=None):
    sys.argv[:], resource_profile = consume_profile(sys.argv)
    assert len(sys.argv) in (4,5)
    assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
    assert os.getuid()==pwd.getpwnam('dflanag3').pw_uid>0
    build,source=map(lambda value:Path(value).resolve(),sys.argv[1:3])
    evidence=(Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])/('workspace-copy-engine-'+uuid.uuid4().hex)).resolve() if sys.argv[3]=='--evidence-from-env' else Path(sys.argv[3]).resolve()
    selected_case=sys.argv[4] if len(sys.argv)==5 else None
    fixture_name='test_workspace_copy_engine.py';discovery_name='inspect_workspace_copy_fixture.py'
    fixture_class='WorkspaceCopyEngineTests';fixture_count=13;qualification='UNQUALIFIED'
    completed_status='focused-pass';runtime_bound=960
    if fixture_spec is not None:
        assert set(fixture_spec)=={'fixture','discovery','class','mode','runtime_bound'}
        fixture_name=fixture_spec['fixture'];discovery_name=fixture_spec['discovery']
        fixture_class=fixture_spec['class'];fixture_count=3;qualification='UNQUALIFIED_DIAGNOSTIC'
        completed_status='diagnostic-complete';runtime_bound=fixture_spec['runtime_bound']
        assert fixture_name=='diagnose_workspace_copy_quota.py' and discovery_name=='inspect_workspace_copy_quota_diagnostic.py'
        assert fixture_class=='QuotaDiagnosticTests' and selected_case is not None
        mode=fixture_spec['mode']
        selection={
            'nocopy':('test_no_copy_live_writers_sixty_second_diagnostic',120),
            'copy':('test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch',360),
            'smallquota':('test_small_source_quota_rejects_actual_writer_and_preserves_peer_application',120)}
        assert mode in selection
        assert (selected_case,runtime_bound)==selection[mode]
        for path in (source/'tests/core'/fixture_name,source/'tools'/discovery_name):
            assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(source)
            assert not path.stat().st_mode&0o222,'Diagnostic fixture specification requires frozen source files'
    if selected_case is not None:
        # Reject unsupported or multiple selectors before creating evidence or
        # installing helpers. Read the existing literal fixture selection only.
        assert re.fullmatch(r'test_[a-z0-9_]+',selected_case),'Invalid exact method selector'
        tree=ast.parse((source/'tests/core'/fixture_name).read_bytes())
        load=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='load_tests')
        allowed=[node.value for node in ast.walk(load) if isinstance(node,ast.Constant) and
                 isinstance(node.value,str) and node.value.startswith('test_')]
        assert selected_case in allowed,'Method is outside the existing copy fixture selection'
    assert build.is_dir() and source.is_dir() and not evidence.exists()
    assert evidence.parent.is_dir() and evidence.is_relative_to(Path.home()/'spatial-artifacts')
    capacity=require_group_capacity(evidence.parent,1 if selected_case else fixture_count)
    evidence.mkdir(mode=0o700)
    create_private_evidence_children(evidence)
    backups=evidence/'helper-backups';backups.mkdir(mode=0o700)
    report=dict(qualification=qualification,capacity_preflight=capacity,selected_methods=1 if selected_case else fixture_count,
        selected_case=selected_case,runtime_status='running',helpers={},commands=[])
    def save():(evidence/'review.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    def root_command(arguments,timeout=30):
        result=subprocess.run(['sudo','-n',*map(str,arguments)],capture_output=True,text=True,timeout=timeout)
        report['commands'].append(dict(argv=['sudo','-n',*map(str,arguments)],exit=result.returncode,stdout=result.stdout,stderr=result.stderr));save()
        assert result.returncode==0,report['commands'][-1]
        return result.stdout
    bootstrap=Path('/usr/local/libexec/spatial-bwrap')
    bootstrap_state=helper_state(bootstrap)
    assert bootstrap_state and bootstrap_state['sha256']==digest(Path('/usr/bin/bwrap'))
    assert bootstrap_state['mode']==0o755
    policy=Path('/etc/apparmor.d/spatial-bwrap')
    expected_policy=b'abi <abi/4.0>,\ninclude <tunables/global>\nprofile spatial-bwrap /usr/local/libexec/spatial-bwrap flags=(unconfined) {\n  userns,\n}\n'
    assert helper_state(policy) and policy.read_bytes()==expected_policy
    report['bootstrap']=dict(binary=bootstrap_state,profile=helper_state(policy));save()
    sys.path.insert(0,str(build/'python'))
    try:
        report['runtime_preflight']=runtime_config_preflight(build);save()
        report['resource_profile'] = instrumentation_preflight(build, resource_profile);save()
    except BaseException as error:
        report['runtime_status']='setup-failed';report['failure']=repr(error);save();raise
    import broker_pb2 as broker
    installed=[]
    child=None
    cancellations=[];previous={}
    for signum in (signal.SIGTERM,signal.SIGINT):
        previous[signum]=signal.signal(signum,lambda number,_frame:cancellations.append(number))
    try:
        discovery=subprocess.run([sys.executable,'-B',str(source/'tools'/discovery_name),str(build),str(source)],
            capture_output=True,text=True,timeout=15,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        (evidence/'fixture-discovery.stdout').write_text(discovery.stdout)
        (evidence/'fixture-discovery.stderr').write_text(discovery.stderr)
        report['fixture_discovery_exit']=discovery.returncode;save()
        if discovery.returncode:
            raise RuntimeError('Copy fixture import/discovery failed before helper installation: '+discovery.stderr[-16000:])
        discovered=json.loads(discovery.stdout);assert len(discovered['methods'])==fixture_count
        if selected_case:assert selected_case in discovered['methods']
        assert not discovered['services_started'] and discovered['methods_executed']==0
        report['fixture_discovery']=discovered;save()
        for name in ('spatial-copy-tree-worker','spatial-copy-finalize-worker'):
            target=Path('/usr/local/libexec')/name;binary=build/name
            assert binary.is_file() and binary.stat().st_mode&0o111
            before=helper_state(target);report['helpers'][name]=dict(before=before,candidate_sha256=digest(binary));save()
            if before:
                backup=backups/name;backup.write_bytes(target.read_bytes());backup.chmod(0o600)
                assert digest(backup)==before['sha256']
            # Record the planned restoration before the first privileged change.
            installed.append((target,before));save()
            root_command(['install','-o','root','-g','root','-m','755',binary,target])
            expected=dict(sha256=report['helpers'][name]['candidate_sha256'],uid=0,gid=0,mode=0o755)
            assert helper_state(target)==expected
            report['helpers'][name]['installed']=expected;save()
        command=[sys.executable,'-u','-B',str(source/'tools/with_resource_delegate.py'),'--native','--copy-engine-v2','--',
                 sys.executable,'-B',str(source/'tools/with_adapter_resources.py'),'--',sys.executable,'-B',
                 str(source/'tests/core'/fixture_name),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd'),'-v']
        if fixture_spec is not None:
            # systemd forwards a fixed environment allowlist. Place private diagnostic
            # inputs below both resource wrappers, directly before fixture Python.
            fixture_index=command.index(str(source/'tools/with_adapter_resources.py'))+2
            assert command[fixture_index-1]=='--' and command[fixture_index]==sys.executable
            command[fixture_index:fixture_index]=['env',
                'SPATIAL_COPY_QUOTA_DIAGNOSTIC_DIR='+str(evidence/'observer'),
                'SPATIAL_COPY_QUOTA_DIAGNOSTIC_MODE='+fixture_spec['mode']]
        if selected_case:command.append(fixture_class+'.'+selected_case)
        if resource_profile == 'ASan-UBSan':
            adapter_separator=command.index(str(source/'tools/with_adapter_resources.py'))+1
            assert command[adapter_separator]=='--'
            command.insert(adapter_separator,'--copy-profile=ASan-UBSan')
        environment=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',
            PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'),
            SPATIAL_SECURITY_EVIDENCE=str(evidence/'cases'),SPATIAL_SCOPE_EVIDENCE=str(evidence/'scopes'),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        if os.environ.get('SPATIAL_SCOPE_READTRACE') == '1':
            trace_path=evidence/'scope-readtrace.raw'
            descriptor=os.open(trace_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(descriptor)
            environment['SPATIAL_SCOPE_READTRACE_PATH']=str(trace_path)
            report['scope_readtrace']=dict(path=str(trace_path),maximum_bytes=65536,
                maximum_record_bytes=4096,maximum_depth=2,maximum_child_directories=8,maximum_pids=8)
        environment.pop('LSAN_OPTIONS',None)
        if fixture_spec is not None:
            observer=evidence/'observer';observer.mkdir(mode=0o700)
            report['diagnostic']=dict(mode=fixture_spec['mode'],observer_directory=str(observer),runtime_bound_seconds=runtime_bound)
        report['argv']=command;save()
        with (evidence/'stdout.log').open('wb') as stdout,(evidence/'stderr.log').open('wb') as stderr:
            child=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=environment,cwd=source)
            report['launcher_pid']=child.pid;save()
            deadline=time.monotonic()+runtime_bound
            while child.poll() is None and time.monotonic()<deadline and not cancellations:
                try:child.wait(timeout=1)
                except subprocess.TimeoutExpired:pass
            if child.poll() is None:
                # The wrapper records its exact transient unit before starting the fixture.
                units=[]
                for line in (evidence/'stdout.log').read_text(errors='replace').splitlines():
                    try:item=json.loads(line)
                    except ValueError:continue
                    unit=item.get('unit') if isinstance(item,dict) else None
                    if unit and re.fullmatch(r'spatial-m03-resources-[0-9a-f]{32}\.service',unit):units.append(unit)
                assert len(set(units))==1,'Cannot prove the owned transient unit for cancellation'
                root_command(['systemctl','stop',units[0]])
                child.wait(timeout=15)
                raise RuntimeError('Owned runtime cancelled: '+repr(cancellations or [str(runtime_bound)+'-second suite bound']))
            status=child.returncode
        report['command_exit']=status
        if fixture_spec is not None:
            try:report['observer_completion']=json.loads((evidence/'observer/observer-finish-status.json').read_text())
            except Exception as error:report['observer_completion']=dict(dataset_valid=False,diagnostic_error=repr(error))
        output=(evidence/'stdout.log').read_text(errors='replace')+(evidence/'stderr.log').read_text(errors='replace')
        service_logs=list((evidence/'cases').glob('*.log'))
        report['service_logs']={str(path.relative_to(evidence)):digest(path) for path in service_logs}
        output+=''.join(path.read_text(errors='replace') for path in service_logs)
        if status!=0:
            report['inner_failure']=dict(exit=status,stdout=str(evidence/'stdout.log'),stderr=str(evidence/'stderr.log'),
                diagnostic_tail=output[-16000:]);save()
            raise RuntimeError('Copy engine inner fixture exited '+str(status)+'; original stdout/stderr retained: '+output[-16000:])
        if fixture_spec is not None:
            assert report['observer_completion'].get('dataset_valid') is True,'Observer completion did not prove a complete diagnostic capture'
        volumes=list((evidence/'cases').glob('*/volume.json'))
        assert len(volumes)==report['selected_methods'] and all(json.loads(path.read_text())['cleanup']=='unmounted' for path in volumes)
        assert not any(marker in output for marker in ('AddressSanitizer','LeakSanitizer','UndefinedBehaviorSanitizer','runtime error:'))
        assert status==0,output[-16000:]
        assert re.search(r'Ran '+str(report['selected_methods'])+r' tests? in ',output) and '\nOK' in output,output[-16000:]
        if selected_case:
            assert selected_method_passed(output,selected_case,fixture_class), 'Exact selected method did not report PASS: '+selected_case
        report['runtime_status']=completed_status
    except BaseException as error:
        report['runtime_status']='failed-or-incomplete';report['failure']=repr(error);raise
    finally:
        # This launcher lives outside the delegated unit. Recover only exact volumes
        # declared before creation, using the root-owned persistent provisioning record.
        recovery=[];report['volume_recovery']=recovery;save()
        for inventory in sorted((evidence/'cases').glob('*/volume.json')):
            item=dict(inventory=str(inventory),confirmed=False);recovery.append(item);save()
            try:
                assert not inventory.is_symlink() and inventory.stat().st_uid==os.getuid()
                record=json.loads(inventory.read_text());name=record['name']
                assert re.fullmatch(r'm07-engine-[0-9a-f]{16}',name)
                entry=Path('/var/lib/spatial/volumes')/name
                assert record['entry']==str(entry) and record['volume']==str(entry/'mount')
                if record.get('image_cleanup'):
                    proof=verify_previous_cleanup(inventory,build/'python')
                    item.update(confirmed=True,state='verified-already-removed',receipt=proof)
                    save()
                    continue
                command=[sys.executable,'-B',str(source/'tools/provision_storage.py'),'unmount','--bindings-dir',str(build/'python'),'--name',name,'--apply']
                item['recovery_command']=['sudo','-n',*command];save()
                if not entry.exists():item.update(confirmed=True,state='not-created');continue
                # protobuf bytes are binary. Read them through a separate bounded command.
                result=subprocess.run(['sudo','-n','cat',str(entry/'record.pb')],capture_output=True,timeout=10)
                assert result.returncode==0
                persistent=broker.StorageVolumeRecord.FromString(result.stdout)
                expected=broker.StorageVolumeDefinition.FromString(bytes.fromhex(record['definition_hex']))
                assert persistent.protocol_version==1 and persistent.definition==expected and len(persistent.filesystem_id)==16
                if 'created' in record:
                    assert persistent.filesystem_id.hex()==record['created']['filesystem_id']
                item['filesystem_id']=persistent.filesystem_id.hex();save()
                result=json.loads(root_command(command,timeout=90))
                assert result['status']=='unmounted' and result['applied'] and not result['mounted'] and result['loop'] is None
                item.update(confirmed=True,state='unmounted',receipt=result)
            except BaseException as error:item['failure']=repr(error)
            save()
        restoration=[]
        for target,before in reversed(installed):
            try:
                restore=['install','-o',str(before['uid']),'-g',str(before['gid']),'-m',oct(before['mode'])[2:],backups/target.name,target] if before else ['rm','--',target]
                report['helpers'][target.name]['recovery_command']=['sudo','-n',*map(str,restore)];save()
                expected=dict(sha256=report['helpers'][target.name]['candidate_sha256'],uid=0,gid=0,mode=0o755)
                assert helper_state(target)==expected,'Current helper differs from installed candidate; refuse to overwrite'
                if before:
                    root_command(['install','-o',str(before['uid']),'-g',str(before['gid']),'-m',oct(before['mode'])[2:],backups/target.name,target])
                else:root_command(['rm','--',target])
                assert helper_state(target)==before
                restoration.append(dict(path=str(target),restored=True))
            except BaseException as error:restoration.append(dict(path=str(target),restored=False,error=repr(error)))
        report['restoration']=restoration;report['finished']=time.time();save()
        for signum,handler in previous.items():signal.signal(signum,handler)
        assert all(item['restored'] for item in restoration),'Helper restoration is incomplete; inspect exact recovery commands in review.json'
        assert all(item['confirmed'] for item in recovery),'Volume cleanup is incomplete; inspect exact recovery commands in review.json'
        if fixture_spec is None and engine_group_passed(report,completed_status,recovery,restoration):
            receipts=sorted((evidence/'cases').glob('*/volume.json'))
            try:
                report['image_cleanup']=cleanup_engine_group(receipts,build/'python',evidence/'cases',
                    report=report,completed_status=completed_status,recovery=recovery,
                    restoration=restoration,runner=subprocess.run)
            except BaseException as error:
                report['image_cleanup']={'status':'incomplete','failure':repr(error)}
                save()
                raise
            save()
    print(json.dumps(report,sort_keys=True),flush=True)

if __name__=='__main__':main()
