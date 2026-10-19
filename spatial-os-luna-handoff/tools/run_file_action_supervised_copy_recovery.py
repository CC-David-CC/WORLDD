"""Run the supervised file-action copy crash/recovery fixture with reviewed, temporary helper binaries."""
import ast
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
# File-based preflight importers need the adjacent, frozen profile helper too.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from copy_fixture_profiles import consume_profile, instrumentation_preflight


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
HELPER_LIFECYCLE_SHA256 = "128128ad18ca42d6629716279b3468a6681847bb86b0c2141eae35ce3b19b249"


def runtime_command(build, source, profile, selected_case, evidence):
    if profile not in ('Debug', 'ASan-UBSan'):
        raise ValueError('Unsupported explicit copy profile')
    if not re.fullmatch(r'test_[a-z0-9_]+', selected_case):
        raise ValueError('Invalid exact method selector')
    command = [sys.executable, '-B', str(source/'tools/with_resource_delegate.py'),
        '--native', '--copy-engine-v2', '--', sys.executable, '-B',
        str(source/'tools/with_adapter_resources.py')]
    if profile == 'ASan-UBSan':
        command.append('--copy-profile=ASan-UBSan')
    command += ['--', sys.executable, '-B',
        str(source/'tests/core/test_file_action_supervised_copy_recovery.py'),
        str(build/'identityd'), str(build/'spatial-issuer-client'), str(build/'worldd'),
        '-v', 'SupervisedFileActionCopyRecoveryTests.'+selected_case]
    return [sys.executable, '-B', str(source/'tools/with_copy_worker_helpers.py'),
        '--build', str(build), '--profile', profile,
        '--evidence', str(evidence/'helper-lifecycle'), '--', *command]


def capacity_plan(build, available):
    # Existing packages need no transfer allocation. Count helper backups,
    # the fixture volume, diagnostic output, and a separate free-space reserve.
    helpers = sum((build/name).stat().st_size for name in
        ('spatial-copy-tree-worker', 'spatial-copy-finalize-worker'))
    projected = 512*2**20 + helpers + 256*2**20
    reserve = 512*2**20
    if available < projected + reserve:
        raise RuntimeError('Insufficient capacity for projected fixture output and reserve')
    return dict(available_bytes=available, projected_bytes=projected,
        reserve_bytes=reserve, helper_backup_bytes=helpers, volume_bytes=512*2**20,
        diagnostic_budget_bytes=256*2**20)


def validate_selector(source, selected_case):
    if not re.fullmatch(r'test_[a-z0-9_]+', selected_case):
        raise ValueError('Invalid exact method selector')
    tree=ast.parse((source/'tests/core/test_file_action_supervised_copy_recovery.py').read_bytes())
    fixture=next(node for node in tree.body if isinstance(node,ast.ClassDef) and
                 node.name=='SupervisedFileActionCopyRecoveryTests')
    allowed=[node.name for node in fixture.body if isinstance(node,ast.FunctionDef) and
             node.name.startswith('test_')]
    if selected_case not in allowed:
        raise ValueError('Method is outside the supervised copy fixture selection')



def validate_unittest_result(output, selected_case, expected_count):
    if expected_count != 1:
        raise RuntimeError('This launcher requires exactly one selected method')
    summaries=list(re.finditer(r'(?m)^Ran \d+ tests? in [0-9]+(?:\.[0-9]+)?s$',output))
    if len(summaries)!=1:
        raise RuntimeError('Selected fixture unittest summary missing or duplicated')
    summary=summaries[0]
    terminal=output[summary.start():]
    epilogue=(r'Ran 1 tests? in [0-9]+(?:\.[0-9]+)?s\n\nOK\n+'
        r'Finished with result: success\n'
        r'Main processes terminated with: code=exited/status=0\n'
        r'Service runtime: [0-9]+(?:\.[0-9]+)?s\n'
        r'CPU time consumed: [0-9]+(?:\.[0-9]+)?s\n'
        r'Memory peak: [0-9]+(?:\.[0-9]+)?(?:B|K|M|G|T)\n'
        r'Memory swap peak: [0-9]+(?:\.[0-9]+)?(?:B|K|M|G|T)\n*')
    if not re.fullmatch(epilogue,terminal):
        raise RuntimeError('Selected fixture did not end in the recognized successful systemd result')
    body=output[:summary.start()]
    separators=list(re.finditer(r'(?m)^-{10,}$',body))
    if len(separators)!=1:
        raise RuntimeError('Selected fixture unittest separator missing or duplicated')
    body=body[:separators[0].start()]
    headers=list(re.finditer(r'(?m)^(test_[A-Za-z0-9_]+) \(([^)]+)\) \.\.\.',body))
    if len(headers)!=1 or headers[0].group(1)!=selected_case:
        raise RuntimeError('Selected fixture method header missing or duplicated')
    header=headers[0]
    if not re.fullmatch(r'(?:__main__\.)?SupervisedFileActionCopyRecoveryTests(?:\.'+re.escape(selected_case)+r')?',header.group(2)):
        raise RuntimeError('Unexpected fixture class')
    result=body[header.end():].strip()
    if result.splitlines()[-1:]!=['ok'] or re.search(r'(?m)^\s*(?:FAIL|ERROR)(?:\b|$)',result):
        raise RuntimeError('Selected fixture method result is not exactly successful')
    return True


def validate_helper_cleanup(helper_report):
    if not isinstance(helper_report,dict):
        raise RuntimeError('Helper lifecycle receipt is missing or malformed')
    child=helper_report.get('child_process')
    if helper_report.get('cleanup_status')!='confirmed':
        raise RuntimeError('Helper cleanup is unconfirmed')
    if not isinstance(child,dict) or child.get('process_group_empty') is not True:
        raise RuntimeError('Owned child retirement is missing or unconfirmed')
    return True


def validate_runner_controls():
    method='test_sigkill_checked_retirement_and_restart_reconcile_same_copy_journal'
    positive=(method+' (__main__.SupervisedFileActionCopyRecoveryTests.'+method+') ... ok\n'
        '\n----------------------------------------------------------------------\nRan 1 test in 11.375s\n\nOK\n\n'
        'Finished with result: success\nMain processes terminated with: code=exited/status=0\n'
        'Service runtime: 11.693s\nCPU time consumed: 1.657s\nMemory peak: 2.5M\nMemory swap peak: 0B\n\n')
    validate_unittest_result(positive,method,1)
    interleaved=positive.replace('... ok\n','... Healthy issuer timing {}\nhelper progress\nok\n',1).replace('OK\n\nFinished','OK\nFinished')
    validate_unittest_result(interleaved,method,1)
    for bad in (positive.replace('Finished with result: success','Finished with result: exit-code',1),
                positive.replace('Ran 1 test in','Ran 2 tests in',1),
                positive.replace('... ok\n','... not ok\n',1), positive+'unexpected output\n',
                positive.replace('status=0','status=1'), positive.replace('OK\n\nFinished','FAILED\n\nFinished')):
        try: validate_unittest_result(bad,method,1)
        except RuntimeError: pass
        else: raise RuntimeError('Unittest parser accepted a failure control')
    validate_helper_cleanup({'cleanup_status':'confirmed','child_process':{'process_group_empty':True}})
    for bad in ({'cleanup_status':'confirmed','child_process':{}},
                {'cleanup_status':'confirmed','child_process':{'process_group_empty':False}},
                {'cleanup_status':'unconfirmed','child_process':{'process_group_empty':True}}):
        try: validate_helper_cleanup(bad)
        except RuntimeError: pass
        else: raise RuntimeError('Helper cleanup control accepted missing or failed cleanup')

def main():
    sys.argv[:], resource_profile = consume_profile(sys.argv)
    assert len(sys.argv)==5,'An exact single-case selector is required'
    assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
    assert os.getuid()==pwd.getpwnam('dflanag3').pw_uid>0
    build,source=map(lambda value:Path(value).resolve(),sys.argv[1:3])
    evidence=(Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])/('supervised-copy-recovery-'+uuid.uuid4().hex)).resolve() if sys.argv[3]=='--evidence-from-env' else Path(sys.argv[3]).resolve()
    assert os.environ.get('SPATIAL_SECURITY_TEST_CORE_ONLY') != '1', 'Native graphics closure is required'
    native_wrapper=Path(os.environ['SPATIAL_TEST_XSERVER']).resolve(strict=True)
    assert native_wrapper.is_file() and os.access(native_wrapper,os.X_OK)
    selected_case=sys.argv[4]
    validate_selector(source,selected_case)
    validate_runner_controls()
    assert build.is_dir() and source.is_dir() and not evidence.exists()
    assert evidence.parent.is_dir() and evidence.is_relative_to(Path.home()/'spatial-artifacts')
    python_fixture_sources=(
        'tests/core/test_file_action_supervised_copy_recovery.py',
        'tests/core/test_supervised_copy_recovery_adapter_contract.py',
        'tests/core/test_workspace_copy_engine.py',
        'tests/core/test_workspace_authority.py',
        'tests/core/test_workspace_copy_faults.py',
        'tests/native/supervised_copy_broker_recovery.py',
        'tests/native/supervised_delegation_parent.py',
        'tests/native/copy_fault_supervised_world.py',
        'tests/native/storage_owner_copy_fault_relay.py',
        'tests/native/test_security_identity.py',
        'tests/native/display_ready.py',
        'tests/native/native_display_geometry.py',
        'harness/copy_fault_evidence.py',
        'harness/issuer_progress.py',
        'harness/issuer_relay.py',
        'harness/issuer_launch_denial.py',
        'harness/wire.py',
        'tools/copy_fixture_profiles.py',
        'tools/inspect_file_action_supervised_copy_recovery.py',
        'tools/with_resource_delegate.py',
        'tools/with_adapter_resources.py',
        'tools/with_copy_worker_helpers.py',
    )
    report_sources={}
    for relative in python_fixture_sources:
        path=source/relative
        assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(source)
        report_sources[relative]=digest(path)
    assert report_sources['tools/with_copy_worker_helpers.py']==HELPER_LIFECYCLE_SHA256
    capacity = capacity_plan(build, os.statvfs(evidence.parent).f_bavail*os.statvfs(evidence.parent).f_frsize)
    volume_root=Path('/var/lib/spatial/volumes')
    capacity['volume_filesystem']=capacity_plan(build, os.statvfs(volume_root).f_bavail*os.statvfs(volume_root).f_frsize)
    evidence.mkdir(mode=0o700)
    report=dict(qualification='UNQUALIFIED',selected_methods=1,
        selected_case=selected_case,runtime_status='running',commands=[],capacity=capacity,
        python_fixture_sources=report_sources,native_wrapper=dict(path=str(native_wrapper),sha256=digest(native_wrapper)))
    def save():(evidence/'review.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    def root_command(arguments,timeout=30):
        result=subprocess.run(['sudo','-n',*map(str,arguments)],capture_output=True,text=True,timeout=timeout)
        report['commands'].append(dict(argv=['sudo','-n',*map(str,arguments)],exit=result.returncode,stdout=result.stdout,stderr=result.stderr));save()
        assert result.returncode==0,report['commands'][-1]
        return result.stdout
    sys.path.insert(0,str(build/'python'))
    try:
        extra=('spatial-authority-supervisor',) if selected_case=='test_sigkill_checked_retirement_and_restart_reconcile_same_copy_journal' else ()
        report['resource_profile'] = instrumentation_preflight(build, resource_profile,additional_binaries=extra);save()
        if extra:
            closure=subprocess.run([sys.executable,'-B',str(source/'tools/inspect_workspace_copy_supervisor_closure.py'),
                '--build',str(build),'--source',str(source),'--profile',resource_profile],capture_output=True,text=True,timeout=30)
            (evidence/'supervisor-closure.stdout').write_text(closure.stdout)
            (evidence/'supervisor-closure.stderr').write_text(closure.stderr)
            report['supervisor_closure_exit']=closure.returncode;save()
            if closure.returncode:raise RuntimeError('Lost-ACK supervisor closure preflight failed: '+closure.stderr[-4000:])
            report['supervisor_closure']=json.loads(closure.stdout);save()
    except BaseException as error:
        report['runtime_status']='setup-failed';report['failure']=repr(error);save();raise
    import broker_pb2 as broker
    child=None
    cancellations=[];previous={}
    for signum in (signal.SIGTERM,signal.SIGINT):
        previous[signum]=signal.signal(signum,lambda number,_frame:cancellations.append(number))
    try:
        discovery=subprocess.run([sys.executable,'-B',str(source/'tools/inspect_file_action_supervised_copy_recovery.py'),str(build),str(source)],
            capture_output=True,text=True,timeout=15,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        (evidence/'fixture-discovery.stdout').write_text(discovery.stdout)
        (evidence/'fixture-discovery.stderr').write_text(discovery.stderr)
        report['fixture_discovery_exit']=discovery.returncode;save()
        if discovery.returncode:
            raise RuntimeError('Copy fixture import/discovery failed before helper installation: '+discovery.stderr[-16000:])
        discovered=json.loads(discovery.stdout);assert len(discovered['methods'])==1
        if selected_case:assert selected_case in discovered['methods']
        assert not discovered['services_started'] and discovered['methods_executed']==0
        report['fixture_discovery']=discovered;save()
        command=runtime_command(build,source,resource_profile,selected_case,evidence)
        environment=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',
            PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'),
            SPATIAL_SECURITY_EVIDENCE=str(evidence/'cases'),SPATIAL_SCOPE_EVIDENCE=str(evidence/'scopes'),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        environment.pop('LSAN_OPTIONS',None)
        report['argv']=command;save()
        with (evidence/'stdout.log').open('wb') as stdout,(evidence/'stderr.log').open('wb') as stderr:
            child=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=environment,cwd=source)
            report['launcher_pid']=child.pid;save()
            deadline=time.monotonic()+600
            output_budget_exceeded=False
            while child.poll() is None and time.monotonic()<deadline and not cancellations:
                try:child.wait(timeout=1)
                except subprocess.TimeoutExpired:pass
                diagnostic_bytes=sum(path.stat().st_size for path in evidence.rglob('*') if path.is_file())
                if diagnostic_bytes>capacity['diagnostic_budget_bytes']+capacity['helper_backup_bytes']:
                    output_budget_exceeded=True
                    break
            if child.poll() is None:
                # The wrapper records its exact transient unit before starting the fixture.
                units=[]
                for line in (evidence/'stdout.log').read_text(errors='replace').splitlines():
                    try:item=json.loads(line)
                    except ValueError:continue
                    unit=item.get('unit') if isinstance(item,dict) else None
                    if unit and re.fullmatch(r'spatial-m03-resources-[0-9a-f]{32}\.service',unit):units.append(unit)
                assert len(set(units))<=1,'Ambiguous owned transient unit for cancellation'
                if units:root_command(['systemctl','stop',units[0]])
                child.send_signal(signal.SIGTERM)
                child.wait(timeout=60)
                raise RuntimeError('Owned runtime cancelled: '+repr(cancellations or ['diagnostic budget exceeded' if output_budget_exceeded else '600-second supervised-copy-recovery bound']))
            status=child.returncode
        report['command_exit']=status
        output=(evidence/'stdout.log').read_text(errors='replace')+(evidence/'stderr.log').read_text(errors='replace')
        service_logs=list((evidence/'cases').glob('*.log'))
        report['service_logs']={str(path.relative_to(evidence)):digest(path) for path in service_logs}
        output+=''.join(path.read_text(errors='replace') for path in service_logs)
        if status!=0:
            report['inner_failure']=dict(exit=status,stdout=str(evidence/'stdout.log'),stderr=str(evidence/'stderr.log'),
                diagnostic_tail=output[-16000:]);save()
            raise RuntimeError('Copy engine inner fixture exited '+str(status)+'; original stdout/stderr retained: '+output[-16000:])
        volumes=list((evidence/'cases').glob('*/volume.json'))
        assert len(volumes)==report['selected_methods'] and all(json.loads(path.read_text())['cleanup']=='unmounted' for path in volumes)
        assert not any(marker in output for marker in ('AddressSanitizer','LeakSanitizer','UndefinedBehaviorSanitizer','runtime error:'))
        assert status==0,output[-16000:]
        validate_unittest_result((evidence/'stderr.log').read_text(errors='replace'),selected_case,report['selected_methods'])
        report['verified_named_method']=selected_case
        report['runtime_status']='focused-pass-unqualified'
    except BaseException as error:
        report['runtime_status']='failed-or-incomplete';report['failure']=repr(error);raise
    finally:
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=60)
        lifecycle=evidence/'helper-lifecycle/lifecycle.json'
        if lifecycle.exists():
            helper_report=json.loads(lifecycle.read_text())
            report['helper_lifecycle']=helper_report
            save()
            validate_helper_cleanup(helper_report)
        elif child is not None:
            raise RuntimeError('Missing helper lifecycle receipt; refuse volume recovery')
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
        report['finished']=time.time();save()
        for signum,handler in previous.items():signal.signal(signum,handler)
        assert all(item['confirmed'] for item in recovery),'Volume cleanup is incomplete; inspect review.json'
    print(json.dumps(report,sort_keys=True),flush=True)

if __name__=='__main__':main()
