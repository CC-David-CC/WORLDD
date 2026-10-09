import hashlib,json,pathlib,re,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');assert socket.gethostname().split('.')[0]=='sb16'
files=[];summary=dict(host='sb16',integrated=False,failed=0,skipped=0)
for candidate in ['01','02']:
    source=r/('local/m02-workspace-participant-'+candidate+'/source');manifest=json.loads((r/('local/workspace-participant-candidate-'+candidate+'.json')).read_text())['files']
    for name,digest in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
    out=source.parent/'debug';run=json.loads((out/'run.json').read_text());assert all(x['exit_code']==0 for x in run['results'])
    if candidate=='01':
        log=(out/'workspace.log').read_text();assert 'Ran 6 tests' in log and '100% tests passed' in log
        text=(out/'related.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
        assert sorted(counts)==[6,6,14] and '100% tests passed' in text
        summary['candidate01']=dict(source_files=249,workspace_checks=6,world_checks=6,worker_checks=14,process_checks=6)
    else:
        for name,count in [('native',2),('workspace',6)]:
            text=(out/(name+'.log')).read_text();assert f'Ran {count} tests' in text and text.rstrip().endswith('OK') and 'skipped' not in text
        summary['candidate02']=dict(source_files=250,workspace_checks=6,native_broker_checks=2,production_binaries='unchanged candidate01')
    for name in ['run.json']+[x['name']+'.log' for x in run['results']]:files.append((out/name,'candidate-'+candidate+'/sb16/debug/'+name))
    if (out/'server').exists():
        for path in (out/'server').glob('*.log'):files.append((path,'candidate-'+candidate+'/sb16/debug/server/'+path.name))
    files.append((r/('local/workspace-participant-candidate-'+candidate+'.json'),'candidate-'+candidate+'/source-manifest.json'))
p=r/'local/workspace-participant-focused-02.json';p.write_text(json.dumps(summary,indent=2)+'\n');files.append((p,'focused-summary-02.json'))
with tarfile.open(r/'local/workspace-participant-focused-02.tar.gz','w:gz') as archive:
    for path,name in files:archive.add(path,arcname=name)
print('Verified workspace32 focused checks and native2; six workspace checks rerun on fixture02')
