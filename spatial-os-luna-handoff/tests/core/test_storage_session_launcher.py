"""Regression controls for the storage-session launcher failure boundaries."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[2]/'tools/run_storage_session_profile.py'
spec=importlib.util.spec_from_file_location('storage_launcher',path)
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)

class StorageLauncherTests(unittest.TestCase):
 def record(self):
  return dict(test_exit=0,command_exit='0',wrapper_exit='0',terminal_status='COMPLETE',unittest_ok=True,
   passed_methods=list(launcher.M),sanitizer_findings=[],volume_cleanup_ok=True,descriptor_cleanup_ok=True,
   active_conflicts_after=[],process_stop=dict(live=[],forced=False),transient_service=dict(unresolved=[],forced=[]),
   errors=[],restored_owner_sha256=launcher.BASE,interrupted=None,
   status='FOCUSED_PASS_UNQUALIFIED',host='sb16',profile='debug',source=launcher.SRC,fixture_sha256=launcher.TEST,
   launcher_sha256=launcher.h(launcher.__file__),inventory_sha256=launcher.P['debug']['inv'])

 def test_zero_exit_does_not_hide_incomplete_evidence(self):
  good=self.record();self.assertTrue(launcher.successful(good))
  changes=[('wrapper_exit','1'),('command_exit',None),('terminal_status','RUNNING'),('passed_methods',[]),
   ('volume_cleanup_ok',False),('descriptor_cleanup_ok',False),('sanitizer_findings',['runtime error']),
   ('active_conflicts_after',[dict(pid=123)]),('process_stop',dict(live=[123],forced=True)),
   ('transient_service',dict(unresolved=['owned.service'],forced=[])),('restored_owner_sha256','wrong'),
   ('errors',['restore failed']),('interrupted',signal.SIGTERM)]
  for key,value in changes:
   with self.subTest(key=key):
    bad=copy.deepcopy(good);bad[key]=value;self.assertFalse(launcher.successful(bad))
  for key in good:
   if key in ('status','host','profile','source','fixture_sha256','launcher_sha256','inventory_sha256'):continue
   bad=copy.deepcopy(good);bad.pop(key)
   if key not in ('interrupted','errors'):self.assertFalse(launcher.successful(bad),key)

 def test_sanitizer_requires_matching_debug_identity(self):
  good=self.record();launcher.validate_debug(good,'sb16')
  for key,value in [('host','sb16-2'),('profile','san'),('source','other'),('launcher_sha256','other'),
                    ('fixture_sha256','other'),('inventory_sha256','other'),('status','RETURNED')]:
   with self.subTest(key=key):
    bad=copy.deepcopy(good);bad[key]=value
    with self.assertRaises(launcher.Refusal):launcher.validate_debug(bad,'sb16')

 def test_parent_exit_does_not_leave_live_child(self):
  code="import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True)"
  parent=subprocess.Popen([sys.executable,'-c',code],start_new_session=True,stdout=subprocess.PIPE,text=True)
  try:
   child=int(parent.stdout.readline());parent.wait(timeout=5)
   self.assertTrue(any(row['pid']==child and row['state']!='Z' for row in launcher.group_members(parent.pid)))
   result=launcher.kill_group(parent,grace=.2)
   self.assertEqual(result['live'],[]);self.assertTrue(result['forced'])
  finally:
   launcher.kill_group(parent,grace=.1);parent.stdout.close()

 def test_signal_targets_active_process_group(self):
  child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],start_new_session=True)
  launcher.ACTIVE=child;launcher.INTERRUPTED=None
  try:
   launcher.sig_handler(signal.SIGTERM,None)
   self.assertEqual(child.wait(timeout=5),-signal.SIGTERM)
   self.assertEqual(launcher.INTERRUPTED,signal.SIGTERM)
  finally:
   launcher.kill_group(child,grace=.1);launcher.ACTIVE=None;launcher.INTERRUPTED=None

 def test_restoration_failure_is_not_a_pass(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);backup=root/'backup';backup.write_bytes(b'baseline')
   baseline=dict(sha=launcher.BASE,uid=0,gid=0,mode=0o755,size=13841840)
   with patch.object(launcher,'ident',return_value=dict(sha='profile')),patch.object(launcher,'h',return_value=launcher.BASE),patch.object(launcher,'root',return_value=subprocess.CompletedProcess([],1,'','installer failed')):
    with self.assertRaisesRegex(launcher.Refusal,'restoration failed'):
     launcher.restore_original(root,'profile',backup,baseline,root)
   with patch.object(launcher,'ident',return_value=dict(sha='unknown')),patch.object(launcher,'root') as install:
    with self.assertRaisesRegex(launcher.Refusal,'identity mismatch'):
     launcher.restore_original(root,'profile',backup,baseline,root)
    install.assert_not_called()

 def test_graphics_environment_crosses_delegate_inside_command(self):
  cmd=launcher.registration(Path('/src'),Path('/build'),'/wrapper',Path('/evidence'),{'LD_LIBRARY_PATH':'/graphics','__EGL_VENDOR_LIBRARY_FILENAMES':'/graphics/vendor.json'})
  adapter=cmd.index('/src/tools/with_adapter_resources.py')
  self.assertIn('LD_LIBRARY_PATH=/graphics',cmd[adapter:])
  self.assertIn('__EGL_VENDOR_LIBRARY_FILENAMES=/graphics/vendor.json',cmd[adapter:])
  self.assertIn('SPATIAL_TEST_XSERVER=/wrapper',cmd)

 def test_failed_or_missing_unittest_summary_is_rejected(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);run=root/'canonical-run';run.mkdir()
   methods='\n'.join(name+' (__main__.NativeStorageSessionTests.'+name+') ... ok' for name in launcher.M)
   (run/'stdout.log').write_text('');(run/'stderr.log').write_text(methods+'\nRan 7 tests in 1.234s\n\nOK\n')
   result=launcher.inspect_logs(root);self.assertTrue(result['unittest_ok']);self.assertEqual(result['passed_methods'],list(launcher.M))
   interleaved='\n'.join(name+' (__main__.NativeStorageSessionTests.'+name+') ... Healthy issuer timing {}\nok' for name in launcher.M)
   (run/'stderr.log').write_text(interleaved+'\nRan 7 tests in 1.234s\n\nOK\n')
   result=launcher.inspect_logs(root);self.assertTrue(result['unittest_ok']);self.assertEqual(result['passed_methods'],list(launcher.M))
   (run/'stderr.log').write_text(methods+'\nRan 7 tests in 1.234s\n\nFAILED (errors=1)\n')
   self.assertFalse(launcher.inspect_logs(root)['unittest_ok'])
   (run/'stderr.log').write_text('OK from setup only\n')
   self.assertFalse(launcher.inspect_logs(root)['unittest_ok'])
   (run/'stderr.log').write_text(methods+'\nRan 7 tests in 1.234s\n\nOK\n')
   (root/'child.log').write_text('x.cpp:3: runtime error: signed integer overflow\n')
   self.assertEqual(len(launcher.inspect_logs(root)['sanitizer_findings']),1)

if __name__=='__main__':unittest.main(verbosity=2)
