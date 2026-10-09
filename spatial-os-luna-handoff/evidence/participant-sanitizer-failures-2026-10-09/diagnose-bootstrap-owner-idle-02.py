import json,os,socket,subprocess,sys,time,unittest
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
source=r/'local/m02-participant-fixtures-07/source';build=source.parent/'runtime-sanitize/build'
if len(sys.argv)>1:
    mode=sys.argv[1];assert mode in ('baseline','fresh')
    sys.path[:0]=[str(source/'tests/core'),str(build/'python'),str(source)]
    sys.argv=[sys.argv[0]]+[str(build/n) for n in ['identityd','spatial-issuer-client','worldd']]
    from test_workspace_bootstrap import WorkspaceBootstrapTests
    class IdleOwner(WorkspaceBootstrapTests):
        def workspace(self,*args,**kwargs):
            result=super().workspace(*args,**kwargs)
            time.sleep(3.2) # Exceed the unchanged issuer fixture's 3000ms idle limit.
            return result
        def publish(self,*args,**kwargs):
            if mode=='fresh':
                self.owner_peer.close();self.owner_peer=self.connect()
            return super().publish(*args,**kwargs)
    case=IdleOwner('test_explicit_application_enables_confined_real_file_write')
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([case]))
    raise SystemExit(0 if result.wasSuccessful() else 1)
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/bootstrap-owner-idle-02';out.mkdir();results=[]
for mode in ('baseline','fresh'):
    target=out/mode;target.mkdir()
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_SECURITY_EVIDENCE=str(target/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    command=['python3',__file__,mode]
    with (target/'test.log').open('wb') as log:run=subprocess.run(command,env=env,cwd=source,stdout=log,stderr=subprocess.STDOUT)
    print(host,mode,run.returncode,flush=True)
    print((target/'test.log').read_text(),flush=True)
    results.append(dict(mode=mode,exit_code=run.returncode,command=command))
(out/'run.json').write_text(json.dumps(dict(host=host,candidate=6,results=results),indent=2)+'\n')
assert [x['exit_code'] for x in results]==[1,0]
