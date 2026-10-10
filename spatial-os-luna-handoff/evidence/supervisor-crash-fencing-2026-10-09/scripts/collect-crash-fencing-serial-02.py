"""Collect complete frozen294 profiles; refuse missing, failed, or overlapping runs."""
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
mode=sys.argv[1];assert mode in ('debug','final')
profiles=['debug'] if mode=='debug' else ['debug','runtime-sanitize']
out=r/'local/crash-fencing-serial-02'
candidate=r/'local/m02-supervisor-crash-fencing-01'
source=candidate/'source'
manifest_path=r/'local/supervisor-crash-fencing-candidate-01.json'
manifest=json.loads(manifest_path.read_text())['files'];assert len(manifest)==294
locks=json.loads((source/'acceptance.lock.json').read_text())['files'];assert len(locks)==41
for files in (manifest,locks):
    for name,value in files.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==value,name
run=json.loads((out/'summary.json').read_text())
assert run['host']==host and run['source_manifest']==manifest and not run['compiler_overlap']
if mode=='final':assert run['qualification']=='passed'
monitor=(out/'compiler-monitor.jsonl').read_text()
observations=[json.loads(line) for line in monitor.splitlines()]
assert observations and not any(x['compilers'] for x in observations)
summary=dict(host=host,mode=mode,source_files=294,locked_files=41,profiles={},integrated=False,
    production_changed=False,source_manifest=manifest,scheduling=run['scheduling'])
collected=[(manifest_path,'source-manifest.json')]
inventories={}
for profile in profiles:
    target=out/profile;data=run['profiles'][profile];results=data['results']
    assert [x['name'] for x in results]==['harness','fencing','focused','core','P10']
    assert all(x['exit_code']==0 for x in results)
    assert observations[0]['time']<=results[0]['start'] and observations[-1]['time']>=results[-1]['end']-2
    build=candidate/profile/'build'
    assert str(build)==data['build']
    cache=(build/'CMakeCache.txt').read_text()
    assert 'CMAKE_BUILD_TYPE:STRING=Debug' in cache
    assert 'SPATIAL_SANITIZERS:BOOL='+('OFF' if profile=='debug' else 'ON') in cache
    for name,value in data['binaries'].items():assert hashlib.sha256((build/name).read_bytes()).hexdigest()==value,name
    server=data['server'];assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
    inventory=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
    inventories[profile]=inventory
    suites=[];total=0;parts={}
    for part in ('fencing','focused','core'):
        text=(target/(part+'.log')).read_text()
        counts=list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)))
        names=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',text,re.M)
        assert counts and len(counts)==len(names)
        assert f'100% tests passed, 0 tests failed out of {len(counts)}' in text
        assert 'skipped=' not in text and '... skipped' not in text
        assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',text)
        if part=='fencing':assert counts==[10]
        suites.extend(names);total+=sum(counts);parts[part]=dict(zip(names,counts))
    assert total==1453 and len(suites)==len(set(suites))==99
    assert set(suites)=={x['name'] for x in inventory['tests']}
    harness=(target/'harness.log').read_text()
    assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    assert 'skipped=' not in harness and '... skipped' not in harness
    p10=json.loads((target/'P10.json').read_text())
    assert p10['status']=='passed' and p10['executed']==len(p10['tests'])==151
    assert all(x['status']=='passed' for x in p10['tests'])
    summary['profiles'][profile]=dict(core_checks=1453,core_suites=99,harness_checks=51,P10_cases=151,
        failed=0,skipped=0,parts=parts,binaries=data['binaries'],server=server,commands=results,
        sanitizer_configuration='Debug' if profile=='debug' else 'ASan with leak detection and UBSan',
        compiler_monitor_samples=sum(x['time']<=results[-1]['end']+1 for x in observations))
    for name in ('harness.log','fencing.log','focused.log','core.log','P10.log','P10.json'):
        collected.append((target/name,profile+'/'+name))
    for name in ('configure.log','build.log','run.json'):
        collected.append((candidate/profile/name,profile+'/original-build/'+name))
    collected.append((build/'Testing/Temporary/LastTest.log',profile+'/LastTest.log'))
# Write only after every requested profile passes the checks above.
summary_path=r/('local/crash-fencing-serial-summary-'+mode+'-'+host+'-02.json')
summary_path.write_text(json.dumps(summary,indent=2)+'\n')
snapshot=r/('local/crash-fencing-serial-run-'+mode+'-'+host+'-02.json')
snapshot.write_text(json.dumps(run,indent=2)+'\n')
monitor_path=r/('local/crash-fencing-serial-monitor-'+mode+'-'+host+'-02.jsonl')
monitor_path.write_text(monitor)
collected += [(summary_path,'summary-'+mode+'.json'),(snapshot,'run-'+mode+'.json'),
              (monitor_path,'compiler-monitor-'+mode+'.jsonl')]
for profile,value in inventories.items():
    path=r/('local/crash-fencing-serial-inventory-'+profile+'-'+host+'-02.json')
    path.write_text(json.dumps(value,indent=2)+'\n');collected.append((path,profile+'/ctest-inventory.json'))
with tarfile.open(r/('local/crash-fencing-serial-'+mode+'-'+host+'-02.tar.gz'),'w:gz') as archive:
    for path,name in collected:archive.add(path,arcname='serial-02/'+host+'/'+name)
print(host,mode,'verified294source41locks1453core99suites51harness151P10')
