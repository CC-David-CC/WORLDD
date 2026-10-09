import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
folder=root/'local/m01-adapters-debug-03'
report=json.loads((folder/'run.json').read_text())
assert [r['name'] for r in report['results'][:4]]==['configure','build','harness','adapters']
assert all(r['exit_code']==0 for r in report['results'][:4])
log=(folder/'adapters.log').read_text()
counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
names=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M)
assert len(counts)==len(names)==5 and sum(counts)==63
assert '100% tests passed, 0 tests failed out of 5' in log and 'skipped=' not in log and '... skipped ' not in log
harness=(folder/'harness.log').read_text();assert re.search(r'Ran 51 tests in ',harness) and 'OK' in harness
manifest=json.loads((root/'local/adapters-tested-tree-03.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((root/'local/m01-adapters-source-03'/name).read_bytes()).hexdigest()==digest,name
summary={'host':host,'profile':'Debug','source_files':len(manifest['files']),
    'adapter_checks':63,'suites':dict(zip(names,counts)),'harness_checks':51,'failed':0,'skipped':0,
    'full_profile':'pending','results':report['results'][:4]}
path=root/'local/adapter-partition-debug-03.json';path.write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(root/('local/adapter-partition-'+host+'-03.tar.gz'),'w:gz') as archive:
    archive.add(path,arcname=host+'/adapter-partition-debug-03.json')
    for name in ('configure.log','build.log','harness.log','adapters.log'):
        archive.add(folder/name,arcname=host+'/'+folder.name+'/'+name)
print(host,'63 adapter and 51 harness checks passed; complete matrix pending',flush=True)
