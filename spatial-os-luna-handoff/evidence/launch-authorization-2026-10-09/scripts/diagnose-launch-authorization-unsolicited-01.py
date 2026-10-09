import hashlib,json,os,pathlib,socket,subprocess
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');source=r/'local/m02-launch-authorization-02/source';build=source.parent/'runtime-sanitize/build'
out=r/'local/m02-launch-authorization-unsolicited-diagnostic-01';out.mkdir()
manifest=json.loads((r/'local/launch-authorization-candidate-02.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
driver=out/'driver.py';driver.write_text('''import json,runpy,sys,unittest
scope=runpy.run_path(sys.argv.pop(1),run_name="diagnostic")
original=scope["LaunchAuthorizationTests"]
class Diagnostic(original):
    def poisoned(self,client,request,peer,count):
        try:return super().poisoned(client,request,peer,count)
        except BaseException:
            print(json.dumps({"requests":[r.WhichOneof("operation") for r in peer.requests],"connections":peer.connections,"stderr":open(client.stderr.name).read()}),flush=True)
            raise
suite=unittest.TestSuite(Diagnostic("test_client_unsolicited_authorization_is_rejected") for _ in range(10))
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
''')
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'),
    ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
command=['python3',str(driver),str(source/'tests/core/test_launch_authorization.py'),str(build/'identityd'),str(build/'spatial-issuer-client')]
with (out/'diagnostic.log').open('wb') as log:p=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=host,command=command,exit_code=p.returncode,source_files=len(manifest)),indent=2)+'\n')
print((out/'diagnostic.log').read_text(),flush=True)
print(host,'diagnostic exit',p.returncode,flush=True)
