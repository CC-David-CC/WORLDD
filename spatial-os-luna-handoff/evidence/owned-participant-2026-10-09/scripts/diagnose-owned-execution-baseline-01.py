import hashlib,json,os,pathlib,socket,subprocess
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');source=r/'local/m02-owned-participant-baseline-01/source';build=source.parent/'debug/build'
manifest=json.loads((r/'local/owned-participant-baseline-01.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
out=r/'local/m02-owned-execution-baseline-01';out.mkdir()
driver=out/'driver.py';driver.write_text('''import json,runpy,sys,unittest
scope=runpy.run_path(sys.argv.pop(1),run_name="diagnostic")
original=scope["OwnedParticipantTests"]
class Diagnostic(original):
    def test_independent_child_observes_execution_without_approval(self):
        proc=self.probe();view=self.prepared(proc);observed=self.root/'execution-witness'
        code=('import json,os,pathlib,time\\n'
            f'p=pathlib.Path({str(observed)!r});t=p.with_suffix(".tmp")\\n'
            't.write_text(json.dumps({"pid":os.getpid(),"cgroup":pathlib.Path("/proc/self/cgroup").read_text()}));t.replace(p);time.sleep(30)')
        reply=self.run_child(proc,code);self.assertTrue(reply.HasField('view'),reply)
        self.wait_file(observed);actual=json.loads(observed.read_text())
        self.assertIn(view.record.process.scope_name,actual['cgroup'])
        self.assertFalse(self.read_record().HasField('authorization'))
        print(json.dumps(dict(confirmed_missing_gate=True,launch_id=view.record.process.launch_id.hex(),child=actual)),flush=True)
        self.stopped(proc)
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([Diagnostic('test_independent_child_observes_execution_without_approval')])).wasSuccessful())
''')
server=json.loads((source.parent/'debug/run.json').read_text())['server'];assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
paths=':'.join(map(str,[build/'python',source,source/'tests/native',source/'tests/core']))
command=['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-owned-baseline-witness-01','--property=Delegate=yes',
    '--setenv=PYTHONPATH='+paths,'--setenv=SPATIAL_TEST_XSERVER='+server['path'],'--setenv=SPATIAL_SECURITY_EVIDENCE='+str(out/'server'),
    'python3',str(driver),str(source/'tests/native/test_authority_owned_participant.py'),str(build/'spatial-authority-launch-probe'),
    str(build/'spatial-security-auth'),str(build/'identityd'),str(build/'spatial-issuer-client')]
with (out/'diagnostic.log').open('wb') as log:p=subprocess.run(command,cwd=source,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=host,command=command,exit_code=p.returncode,source_files=len(manifest),source='owned-participant-baseline-01'),indent=2)+'\n')
log=(out/'diagnostic.log').read_text();print(log,flush=True);assert p.returncode==0 and '"confirmed_missing_gate": true' in log
