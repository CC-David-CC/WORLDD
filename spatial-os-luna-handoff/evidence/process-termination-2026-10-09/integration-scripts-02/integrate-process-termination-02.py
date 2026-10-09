import hashlib,json,pathlib,shutil
r=pathlib.Path(__file__).resolve().parents[1];e=r/'evidence/process-termination-2026-10-09'
manifest=json.loads((r/'local/process-termination-candidate-02.json').read_text(encoding='utf-8'))['files']
base=json.loads((r/'evidence/participant-barrier-2026-10-09/integration-applied-05.json').read_text(encoding='utf-8'))['files']
for host in ['sb16','sb16-2']:
    folder=e/'candidate-02'/host;summary=json.loads((folder/'summary-final.json').read_text(encoding='utf-8'))
    assert json.loads((folder/'source-manifest.json').read_text(encoding='utf-8'))['files']==manifest
    for profile in ['debug','runtime-sanitize']:
        result=summary[profile]
        assert tuple(result[k] for k in ['core_checks','core_suites','harness_checks','P10_cases','failed','skipped'])==(1192,69,51,151,0,0)
        run=json.loads((folder/profile/'run.json').read_text(encoding='utf-8'));assert [x['name'] for x in run['results']]==['configure','build','harness','termination','core','P10'] and all(x['exit_code']==0 for x in run['results'])
        report=json.loads((folder/profile/'P10.json').read_text(encoding='utf-8'));assert report['status']=='passed' and report['executed']==len(report['tests'])==151 and all(x['status']=='passed' for x in report['tests'])
for n,h in base.items():assert hashlib.sha256((r/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==h,n
changed={n for n in manifest if n not in base or manifest[n]!=base[n]}
assert changed=={'CMakeLists.txt','src/workspace/process.cpp','src/workspace/process.hpp','src/testing/process_termination_probe.cpp','tests/core/test_process_termination.py'}
draft=r/'local/process-termination-draft-01'
data={n:(draft/n).read_bytes().replace(b'\r\n',b'\n') for n in manifest}
for n,h in manifest.items():assert hashlib.sha256(data[n]).hexdigest()==h,n
for n in changed:(r/n).write_bytes(data[n])
for n,h in manifest.items():assert hashlib.sha256((r/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==h,n
(e/'integration-applied-02.json').write_text(json.dumps(dict(files=manifest,changed=sorted(changed),hosts=['sb16','sb16-2'],profiles=['Debug','runtime ASan/UBSan'],core_checks=1192,harness_checks=51,P10_cases=151,locked_files=41,review='Primary agent reviewed all five changed source and test files; baseline failures preserved.'),indent=2)+'\n',encoding='utf-8',newline='\n')
print('Integrated five reviewed files; all242 source hashes match both full qualifications')
