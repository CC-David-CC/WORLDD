import json,re,shutil,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];local=r/'local';e=r/'evidence/participant-relay-2026-10-09'
def write(p,s):p.write_text(s,encoding='utf-8',newline='\n')
for host in ['sb16','sb16-2']:
    with tarfile.open(local/('participant-relay-debug-'+host+'-01.tar.gz')) as a:a.extractall(e,filter='data')
    summary=json.loads((e/'candidate-01'/host/'summary-debug.json').read_text())
    assert summary['source_files']==277 and summary['locked_files']==41 and summary['schema']['existing_descriptors_unchanged']
    result=summary['debug'];assert tuple(result[k] for k in ['checks','suites','new_checks','harness_checks','failed','skipped'])==(366,23,10,51,0,0)
    log=(e/'candidate-01'/host/'debug/focused.log').read_text()
    counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
    assert len(counts)==23 and sum(counts)==366 and '100% tests passed, 0 tests failed out of 23' in log
p=e/'source-review.json';d=json.loads(p.read_text());d['qualification']='Both hosts passed 366 focused Debug checks across 23 suites and 51 harness checks. No failures or skips. Runtime sanitizer qualification remains active.';write(p,json.dumps(d,indent=2)+'\n')
p=r/'CURRENT_STATE.md';s=p.read_text(encoding='utf-8').replace('Its Debug builds are running on both hosts. No relay qualification result is claimed yet.','Both hosts passed 366 focused Debug checks across 23 suites and 51 harness checks.\nNo Debug case failed or skipped. Runtime sanitizer qualification is active.\nDescriptor review confirms additive protobuf changes. Full cumulative continuation is prepared but has not started.');write(p,s)
p=r/'docs/AUTHORITY_PARTICIPANT_RELAY.txt';s=p.read_text().replace('Status: isolated candidate01 implemented. Both-host qualification is running.','Status: isolated candidate01 passed focused Debug on both hosts. Sanitizer qualification is running.')
s+='\nBoth hosts passed 366 focused Debug checks across 23 suites and 51 harness checks.\nAll ten new relay tests passed. No Debug case failed or skipped.\nExisting protobuf declarations and 41 locked files remain unchanged.\nFull cumulative scripts reuse the frozen builds and do not repeat completed focused tests.\n';write(p,s)
p=r/'tasks/m1/identity.participant_relay.json';d=json.loads(p.read_text());d['Debug']='Both hosts passed 366 focused checks across 23 suites and 51 harness checks; no failures or skips.';d['next']='Finish runtime sanitizers, then run the prepared cumulative continuation. Wire the production supervisor event loop to worldd and workspaced.';write(p,json.dumps(d,indent=2)+'\n')
for name in ['prepare-participant-relay-full-01.py','finish-participant-relay-full-01.py','collect-participant-relay-full-01.py','checkpoint-participant-relay-debug-01.py']:
    shutil.copyfile(local/name,e/'scripts'/name)
p=local/'driver-state.json';d=json.loads(p.read_text());d['commit']='b87b8c5347581186f8456d422bdc571462116f5f';d['last_turn']='Progress: b87b8c5 records the reviewed relay candidate, both-host baseline, completed child-channel focused profiles, and full launch-authorization matrix. Relay Debug now passed 366 checks across 23 suites and 51 harness checks on both hosts. Sanitizers remain active. Cumulative scripts are prepared locally, not copied or started.';d['next_test']='Poll relay 96501/69305 and owned full 24301/73141. Collect relay final with collect-participant-relay-01.py. After both focused profiles complete, copy/run finish-participant-relay-full-01.py and collector. Expected full totals: 1366 core checks, 89 suites, 51 harness checks, 151 P10 cases per profile. These are expected totals, not results. Then wire production supervisor event loop, worldd, and workspaced.';write(p,json.dumps(d,indent=2)+'\n')
print('Saved both-host relay Debug evidence: 366 checks, 23 suites, 51 harness checks each. Sanitizer and full qualification pending.')
