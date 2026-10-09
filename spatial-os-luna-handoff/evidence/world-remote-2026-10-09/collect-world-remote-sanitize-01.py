import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
out=root/'local/m01-world-remote-runtime-sanitize-01';source=root/'local/m01-world-remote-draft-01/source';build=out/'build'
manifest=json.loads((root/'local/world-remote-source-01.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
run=json.loads((out/'run.json').read_text())
assert [(v['name'],v['exit_code']) for v in run['results']]==[(n,0) for n in ('configure','build','focused','security','lifecycle','native')]
log=(out/'focused.log').read_text();counts=sorted(map(int,re.findall(r'Ran (\d+) tests?',log)))
assert counts==[5,6,14,15,20,27],counts
assert '100% tests passed' in log and 'skipped=' not in log
for name,count in [('security',13),('lifecycle',6),('native',3)]:
    log=(out/(name+'.log')).read_text();assert f'Ran {count} tests' in log and '\nOK\n' in log and 'skipped=' not in log
summary={'host':'sb16','profile':'runtime ASan/UBSan','source_files':226,'focused_tests':109,
    'new_remote_tests':27,'existing_tests':82,'failed':0,'skipped':0,
    'ASAN_OPTIONS':run['ASAN_OPTIONS'],'UBSAN_OPTIONS':run['UBSAN_OPTIONS'],
    'binaries':{name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in ('identityd','spatial-issuer-client','worldd','spatialwm')},
    'limits':'Focused only. Full cumulative, second-host, and cross-host qualification remain pending.'}
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(root/'local/world-remote-sanitize-evidence-01.tar.gz','w:gz') as archive:
    for path in out.iterdir():
        if path.is_file() and path.suffix in ('.log','.json','.py'):archive.add(path,arcname='runtime-sanitize-01/sb16/'+path.name)
    for path in (out/'server').rglob('*'):
        if path.is_file() and path.suffix in ('.log','.json','.jsonl'):
            archive.add(path,arcname='runtime-sanitize-01/sb16/server/'+path.relative_to(out/'server').as_posix())
print(json.dumps(summary),flush=True)
