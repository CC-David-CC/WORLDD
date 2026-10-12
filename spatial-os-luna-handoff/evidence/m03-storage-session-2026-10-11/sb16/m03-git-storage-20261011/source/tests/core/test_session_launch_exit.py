"""An independent lifecycle peer checks retained receipt and child-exit correlation."""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import launch_pb2 as launch
from harness.wire import read_message

BINARY = Path(sys.argv.pop(1))


class SessionExitTests(unittest.TestCase):
    def replies(self, status):
        with tempfile.TemporaryDirectory(prefix='spatial-session-exit-') as directory:
            result = subprocess.run([str(BINARY), directory, str(status)], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'): self.assertNotIn(marker, result.stderr)
        stream = io.BytesIO(result.stdout)
        first, second = [read_message(stream, launch.AuthorityLaunchReply) for _ in range(2)]
        self.assertEqual((first.protocol_version, second.protocol_version), (1, 1))
        self.assertEqual((first.request_id, second.request_id), (1, 2))
        self.assertEqual(stream.read(), b'')
        return first, second

    def test_failed_exit_cannot_become_success_on_retry(self):
        for status in (1, 2, 7, 255):
            with self.subTest(exit_status=status):
                first, second = self.replies(status)
                for reply in (first, second):
                    self.assertTrue(reply.HasField('error'), reply)
                    self.assertIn('exit does not match', reply.error.message)

    def test_matching_exit_allows_exact_completed_retry(self):
        first, second = self.replies(0)
        self.assertTrue(first.HasField('view'), first)
        self.assertTrue(second.HasField('view'), second)
        self.assertEqual(first.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(first.view.record, second.view.record)


if __name__ == '__main__': unittest.main(verbosity=2)
