import hashlib,json,pathlib,re,socket,subprocess,sys,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
mode=sys.argv[1];assert mode in ('focused','debug','final')
source=root/'local/m02-process-termination-source-02';manifest=json.loads((root/'local/process-termination-candidate-02.json').read_text());base=json.loads((root/'evidence/participant-barrier-2026-10-09/integration-applied-05.json').read_text())['files']
for n,h in manifest['files'].items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
assert {n for n,h in base.items() if manifest['files'][n]!=h}=={'CMakeLists.txt','src/workspace/process.cpp','src/workspace/process.hpp'}
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for n,h in locked.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
files=[];summary=dict(host=host,mode=mode,source_files=len(manifest['files']),locked_files=len(locked),existing_tests_and_schemas='unchanged',integrated=False)
for profile in ['debug']+(['runtime-sanitize'] if mode=='final' else []):
    folder=root/('local/m02-process-termination-'+profile+'-02');run=json.loads((folder/'run.json').read_text());commands=['configure','build','harness','termination']+(['core','P10'] if mode!='focused' else [])
    assert [r['name'] for r in run['results']][:len(commands)]==commands and all(r['exit_code']==0 for r in run['results'][:len(commands)])
    parts={};names=[];total=0
    for name,suites,count in [('termination',1,6)]+([('core',68,1186)] if mode!='focused' else []):
        text=(folder/(name+'.log')).read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)];assert len(counts)==suites and sum(counts)==count and f'100% tests passed, 0 tests failed out of {suites}' in text and 'skipped=' not in text and '... skipped ' not in text
        executed=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',text,re.M);assert len(executed)==len(set(executed))==suites;names+=executed;total+=count;parts[name]=dict(checks=count,suites=dict(zip(executed,counts)))
    text=(folder/'harness.log').read_text();assert re.search(r'Ran 51 tests in ',text) and text.rstrip().endswith('OK')
    if mode!='focused':
        assert len(names)==len(set(names))==69 and total==1192
        listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(folder/'build'),'--show-only=json-v1']));assert {t['name'] for t in listed['tests']}==set(names)
        report=json.loads((folder/'P10.json').read_text());assert report['status']=='passed' and report['selected']==report['executed']==len(report['tests'])==151 and all(t['status']=='passed' for t in report['tests'])
    summary[profile]=dict(core_checks=total,core_suites=len(names),harness_checks=51,P10_cases=151 if mode!='focused' else 'uncollected',parts=parts,failed=0,skipped=0,binaries={n:hashlib.sha256((folder/'build'/n).read_bytes()).hexdigest() for n in ['workspaced','worldd','spatial-process-termination-probe','spatial-app-init','spatialwm','spatial-test-host']})
    label=profile+'-focused' if mode=='focused' else profile
    for name in ['run.json']+[n+'.log' for n in commands]+(['P10.json'] if mode!='focused' else []):files.append((folder/name,'candidate-02/'+host+'/'+label+'/'+name))
p=root/('local/process-termination-summary-'+mode+'-'+host+'-02.json');p.write_text(json.dumps(summary,indent=2)+chr(10));files.extend([(p,'candidate-02/'+host+'/summary-'+mode+'.json'),(root/'local/process-termination-candidate-02.json','candidate-02/'+host+'/source-manifest.json')])
with tarfile.open(root/('local/process-termination-'+mode+'-'+host+'-02.tar.gz'),'w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print(host,mode,'verified',len(manifest['files']),'source hashes and',len(locked),'locked files',flush=True)
