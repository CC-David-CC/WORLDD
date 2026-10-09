import difflib,hashlib,json,re,shutil,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];local=r/'local';e=r/'evidence/participant-channel-2026-10-09';e.mkdir(exist_ok=True)
def write(p,s):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s,encoding='utf-8',newline='\n')
for host in ['sb16','sb16-2']:
    for archive,destination in [('participant-channel-baseline-'+host+'-01.tar.gz',e),('owned-participant-final-'+host+'-02.tar.gz',r/'evidence/owned-participant-2026-10-09'),('launch-authorization-full-debug-'+host+'-03.tar.gz',r/'evidence/launch-authorization-2026-10-09')]:
        with tarfile.open(local/archive) as a:a.extractall(destination,filter='data')
    baseline=json.loads((e/'baseline-01'/host/'diagnostic.json').read_text());assert baseline['exit_code']==2 and baseline['stderr']=='Invalid authority participant configuration\n'
    owned=json.loads((r/'evidence/owned-participant-2026-10-09/candidate-01'/host/'summary-final.json').read_text())
    assert owned['schema']['existing_descriptors_unchanged'] and owned['source_files']==274 and owned['locked_files']==41
    for profile in ['debug','runtime-sanitize']:
        result=owned[profile];assert tuple(result[k] for k in ['checks','suites','harness_checks','failed','skipped'])==(339,21,51,0,0)
        log=(r/'evidence/owned-participant-2026-10-09/candidate-01'/host/profile/'focused.log').read_text()
        counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
        assert len(counts)==21 and sum(counts)==339 and '100% tests passed, 0 tests failed out of 21' in log
    auth=json.loads((r/'evidence/launch-authorization-2026-10-09/full-candidate-03'/host/'summary-debug.json').read_text())
    result=auth['debug'];assert tuple(result[k] for k in ['core_checks','core_suites','harness_checks','P10_cases','failed','skipped'])==(1328,86,51,151,0,0)
# Preserve exact baseline and candidate patches against their common reviewed source.
with tarfile.open(local/'owned-participant-candidate-01.tar') as a:base={m.name:a.extractfile(m).read() for m in a if m.isfile()}
for label in ['baseline','candidate']:
    name='participant-channel-'+label+'-01'
    manifest=json.loads((local/(name+'.json')).read_text());pieces=[]
    with tarfile.open(local/(name+'.tar')) as a:files={m.name:a.extractfile(m).read() for m in a if m.isfile()}
    for n,h in manifest['files'].items():assert hashlib.sha256(files[n]).hexdigest()==h,n
    assert set(base)<=set(files)
    for n in sorted(files):
        if files[n]!=base.get(n,b''):
            pieces.append(''.join(difflib.unified_diff(base.get(n,b'').decode().splitlines(True),files[n].decode().splitlines(True),fromfile='a/'+n if n in base else '/dev/null',tofile='b/'+n)))
    write(e/(name+'.patch'),''.join(pieces));shutil.copyfile(local/(name+'.json'),e/(name+'.json'))
for name in ['freeze-participant-channel-01.py','qualify-participant-channel-01.py','diagnose-participant-channel-baseline-01.py','qualify-participant-channel-baseline-01.py','checkpoint-participant-channel-01.py']:
    (e/'scripts').mkdir(exist_ok=True);shutil.copyfile(local/name,e/'scripts'/name)
write(e/'source-review.json',json.dumps(dict(candidate=1,source_files=276,reviewer='Primary agent',integrated=False,
    existing_test_files_unchanged=True,locked_files_unchanged=41,
    review=['Private AF_UNIX stream validates direct parent PID and effective UID.','Descriptor ownership is exclusive and close-on-exec.','Framing, output, startup, and consequential replies have bounds and deadlines.','Child validates identity, domain, revision, generation, and reply correlation.','No signing keys or TLS configuration in inherited mode.','No automatic reconnect or command replay.'],
    baseline='Both hosts reject inherited configuration before authentication. Original BrokenPipe failure and exact child diagnostic preserved.',
    qualification='Candidate01 frozen with17newchecks. Both-host Debug builds started; no candidate pass claimed.',
    limitations=['Production supervisor relay remains unimplemented.','Parent receipt cannot acknowledge child cancellation.','World/broker integration, fencing, and checked recovery remain required.']),indent=2)+'\n')
p=r/'CURRENT_STATE.md';s=p.read_text(encoding='utf-8');title='# Implementation status\n\n';assert s.startswith(title)
write(p,title+'''## 2026-10-09: Owned participant sanitizer checks pass; private child channel enters qualification

Owned participant candidate01 passed both focused profiles on both hosts.
Each profile passed 339 checks across 21 suites and 51 harness checks.
No case failed or skipped. All 274 source hashes and 41 locked hashes match.
Full qualification continues on the same frozen binaries. Completed focused tests are not repeated.

Launch authorization candidate03 passed full Debug qualification on both hosts.
Each host passed 1328 core checks across 86 suites, 51 harness checks, and 151 P10 cases.
No Debug case failed or skipped. Full runtime sanitizer qualification remains active.

Both hosts reproduced rejection of inherited participant configuration in the old runtime.
The original broken-pipe test failures remain preserved with independent child exit diagnostics.
Private child-channel candidate01 adds bounded Protobuf transport without child signing keys or direct TLS.
Seventeen new checks cover real issuer transitions, revocation, malformed peers, descriptor identity, and exec inheritance.
Both-host Debug builds have started. No candidate qualification result is claimed yet.
The candidate preserves all prior test files and 41 locked files.

No new candidate source is integrated.
The production supervisor relay must distinguish child consumption from parent receipt.
World and broker integration, fencing, checked recovery, M02, and Spatial-M1 remain incomplete.

'''+s[len(title):])
p=r/'docs/AUTHORITY_PARTICIPANT_CHANNEL.txt';s=p.read_text().replace('Status: contract and missing-feature baseline. Implementation is pending.','Status: isolated child-transport candidate01. Both-host qualification is running. Production relay remains pending.');write(p,s)
p=r/'docs/AUTHORITY_OWNED_PARTICIPANT.txt';s=p.read_text().replace('Status: isolated candidate01 passed 339 focused Debug checks on both hosts. Sanitizer qualification is running.','Status: isolated candidate01 passed both focused profiles on both hosts. Full qualification is running.');s+='\nBoth focused profiles now passed 339 checks across 21 suites and 51 harness checks on each host.\nNo final focused case failed or skipped. Full qualification reuses the frozen output02 binaries.\n';write(p,s)
p=r/'tasks/m1/identity.owned_participant.json';d=json.loads(p.read_text());d['focused']='Both hosts passed339checks21suites51harness in Debug and runtime ASan/UBSan;0failures0skips.';d['remaining'][0]='Complete full qualification on both hosts.';write(p,json.dumps(d,indent=2)+'\n')
p=r/'evidence/owned-participant-2026-10-09/source-review.json';d=json.loads(p.read_text());d['tests']='Both hosts passed339focusedchecks21suites51harness in Debug and runtime ASan/UBSan. No failures or skips. Full continuation active on the same frozen binaries.';write(p,json.dumps(d,indent=2)+'\n')
write(r/'tasks/m1/identity.participant_channel.json',json.dumps(dict(id='identity.participant_channel',phase='M02',status='in_progress',parent='identity.supervisor_recovery',contract='docs/AUTHORITY_PARTICIPANT_CHANNEL.txt',evidence='evidence/participant-channel-2026-10-09',candidate=1,source_files=276,integrated=False,new_tests=17,remaining=['Complete both-host Debug and runtime sanitizer qualification.','Implement supervisor relay with actual child consumption semantics.','Connect worldd and workspaced, then qualify fencing and checked recovery.']),indent=2)+'\n')
p=local/'driver-state.json';d=json.loads(p.read_text());d.update(current_step='participant_channel_candidate01_qualifying',previous_goal_turn='verified wait: four specific live qualification handles polled.',last_turn='progress: froze276file privatechannel candidate17newchecks; baseline diagnosed and preserved bothhosts; builds started. Collected ownedfocusedbothprofiles and authorizationfullDebug.',next_test='Poll23021/62845 privatechannel;78618/7728 authorizationfull;24301/73141 ownedfull. Continue production parent relay only after child semantics reviewed. Do not restart live jobs.')
d['qualification_sessions']={'launch_authorization_full_sb16':78618,'launch_authorization_full_sb16_2':7728,'owned_participant_full_sb16':24301,'owned_participant_full_sb16_2':73141,'participant_channel_sb16':23021,'participant_channel_sb16_2':62845}
d['completed_session_handles'].update(owned_participant_focused_sb16=7443,owned_participant_focused_sb16_2=76958,participant_channel_baseline_sb16=60658,participant_channel_baseline_sb16_2=29092)
write(p,json.dumps(d,indent=2)+'\n')
print('Saved child baseline and isolated patch; verified ownedfocused bothprofiles and authorization fullDebug evidence. No source integrated.')
