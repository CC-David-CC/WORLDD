"""Collect completed supervisor fencing profiles without changing running jobs."""
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tarfile

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
mode=sys.argv[1];assert mode in ('new-debug','new-final','debug','final')
full=mode in ('debug','final');profiles=['debug','runtime-sanitize'] if mode in ('new-final','final') else ['debug']
out=r/'local/m02-supervisor-crash-fencing-01';source=out/'source'
manifest_path=r/'local/supervisor-crash-fencing-candidate-01.json';manifest=json.loads(manifest_path.read_text())['files']
assert len(manifest)==294
locked=json.loads((source/'acceptance.lock.json').read_text())['files'];assert len(locked)==41
for files in (manifest,locked):
    for n,digest in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==digest,n
schema=json.loads((out/'schema-audit.json').read_text())
assert schema['existing_schema_sources_unchanged'] and schema['existing_tests_unchanged'] and schema['locked_files']==41
prefix='candidate-01/'+host+'/'
files=[(manifest_path,prefix+'source-manifest.json')]+[(out/n,prefix+n) for n in ('schema-audit.json',)]
summary=dict(host=host,candidate=1,source_files=294,locked_files=41,mode=mode,integrated=False,schema=schema)
for profile in profiles:
    target=out/profile;run_name='run-full.json' if full else 'run.json'
    run=json.loads((target/run_name).read_text())
    names=['configure','build','harness','fencing']+(['focused','core','P10'] if full else [])
    assert [v['name'] for v in run['results']][:len(names)]==names
    assert all(v['exit_code']==0 for v in run['results'][:len(names)])
    assert run['host']==host and run['source_files']==294
    server=run['server'];assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
    harness=(target/'harness.log').read_text();assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    suite_names=[];total=0;parts={}
    for part in ['fencing']+(['focused','core'] if full else []):
        log=(target/(part+'.log')).read_text()
        counts=list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)))
        executed=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M)
        assert counts and len(counts)==len(executed)
        assert f'100% tests passed, 0 tests failed out of {len(counts)}' in log
        assert 'skipped=' not in log and '... skipped' not in log
        assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',log)
        if part=='fencing':assert len(counts)==1 and sum(counts)==10
        parts[part]=dict(zip(executed,counts));suite_names.extend(executed);total+=sum(counts)
    assert len(suite_names)==len(set(suite_names))==(99 if full else 1)
    assert total==(1453 if full else 10)
    if full:
        listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(target/'build'),'--show-only=json-v1']))
        assert {t['name'] for t in listed['tests']}==set(suite_names)
        p10=json.loads((target/'P10.json').read_text());assert p10['status']=='passed' and p10['executed']==len(p10['tests'])==151
        assert all(t['status']=='passed' for t in p10['tests'])
    summary[profile]=dict(core_checks=total,core_suites=len(suite_names),harness_checks=51,
        P10_cases=151 if full else 'uncollected',failed=0,skipped=0,parts=parts,
        binaries={n:hashlib.sha256((target/'build'/n).read_bytes()).hexdigest() for n in
            ('worldd','workspaced','identityd','spatialwm','spatial-test-host','spatial-authority-supervisor',
             'spatial-issuer-client','spatial-authority-launch-probe')})
    label=profile if full else profile+'-new'
    for n in [run_name]+[n+'.log' for n in names]+(['P10.json'] if full else []):files.append((target/n,prefix+label+'/'+n))
target=r/('local/supervisor-crash-fencing-summary-'+mode+'-'+host+'-01.json')
target.write_text(json.dumps(summary,indent=2)+'\n');files.append((target,prefix+'summary-'+mode+'.json'))
with tarfile.open(r/('local/supervisor-crash-fencing-'+mode+'-'+host+'-01.tar.gz'),'w:gz') as archive:
    for p,n in files:archive.add(p,arcname=n)
print(host,mode,'verified294source41locks',total,'core checks per collected profile')
