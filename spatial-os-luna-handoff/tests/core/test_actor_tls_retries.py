"""Exercise bounded production retries with explicit OpenSSL fault injection."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROBE=Path(sys.argv.pop(1))


class ActorTlsRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='actor-tls-retries-');cls.root=Path(cls.temp.name)
        cls.addClassCleanup(cls.temp.cleanup)
        for name in ('server','client'):
            result=subprocess.run(['openssl','req','-x509','-newkey','ed25519','-nodes',
                '-keyout',str(cls.root/(name+'.key')),'-out',str(cls.root/(name+'.crt')),
                '-days','2','-subj','/CN='+name+'.test','-addext','subjectAltName=DNS:'+name+'.test'],capture_output=True,timeout=10)
            if result.returncode:raise AssertionError(result.stderr)
            (cls.root/(name+'.key')).chmod(0o600);(cls.root/(name+'.crt')).chmod(0o644)

    def check_case(self,case):
        result=subprocess.run([str(PROBE),str(self.root),case],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn(case+' PASS',result.stdout)

    def test_stalled_read_to_write_retry_expires(self):self.check_case('retry-stalls')
    def test_read_retry_transition_preserves_deadline(self):self.check_case('retry-wants-read')
    def test_repeated_retry_does_not_refresh_deadline(self):self.check_case('retry-progress')
    def test_completed_read_clears_retry_deadline(self):self.check_case('retry-recovers')
    def test_authenticated_idle_has_no_io_deadline(self):self.check_case('authenticated-idle')


if __name__=='__main__':unittest.main(verbosity=2)
