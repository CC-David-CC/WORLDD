import hashlib,json,os,pathlib,socket,subprocess
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/m02-participant-incarnation-baseline-01';out.mkdir()
source=r/'local/m02-authority-launch-coordinator-03/source';build=source.parent/'debug/build'
test=r/'local/test_participant_incarnation_baseline_01.py'
driver=out/'baseline_driver.py';driver.write_text('import runpy,sys,unittest\nscope=runpy.run_path(sys.argv.pop(1),run_name="counterexample")\nsuite=unittest.TestSuite([scope["IncarnationTests"]("test_exact_preassigned_incarnation_reaches_durable_activation")])\nraise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())\n')
command=['python3',str(driver),str(test),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'spatial-participant-runtime-probe')]
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
with (out/'baseline.log').open('wb') as log:p=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=host,command=command,exit_code=p.returncode,test_sha256=hashlib.sha256(test.read_bytes()).hexdigest()),indent=2)+'\n')
text=(out/'baseline.log').read_text();print(text,flush=True)
assert p.returncode==1 and 'FAILED (failures=1)' in text and 'has no field named "service_incarnation"' in text
print(host,'preassigned incarnation is unsupported by the predecessor',flush=True)
