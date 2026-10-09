import hashlib,json,pathlib,socket,subprocess,sys
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');repo=r.parent;host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
expected=sys.argv[1];assert len(expected)==40 and all(c in '0123456789abcdef' for c in expected)
def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
assert git('status','--porcelain')=='' and git('branch','--show-current')=='main'
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:args=p.read_bytes().split(b'\0')
    except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    assert not any(arg.endswith(b'/qualify-process-termination-02.py') for arg in args),'Process qualification is still active'
before=git('rev-parse','HEAD');subprocess.run(['git','-C',str(repo),'fetch','origin','main'],check=True)
assert git('rev-parse','origin/main')==expected
subprocess.run(['git','-C',str(repo),'merge','--ff-only',expected],check=True,stdout=subprocess.DEVNULL)
manifest=json.loads((r/'evidence/process-termination-2026-10-09/integration-applied-02.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((r/n).read_bytes()).hexdigest()==h,n
assert git('rev-parse','HEAD')==expected and git('status','--porcelain')==''
record=dict(host=host,before=before,commit=expected,source_hashes_matched=len(manifest),worktree_clean=True,runtime_restarted=False,operator_state_migrated=False)
(r/'local'/('process-termination-adoption-'+host+'-02.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
