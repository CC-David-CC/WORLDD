import json,os,select,socket,subprocess,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
source=r/'local/m02-participant-fixtures-07/source'
if len(sys.argv)>1:
    profile,mode=sys.argv[1:];assert mode in ('baseline','retry')
    build=source.parent/profile/'build'
    sys.path[:0]=[str(source/'tests/native'),str(build/'python'),str(source)]
    sys.argv=[sys.argv[0],str(build/'spatial-security-auth')]
    import test_native_launch as launch
    import test_security_identity as native
    class DiscoveryRace(launch.NativeLaunchTests):
        def test_revoke_after_setup_before_extension_discovery(self):
            self.launch_fixture();process=self.launch();record=self.record(process)
            release=self.broker.state/'applications'/process.process_id/'data/release'
            original=native.NativeClient.request;interrupted=[]
            def request(client,opcode,*args,**kwargs):
                if opcode==98 and not interrupted:
                    release.touch()
                    self.assertTrue(select.select([client.stream],[],[],3)[0])
                    self.assertEqual(client.stream.recv(1),b'')
                    interrupted.append(True)
                return original(client,opcode,*args,**kwargs)
            deadline=time.monotonic()+3
            with patch.object(native.NativeClient,'request',request):
                while True:
                    try:peer=native.NativeClient(self.display,bytes.fromhex(record['cookie']))
                    except native.Denied:break
                    except (ConnectionError,EOFError):
                        if mode=='baseline':raise
                    else:peer.close()
                    self.assertLess(time.monotonic(),deadline,'Fresh connection never received protocol denial')
                    time.sleep(.01)
            self.assertEqual(interrupted,[True])
            self.assertEqual(self.broker.call('process.wait',id=process.process_id,timeout_seconds=0),{'exit_code':0})
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([DiscoveryRace('test_revoke_after_setup_before_extension_discovery')]))
    raise SystemExit(0 if result.wasSuccessful() else 1)
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/native-launch-discovery-01';out.mkdir();results=[]
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
for profile in ['debug','runtime-sanitize']:
    for mode in ['baseline','retry']:
        target=out/profile/mode;target.mkdir(parents=True)
        build=source.parent/profile/'build'
        env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(target/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        command=['python3',__file__,profile,mode]
        with (target/'test.log').open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(profile=profile,mode=mode,command=command,exit_code=run.returncode));print(host,profile,mode,run.returncode,flush=True)
        if run.returncode:print((target/'test.log').read_text(),flush=True)
(out/'run.json').write_text(json.dumps(dict(host=host,candidate=6,results=results),indent=2)+'\n')
assert [x['exit_code'] for x in results]==[1,0,1,0]
