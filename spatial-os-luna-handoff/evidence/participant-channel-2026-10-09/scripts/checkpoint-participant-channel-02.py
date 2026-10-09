import difflib,hashlib,json,re,shutil,subprocess,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];repo=r.parent;local=r/'local';e=r/'evidence/participant-channel-2026-10-09'
def write(p,s):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s,encoding='utf-8',newline='\n')
for host in ['sb16','sb16-2']:
    for label in ['failure-'+host+'-01','cleanup-'+host+'-02']:
        with tarfile.open(local/('participant-channel-'+label+'.tar.gz')) as a:a.extractall(e,filter='data')
    failure=e/'candidate-01'/host
    summary=json.loads((failure/'summary-failure.json').read_text());assert summary['schema']['existing_descriptors_unchanged']
    log=(failure/'debug/focused.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
    assert len(counts)==22 and sum(counts)==356 and '95% tests passed, 1 tests failed out of 22' in log
    assert 'ssl.SSLEOFError' in log and 'self.ready_private();runtime.send(55' in log
    assert [x['exit_code'] for x in summary['debug']['results']]==[0,0,0,8]
    fixed=e/'fixture-correction-02'/host
    result=json.loads((fixed/'run.json').read_text());assert result['exit_code']==0 and result['production_identical']
    log=(fixed/'tests.log').read_text();assert 'Ran 17 tests' in log and log.rstrip().endswith('OK')
    with tarfile.open(local/('owned-participant-full-debug-'+host+'-02.tar.gz')) as a:a.extractall(r/'evidence/owned-participant-2026-10-09',filter='data')
    result=json.loads((r/'evidence/owned-participant-2026-10-09/full-candidate-01'/host/'summary-debug.json').read_text())['debug']
    assert tuple(result[k] for k in ['core_checks','core_suites','harness_checks','P10_cases','failed','skipped'])==(1339,87,51,151,0,0)
with tarfile.open(local/'participant-channel-candidate-01.tar') as a:before={m.name:a.extractfile(m).read() for m in a if m.isfile()}
with tarfile.open(local/'participant-channel-candidate-02.tar') as a:after={m.name:a.extractfile(m).read() for m in a if m.isfile()}
assert before.keys()==after.keys();changed=[n for n in before if before[n]!=after[n]];assert changed==['tests/core/test_participant_channel.py']
n=changed[0];patch=''.join(difflib.unified_diff(before[n].decode().splitlines(True),after[n].decode().splitlines(True),fromfile='a/'+n,tofile='b/'+n))
write(e/'candidate-02-fixture.patch',patch);shutil.copyfile(local/'participant-channel-candidate-02.json',e/'participant-channel-candidate-02.json')
review=local/'participant-channel-fixture-review-02';review.mkdir()
with tarfile.open(local/'participant-channel-candidate-01.tar') as a:a.extractall(review,filter='data')
command=['git','apply','--directory='+review.relative_to(repo).as_posix(),str(e/'candidate-02-fixture.patch')]
subprocess.run(command[:2]+['--check']+command[2:],cwd=repo,check=True);subprocess.run(command,cwd=repo,check=True)
for n,raw in after.items():assert (review/n).read_bytes()==raw,n
for name in ['freeze-participant-channel-02.py','qualify-participant-channel-02.py','collect-participant-channel-02.py','diagnose-participant-channel-cleanup-02.py','checkpoint-participant-channel-02.py']:
    shutil.copyfile(local/name,e/'scripts'/name)
write(e/'fixture-review-02.txt','''Candidate02 fixture correction

Both original Debug runs executed 356 checks across 22 suites.
One new test errored before its final revision assertion. The other 355 checks passed.
The fixture retained one real issuer connection for every negative subcase.
Its eighth subcase exceeded the configured limit of eight connections, including the existing fixture connection.
The issuer closed the excess TLS connection during its handshake.

Close completed subcase peers and child resources before starting the next subcase.
Keep the connection limit, negative reply payloads, and every assertion unchanged.
All production files and prior test files match candidate01 exactly.
The correction patch reconstructs all 276 frozen files.

Both hosts ran the corrected seventeen tests against the original candidate01 production binaries.
All seventeen passed on each host. No case skipped.
This diagnoses the fixture lifecycle without attributing success to a production change.
Fresh candidate02 Debug and runtime ASan/UBSan qualification remains active.
The original failure logs, commands, source hashes, and descriptor audits are preserved.
''')
p=e/'source-review.json';d=json.loads(p.read_text());d['candidate']=2;d['qualification']='Candidate01 Debug356checks had1newfixture error on bothhosts. Candidate02 changes only completed subcase cleanup. Corrected17checks pass original binaries on bothhosts. Fresh both-profile qualification active.';d['fixture_review']='fixture-review-02.txt';write(p,json.dumps(d,indent=2)+'\n')
p=r/'CURRENT_STATE.md';s=p.read_text(encoding='utf-8');title='# Implementation status\n\n';assert s.startswith(title)
write(p,title+'''## 2026-10-09: Child-channel fixture corrected; owned participant full Debug passes

Private channel candidate01 ran 356 Debug checks across 22 suites on each host.
One new fixture test retained too many issuer connections and errored before its final assertion.
The other 355 checks passed. Both harness runs passed all 51 checks.
The original failures, commands, source, and additive protobuf audits remain preserved.

Candidate02 closes completed subcase connections before starting the next subcase.
All production files, previous tests, connection limits, and assertions remain unchanged.
Both hosts passed all seventeen corrected channel tests using the original production binaries.
Fresh candidate02 Debug and runtime ASan/UBSan qualification is active on both hosts.
Read evidence/participant-channel-2026-10-09/fixture-review-02.txt.

Owned participant candidate01 passed full Debug qualification on both hosts.
Each host passed 1339 core checks across 87 suites, 51 harness checks, and 151 P10 cases.
No Debug case failed or skipped. Full runtime sanitizer qualification continues.
The launch-authorization full sanitizer matrix also remains active.

No new source is integrated. Production relay, fencing, checked recovery, M02, and Spatial-M1 remain incomplete.

'''+s[len(title):])
p=r/'docs/AUTHORITY_PARTICIPANT_CHANNEL.txt';s=p.read_text().replace('isolated child-transport candidate01','isolated child-transport candidate02');s+='\nCandidate01 exposed a new fixture connection-lifetime error on both hosts.\nCandidate02 closes completed subcases without changing production or assertions.\nAll seventeen corrected checks passed on both hosts using original production binaries.\nFresh both-profile qualification remains active.\n';write(p,s)
p=r/'tasks/m1/identity.participant_channel.json';d=json.loads(p.read_text());d['candidate']=2;d['qualification']='Corrected17checks passed original candidate01 binaries bothhosts. Full focused Debug/runtime qualification active on candidate02.';write(p,json.dumps(d,indent=2)+'\n')
p=r/'tasks/m1/identity.owned_participant.json';d=json.loads(p.read_text());d['full_Debug']='Both hosts passed1339core87suites51harness151P10; no failures or skips. Runtime sanitizer active.';write(p,json.dumps(d,indent=2)+'\n')
p=local/'driver-state.json';d=json.loads(p.read_text());d.update(current_step='participant_channel_candidate02_qualifying',last_turn='progress: cf21a64 preserved baseline and earlier results. Candidate01 terminal bothhosts1fixtureerror/356checks. Candidate02 closes completed subcases only. Corrected17checks pass original binaries bothhosts. FullownedDebug1339core87suites51harness151P10 collected bothhosts.',next_test='Poll91399/1783 privatechannel02;78618/7728 authorizationfull;24301/73141 ownedfull. Candidate01 23021/62845 terminal and preserved. Diagnostics74827/79694 terminal passed. Do not restart. Continue parent relay with child consumption semantics.')
d['qualification_sessions'].update(participant_channel_sb16=91399,participant_channel_sb16_2=1783)
d['completed_session_handles'].update(participant_channel01_sb16=23021,participant_channel01_sb16_2=62845,participant_channel_cleanup_sb16=74827,participant_channel_cleanup_sb16_2=79694)
write(p,json.dumps(d,indent=2)+'\n')
print('Preserved both original failures and corrected17check diagnostics; saved owned fullDebug1339checks bothhosts.')
