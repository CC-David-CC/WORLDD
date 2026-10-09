import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=root/'local/m01-world-remote-source-02';build=root/'local/m01-world-remote-debug-02/build'
out=root/'local/m02-revocation-barrier-gap-01';out.mkdir()
env=dict(os.environ,PYTHONPATH=':'.join(map(str,[build/'python',source,source/'tests/core'])),SPATIAL_BARRIER_GAP_REPORT=str(out/'observation.json'))
command=['python3',str(root/'local/probe-revocation-barrier-gap-01.py'),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]
with (out/'probe.log').open('wb') as log:
    result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=35)
record={'host':host,'command':command,'exit_code':result.returncode,
    'binary_sha256':{name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in ('identityd','spatial-issuer-client','worldd')},
    'scope':'diagnostic of missing distributed cancellation barrier; not M02 acceptance'}
(out/'run.json').write_text(json.dumps(record,indent=2)+'\n')
print((out/'probe.log').read_text()[-4500:],flush=True)
raise SystemExit(result.returncode)
