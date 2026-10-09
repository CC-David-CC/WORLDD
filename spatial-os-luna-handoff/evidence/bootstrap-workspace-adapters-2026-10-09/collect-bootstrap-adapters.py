import hashlib,json,pathlib,re,socket,subprocess,sys,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
mode=sys.argv[1];assert mode in ('debug','final')
source=root/'local/m01-adapters-source-03'
manifest=json.loads((root/'local/adapters-tested-tree-03.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
summary={};files=[]
for label in ['debug']+(['runtime-sanitize'] if mode=='final' else []):
    folder=root/('local/m01-adapters-'+label+'-03');build=folder/'build'
    report=json.loads((folder/'run.json').read_text())
    assert [r['name'] for r in report['results']]==['configure','build','harness','adapters','core','P10']
    assert all(r['exit_code']==0 for r in report['results'])
    names=[];total=0;partitions={}
    for part,suites,checks in [('adapters',5,63),('core',50,973)]:
        log=(folder/(part+'.log')).read_text()
        counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
        assert len(counts)==suites and sum(counts)==checks,(host,label,part,counts)
        assert f'100% tests passed, 0 tests failed out of {suites}' in log
        assert 'skipped=' not in log and '... skipped ' not in log
        executed=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M)
        assert len(executed)==suites and len(set(executed))==suites
        names+=executed;total+=sum(counts);partitions[part]={'suites':dict(zip(executed,counts)),'checks':sum(counts)}
    assert total==1036 and len(names)==len(set(names))==55
    listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
    assert {t['name'] for t in listed['tests']}==set(names)
    harness=(folder/'harness.log').read_text()
    assert re.search(r'Ran 51 tests in ',harness) and 'OK' in harness and 'skipped=' not in harness
    p10=json.loads((folder/'P10.json').read_text())
    assert p10['status']=='passed' and p10['selected']==p10['executed']==len(p10['tests'])==151
    assert all(t['status']=='passed' for t in p10['tests'])
    summary[label]={'core_checks':total,'core_suites':len(names),'partitions':partitions,'harness_checks':51,'P10_cases':151,
        'failed':0,'skipped':0,'binaries':{name:hashlib.sha256((build/name).read_bytes()).hexdigest()
            for name in ('worldd','workspaced','identityd','spatialwm','spatial-test-host','spatial-broker-control-client-probe')}}
    for path in folder.iterdir():
        if path.is_file() and path.suffix in ('.log','.json'):files.append((path,host+'/'+folder.name+'/'+path.name))
summary.update(source_files=len(manifest['files']),production_integration='pending',native_server_GLX_sanitizer='unqualified')
record=root/('local/m01-adapters-summary-'+mode+'-03.json');record.write_text(json.dumps(summary,indent=2)+'\n')
files.append((record,host+'/summary-'+mode+'-03.json'))
files.append((root/'local/adapters-tested-tree-03.json',host+'/adapters-tested-tree-03.json'))
with tarfile.open(root/('local/adapters-evidence-'+mode+'-'+host+'-03.tar.gz'),'w:gz') as archive:
    for path,name in files:archive.add(path,arcname=name)
print(host,mode,'verified 1036 core checks, 51 harness checks, 151 P10 cases per collected profile',flush=True)
