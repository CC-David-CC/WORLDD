"""Real syncfs admission and honest failures. These checks do not qualify a world."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('flush_deployment', sys.argv.pop(1))
flush = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flush)


class DeploymentWritebackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='spatial-writeback-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.payload = self.root / 'payload'
        self.payload.mkdir()

    def test_real_flush_pins_directory_preserves_bytes_and_records_elapsed_time(self):
        data = self.payload / 'data'
        data.write_bytes(b'actual deployment bytes\n')
        identity = self.payload.stat()
        report = flush.flush_payload(self.payload)
        self.assertEqual(report['status'], 'WRITEBACK_FLUSHED', report)
        self.assertEqual((report['device'], report['inode']), (identity.st_dev, identity.st_ino))
        self.assertGreaterEqual(report['seconds'], 0)
        self.assertEqual(data.read_bytes(), b'actual deployment bytes\n')
        flush.write_receipt(self.root / 'receipt.json', report)
        self.assertEqual((self.root / 'receipt.json').stat().st_mode & 0o777, 0o600)

    def test_symlink_and_non_directory_are_refused_before_sync(self):
        link = self.root / 'link'
        link.symlink_to(self.payload, target_is_directory=True)
        file = self.root / 'file'
        file.write_text('ordinary file')
        with patch.object(flush.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'canonical'):
                flush.flush_payload(link)
            with self.assertRaises(NotADirectoryError):
                flush.flush_payload(file)
            command.assert_not_called()

    def test_failed_sync_never_reports_flushed(self):
        with patch.object(flush.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2, '', 'sync failed')):
            report = flush.flush_payload(self.payload)
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(report['exit'], 2)

    def test_timeout_never_reports_flushed_and_closes_pinned_descriptor(self):
        captured = []
        def timeout(argv, **kwargs):
            captured.extend(kwargs['pass_fds'])
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        with patch.object(flush.subprocess, 'run', side_effect=timeout):
            report = flush.flush_payload(self.payload, .1)
        self.assertEqual(report['status'], 'TIMEOUT')
        with self.assertRaises(OSError):
            os.fstat(captured[0])

    def test_receipts_cannot_overwrite_evidence_or_modify_payload(self):
        report = flush.flush_payload(self.payload)
        self.assertEqual(report['status'], 'WRITEBACK_FLUSHED', report)
        receipt = self.root / 'receipt.json'
        flush.write_receipt(receipt, report)
        before = receipt.read_bytes()
        with self.assertRaises(FileExistsError):
            flush.write_receipt(receipt, report)
        self.assertEqual(receipt.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'outside'):
            flush.write_receipt(self.payload / 'receipt.json', report)

    def test_wrapper_starts_command_after_checked_writeback(self):
        marker = self.root / 'command-started'
        receipt = self.root / 'wrapper.json'
        code = "from pathlib import Path; import json; " + \
            "r=json.loads(Path(" + repr(str(receipt)) + ").read_text()); " + \
            "assert r['status']=='WRITEBACK_FLUSHED'; Path(" + repr(str(marker)) + ").write_text('started')"
        result = subprocess.run([sys.executable, '-B', str(Path(flush.__file__)),
            '--payload', str(self.payload), '--receipt', str(receipt), '--',
            sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=130)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), 'started')

    def test_wrapper_refuses_command_when_flush_fails(self):
        marker = self.root / 'command-started'
        # A real child invokes the module with a failing sync subprocess.
        # It keeps the production CLI and original post-flush command unchanged.
        code = "import importlib.util,sys,subprocess; from unittest.mock import patch; " + \
            "s=importlib.util.spec_from_file_location('flush'," + repr(str(Path(flush.__file__))) + "); " + \
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); " + \
            "sys.argv=['flush','--payload'," + repr(str(self.payload)) + ", '--receipt'," + repr(str(self.root/'failed.json')) + ", '--'," + \
            "sys.executable,'-c'," + repr("from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('bad')") + "]; " + \
            "p=patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],2,'','failed')); p.start(); raise SystemExit(m.main())"
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
