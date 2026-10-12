"""Measure the preserved startup case with service logs and sync-only tracing."""
import fcntl,hashlib,json,os,socket,subprocess,time
from pathlib import Path
assert socket.gethostname().split('.')[0]=='sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local');root=base/'m03-authenticated-startup-diagnostic-01'
assert not root.exists();root.mkdir()
lock=(base/'m02-phase-qualification.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
source=base/'m03-authenticated-resources-03/source';build=base/'m03-adapter-resources-01/runtime-sanitize/build'
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
record=dict(host='sb16',status='running',started=time.time(),source_basis='m03-authenticated-resources-03',source_manifest_sha256=sha(base/'m03-authenticated-resources-03/manifest.json'),runner_sha256=sha(Path(__file__)),commands=[],trials=[],qualification=False)
def save():(root/'review.json').write_text(json.dumps(record,indent=2)+'\n')
try:
    subprocess.run(['sync','-f',str(root)],check=True,timeout=60)
    for number in range(3):
        trial=root/('trial-'+str(number+1));trial.mkdir();env=dict(os.environ)
        for name in ('DISPLAY','SPATIAL_TEST_DISPLAY','SPATIAL_TEST_BROKER_CONFIG','LSAN_OPTIONS'):env.pop(name,None)
        env.update(PYTHONPATH=str(build/'python')+':'+str(source),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',SPATIAL_SCOPE_EVIDENCE=str(trial/'witnesses'),SPATIAL_SECURITY_EVIDENCE=str(trial/'witnesses'))
        command=['python3',str(source/'tools/with_resource_delegate.py'),'--native','--','python3',str(source/'tools/with_adapter_resources.py'),'--','strace','-f','-ttt','-T','-e','trace=fsync,fdatasync','-o',str(trial/'sync.log'),'python3',str(source/'tests/core/test_workspace_authority.py'),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd'),'WorkspaceAuthorityTests.test_grant_revocation_kills_confined_process_without_recipient_request']
        item=dict(argv=command,started=time.time());record['commands'].append(item);save()
        with (trial/'case.log').open('xb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120)
        item.update(exit_code=result.returncode,ended=time.time());record['trials'].append(dict(number=number+1,exit_code=result.returncode));save();print('sb16 diagnostic trial',number+1,'exit',result.returncode,flush=True)
    record['status']='complete_diagnostic'
except BaseException as error:record.update(status='failed_diagnostic',error=repr(error));raise
finally:
    record['ended']=time.time();record['attachments']={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file() and p.name!='review.json' and p.suffix in ('.json','.log')};save()
