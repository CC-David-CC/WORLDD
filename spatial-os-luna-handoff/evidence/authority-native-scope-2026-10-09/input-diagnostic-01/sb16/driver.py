import os,sys,unittest
from pathlib import Path
sys.path.insert(0,'/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m02-authority-native-scope-01/source/tests/native')
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
