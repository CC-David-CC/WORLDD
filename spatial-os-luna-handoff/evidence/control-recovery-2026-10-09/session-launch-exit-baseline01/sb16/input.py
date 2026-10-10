"""A rejected supervisor exit must remain rejected on the same retained launch."""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import launch_pb2 as launch
from harness.wire import read_message

BINARY = Path(sys.argv.pop(1))


class ExitStatusBaseline(unittest.TestCase):
    def test_failed_exit_cannot_become_success_on_retry(self):
        with tempfile.TemporaryDirectory(prefix='spatial-exit-status-') as directory:
            result = subprocess.run([str(BINARY), directory, '7'], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        stream = io.BytesIO(result.stdout)
        first, second = [read_message(stream, launch.AuthorityLaunchReply) for _ in range(2)]
        self.assertTrue(first.HasField('error'), first)
        self.assertIn('exit does not match', first.error.message)
        self.assertEqual(stream.read(), b'')
        self.assertTrue(second.HasField('error'),
            'Retry forgot the rejected supervisor exit status and accepted cleanup: ' + str(second))


if __name__ == '__main__': unittest.main(verbosity=2)
