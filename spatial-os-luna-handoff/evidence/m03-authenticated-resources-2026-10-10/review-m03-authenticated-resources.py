"""Audit composed authenticated-resource fixture evidence."""
import ast,hashlib,json,re,subprocess,tarfile
from pathlib import Path
root=Path(__file__).resolve().parents[1];evidence=root/'evidence/m03-authenticated-resources-2026-10-10'
sha=lambda data:hashlib.sha256(data).hexdigest();read=lambda p:json.loads(p.read_bytes())
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes((json.dumps(value,indent=2)+'\n').encode())
def cases(data):
    return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(data)) if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
manifests={};snapshots={};archives={}
for candidate in ('01','02','03','04'):
    path=root/'local'/('m03-authenticated-resources-'+candidate+'-source.tar.gz');archives[candidate]=sha(path.read_bytes())
    with tarfile.open(path) as tar:
        prefix='m03-authenticated-resources-'+candidate+'/'
        manifest=json.loads(tar.extractfile(prefix+'manifest.json').read());manifests[candidate]=manifest
        snapshots[candidate]={name:tar.extractfile(prefix+'source/'+name).read() for name in manifest['files']}
    assert all(sha(data)==manifest['files'][name] for name,data in snapshots[candidate].items())
    assert len(manifest['files'])==369 and len(manifest['locked_files'])==41
    assert all(manifest['files'][name]==value for name,value in manifest['locked_files'].items())
assert {name for name in snapshots['03'] if snapshots['02'][name]!=snapshots['03'][name]}=={'tests/core/test_authenticated_resources.py'}
assert manifests['04']['files']==manifests['03']['files']
assert all(sha((root/name).read_bytes())==value for name,value in manifests['04']['files'].items())
base=manifests['03']['base_commit'];assert base=='6ebd4ebd32934e6f3360eadda0f697e193321911'
for name in ('tests/core/test_workspace_authority.py','tests/core/test_world_identity.py'):
    prior=subprocess.check_output(['git','show',base+':spatial-os-luna-handoff/'+name],cwd=root)
    assert cases(prior)==cases(snapshots['03'][name]) and len(cases(prior))==20
old=read(root/'evidence/m03-adapter-resources-2026-10-10/candidate-01/sb16/manifest.json')
assert all(manifests['03']['files'][name]==value for name,value in old['files'].items() if name.startswith(('src/','proto/')))
record=dict(status='primary_review_complete',phase='M03',phase_complete=False,base_commit=base,source_files=369,locked_files=41,source_archives=archives,passed=144,selected_per_host_profile=36,failed_final=0,skipped=0,verified_attachments=0,hosts={},unchanged_existing_test_bodies=40,qualification='Candidate04: twenty workspace-authority cases, fifteen non-native world-identity cases, and one authenticated kernel witness per host/profile. All service logs retained. Existing assertions and deadlines unchanged.',production_basis='Unchanged production source and reused m03-adapter-resources-01 binaries. Missing issuer binaries and Python bindings built from that frozen production source. CMake test registration changed; no current-source runtime rebuild required.',review='Primary agent reviewed helper ownership, actual broker PID, sealed policy, fixtures, source integrity, and raw logs. No local-model or subagent review.')
logs=[]
native={'test_disconnect_releases_real_native_input_and_keeps_application','test_credential_revocation_releases_native_input_without_another_actor_request','test_actor_revocation_releases_native_input','test_issuer_failure_cancels_live_control_and_does_not_kill_application','test_excessive_frame_closes_actor_channel_and_releases_input'}
expected={'workspace-authority':set(cases(snapshots['03']['tests/core/test_workspace_authority.py'])),'world-identity':set(cases(snapshots['03']['tests/core/test_world_identity.py']))-native,'authenticated-resources':set(cases(snapshots['03']['tests/core/test_authenticated_resources.py']))}
for host in ('sb16','sb16-2'):
    reviews={}
    for candidate in ('01','02','03','04'):
        directory=evidence/('candidate-'+candidate)/host;value=read(directory/'review.json');reviews[candidate]=value
        assert 'ended' in value and read(directory/'manifest.json')==manifests[candidate]
        for name,digest in value['attachments'].items():
            assert sha((directory/name).read_bytes())==digest;record['verified_attachments']+=1
            if name.endswith('.log'):logs.append(directory/name)
        for command in value['commands']:
            assert sha((directory/(command['label']+'.log')).read_bytes())==command['sha256']
    assert reviews['01']['status']=='failed'
    assert 'No module named' in (evidence/'candidate-01'/host/'baseline.log').read_text()
    assert reviews['02']['status']==('failed' if host=='sb16' else 'passed_review_pending')
    assert reviews['03']['status']==reviews['04']['status']=='passed_review_pending'
    assert reviews['02']['baseline']=='expected_unconfigured_authenticated_launch_denial'
    baseline=(evidence/'candidate-02'/host/'baseline.log').read_text();assert 'UNAVAILABLE' in baseline and 'FAILED (failures=1)' in baseline
    profiles={}
    for profile in ('debug','runtime-sanitize'):
        assert reviews['04']['profiles'][profile]['passed']==36 and reviews['04']['profiles'][profile]['failed']==reviews['04']['profiles'][profile]['skipped']==0
        paths=[]
        for suite,names in expected.items():
            candidate='04'
            path=evidence/('candidate-'+candidate)/host/(profile+'-'+suite+'.log');text=path.read_text()
            passed=set(re.findall(r'^(test_\w+) \([^\n]+\) \.\.\. ok$',text,re.M))
            assert passed==names,(host,profile,suite,passed^names)
            assert re.search(r'^OK$',text,re.M) and 'skipped=' not in text
            paths.append(str(path.relative_to(evidence)))
        kernel=read(evidence/'candidate-04'/host/profile/'witnesses/authenticated-kernel.json')
        assert kernel['broker_pid']!=kernel['owner_pid'] and Path(kernel['manager']).name=='broker-manager'
        for name,value in {'memory.max':'134217728','memory.swap.max':'0','pids.max':'16','cpu.max':'100000 100000'}.items():assert kernel['limits'][name]==value
        assert 'wbps=16777216' in kernel['limits']['io.max']
        assert not any('fixture-resource-policy' in name for name in kernel['broker_fds'])
        for candidate in ('04',):
            receipts=list((evidence/('candidate-'+candidate)/host/profile/'witnesses').glob('adapter-provisioning-*.json'))
            assert len(receipts)==2
            for receipt in map(read,receipts):assert receipt['scope_removed'] and not receipt['unexpected_population'] and receipt['exit_code']==0
        assert reviews['04']['profiles'][profile]['binaries']==reviews['03']['profiles'][profile]['binaries']
        profiles[profile]=dict(passed=36,failed=0,skipped=0,logs=paths,binaries=reviews['04']['profiles'][profile]['binaries'],kernel=kernel)
    cleanup=read(evidence/(host+'-final-cleanup.json'));assert len(cleanup['units'])==(19 if host=='sb16' else 13) and cleanup['ssh_recovery'] and cleanup['normal_checkout_status']==''
    assert cleanup['normal_checkout_head']=='b5902eaaad0a9d0ce3cb272d37f0d0936a6575cd'
    record['hosts'][host]=dict(profiles=profiles,unloaded_units=len(cleanup['units']),cleanup_sha256=sha((evidence/(host+'-final-cleanup.json')).read_bytes()))
for path in logs:
    assert not re.search(r'ERROR: AddressSanitizer|ERROR: LeakSanitizer|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:|LeakSanitizer has encountered a fatal error',path.read_text(errors='replace')),path
record.update(sanitizer_logs_scanned=len(logs),sanitizer_matches=0,preserved_failures=[
    'Candidate01: missing world_session_pb2 stopped baseline before tests on both hosts.',
    'Candidate02 sb16 sanitizer: one world startup EOF before an application launch. Exact server cause remains unknown.',
    'Candidate02 omitted SPATIAL_SECURITY_EVIDENCE. The failed service logs were deleted. Its suite transcript and scope receipt remain preserved.',
    'Diagnostic01: LeakSanitizer rejected ptrace. This does not explain the original EOF.',
    'Diagnostic02 and Diagnostic03 passed isolated and full workspace cases with service logs retained. These passes do not erase the original failure.'],
    review_correction='Candidate03 creates the kernel evidence directory explicitly. Candidate04 qualifies all selected checks with both evidence variables and no compiler overlap; build writeback is flushed before testing.',
    limitations=[
        'Focused checks do not complete M03 or Spatial-M1. Five native world-identity cases were not selected.',
        'Original candidate02 world startup cause remains unknown; no claim of repaired latency follows from fresh passes.',
        'Other direct-broker fixtures, operator deployment, storage quotas, broker Git/file I/O accounting, and issuer pressure recovery remain pending.',
        'No full M03 gate, native GPU, physical input, real model, or power-loss qualification ran.',
        'Production source and protobuf are unchanged. Existing production binaries are reused. Current CMake registration is reviewed; selected suites ran through explicit CLI commands.',
        'Five-second production timeout, short fault fixtures, and database durability remain unchanged.',
        'Normal host checkouts remain unchanged. No operator runtime restarted.'],
    next=['Implement the remaining M03 scope as one larger coherent increment.',
          'Run related regressions over the combined change and complete the full M03 phase gate on both hosts.',
          'Isolate reproducible regressions using source history, then proceed to M04.'])
write(evidence/'primary-review.json',record)
for name in ('review-m03-authenticated-resources.py','check-m03-authenticated-cleanup.py','collect-m03-authenticated-resources.py'):(evidence/name).write_bytes((root/'local'/name).read_bytes())
print(json.dumps({name:record[name] for name in ('status','passed','verified_attachments','sanitizer_logs_scanned')}))
