"""Real application namespaces expose public X resources as read-only dependencies."""
import hashlib
import json
from pathlib import Path
import unittest
import core_pb2 as core
import broker_pb2 as broker
import test_application_access as existing

PROGRAM=r'''import errno,hashlib,json,os
from pathlib import Path
path=Path('/etc/X11/app-defaults/Xedit')
result={'present':path.exists(),'private_etc_visible':Path('/etc/passwd').exists()}
if path.exists():
 data=path.read_bytes();result['sha256']=hashlib.sha256(data).hexdigest()
 result['readonly']=bool(os.statvfs(path).f_flag&os.ST_RDONLY)
 result['write_denials']=[]
 for target in [path,path.parent/'spatial-m10-unexpected-write']:
  try:
   fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
  except OSError as error:result['write_denials'].append(error.errno)
  else:os.close(fd);raise SystemExit('Public resource mount permitted writing')
 assert all(value in (errno.EROFS,errno.EACCES,errno.EPERM) for value in result['write_denials'])
Path('/state/data/dependencies.json').write_text(json.dumps(result))
'''


class NativeApplicationDependencyTests(existing.ApplicationAccessTests):
    def checked(self,profile,native):
        held=self.setup_application(native_enabled=native);self.finish_application(held)
        self.sequence+=1;principal=self.principal()
        actor='identity:'+principal.issuer_id.hex()+':'+principal.actor_id.hex()
        reply=self.broker.request(launch=broker.LaunchRequest(workspace='one',actor=actor,principal=principal,
            command=core.ProcessLaunchRequest(operation_id='public-dependencies-'+str(self.sequence),profile=profile,
                argv=['/usr/bin/python3','-c',PROGRAM])))
        self.assertTrue(reply.HasField('launched'),reply)
        app=broker.ApplicationRef(workspace=b'one',application=reply.launched.process_id.encode(),server_id=self.server_id)
        self.assertEqual(self.broker.call('process.wait',id=reply.launched.process_id,timeout_seconds=5),{'exit_code':0})
        result=json.loads((self.data(app)/'dependencies.json').read_text())
        self.assertFalse(result['private_etc_visible'])
        self.assertEqual(result['present'],native)
        if native:
            expected=hashlib.sha256(Path('/etc/X11/app-defaults/Xedit').read_bytes()).hexdigest()
            self.assertEqual(result['sha256'],expected)
            self.assertTrue(result['readonly'])
            self.assertEqual(len(result['write_denials']),2)
        self.assertFalse(Path('/etc/X11/app-defaults/spatial-m10-unexpected-write').exists())

    def test_native_development_profile_reads_public_defaults_without_write_authority(self):self.checked('development',True)
    def test_native_read_only_profile_reads_public_defaults_without_write_authority(self):self.checked('read-only',True)
    def test_headless_launch_keeps_existing_system_dependency_boundary(self):self.checked('development',False)


if __name__=='__main__':
    names=[name for name in NativeApplicationDependencyTests.__dict__ if name.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(NativeApplicationDependencyTests(name) for name in names)).wasSuccessful())
