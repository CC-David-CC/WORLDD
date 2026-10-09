import json,os,socket,subprocess,sys
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/native-scope-input-diagnostic-01';out.mkdir()
source=r/'local/m02-authority-native-scope-01/source';build=source.parent/'debug/build'
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
script=out/'driver.py'
script.write_text('''import os,sys,unittest
from pathlib import Path
sys.path.insert(0,'''+repr(str(source/'tests/native'))+''')
mode=sys.argv.pop(1)
import test_authority_native_scope as original
if mode=='core':
    old=original.Probe
    def core_probe(*args,**kwargs):
        kwargs['xi2']=False
        return old(*args,**kwargs)
    original.Probe=core_probe
class Diagnostic(original.NativeScopeTests):
    def setUp(self):
        super().setUp()
        def retain():
            target=Path(os.environ['SPATIAL_DIAGNOSTIC_OUTPUT'])
            for pattern in ('*.stderr','bridge.log','*.jsonl'):
                for p in self.root.glob(pattern):(target/(mode+'-'+p.name)).write_bytes(p.read_bytes())
        self.addCleanup(retain)
result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([Diagnostic('test_compositor_stop_releases_real_native_key_and_button')]))
raise SystemExit(not result.wasSuccessful())
''')
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_DIAGNOSTIC_OUTPUT=str(out))
results=[]
for mode in ['original','core']:
    command=['python3',str(script),mode,str(build/'spatial-native-scope-probe'),str(build/'spatial-security-auth')]
    with (out/(mode+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(mode=mode,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,results=results),indent=2)+'\n')
    print(host,mode,run.returncode,flush=True)
    if run.returncode:print((out/(mode+'.log')).read_text()[-5000:],flush=True)
for p in out.glob('*.stderr'):print(p.name,p.read_text()[-3000:],flush=True)
