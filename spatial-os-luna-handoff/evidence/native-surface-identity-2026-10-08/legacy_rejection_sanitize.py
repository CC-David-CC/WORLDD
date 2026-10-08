import pathlib,os,json,subprocess
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');out=p/'local/m04-surface-identity-legacy-rejection-sanitize-01';out.mkdir(exist_ok=False)
old=json.loads((p/'local/m04-actor-binding-build-03/run.json').read_text())['binaries']['Xvfb']
script="""import unittest,subprocess,os,struct
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
  data=b'\\xff\\xff'
  for part in (b'',str(self.display).encode(),b'MIT-MAGIC-COOKIE-1',cookie):data+=struct.pack('!H',len(part))+part
  auth=self.root/'auth';auth.write_bytes(data);auth.chmod(0o600)
  (self.root/'renderer').mkdir()
  proc=subprocess.run([os.environ['SPATIAL_COMPOSITOR'],'--state-dir',str(self.root/'renderer'),'--display',':'+str(self.display)],input=b'',stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=dict(os.environ,XAUTHORITY=str(auth),LIBGL_ALWAYS_SOFTWARE='1'),timeout=5)
  self.assertEqual(proc.returncode,2,proc.stderr)
  self.assertEqual(proc.stdout,b'')
  self.assertIn(b'Native server omitted its security mode',proc.stderr)
unittest.main(verbosity=2)
"""
(out/'rejection.py').write_text(script)
env=dict(os.environ,PYTHONPATH=str(p/'build-sanitize/python')+':'+str(p)+':'+str(p/'tests/native'),SPATIAL_TEST_XSERVER=old['path'],ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',SPATIAL_COMPOSITOR=str(p/'build-sanitize/spatialwm'),SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
cmd=['python3',str(out/'rejection.py'),str(p/'build-sanitize/spatial-security-auth')]
with (out/'test.log').open('w') as log:r=subprocess.run(cmd,cwd=p,env=env,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps({'command':cmd,'old_server':old,'exit_code':r.returncode},indent=2)+'\n')
print((out/'test.log').read_text());raise SystemExit(r.returncode)

