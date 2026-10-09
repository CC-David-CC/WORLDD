import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host=='sb16'
source=root/'local/m02-participant-barrier-draft-03/source';build=root/'local/m02-participant-barrier-draft-03/debug/build';world=root/'local/m02-participant-session-debug-03/build/worldd'
out=root/'local/m02-runtime-participation-gap-01';out.mkdir()
env=dict(os.environ,PYTHONPATH=':'.join(map(str,[build/'python',source,source/'tests/core'])),SPATIAL_BARRIER_GAP_REPORT=str(out/'observation.json'))
command=['python3',str(root/'local/probe-runtime-participation-gap-01.py'),str(build/'identityd'),str(build/'spatial-issuer-client'),str(world)]
with (out/'probe.log').open('wb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=40)
record={'host':host,'command':command,'exit_code':result.returncode,'binary_sha256':{n:hashlib.sha256(p.read_bytes()).hexdigest() for n,p in {'identityd':build/'identityd','spatial-issuer-client':build/'spatial-issuer-client','worldd':world}.items()},'scope':'diagnostic of missing runtime participation; no M02 completion claim'}
(out/'run.json').write_text(json.dumps(record,indent=2)+'\n');print((out/'probe.log').read_text()[-6000:],flush=True);raise SystemExit(result.returncode)
