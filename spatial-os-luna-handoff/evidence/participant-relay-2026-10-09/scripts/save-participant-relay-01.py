import difflib,hashlib,json,shutil,subprocess,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];repo=r.parent;local=r/'local';e=r/'evidence/participant-relay-2026-10-09'
e.mkdir();(e/'scripts').mkdir()
def write(p,s):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s,encoding='utf-8',newline='\n')
with tarfile.open(local/'participant-channel-candidate-02.tar') as a:base={m.name:a.extractfile(m).read() for m in a if m.isfile()}
for label in ['baseline','candidate']:
    name='participant-relay-'+label+'-01';manifest=json.loads((local/(name+'.json')).read_text())['files']
    with tarfile.open(local/(name+'.tar')) as a:after={m.name:a.extractfile(m).read() for m in a if m.isfile()}
    for n,h in manifest.items():assert hashlib.sha256(after[n]).hexdigest()==h,n
    patch=''
    for n,raw in after.items():
        if raw!=base.get(n):patch+=''.join(difflib.unified_diff(base.get(n,b'').decode().splitlines(True),raw.decode().splitlines(True),fromfile='a/'+n if n in base else '/dev/null',tofile='b/'+n))
    write(e/(name+'.patch'),patch);shutil.copyfile(local/(name+'.json'),e/(name+'.json'))
    tree=local/('participant-relay-'+label+'-review-01');tree.mkdir()
    with tarfile.open(local/'participant-channel-candidate-02.tar') as a:a.extractall(tree,filter='data')
    command=['git','apply','--directory='+tree.relative_to(repo).as_posix(),str(e/(name+'.patch'))]
    subprocess.run(command[:2]+['--check']+command[2:],cwd=repo,check=True);subprocess.run(command,cwd=repo,check=True)
    for n,raw in after.items():assert (tree/n).read_bytes()==raw,n
    for n in base:
        if n.startswith('tests/'):assert after[n]==base[n],n
    for n,h in json.loads(after['acceptance.lock.json'])['files'].items():assert hashlib.sha256(after[n]).hexdigest()==h,n
for host in ['sb16','sb16-2']:
    for archive,destination in [('participant-relay-baseline-'+host+'-01.tar.gz',e),
        ('participant-channel-final-'+host+'-02.tar.gz',r/'evidence/participant-channel-2026-10-09'),
        ('launch-authorization-full-final-'+host+'-03.tar.gz',r/'evidence/launch-authorization-2026-10-09')]:
        with tarfile.open(local/archive) as a:a.extractall(destination,filter='data')
    baseline=json.loads((e/'baseline-01'/host/'summary.json').read_text())
    assert baseline['expected_failures']==1 and baseline['harness_checks']==51
    channel=json.loads((r/'evidence/participant-channel-2026-10-09/candidate-02'/host/'summary-final.json').read_text())
    assert channel['source_files']==276 and channel['locked_files']==41 and channel['schema']['existing_descriptors_unchanged']
    authorization=json.loads((r/'evidence/launch-authorization-2026-10-09/full-candidate-03'/host/'summary-final.json').read_text())
    for profile in ['debug','runtime-sanitize']:
        result=channel[profile];assert tuple(result[k] for k in ['checks','suites','harness_checks','failed','skipped'])==(356,22,51,0,0)
        result=authorization[profile];assert tuple(result[k] for k in ['core_checks','core_suites','harness_checks','P10_cases','failed','skipped'])==(1328,86,51,151,0,0)
for name in ['prepare-participant-relay-baseline-01.py','qualify-participant-relay-baseline-01.py','collect-participant-relay-baseline-01.py','freeze-participant-relay-01.py','qualify-participant-relay-01.py','collect-participant-relay-01.py','save-participant-relay-01.py']:
    shutil.copyfile(local/name,e/'scripts'/name)
write(e/'source-review.json',json.dumps(dict(source_files=277,locked_files=41,candidate=1,integrated=False,
    existing_tests_unchanged=True,patch_reconstruction='Both baseline and candidate patches reproduce every frozen hash.',
    review=['Reserved inherited endpoint passes only through retained ProcessScope startup.','Durable approval and authentication precede channel preparation.','External polling and command APIs cannot compete with private relay ownership.','The parent checks live child scope before accepting commands.','Private forwarded submission preserves child revision; the real issuer checks current state.','The existing direct submit revision equality remains unchanged.','Messages and bootstrap queues are bounded.','Failure closes routing and retains unresolved issuer obligations.','Clean release drains its reply before endpoint closure.'],
    baseline='Both hosts reject prepare_channel with Unknown launch operation;51harness passed.',
    qualification='Candidate01 builds are running on both hosts. Ten new checks and all prior focused checks are pending.',
    remaining=['Complete Debug/runtime sanitizer qualification and cumulative gate.','Wire worldd and workspaced through the supervisor event loop.','Implement checked fencing, recovery, and generation retirement.']),indent=2)+'\n')
p=r/'CURRENT_STATE.md';s=p.read_text(encoding='utf-8');title='# Implementation status\n\n';assert s.startswith(title)
write(p,title+'''## 2026-10-09: Private child channel passes both profiles; supervisor relay enters qualification

Private child-channel candidate02 passed focused Debug and runtime ASan/UBSan on both hosts.
Each profile passed 356 checks across 22 suites and 51 harness checks.
No final case failed or skipped. All 276 source hashes and 41 locked hashes match.
The original fixture failure and corrected diagnostic remain preserved.

Launch authorization candidate03 completed full Debug and runtime ASan/UBSan on both hosts.
Each profile passed 1328 core checks across 86 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. All 273 source hashes and 41 locked hashes match.

Both hosts reproduced the supervisor's unsupported private-channel preparation operation.
The isolated relay now reserves the child endpoint, passes it through the owned process launch, and forwards exact commands.
It retains durable approval, authenticated issuer ownership, bounded transport, and checked child lifetime.
External participant polling and command injection cannot compete with the relay.
A forwarded stale revision remains unchanged for the real issuer to reject.
Parent receipt and child delivery do not acknowledge completed revocation effects.

Relay candidate01 contains 277 files and ten new tests.
Its Debug builds are running on both hosts. No relay qualification result is claimed yet.
All previous test files and 41 locked files remain unchanged.
Read docs/AUTHORITY_PARTICIPANT_RELAY.txt and evidence/participant-relay-2026-10-09.

Owned participant full Debug passed both hosts; its runtime sanitizer matrix continues.
No new source is integrated. World/broker wiring, fencing, checked recovery, M02, and Spatial-M1 remain incomplete.

'''+s[len(title):])
p=r/'docs/AUTHORITY_PARTICIPANT_CHANNEL.txt';s=p.read_text().replace('Status: isolated child-transport candidate02. Both-host qualification is running. Production relay remains pending.','Status: isolated child-transport candidate02 passed both focused profiles on both hosts. Production relay qualification is pending.')
s+='\nBoth profiles passed 356 focused checks across 22 suites and 51 harness checks on each host.\nNo final case failed or skipped. All 276 source hashes and 41 locked hashes match.\n';write(p,s)
p=r/'docs/AUTHORITY_PARTICIPANT_RELAY.txt';s=p.read_text().replace('Status: design and missing-feature baseline. Implementation is pending.','Status: isolated candidate01 implemented. Both-host qualification is running.');write(p,s)
p=r/'docs/AUTHORITY_LAUNCH_AUTHORIZATION.txt';s=p.read_text().replace('Status: isolated candidate03 passed both focused profiles on both hosts. Full qualification is running.','Status: isolated candidate03 passed full Debug and runtime ASan/UBSan on both hosts. Source integration remains pending.');s+='\nFull qualification passed 1328 core checks across 86 suites, 51 harness checks, and 151 P10 cases per profile on both hosts.\nNo final case failed or skipped. All 273 source hashes and 41 locked hashes match.\n';write(p,s)
p=r/'tasks/m1/identity.participant_channel.json';d=json.loads(p.read_text());d['qualification']='Both hosts passed356checks22suites51harness in Debug and runtime ASan/UBSan. No failures or skips;276source41lockedhashes match.';d['remaining'][0]='Complete cumulative qualification with the production relay and integration.';write(p,json.dumps(d,indent=2)+'\n')
p=r/'evidence/participant-channel-2026-10-09/source-review.json';d=json.loads(p.read_text());d['qualification']='Candidate02 passed356checks22suites51harness in bothprofiles on bothhosts. No failures or skips. Original failure preserved.';write(p,json.dumps(d,indent=2)+'\n')
write(r/'tasks/m1/identity.participant_relay.json',json.dumps(dict(id='identity.participant_relay',phase='M02',status='in_progress',parent='identity.supervisor_recovery',contract='docs/AUTHORITY_PARTICIPANT_RELAY.txt',candidate=1,source_files=277,new_tests=10,integrated=False,evidence='evidence/participant-relay-2026-10-09',remaining=['Complete both-host focused and cumulative qualification.','Wire worldd and workspaced.','Implement fencing and checked recovery.']),indent=2)+'\n')
p=r/'tasks/m1/identity.supervisor_recovery.json';d=json.loads(p.read_text());d['current_step']='private_participant_relay_qualification';d['participant_relay_task']='tasks/m1/identity.participant_relay.json';write(p,json.dumps(d,indent=2)+'\n')
p=local/'driver-state.json';d=json.loads(p.read_text());d.update(current_step='participant_relay_candidate01_qualifying',previous_goal_turn='progress:4815c69 preserved child fixture correction and owned fullDebug.',last_turn='progress: childchannel02 bothprofiles356checks22suites51harness and launchauthorization03 fullbothprofiles1328core86suites51harness151P10 collected bothhosts. Relay baseline failed expected bothhosts. Implemented/froze277files10newtests; builds active.',next_test='Poll96501/69305 relay01 and24301/73141 ownedfull. Childchannel91399/1783, authorization78618/7728, relaybaseline20408/4366 terminal,collected;do not restart. Review relay tests then wire production world/broker.')
d['qualification_sessions']={'participant_relay_sb16':96501,'participant_relay_sb16_2':69305,'owned_participant_full_sb16':24301,'owned_participant_full_sb16_2':73141}
d['completed_session_handles'].update(participant_channel02_sb16=91399,participant_channel02_sb16_2=1783,launch_authorization_full_sb16=78618,launch_authorization_full_sb16_2=7728,participant_relay_baseline_sb16=20408,participant_relay_baseline_sb16_2=4366)
write(p,json.dumps(d,indent=2)+'\n')
print('Saved reviewed relay patches and baselines; both-host childchannel focused and launchauthorization full matrices verified.')
