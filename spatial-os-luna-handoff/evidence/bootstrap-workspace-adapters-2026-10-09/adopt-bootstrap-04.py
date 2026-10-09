import hashlib,json,pathlib,socket,subprocess,sys
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');repo=root.parent
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
expected=sys.argv[1];assert len(expected)==40 and all(c in '0123456789abcdef' for c in expected)
def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
assert git('status','--porcelain')=='','Host worktree must be clean before adoption'
before=git('rev-parse','HEAD');assert git('branch','--show-current')=='main'
subprocess.run(['git','-C',str(repo),'pull','--ff-only','origin','main'],check=True)
assert git('rev-parse','HEAD')==expected
manifest=json.loads((root/'evidence/bootstrap-workspace-adapters-2026-10-09/integration-prepared-04.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
assert git('status','--porcelain')==''
record={'host':host,'before':before,'commit':expected,'source_hashes_matched':len(manifest['files']),
    'worktree_clean':True,'status':'adopted qualified source','runtime_restarted':False,'operator_state_migrated':False}
out=root/'local'/('adapter-adoption-'+host+'-04.json');out.write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
