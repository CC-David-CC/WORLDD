"""Collect completed owner qualification profiles. Never promote a running profile."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import tarfile
import time

parser=argparse.ArgumentParser()
parser.add_argument('--candidate',choices=('03','04','binding01','reply01','handoff01','admin01','handle01'),required=True)
parser.add_argument('--mode',choices=('debug','final'),required=True)
args=parser.parse_args()
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
binding_candidate=args.candidate=='binding01'
reply_candidate=args.candidate=='reply01'
handoff_candidate=args.candidate=='handoff01'
admin_candidate=args.candidate=='admin01'
handle_candidate=args.candidate=='handle01'
candidate_name=('retained-child-handle-01' if handle_candidate else 'control-admin-01' if admin_candidate else 'supervisor-handoff-01' if handoff_candidate else 'control-reply-01' if reply_candidate else
                'control-binding-01' if binding_candidate else 'control-owner-'+args.candidate)
root=r/'local'/('m02-'+candidate_name)
out=root/'qualification'
manifest=json.loads((root/'manifest.json').read_text())
raw_summary=(out/'summary.json').read_bytes()
run=json.loads(raw_summary)
assert run['host']==host and run['source_manifest']==manifest['files']
source_count=311 if handle_candidate else 309 if admin_candidate else 304 if handoff_candidate else 303 if reply_candidate else 298 if binding_candidate else 297
assert len(manifest['files'])==source_count and len(manifest['locked_files'])==41
assert not run['compiler_overlap']
assert run['status']=='passed' if args.mode=='final' else run['status'] in ('qualifying','passed')
profiles=('debug','runtime-sanitize') if args.mode=='final' else ('debug',)
expected_owners=19 if args.candidate=='03' else 21
result=dict(host=host,candidate=args.candidate,mode=args.mode,source_files=source_count,locked_files=41,
            source_manifest=manifest['files'],profiles={},collected=time.time(),
            integration_ready=False,production_recovery_qualified=False)
archive=r/'local'/f'control-owner-qualified-{args.candidate}-{args.mode}-{host}.tar.gz'
assert not archive.exists(), 'Preserve the existing collection; do not overwrite it'
attachments={}

def sha(data):return hashlib.sha256(data).hexdigest()
def collect(path,name=None):
    data=path.read_bytes()
    assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data, path
    attachments[name or path.relative_to(out).as_posix()]=data
    return data
def clean(log):
    assert '... skipped' not in log and 'skipped=' not in log
    assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',log)
def direct_suite(log,count):
    clean(log)
    assert re.findall(r'^Ran (\d+) tests? in ',log,re.M)==[str(count)]
    assert log.rstrip().endswith('OK')
    assert len(re.findall(r'^test_.* \.\.\. ok$',log,re.M))==count

for group in ('files','locked_files'):
    assert all(sha((root/'source'/name).read_bytes())==digest for name,digest in manifest[group].items())
if args.mode=='final':
    launch=json.loads((root/'launch.json').read_text())
    marker=Path('/proc')/str(launch['pid'])/'cmdline'
    assert not marker.exists() or ('qualify-'+candidate_name+'.py').encode() not in marker.read_bytes()

monitor=(out/'compiler-monitor.jsonl').read_text()
complete_lines=monitor.splitlines() if monitor.endswith('\n') else monitor.splitlines()[:-1]
samples=[json.loads(line) for line in complete_lines]
assert samples and not any(sample['compilers'] for sample in samples)
attachments['compiler-monitor-'+args.mode+'.jsonl']=('\n'.join(complete_lines)+'\n').encode()

for profile in profiles:
    value=run['profiles'][profile]
    build=Path(value['build']);folder=out/profile
    steps={x['name']:x for x in value['results']}
    required=['harness','handle' if handle_candidate else 'admin' if admin_candidate else 'handoff' if handoff_candidate else 'reply' if reply_candidate else 'binding' if binding_candidate else 'owners','core','P10']
    if args.candidate!='03':required.insert(2,'world')
    assert all(name in steps and steps[name]['exit_code']==0 for name in required), (profile,steps)
    assert all(sha((build/name).read_bytes())==digest for name,digest in value['binaries'].items())
    assert samples[0]['time']<=steps['harness']['started']
    assert samples[-1]['time']>=steps['P10']['ended']-2
    cache=(build/'CMakeCache.txt').read_text()
    expected='ON' if profile=='runtime-sanitize' else 'OFF'
    assert 'SPATIAL_SANITIZERS:BOOL='+expected in cache
    assert 'CMAKE_BUILD_TYPE:STRING=Debug' in cache
    collect(build/'build_id.hpp',profile+'/build_id.hpp')
    attachments[profile+'/sanitizer-config.json']=(json.dumps(dict(profile=profile,cmake_sanitizers=expected,
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',
        qualifier_sha256=sha((r/'local'/('qualify-'+candidate_name+'.py')).read_bytes())),indent=2)+'\n').encode()
    if handle_candidate:
        focused=collect(folder/'handle.log').decode();clean(focused)
        assert re.findall(r'^\s*Start\s+\d+:\s+(\S+)',focused,re.M)==['retained-child-handle']
        assert re.findall(r'^\d+: Ran (\d+) tests? in ',focused,re.M)==['5']
        assert '100% tests passed, 0 tests failed out of 1' in focused
    elif admin_candidate:
        focused=collect(folder/'admin.log').decode();clean(focused)
        assert re.findall(r'^\s*Start\s+\d+:\s+(\S+)',focused,re.M)==['control-administrative-transport']
        assert re.findall(r'^\d+: Ran (\d+) tests? in ',focused,re.M)==['11']
        assert '100% tests passed, 0 tests failed out of 1' in focused
    elif handoff_candidate:
        focused=collect(folder/'handoff.log').decode();clean(focused)
        assert re.findall(r'^\s*Start\s+\d+:\s+(\S+)',focused,re.M)==['supervisor-handoff-lifetime']
        assert re.findall(r'^\d+: Ran (\d+) tests? in ',focused,re.M)==['2']
        assert '100% tests passed, 0 tests failed out of 1' in focused
    elif reply_candidate:
        focused=collect(folder/'reply.log').decode();clean(focused)
        assert re.findall(r'^\s*Start\s+\d+:\s+(\S+)',focused,re.M)==['control-supervisor-reply']
        assert re.findall(r'^\d+: Ran (\d+) tests? in ',focused,re.M)==['16']
        assert '100% tests passed, 0 tests failed out of 1' in focused
    elif binding_candidate:
        focused=collect(folder/'binding.log').decode();clean(focused)
        assert re.findall(r'^\s*Start\s+\d+:\s+(\S+)',focused,re.M)==['authority-control-binding']
        assert re.findall(r'^\d+: Ran (\d+) tests? in ',focused,re.M)==['6']
        assert '100% tests passed, 0 tests failed out of 1' in focused
    else:
        owners=collect(folder/'owners.log').decode();direct_suite(owners,expected_owners)
    harness=collect(folder/'harness.log').decode();clean(harness)
    assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    core=collect(folder/'core.log').decode();clean(core)
    names=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',core,re.M)
    counts=list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',core,re.M)))
    core_suites=104 if handle_candidate else 103 if admin_candidate else 102 if handoff_candidate else 101 if reply_candidate else 100 if binding_candidate else 99
    core_checks=1509 if handle_candidate else 1498 if admin_candidate else 1496 if handoff_candidate else 1480 if reply_candidate else 1474 if binding_candidate else 1453
    excluded='retained-child-handle' if handle_candidate else 'control-administrative-transport' if admin_candidate else 'supervisor-handoff-lifetime' if handoff_candidate else 'control-supervisor-reply' if reply_candidate else 'authority-control-binding' if binding_candidate else 'control-owner-channels'
    assert len(names)==len(set(names))==len(counts)==core_suites and sum(counts)==core_checks
    assert f'100% tests passed, 0 tests failed out of {core_suites}' in core
    assert excluded not in names
    if binding_candidate or reply_candidate or handoff_candidate or admin_candidate or handle_candidate:assert dict(zip(names,counts))['control-owner-channels']==21
    if reply_candidate or handoff_candidate or admin_candidate or handle_candidate:assert dict(zip(names,counts))['authority-control-binding']==6
    if handoff_candidate or admin_candidate or handle_candidate:assert dict(zip(names,counts))['control-supervisor-reply']==16
    if admin_candidate or handle_candidate:assert dict(zip(names,counts))['supervisor-handoff-lifetime']==2
    if handle_candidate:assert dict(zip(names,counts))['control-administrative-transport']==11
    inventory=subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1'])
    entries=json.loads(inventory)['tests']
    assert {entry['name'] for entry in entries}==set(names)|{excluded}
    attachments[profile+'/ctest-inventory.json']=inventory
    # CTest inventory may rewrite LastTest.log. Captured core.log is the execution evidence.
    p10=json.loads(collect(folder/'P10.json'))
    assert p10['status']=='passed' and p10['executed']==len(p10['tests'])==151
    assert all(case['status']=='passed' for case in p10['tests'])
    clean(collect(folder/'P10.log').decode())
    native_checks=0
    if args.candidate!='03':
        direct_suite(collect(folder/'world.log').decode(),3)
        native_checks=3
        witnesses=list((folder/'witness').glob('*.json'));assert len(witnesses)==3
        for path in witnesses:
            witness=json.loads(collect(path))
            assert witness['native_release_confirmed'] and witness['application_alive']
            assert not witness['production_supervisor_binding'] and not witness['production_recovery_release']
    for name in ('configure.log','build.log'):
        if (folder/name).exists():collect(folder/name)
    for path in (folder/'native').rglob('*.log'):collect(path)
    result['profiles'][profile]=dict(core_checks=core_checks+(5 if handle_candidate else 11 if admin_candidate else 2 if handoff_candidate else 16 if reply_candidate else 6 if binding_candidate else expected_owners),core_suites=core_suites+1,
        owner_checks=expected_owners,native_world_checks=native_checks,harness_checks=51,P10_cases=151,
        failed=0,skipped=0,commands=value['results'],binaries=value['binaries'],core_parts=dict(zip(names,counts)))
    if binding_candidate or reply_candidate or handoff_candidate or admin_candidate or handle_candidate:result['profiles'][profile]['binding_checks']=6
    if reply_candidate or handoff_candidate or admin_candidate or handle_candidate:result['profiles'][profile]['reply_checks']=16
    if handoff_candidate or admin_candidate or handle_candidate:result['profiles'][profile]['handoff_checks']=2
    if admin_candidate or handle_candidate:result['profiles'][profile]['admin_checks']=11
    if handle_candidate:result['profiles'][profile]['handle_checks']=5

if handoff_candidate:
    for profile in ('debug','runtime-sanitize'):
        baseline=run['baseline'][profile]
        assert baseline['retained_copies_reproduced'] and baseline['results'][0]['exit_code']==1
        log=collect(out/('baseline-'+profile)/'handoff.log').decode()
        assert 'Ran 2 tests' in log and 'FAILED (failures=2)' in log and 'ERROR:' not in log
        assert set(re.findall(r'^FAIL: (test_\w+)',log,re.M))=={
            'test_selected_endpoint_has_no_supervisor_copy_after_handoff',
            'test_child_stdout_has_no_supervisor_writer_after_handoff'}
        assert 'Supervisor retained an endpoint after its only child exited' in log
elif binding_candidate:
    expected_failures={
        'test_prepared_owner_matches_activation_and_exact_issuer_fence',
        'test_reopened_authorized_record_cannot_reconstruct_control_owner',
        'test_failed_authorization_commit_cannot_expose_owner',
        'test_query_rejects_nested_unknown_owner_selector_without_mutation',
        'test_production_supervisor_returns_exact_owned_binding_without_stopping_child'}
    for profile in ('debug','runtime-sanitize'):
        baseline=run['baseline'][profile]
        assert baseline['missing_binding_reproduced'] and baseline['results'][0]['exit_code']==1
        log=collect(out/('baseline-'+profile)/'binding.log').decode()
        assert 'Ran 6 tests' in log and 'FAILED (failures=5)' in log and 'ERROR:' not in log
        assert set(re.findall(r'^FAIL: (test_\w+)',log,re.M))==expected_failures
        assert 'Missing owned control binding:' in log and 'Missing production control binding:' in log
elif args.candidate=='04':
    for profile in ('debug','runtime-sanitize'):
        baseline=run['baseline'][profile]
        assert baseline['defect_reproduced'] and baseline['results'][0]['exit_code']==1
        log=collect(out/('baseline-'+profile)/'validation.log').decode()
        assert 'Ran 2 tests' in log and 'FAILED (failures=2)' in log
        assert set(re.findall(r'^FAIL: (test_\w+)',log,re.M))=={
            'test_unknown_fields_precede_native_attribution','test_unsupported_version_precedes_native_attribution'}
        assert 'Expected INVALID_ARGUMENT before native attribution:' in log
        assert 'Expected UNSUPPORTED_VERSION before native attribution:' in log
        assert 'code: FORBIDDEN' in log and 'ERROR:' not in log
elif args.candidate=='03':
    original=run['debug_reuse']['original_source_manifest']
    assert [name for name in original if original[name]!=manifest['files'][name]]==['tests/core/test_control_owner_channels.py']
    assert 'control-owner-channels' not in result['profiles']['debug']['core_parts']
    result['known_integration_hold']='Strict-validation ordering remains incorrect. Candidate04 is required.'

attachments['run-'+args.mode+'.json']=raw_summary
attachments['source-manifest.json']=(root/'manifest.json').read_bytes()
result['attachment_hashes']={name:sha(data) for name,data in attachments.items()}
summary=(json.dumps(result,indent=2)+'\n').encode()
(root/('collected-'+args.mode+'.json')).write_bytes(summary)
attachments['summary-'+args.mode+'.json']=summary
import io
with tarfile.open(archive,'w:gz') as output:
    for name,data in attachments.items():
        entry=tarfile.TarInfo('qualified-'+args.candidate+'/'+host+'/'+name)
        entry.size=len(data);entry.mode=0o600
        output.addfile(entry,io.BytesIO(data))
print(json.dumps(dict(host=host,candidate=args.candidate,mode=args.mode,
    profiles={p:{k:v for k,v in value.items() if k.endswith('checks') or k in ('core_suites','P10_cases','failed','skipped')}
              for p,value in result['profiles'].items()},archive=str(archive)),indent=2))
