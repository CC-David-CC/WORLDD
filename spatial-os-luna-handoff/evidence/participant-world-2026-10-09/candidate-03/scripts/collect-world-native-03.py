import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=root/'local/m02-participant-world-draft-03';source=out/'source';manifest=json.loads((root/'local/participant-world-candidate-03.json').read_text())
for n,h in manifest['files'].items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for n,h in locked.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
base=out if host=='sb16-2' else root/'local/m02-participant-world-draft-02'
files=[];summary=dict(host=host,source_files=len(manifest['files']),locked_files=len(locked),scope='focused world and native input participation; broker, broad regression, supervisor fencing, and M02 qualification remain pending',integrated=False)
for profile in ['debug','runtime-sanitize']:
    folder=base/profile;run=json.loads((folder/'run.json').read_text());assert [r['name'] for r in run['results']]==['configure','build','world','worker'] and all(r['exit_code']==0 for r in run['results'])
    text=(folder/'world.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
    assert sorted(counts)==([1,6] if host=='sb16' else [2,6]) and '100% tests passed, 0 tests failed out of 2' in text
    text=(folder/'worker.log').read_text();assert re.search(r'Ran 14 tests in ',text) and '100% tests passed, 0 tests failed out of 1' in text
    for n in ['run.json','configure.log','build.log','world.log','worker.log','xserver.log']:files.append((folder/n,'candidate-03/'+host+'/'+profile+'/original-'+n))
    if host=='sb16':
        native=out/profile;receipt=json.loads((native/'run.json').read_text());assert receipt['exit_code']==0
        text=(native/'native.log').read_text();assert re.search(r'Ran 2 tests in ',text) and text.rstrip().endswith('OK') and 'skipped' not in text
        for n in ['run.json','native.log','xserver.log']:files.append((native/n,'candidate-03/'+host+'/'+profile+'/native-03-'+n))
    summary[profile]=dict(world_checks=6,native_checks=2,worker_checks=14,failed=0,skipped=0,binaries={n:hashlib.sha256((folder/'build'/n).read_bytes()).hexdigest() for n in ['worldd','identityd','spatialwm','spatial-xprobe','spatial-participant-runtime-probe']})
summarypath=out/'summary.json';summarypath.write_text(json.dumps(summary,indent=2)+chr(10));files.append((summarypath,'candidate-03/'+host+'/summary.json'));files.append((root/'local/participant-world-candidate-03.json','candidate-03/'+host+'/source-manifest.json'))
with tarfile.open(root/('local/world-native-'+host+'-03.tar.gz'),'w:gz') as archive:
    for p,n in files:archive.add(p,arcname=n)
print(host,'6world+2native+14worker each Debug and runtime sanitizers;246 source hashes;',len(locked),'locks verified',flush=True)
