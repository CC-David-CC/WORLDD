import hashlib,json,pathlib,socket,subprocess,sys
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');repo=root.parent
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
expected=sys.argv[1];assert len(expected)==40 and all(c in '0123456789abcdef' for c in expected)
def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
assert git('status','--porcelain')=='','Host worktree must be clean before adoption'
assert git('branch','--show-current')=='main'
for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:args=path.read_bytes().split(b'\0')
    except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    assert not any(arg.endswith(b'/qualify-participant-barrier-04.py') for arg in args),'Qualification is still active'
before=git('rev-parse','HEAD')
subprocess.run(['git','-C',str(repo),'fetch','origin','main'],check=True)
assert git('rev-parse','origin/main')==expected,'Origin changed before adoption'
subprocess.run(['git','-C',str(repo),'merge','--ff-only',expected],check=True,stdout=subprocess.DEVNULL)
manifest=json.loads((root/'evidence/participant-barrier-2026-10-09/integration-applied-05.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
assert git('rev-parse','HEAD')==expected and git('status','--porcelain')==''
record={'host':host,'before':before,'commit':expected,'source_hashes_matched':len(manifest['files']),
    'worktree_clean':True,'status':'adopted qualified source','runtime_restarted':False,'operator_state_migrated':False}
out=root/'local'/('participant-barrier-adoption-'+host+'-05.json');out.write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
