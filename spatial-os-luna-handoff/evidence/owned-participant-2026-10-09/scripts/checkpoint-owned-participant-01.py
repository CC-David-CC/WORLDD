import json,shutil,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];local=r/'local';e=r/'evidence/owned-participant-2026-10-09'
def write(p,s):p.write_text(s,encoding='utf-8',newline='\n')
for host in ['sb16','sb16-2']:
    for mode in ['new','setup']:
        with tarfile.open(local/('owned-participant-'+mode+'-'+host+'-01.tar.gz')) as a:a.extractall(e,filter='data')
    with tarfile.open(local/('owned-execution-witness-'+host+'-01.tar.gz')) as a:a.extractall(e/'baseline-child-witness'/host,filter='data')
    witness=e/'baseline-child-witness'/host
    assert json.loads((witness/'run.json').read_text())['exit_code']==0
    assert '"confirmed_missing_gate": true' in (witness/'diagnostic.log').read_text()
    with tarfile.open(local/('supervisor-registry-full-final-'+host+'-03.tar.gz')) as a:a.extractall(r/'evidence/supervisor-registry-2026-10-09',filter='data')
    summary=json.loads((r/'evidence/supervisor-registry-2026-10-09/full-candidate-01'/host/'summary-final.json').read_text())
    for profile in ['debug','runtime-sanitize']:
        result=summary[profile];assert tuple(result[k] for k in ['core_checks','core_suites','harness_checks','P10_cases','failed','skipped'])==(1305,85,51,151,0,0)
for n in ['collect-owned-participant-01.py','collect-owned-participant-02.py','qualify-owned-participant-02.py','prepare-owned-participant-02.py','diagnose-owned-execution-baseline-01.py','save-owned-participant-01.py','checkpoint-owned-participant-01.py']:
    shutil.copyfile(local/n,e/'scripts'/n)
p=e/'source-review.json';d=json.loads(p.read_text());d['tests']='Eleven new Debug checks and51harness passed both hosts. Output01 passed328 checks across20suites; native scope import failed before its11checks. Output02 builds all targets and runs unchanged source and assertions.';write(p,json.dumps(d,indent=2)+'\n')
p=r/'CURRENT_STATE.md';s=p.read_text(encoding='utf-8');title='# Implementation status\n\n';assert s.startswith(title)
note='''## 2026-10-09: Supervisor owns the launch participant; registry full matrix passes

The isolated coordinator now owns the real participant worker and its issuer connection.
It builds supervisor approval from that retained identity and the durable process launch identity.
It persists the exact approval before releasing supervised child execution.
Failed exec cannot permit activation. Commands require a live direct child in the owned scope.
Restart cannot reconstruct this live ownership from the journal.
Cleanup still preserves unresolved issuer generations and revocation obligations.

Both hosts reproduced the missing start gate and unsupported attachment before implementation.
A separate real child witness confirmed execution without approval inside the exact baseline cgroup.
Candidate01 then passed all eleven new Debug checks and 51 harness checks on both hosts.
The new child witness reads durable approval before execution continues and the real issuer accepts the retained connection.
Rollback, wrong-key, identity mismatch, worker failure, cleanup, and restart checks also passed.

Output01 passed 328 checks across twenty suites on each host.
One existing native suite could not import input_pb2 because the build command omitted native-input dependencies.
Its eleven checks remained unrun. The original command, failure, and source remain preserved.
Corrected output02 builds every target and supplies the independent native input witness.
Both corrected runs are active. They use the same 274 source files and unchanged test assertions.
All 41 locked hashes remain unchanged. Descriptor review confirms additive protobuf changes only.
Read docs/AUTHORITY_OWNED_PARTICIPANT.txt and evidence/owned-participant-2026-10-09.

Registry candidate01 completed full Debug and runtime ASan/UBSan on both hosts.
Each profile passed 1305 core checks across 85 suites, 51 harness checks, and 151 P10 cases.
No final case failed or skipped. All 272 source hashes and 41 locked hashes match.
Full launch-authorization qualification remains active on both hosts.

No new candidate source is integrated.
Private child-channel integration, fencing, recovery, M02, and Spatial-M1 remain incomplete.

'''
write(p,title+note+s[len(title):])
p=r/'docs/AUTHORITY_OWNED_PARTICIPANT.txt';s=p.read_text(encoding='utf-8').replace('Status: design and missing-feature baseline. Implementation is pending.','Status: isolated candidate01. Eleven new Debug checks passed on both hosts. Full qualification is pending.')
s+='''
Current evidence

Both hosts reproduced the missing gate and attachment before implementation.
The independent baseline child recorded its PID and exact owned cgroup without approval.
Candidate01 passed eleven new Debug checks and 51 harness checks on both hosts.
The first regression command omitted native-input build dependencies.
Twenty suites passed 328 checks. The native scope suite could not import its binding; eleven checks remained unrun.
Output02 builds all targets with native input enabled and supplies the independent xprobe path.
Source and assertions are unchanged. Corrected qualification is running on both hosts.
All 274 source hashes and 41 locked hashes match. Existing protobuf declarations remain unchanged.
''';write(p,s)
p=r/'docs/AUTHORITY_SUPERVISOR_REGISTRY.txt';s=p.read_text(encoding='utf-8').replace('Status: isolated implementation in progress. No source integration or operator migration.','Status: full Debug and runtime ASan/UBSan passed on both hosts. No source integration or operator migration.')
s=s.replace('Fresh full qualification runs separately in output03 on both hosts.\nBoth full-build Debug runs passed the eighteen registry checks and 51 harness checks.\nThe remaining full matrix is pending. Source integration remains pending.','Full qualification completed separately in output03 on both hosts.\nEach profile passed 1305 core checks across 85 suites, 51 harness checks, and 151 P10 cases.\nNo final case failed or skipped. All 272 source hashes and 41 locked hashes match.\nSource integration remains pending.');write(p,s)
p=r/'docs/AUTHORITY_LAUNCH_AUTHORIZATION.txt';s=p.read_text(encoding='utf-8').replace('Status: isolated candidate02 passed focused Debug on both hosts. Runtime sanitizer qualification is running.','Status: isolated candidate03 passed both focused profiles on both hosts. Full qualification is running.');write(p,s)
p=r/'docs/AUTHORITY_LAUNCH_COORDINATOR.txt';s=p.read_text(encoding='utf-8').replace('Status: isolated candidate03. Full qualification is running. Source is not integrated.','Status: isolated candidate03 passed full qualification on both hosts. Source is not integrated.');write(p,s)
task=dict(id='identity.owned_participant',phase='M02',parent='identity.supervisor_recovery',status='in_progress',
    invariant='A supervised child executes only after durable approval of its coordinator-owned participant connection.',
    contract='docs/AUTHORITY_OWNED_PARTICIPANT.txt',candidate=1,source_files=274,integrated=False,evidence='evidence/owned-participant-2026-10-09',
    baseline='Both hosts reproduced missing start protection and unsupported attachment. Independent children confirmed execution in exact owned cgroups without approval.',
    focused='Eleven new Debug checks passed on both hosts. Output01 passed328checks20suites51harness; native input binding was missing and11checks unrun. Corrected output02 builds all targets; qualification running.',
    remaining=['Complete both focused profiles and full qualification on both hosts.','Connect worldd and workspaced through a private inherited participant channel.','Implement fencing, checked recovery reconciliation, and exact generation retirement.'])
write(r/'tasks/m1/identity.owned_participant.json',json.dumps(task,indent=2)+'\n')
p=r/'tasks/m1/identity.supervisor_recovery.json';d=json.loads(p.read_text());d['current_step']='owned_participant_implemented_qualification_running';d['owned_participant_task']='tasks/m1/identity.owned_participant.json';write(p,json.dumps(d,indent=2)+'\n')
p=r/'tasks/m1/identity.supervisor_registry.json';d=json.loads(p.read_text());d['full']='Full Debug and runtime ASan/UBSan passed both hosts:1305core85suites51harness151P10 perprofile. No failures or skips;272 source and41locked hashes match.';d['remaining'][0]='Integrate with authenticated launch and checked supervisor recovery.';write(p,json.dumps(d,indent=2)+'\n')
p=local/'driver-state.json';d=json.loads(p.read_text());d.update(current_step='owned_participant_candidate01_output02_qualifying',previous_goal_turn='progress: e247969 recorded authorization focused qualification; full runs started.',last_turn='progress: implemented coordinator-owned participant and durable approval. Both hosts reproduced two baselines and independent child execution. Eleven new Debug checks passed both hosts. Original native dependency setup failure preserved; corrected output02 active. Registry full both profiles completed and collected.',next_test='Poll7443/76958 ownedparticipant02 and78618/7728 authorizationfull03. Registry25633/25219 terminal and collected. Owned73479/90949 terminal setup failure; baselines96577/85820 terminal. Do not restart. Next: finish qualification and private inherited participant channel, then fencing and checked recovery.')
d['qualification_sessions']={'launch_authorization_full_sb16':78618,'launch_authorization_full_sb16_2':7728,'owned_participant_sb16':7443,'owned_participant_sb16_2':76958};write(p,json.dumps(d,indent=2)+'\n')
print('Saved baseline witnesses, reviewed owned-participant patch, original setup failure, and full registry results.')
