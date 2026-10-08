import pathlib,json,subprocess,os
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
server=json.loads((p/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
script="""import unittest,subprocess,os,struct,json,select,time
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
  data=b'\\xff\\xff'
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
"""
for build,label in [('build','debug'),('build-sanitize','runtime-sanitize')]:
 out=p/('local/m04-surface-identity-c-witness-'+label+'-01');out.mkdir(exist_ok=False);(out/'witness.py').write_text(script)
 env=dict(os.environ,PYTHONPATH=str(p/build/'python')+':'+str(p)+':'+str(p/'tests/native'),SPATIAL_XPROBE=str(p/build/'spatial-xprobe'),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
 cmd=['python3',str(out/'witness.py'),str(p/build/'spatial-security-auth')]
 with (out/'test.log').open('w') as f:r=subprocess.run(cmd,cwd=p,env=env,stdout=f,stderr=subprocess.STDOUT)
 (out/'run.json').write_text(json.dumps({'command':cmd,'server':server,'exit_code':r.returncode,'build':build,'ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS']},indent=2)+'\n')
 print(label,r.returncode,flush=True);print((out/'test.log').read_text(),flush=True)
 if r.returncode:raise SystemExit(1)

