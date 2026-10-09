import hashlib,json,pathlib,re,socket,subprocess,sys,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
mode=sys.argv[1];assert mode in ('debug','final')
manifest=json.loads((root/'local/domain-production-tree-01.json').read_text())
for name,digest in manifest['files'].items(): assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
summary={};files=[]
for label,build in [('debug','build')]+([('runtime-sanitize','build-sanitize')] if mode=='final' else []):
    folder=root/('local/m01-domain-'+label+'-01')
    report=json.loads((folder/'run.json').read_text())
    assert [r['name'] for r in report['results']]==['configure','build','harness','authority','core','P10']
    assert all(r['exit_code']==0 for r in report['results'])
    names=[];checks=0;parts={}
    for part,suites,total in [('authority',2,75),('core',48,898)]:
        log=(folder/(part+'.log')).read_text()
        counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
        assert len(counts)==suites and sum(counts)==total,(host,label,part,len(counts),sum(counts))
        assert f'100% tests passed, 0 tests failed out of {suites}' in log
        assert 'skipped=' not in log and '... skipped ' not in log
        executed=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M)
        assert len(executed)==suites and len(set(executed))==suites
        names+=executed;checks+=sum(counts);parts[part]={'checks':sum(counts),'suites':suites}
    assert checks==973 and len(names)==len(set(names))==50
    listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(root/build),'--show-only=json-v1']))
    assert {t['name'] for t in listed['tests']}==set(names)
    harness=(folder/'harness.log').read_text()
    assert re.search(r'Ran 51 tests in ',harness) and 'OK' in harness and 'skipped=' not in harness
    p10=json.loads((folder/'P10.json').read_text())
    assert p10['status']=='passed' and p10['executed']==p10['selected']==len(p10['tests'])==151
    assert all(t['status']=='passed' for t in p10['tests'])
    summary[label]={'core_checks':checks,'core_suites':len(names),'partitions':parts,'harness_checks':51,'P10_cases':151,
                    'failed':0,'skipped':0,'suite_names':sorted(names),
                    'binaries':{name:hashlib.sha256((root/build/name).read_bytes()).hexdigest() for name in
                        ('worldd','workspaced','identityd','spatialwm','spatial-test-host','spatial-authority-probe','spatial-authority-migrate')}}
    for path in folder.iterdir():
        if path.is_file() and path.suffix in ('.log','.json','.descriptor'):
            files.append((path,host+'/'+folder.name+'/'+path.name))
summary.update(source_files=len(manifest['files']),native_server_GLX_sanitizer='unqualified',authenticated_domain_delivery='pending')
record=root/('local/m01-domain-summary-'+mode+'.json');record.write_text(json.dumps(summary,indent=2)+'\n')
files.append((record,host+'/summary-'+mode+'.json'))
for name in ('domain-production-tree-01.json','m01-domain-source-audit-01.json'):
    files.append((root/'local'/name,host+'/'+name))
with tarfile.open(root/('local/domain-evidence-'+mode+'-'+host+'.tar.gz'),'w:gz') as archive:
    for path,name in files: archive.add(path,arcname=name)
print(host,mode,': 973 core checks across all configured suites, 51 harness, 151 P10 per collected profile; no failures or skips',flush=True)
