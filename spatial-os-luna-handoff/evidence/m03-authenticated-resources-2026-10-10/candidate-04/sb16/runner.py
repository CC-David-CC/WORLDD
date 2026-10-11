"""Qualify authenticated fixture provisioning using unchanged production binaries."""
import fcntl,hashlib,json,os,re,socket,subprocess,time
from pathlib import Path
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m03-authenticated-resources-04';source=root/'source';prior=base/'m03-adapter-resources-01'
lock=(base/'m02-phase-qualification.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert not (root/'review.json').exists()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
manifest=json.loads((root/'manifest.json').read_bytes());old=json.loads((prior/'manifest.json').read_bytes())
assert all(sha(source/name)==value for name,value in manifest['files'].items())
assert all(manifest['files'][name]==value for name,value in manifest['locked_files'].items())
assert all(manifest['files'][name]==value for name,value in old['files'].items() if name.startswith(('src/','proto/')))
record=dict(host=host,status='running',started=time.time(),commands=[],profiles={},phase_complete=False,
    manifest_sha256=sha(root/'manifest.json'),runner_sha256=sha(Path(__file__)),production_basis='m03-adapter-resources-01',attachments={})
def save():
    (root/'review.json').write_text(json.dumps(record,indent=2)+'\n')
def run(label,command,env=None,expected=0,timeout=240):
    item=dict(label=label,argv=command,started=time.time());record['commands'].append(item);save()
    with (root/(label+'.log')).open('xb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
    item.update(exit_code=result.returncode,ended=time.time(),sha256=sha(root/(label+'.log')));save()
    text=(root/(label+'.log')).read_text(errors='replace')
    if result.returncode!=expected:
        print('\n'.join(text.splitlines()[-90:]),flush=True);raise RuntimeError(label+' failed')
    return text
native={'test_disconnect_releases_real_native_input_and_keeps_application','test_credential_revocation_releases_native_input_without_another_actor_request','test_actor_revocation_releases_native_input','test_issuer_failure_cancels_live_control_and_does_not_kill_application','test_excessive_frame_closes_actor_channel_and_releases_input'}
import ast
world_cases=sorted(node.name for node in ast.walk(ast.parse((source/'tests/core/test_world_identity.py').read_bytes())) if isinstance(node,ast.FunctionDef) and node.name.startswith('test_') and node.name not in native)
assert len(world_cases)==15
try:
    for profile in ('debug','runtime-sanitize'):
        build=prior/profile/'build';env=dict(os.environ)
        for name in ('DISPLAY','SPATIAL_TEST_DISPLAY','SPATIAL_TEST_BROKER_CONFIG','LSAN_OPTIONS'):env.pop(name,None)
        env.update(PYTHONPATH=str(build/'python')+':'+str(source),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',SPATIAL_SCOPE_EVIDENCE=str(root/profile/'witnesses'),SPATIAL_SECURITY_EVIDENCE=str(root/profile/'witnesses'))
        run(profile+'-build',['cmake','--build',str(build),'--target','identityd','spatial-issuer-client','world-session-python-protobuf','key-python-protobuf','issuer-python-protobuf','grants-python-protobuf','--parallel','2'],env,timeout=600)
        run(profile+'-sync',['sync','-f',str(root)],timeout=60)
        args=[str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]
        wrapper=['python3',str(source/'tools/with_resource_delegate.py'),'--native','--','python3',str(source/'tools/with_adapter_resources.py'),'--']
        if profile=='debug':
            baselineenv=dict(env,PYTHONPATH=str(build/'python')+':'+str(prior/'source'),SPATIAL_SCOPE_EVIDENCE=str(root/'baseline-witnesses'),SPATIAL_SECURITY_EVIDENCE=str(root/'baseline-witnesses'))
            text=run('baseline',['python3',str(source/'tools/with_resource_delegate.py'),'--native','--','python3',str(prior/'source/tests/core/test_workspace_authority.py'),*args,'WorkspaceAuthorityTests.test_authenticated_workspace_selection_and_confined_process'],baselineenv,expected=1)
            assert 'UNAVAILABLE' in text and 'FAILED (failures=1)' in text
            record['baseline']='expected_unconfigured_authenticated_launch_denial';save()
        count=0
        for suite,file,extra,expected in [('workspace-authority','test_workspace_authority.py',[],20),('authenticated-resources','test_authenticated_resources.py',[],1),('world-identity','test_world_identity.py',['WorldIdentityTests.'+name for name in world_cases],15)]:
            command=['python3',str(source/'tests/core'/file),*args,*extra]
            text=run(profile+'-'+suite,wrapper+command if suite!='world-identity' else command,env)
            assert re.search(r'Ran '+str(expected)+r' tests? in ',text) and re.search(r'^OK$',text,re.M) and 'skipped=' not in text
            count+=expected
        assert count==36
        record['profiles'][profile]=dict(passed=count,failed=0,skipped=0,binaries={name:sha(build/name) for name in ('identityd','spatial-issuer-client','worldd','workspaced','spatial-process-scope-probe')})
        save();print(host,profile,'passed',count,flush=True)
    record['status']='passed_review_pending'
except BaseException as error:
    record.update(status='failed',error=repr(error));raise
finally:
    record['ended']=time.time()
    record['attachments']={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file() and 'source' not in p.relative_to(root).parts and p.name!='review.json' and p.suffix in ('.json','.log','.py','.textproto')}
    save();print(host,record['status'],flush=True)
