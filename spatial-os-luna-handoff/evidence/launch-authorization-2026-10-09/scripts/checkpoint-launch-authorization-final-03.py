import hashlib,json,re,shutil,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];local=r/'local';e=r/'evidence/launch-authorization-2026-10-09'
for host in ['sb16','sb16-2']:
    with tarfile.open(local/('launch-authorization-final-'+host+'-03.tar.gz')) as a:a.extractall(e,filter='data')
    summary=json.loads((e/'candidate-03'/host/'summary-final.json').read_text(encoding='utf-8'))
    assert summary['source_files']==273 and summary['locked_files']==41 and summary['schema']['existing_descriptors_unchanged']
    for profile in ['debug','runtime-sanitize']:
        result=summary[profile]
        assert tuple(result[k] for k in ['checks','new_checks','existing_checks','suites','harness_checks','failed','skipped'])==(285,23,262,16,51,0,0)
        log=(e/'candidate-03'/host/profile/'focused.log').read_text(encoding='utf-8')
        counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
        assert len(counts)==16 and sum(counts)==285 and '100% tests passed, 0 tests failed out of 16' in log
        assert 'skipped=' not in log and '... skipped' not in log
def write(p,s):p.write_text(s,encoding='utf-8',newline='\n')
p=e/'candidate-03-review.json';d=json.loads(p.read_text(encoding='utf-8'));d['qualification']='Both hosts passed285 focused checks across16suites and51harness in Debug and runtime ASan/UBSan. No failures or skips. Full qualification running; source not integrated.';write(p,json.dumps(d,indent=2)+'\n')
p=r/'CURRENT_STATE.md';s=p.read_text(encoding='utf-8');title='# Implementation status\n\n';assert s.startswith(title)
note='''## 2026-10-09: Exact launch authorization passes both focused profiles

Candidate03 passed Debug and runtime ASan/UBSan on both hosts.
Each profile passed 285 focused checks across sixteen suites and 51 harness checks.
No final case failed or skipped. All 273 source hashes and 41 locked hashes match.
Descriptor comparison preserves every existing protobuf declaration.
The unique configuration-file correction resolves the reproduced fixture race.
All earlier assertions and production source remain unchanged by candidate03.
Original failures and diagnostics remain preserved beside the final reports.

Full authorization qualification has started on both hosts in output03.
Its expected matrix is 1328 core checks across 86 suites, 51 harness checks, and 151 P10 cases per profile.
Expected totals are not completed results.
The supervisor must still connect approval to retained launch ownership and checked recovery.
No authorization source is integrated. M02 and Spatial-M1 remain incomplete.

'''
write(p,title+note+s[len(title):])
p=r/'docs/AUTHORITY_LAUNCH_AUTHORIZATION.txt';s=p.read_text(encoding='utf-8').replace('Candidate03 passed 285 focused Debug checks and 51 harness checks on both hosts.\nNo Debug case failed or skipped. Runtime sanitizer and full qualification remain pending.','Candidate03 passed 285 focused checks and 51 harness checks on both hosts in Debug and runtime ASan/UBSan.\nNo final case failed or skipped. Full qualification is running separately in output03.')
write(p,s)
p=r/'tasks/m1/identity.launch_authorization.json';d=json.loads(p.read_text(encoding='utf-8'));d['focused']='Candidate03 passed285 focused checks across16suites and51harness on both hosts in Debug and runtime ASan/UBSan. No failures or skips.';d['full']='Output03 full qualification is running on both hosts. Expected1328core86suites51harness151P10 perprofile; no completed full result yet.';d['remaining'][0]='Complete full qualification on both hosts.';write(p,json.dumps(d,indent=2)+'\n')
p=r/'tasks/m1/identity.supervisor_recovery.json';d=json.loads(p.read_text(encoding='utf-8'));d['current_step']='launch_authorization_focused_qualified_full_running';write(p,json.dumps(d,indent=2)+'\n')
p=local/'driver-state.json';d=json.loads(p.read_text(encoding='utf-8'));d['current_step']='launch_authorization_candidate03_full_running';d['last_turn']='progress: candidate03 passed285checks16suites51harness on both hosts in both profiles; final reports collected and audited. Full authorization qualification started in output03 on both hosts.';d['qualification_sessions']={'supervisor_registry_full_sb16':25633,'supervisor_registry_full_sb16_2':25219,'launch_authorization_full_sb16':78618,'launch_authorization_full_sb16_2':7728};d['next_test']='Poll78618/7728 authorizationfull03 and25633/25219 registryfull03. Authorization33950/64105 and incarnation34786/3576 are terminal and collected; do not restart. Continue supervisor-owned participant channel/signing bridge, fencing, and recovery. No new source integrated.';write(p,json.dumps(d,indent=2)+'\n')
for n in ['qualify-launch-authorization-full-03.py','collect-launch-authorization-full-03.py','audit-launch-authorization-debug-03.py','checkpoint-launch-authorization-final-03.py']:
    shutil.copyfile(local/n,e/'scripts'/n)
print('Both focused profiles verified on both hosts; full qualification active; no production integration.')
