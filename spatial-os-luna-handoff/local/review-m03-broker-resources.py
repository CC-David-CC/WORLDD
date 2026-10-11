"""Audit exact source, selected assertions, immutable runs, and recovery receipts."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

root=Path(__file__).resolve().parents[1]
evidence=root/'evidence/m03-broker-resources-2026-10-10'
sha=lambda data:hashlib.sha256(data).hexdigest()
read=lambda p:json.loads(p.read_bytes())
def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(data,indent=2)+'\n').encode())
def cases(data):
    return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(data))
        if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
archives={};snapshots={};manifests={}
for candidate in ('01','02','03','04'):
    path=root/'local'/f'm03-broker-resources-{candidate}-source.tar.gz'
    archives[candidate]=sha(path.read_bytes())
    with tarfile.open(path) as tar:
        prefix=f'm03-broker-resources-{candidate}/'
        manifests[candidate]=json.loads(tar.extractfile(prefix+'manifest.json').read())
        snapshots[candidate]={name:tar.extractfile(prefix+'source/'+name).read() for name in manifests[candidate]['files']}
    for name,data in snapshots[candidate].items():assert sha(data)==manifests[candidate]['files'][name]
    assert manifests[candidate]['locked_files']==manifests['01']['locked_files']
    assert all(manifests[candidate]['files'][name]==value for name,value in manifests[candidate]['locked_files'].items())
for name,value in manifests['04']['files'].items():assert sha((root/name).read_bytes())==value,name
runtime=[name for name in manifests['04']['files'] if name.startswith(('src/','proto/')) and name!='src/testing/process_termination_probe.cpp']
assert all(manifests['02']['files'][name]==manifests['04']['files'][name] for name in runtime)
for name in ('src/testing/process_termination_probe.cpp','tests/core/test_process_termination.py','CMakeLists.txt'):
    assert manifests['03']['files'][name]==manifests['04']['files'][name]
prior=cases(snapshots['02']['tests/core/test_broker_resources.py'])
current=cases(snapshots['04']['tests/core/test_broker_resources.py'])
assert len(prior)==10 and len(current)==11 and all(current[name]==value for name,value in prior.items())
termination_cases=cases(snapshots['04']['tests/core/test_process_termination.py'])
original=subprocess.check_output(['git','show',manifests['04']['base_commit']+':spatial-os-luna-handoff/tests/core/test_process_termination.py'],cwd=root)
assert len(termination_cases)==6 and termination_cases==cases(original)
for candidate in ('01','02','03'):
    changed={name:data for name,data in snapshots[candidate].items() if data!=snapshots['04'][name]}
    for name,data in changed.items():
        path=evidence/('candidate-'+candidate)/'source-delta'/name
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    write(evidence/('candidate-'+candidate)/'source-preservation.json',dict(
        basis='candidate-04 manifest; unchanged files match integrated source',archive_sha256=archives[candidate],
        deltas={name:sha(data) for name,data in changed.items()}))

record=dict(status='primary_review_complete',phase='M03',phase_complete=False,
    base_commit=manifests['04']['base_commit'],passed=176,failed_final=0,skipped=0,
    selected_per_host_profile=44,source_archives=archives,source_files=len(manifests['04']['files']),
    locked_files=len(manifests['04']['locked_files']),verified_attachments=0,hosts={},
    qualification='Composed immutable evidence: candidate02 broker and scope tests; candidate03/04 termination; candidate04 application I/O.',
    review='Primary agent reviewed all changed production, schema, fixture, test, CMake, and evidence code. No local-model or subagent review.')
logs=[]
for host in ('sb16','sb16-2'):
    data={}
    for candidate in ('01','02','03','04'):
        directory=evidence/('candidate-'+candidate)/host
        review=read(directory/'review.json'); data[candidate]=review
        assert 'ended' in review
        assert all(sha((directory/name).read_bytes())==value for name,value in review['attachments'].items())
        record['verified_attachments']+=len(review['attachments'])
        logs.extend(directory/name for name in review['attachments'] if name.endswith('.log'))
    assert data['01']['status']=='failed' and data['02']['status']==data['04']['status']=='passed_review_pending'
    assert data['03']['status']==('passed_review_pending' if host=='sb16' else 'failed')
    baseline=(evidence/'candidate-02'/host/'baseline.log').read_text()
    assert 'FAILED (failures=1)' in baseline and 'launched {' in baseline
    profiles={}
    for profile in ('debug','runtime-sanitize'):
        main_log=evidence/'candidate-02'/host/(profile+'-checks.log')
        text=main_log.read_text()
        passed=re.findall(r'^\d+: (test_\w+) \([^\n]+\) \.\.\. ok$',text,re.M)
        assert len(passed)==37 and set(prior)<=set(passed)
        assert data['02']['profiles'][profile]['passed']==37
        actual=data['04']['profiles'][profile]
        selected='03' if actual['retained_termination_checks'] else '04'
        termination_log=evidence/('candidate-'+selected)/host/(profile+'-checks.log')
        text=termination_log.read_text()
        for name in termination_cases:assert re.search(r'^\d+: '+name+r' \([^\n]+\) \.\.\. ok$',text,re.M)
        io_directory=evidence/'candidate-04'/host/profile/'witnesses/test_application_writes_obey_broker_io_budget'
        io=read(io_directory/'application-io.json')
        assert io['kernel_bytes']>=16*1024*1024 and io['configured_bps']==4*1024*1024
        assert 2.5<=io['seconds']<20
        assert 'wbytes=' in read(io_directory/'after-io.json')['raw']
        profiles[profile]=dict(passed=44,failed=0,skipped=0,main_log=str(main_log.relative_to(evidence)),
            termination_log=str(termination_log.relative_to(evidence)),application_io=io,
            binaries={name:value for name,value in data['02']['profiles'][profile]['binaries'].items() if name!='spatial-process-termination-probe'},qualified_termination_probe=actual['probe_sha256'])
    cleanup=read(evidence/(host+'-cleanup.json'))
    assert cleanup['ssh_recovery'] and cleanup['normal_checkout_status']==''
    assert cleanup['normal_checkout_head']=='b5902eaaad0a9d0ce3cb272d37f0d0936a6575cd'
    record['hosts'][host]=dict(profiles=profiles,unloaded_units=len(cleanup['units']),recovery_sha256=sha((evidence/(host+'-cleanup.json')).read_bytes()))
for log in logs:
    assert not re.search(r'ERROR: AddressSanitizer|ERROR: LeakSanitizer|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:',log.read_text(errors='replace')),log
record.update(sanitizer_logs_scanned=len(logs),sanitizer_matches=0,
    preserved_failures=[
        'Candidate01: both builds succeeded; tests could not import omitted Python protobuf bindings. No test case ran.',
        'Candidate02: the old broker launched without resource configuration on both hosts. Expected baseline failures remain preserved.',
        'Candidate03 sb16-2 Debug: initial io.stat lacked wbytes; the new witness raised KeyError before writing. No original raw counter snapshot was captured.',
        'Candidate04 records initial and final counters. An absent initial wbytes contributes zero; final wbytes and all 16 MiB remain mandatory.'],
    limitations=[
        'M03 is incomplete. Normal host checkouts remain unchanged and this branch is not adopted.',
        'Session launchers and existing application fixtures still need explicit resource configuration and project-scoped controller provisioning.',
        'This increment does not qualify live authenticated resource policies, native GUI launch, or full existing confinement and escape suites.',
        'Resource ceilings are operator configuration; they grant no execution or file authority.',
        'Persistent storage quotas, broker Git/file I/O accounting, and whole-session resource ceilings remain pending.',
        'Direct application I/O throttling does not prove issuer fdatasync latency or availability under contention.',
        'No full M03 gate, physical devices, GPU performance, live-model acceptance, or power-loss tests ran.',
        'Current-source workspaced was not rebuilt after test-only candidate03/04 changes. All production sources match candidate02.',
        'Two candidate03 I/O passes were superseded by four corrected candidate04 measurements; duplicate passes are excluded from 176.'],
    references=['https://docs.kernel.org/admin-guide/cgroup-v2.html#io-interface-files'],
    next=['Provision resource configuration through production session launchers.',
          'Connect existing application and authority integration fixtures to the same provisioned production boundary.',
          'Implement bounded persistent storage and broker effects accounting, then qualify issuer contention recovery.'])
write(evidence/'primary-review.json',record)
print(json.dumps({key:record[key] for key in ('status','passed','verified_attachments','sanitizer_logs_scanned','source_files','locked_files')}))
