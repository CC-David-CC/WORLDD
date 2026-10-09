import hashlib,json,pathlib,re,socket,subprocess,sys,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
mode=sys.argv[1];assert mode in ('focused','debug','final')
source=r/'local/m02-participant-fixtures-06/source';manifest=json.loads((r/'local/participant-fixtures-candidate-05.json').read_text())['files'];locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for files in [manifest,locked]:
    for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
files=[];summary=dict(host=host,candidate=5,output=6,mode=mode,source_files=len(manifest),locked_files=len(locked),integrated=False)
for profile in ['debug']+(['runtime-sanitize'] if mode=='final' else []):
    out=source.parent/profile;run=json.loads((out/'run.json').read_text());commands=['configure','build','harness','focused']+(['core','P10'] if mode!='focused' else [])
    assert [x['name'] for x in run['results']][:len(commands)]==commands and all(x['exit_code']==0 for x in run['results'][:len(commands)])
    names=[];parts={};total=0
    for name,suites,count in [('focused',10,79)]+([('core',66,1151)] if mode!='focused' else []):
        log=(out/(name+'.log')).read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
        assert len(counts)==suites and sum(counts)==count and f'100% tests passed, 0 tests failed out of {suites}' in log and '... skipped' not in log and 'skipped=' not in log
        executed=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M);assert len(executed)==len(set(executed))==suites
        names+=executed;parts[name]=dict(zip(executed,counts));total+=count
    harness=(out/'harness.log').read_text();assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    if mode!='focused':
        listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(out/'build'),'--show-only=json-v1']));assert {t['name'] for t in listed['tests']}==set(names) and len(names)==76 and total==1230
        report=json.loads((out/'P10.json').read_text());assert report['status']=='passed' and report['executed']==len(report['tests'])==151 and all(t['status']=='passed' for t in report['tests'])
    summary[profile]=dict(core_checks=total,core_suites=len(names),parts=parts,harness_checks=51,P10_cases=151 if mode!='focused' else 'uncollected',failed=0,skipped=0,binaries={n:hashlib.sha256((out/'build'/n).read_bytes()).hexdigest() for n in ['worldd','workspaced','identityd','spatialwm','spatial-app-init','spatial-test-host']})
    label=profile+'-focused' if mode=='focused' else profile
    for n in ['run.json']+[n+'.log' for n in commands]+(['P10.json'] if mode!='focused' else []):files.append((out/n,'candidate-05/'+host+'/'+label+'/'+n))
p=r/('local/participant-fixtures-summary-'+mode+'-'+host+'-06.json');p.write_text(json.dumps(summary,indent=2)+'\n');files.append((p,'candidate-05/'+host+'/summary-'+mode+'.json'))
files.append((r/'local/participant-fixtures-candidate-05.json','candidate-05/'+host+'/source-manifest.json'))
with tarfile.open(r/('local/participant-fixtures-'+mode+'-'+host+'-06.tar.gz'),'w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print(host,mode,'verified252 source hashes and41 locked files')
