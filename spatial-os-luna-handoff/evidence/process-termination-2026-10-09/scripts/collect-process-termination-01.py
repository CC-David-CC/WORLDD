import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=root/'local/m02-process-termination-draft-01';source=out/'source';manifest=json.loads((root/'local/process-termination-candidate-01.json').read_text())
for n,h in manifest['files'].items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for n,h in locked.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
files=[];summary=dict(host=host,source_files=len(manifest['files']),locked_files=len(locked),scope='focused checked termination and workspace regression; full matrix and candidate02 destructor witnesses pending',integrated=False)
for profile in ['debug','runtime-sanitize']:
    folder=out/profile;report=json.loads((folder/'run.json').read_text());assert [r['name'] for r in report['results']]==['configure','build','termination','regression'] and all(r['exit_code']==0 for r in report['results'])
    for name,suites,count in [('termination',1,4),('regression',3,51)]:
        text=(folder/(name+'.log')).read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
        assert len(counts)==suites and sum(counts)==count and f'100% tests passed, 0 tests failed out of {suites}' in text and 'skipped' not in text
    summary[profile]=dict(termination_checks=4,regression_checks=51,failed=0,skipped=0,binaries={n:hashlib.sha256((folder/'build'/n).read_bytes()).hexdigest() for n in ['workspaced','worldd','spatial-process-termination-probe','spatial-app-init']})
    for name in ['configure.log','build.log','termination.log','regression.log','run.json']:files.append((folder/name,'candidate-01/'+host+'/'+profile+'/'+name))
p=out/'summary.json';p.write_text(json.dumps(summary,indent=2)+chr(10));files.extend([(p,'candidate-01/'+host+'/summary.json'),(root/'local/process-termination-candidate-01.json','candidate-01/'+host+'/source-manifest.json')])
with tarfile.open(root/('local/process-termination-focused-'+host+'-01.tar.gz'),'w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print(host,'4 new +51regression eachprofile;242hashes41locks passed',flush=True)
