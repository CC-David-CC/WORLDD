import json,os,pathlib,socket,subprocess
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');source=r/'local/m02-launch-authorization-02/source';build=source.parent/'runtime-sanitize/build'
out=r/'local/m02-client-config-overwrite-01';out.mkdir()
driver=out/'driver.py';driver.write_text('''import runpy,sys,unittest
scope=runpy.run_path(sys.argv.pop(1),run_name="diagnostic")
original=scope["LaunchAuthorizationTests"]
class Diagnostic(original):
    def test_configuration_snapshot(self):
        first=self.config_file(1);original=first.read_bytes();second=self.config_file(2)
        self.assertEqual(first.read_bytes(),original,"Another client configuration overwrote the first launch input")
suite=unittest.TestSuite([Diagnostic("test_configuration_snapshot")])
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
''')
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'),
    ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
command=['python3',str(driver),str(source/'tests/core/test_launch_authorization.py'),str(build/'identityd'),str(build/'spatial-issuer-client')]
with (out/'diagnostic.log').open('wb') as log:p=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=host,command=command,exit_code=p.returncode),indent=2)+'\n')
log=(out/'diagnostic.log').read_text();assert p.returncode==1 and 'Another client configuration overwrote the first launch input' in log
print(log,flush=True)
