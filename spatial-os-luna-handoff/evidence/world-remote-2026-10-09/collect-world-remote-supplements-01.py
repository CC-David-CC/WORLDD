import hashlib,json,pathlib,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
digest=hashlib.sha256((root/'local/m01-world-remote-draft-01/build/worldd').read_bytes()).hexdigest()
with tarfile.open(root/'local/world-remote-supplements-01.tar.gz','w:gz') as archive:
    for name,count in [('lifecycle',6),('native',3)]:
        out=root/('local/m01-world-remote-'+name+'-01');run=json.loads((out/'run.json').read_text())
        assert run['exit_code']==0 and run['worldd_sha256']==digest
        log=(out/'tests.log').read_text();assert f'Ran {count} tests' in log and '\nOK\n' in log and 'skipped=' not in log
        for path in out.iterdir():
            if path.is_file() and path.suffix in ('.log','.json','.py'):archive.add(path,arcname=name+'-01/sb16/'+path.name)
        if (out/'server').exists():
            for path in (out/'server').rglob('*'):
                if path.is_file() and path.suffix in ('.log','.json','.jsonl'):
                    archive.add(path,arcname=name+'-01/sb16/server/'+path.relative_to(out/'server').as_posix())
        print(name,count,'passed',flush=True)
