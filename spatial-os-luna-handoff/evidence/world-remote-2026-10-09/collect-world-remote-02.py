import hashlib,json,pathlib,re,socket,subprocess,sys,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');mode=sys.argv[1];assert mode in ('focused','debug','final')
source=root/'local/m01-world-remote-source-02';manifest=json.loads((root/'local/world-remote-source-02.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
files=[];summary={}
for label in ['debug']+(['runtime-sanitize'] if mode=='final' else []):
    folder=root/('local/m01-world-remote-'+label+'-02');build=folder/'build';report=json.loads((folder/'run.json').read_text())
    expected=['configure','build','harness','remote']+(['core','P10'] if mode!='focused' else [])
    assert [r['name'] for r in report['results']][:len(expected)]==expected
    assert all(r['exit_code']==0 for r in report['results'][:len(expected)])
    names=[];total=0;partitions={}
    for part,suites,checks in [('remote',7,46)]+([('core',55,1043)] if mode!='focused' else []):
        log=(folder/(part+'.log')).read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
        assert len(counts)==suites and sum(counts)==checks,(host,label,part,counts)
        assert f'100% tests passed, 0 tests failed out of {suites}' in log and 'skipped=' not in log and '... skipped ' not in log
        executed=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M);assert len(executed)==len(set(executed))==suites
        names+=executed;total+=sum(counts);partitions[part]={'suites':dict(zip(executed,counts)),'checks':sum(counts)}
    if mode!='focused':
        assert total==1089 and len(names)==len(set(names))==62
        listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
        assert {t['name'] for t in listed['tests']}==set(names)
        p10=json.loads((folder/'P10.json').read_text());assert p10['status']=='passed' and p10['selected']==p10['executed']==len(p10['tests'])==151
        assert all(t['status']=='passed' for t in p10['tests'])
    harness=(folder/'harness.log').read_text();assert re.search(r'Ran 51 tests in ',harness) and 'OK' in harness and 'skipped=' not in harness
    summary[label]={'core_checks':total,'core_suites':len(names),'partitions':partitions,'harness_checks':51,
        'P10_cases':151 if mode!='focused' else 'not collected','failed':0,'skipped':0,
        'binaries':{name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in
            ('worldd','workspaced','identityd','spatialwm','spatial-test-host','spatial-actor-tls-retry-probe')}}
    selected=['run.json']+[name+'.log' for name in expected]+(['P10.json'] if mode!='focused' else [])
    evidence_label=label+'-focused' if mode=='focused' else label
    for name in selected:files.append((folder/name,'candidate-02/'+host+'/'+evidence_label+'/'+name))
audit=root/'local/m01-world-remote-audit-02';record=json.loads((audit/'run.json').read_text())
assert record['status']=='passed' and record['unchanged_existing_test_files']==80 and record['locked_files']==41
summary.update(host=host,source_files=len(manifest['files']),scope=mode,production_integration='pending')
summary_file=root/('local/world-remote-summary-'+mode+'-02.json');summary_file.write_text(json.dumps(summary,indent=2)+'\n')
files += [(summary_file,'candidate-02/'+host+'/summary-'+mode+'.json'),(audit/'run.json','candidate-02/'+host+'/schema-audit.json'),
    (root/'local/world-remote-source-02.json','candidate-02/'+host+'/source-manifest.json')]
if mode=='focused':
    if host=='sb16':
        for version in ('01','02'):
            base=root/('local/m01-remote-retry-baseline-'+version)
            for p in base.iterdir():
                if p.is_file() and p.suffix in ('.json','.log','.cpp'):files.append((p,'retry-baseline-'+version+'/sb16/'+p.name))
        for version in ('01','02'):
            base=root/('local/m01-world-crosshost-server-'+version)
            for name in ('ready.json','client-result.json','summary.json','failure.txt'):
                if (base/name).exists():files.append((base/name,'crosshost-'+version+'/sb16/'+name))
        files.append((root/'local/m01-world-crosshost-client-03/result.json','crosshost-03/sb16/result.json'))
    else:
        for version in ('01','02'):files.append((root/('local/m01-world-crosshost-client-'+version+'/result.json'),'crosshost-'+version+'/sb16-2/result.json'))
        for name in ('ready.json','client-result.json','summary.json'):
            files.append((root/'local/m01-world-crosshost-server-03'/name,'crosshost-03/sb16-2/'+name))
with tarfile.open(root/('local/world-remote-'+mode+'-'+host+'-02.tar.gz'),'w:gz') as archive:
    for path,name in files:archive.add(path,arcname=name)
print(host,mode,'verified source233, remote46, harness51; '+('full core1089 and P10 151' if mode!='focused' else 'full profile still pending'),flush=True)
