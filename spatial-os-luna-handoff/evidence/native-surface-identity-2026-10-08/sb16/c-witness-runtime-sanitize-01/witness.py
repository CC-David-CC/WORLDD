import unittest,subprocess,os,struct,json,select,time
import test_compositor_identity as fixture
class CWitness(unittest.TestCase):
 setUp=fixture.CompositorIdentityTests.setUp
 start=fixture.CompositorIdentityTests.start
 connect=fixture.CompositorIdentityTests.connect
 scene=fixture.CompositorIdentityTests.scene
 renderer=fixture.CompositorIdentityTests.renderer
 receive=fixture.CompositorIdentityTests.receive
 snapshot=fixture.CompositorIdentityTests.snapshot
 surface=fixture.CompositorIdentityTests.surface
 def test_independent_c_window_has_server_owned_attribution(self):
  self.scene()
  data=b'\xff\xff'
  for part in (b'',str(self.display).encode(),b'MIT-MAGIC-COOKIE-1',self.owner_grant.cookie):data+=struct.pack('!H',len(part))+part
  auth=self.root/'probe-auth';auth.write_bytes(data);auth.chmod(0o600)
  proc=subprocess.Popen([os.environ['SPATIAL_XPROBE'],'--id','independent-c-attribution'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=dict(os.environ,DISPLAY=':'+str(self.display),XAUTHORITY=str(auth)))
  def close():
   proc.stdin.close()
   try:proc.wait(timeout=3)
   except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
   diagnostics=proc.stderr.read().decode(errors='replace')
   proc.stdout.close();proc.stderr.close()
   self.assertEqual(proc.returncode,0,diagnostics)
   for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,diagnostics)
  self.addCleanup(close)
  self.assertTrue(select.select([proc.stdout],[],[],5)[0],'C witness did not report readiness')
  ready=json.loads(proc.stdout.readline())
  self.assertEqual(ready['event'],'ready')
  self.renderer()
  surface=self.surface(ready['xid'])
  self.assertEqual((surface.application_identity.workspace,surface.application_identity.application),(b'one',b'editor'))
  self.assertEqual((surface.pixels.width,surface.pixels.height),(ready['width'],ready['height']))
  self.assertEqual(surface.probe_id,'independent-c-attribution')
  self.assertGreater(surface.application_identity.connection_id,0)
  self.assertEqual(len(surface.application_identity.server_id),32)
unittest.main(verbosity=2)
