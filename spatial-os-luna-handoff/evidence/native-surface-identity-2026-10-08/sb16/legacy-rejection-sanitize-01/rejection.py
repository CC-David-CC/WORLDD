import unittest,subprocess,os,struct
import test_compositor_identity as fixture
import security_pb2 as sec
class Rejection(unittest.TestCase):
 setUp=fixture.CompositorIdentityTests.setUp
 start=fixture.CompositorIdentityTests.start
 connect=fixture.CompositorIdentityTests.connect
 scene=fixture.CompositorIdentityTests.scene
 def test_previous_server_cannot_silently_select_development_mode(self):
  self.scene()
  cookie=self.supervisor.issue(sec.COMPOSITOR,b'',b'').cookie
  data=b'\xff\xff'
  for part in (b'',str(self.display).encode(),b'MIT-MAGIC-COOKIE-1',cookie):data+=struct.pack('!H',len(part))+part
  auth=self.root/'auth';auth.write_bytes(data);auth.chmod(0o600)
  (self.root/'renderer').mkdir()
  proc=subprocess.run([os.environ['SPATIAL_COMPOSITOR'],'--state-dir',str(self.root/'renderer'),'--display',':'+str(self.display)],input=b'',stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=dict(os.environ,XAUTHORITY=str(auth),LIBGL_ALWAYS_SOFTWARE='1'),timeout=5)
  self.assertEqual(proc.returncode,2,proc.stderr)
  self.assertEqual(proc.stdout,b'')
  self.assertIn(b'Native server omitted its security mode',proc.stderr)
unittest.main(verbosity=2)
