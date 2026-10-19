import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.start_tmux_once import start_once


class StartTmuxOnceTests(unittest.TestCase):
    def runner(self, *, active=False):
        calls = []
        state = {'active': active}
        def invoke(argv, **kwargs):
            calls.append(list(argv))
            if argv[:3] == ['tmux', 'has-session', '-t']:
                return subprocess.CompletedProcess(argv, 0 if state['active'] else 1, '', 'session not found' if not state['active'] else '')
            if argv[:3] == ['tmux', 'new-session', '-d']:
                state['active'] = True
                return subprocess.CompletedProcess(argv, 0, '', '')
            raise AssertionError(argv)
        return invoke, calls

    def test_two_distinct_targets_each_start_once(self):
        with tempfile.TemporaryDirectory() as temp:
            starts = []
            for host in ('sb16', 'sb16-2'):
                run_dir = Path(temp) / host / 'run'
                invoke, calls = self.runner()
                start_once(expected_host=host, hostname=host, session='m08-'+host, run_dir=run_dir,
                           command=['/usr/bin/python3', 'job.py'], runner=invoke)
                starts.extend(c for c in calls if c[:3] == ['tmux', 'new-session', '-d'])
            self.assertEqual(len(starts), 2)
            self.assertEqual([c[4] for c in starts], ['m08-sb16', 'm08-sb16-2'])

    def test_refuses_host_mismatch_before_tmux(self):
        invoke, calls = self.runner()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, 'host mismatch'):
                start_once(expected_host='sb16', hostname='sb16-2', session='job', run_dir=Path(temp)/'new', command=['true'], runner=invoke)
        self.assertEqual(calls, [])

    def test_refuses_existing_run_directory_before_tmux(self):
        invoke, calls = self.runner()
        with tempfile.TemporaryDirectory() as temp:
            existing = Path(temp) / 'already'; existing.mkdir()
            with self.assertRaises(FileExistsError):
                start_once(expected_host='sb16', hostname='sb16', session='job', run_dir=existing, command=['true'], runner=invoke)
        self.assertEqual(calls, [])

    def test_refuses_existing_tmux_session(self):
        invoke, calls = self.runner(active=True)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(FileExistsError, 'tmux session'):
                start_once(expected_host='sb16', hostname='sb16', session='job', run_dir=Path(temp)/'new', command=['true'], runner=invoke)
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(c[:3] == ['tmux', 'new-session', '-d'] for c in calls))

    def test_writes_persistent_receipt_and_starts_once(self):
        invoke, calls = self.runner()
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / 'new'
            receipt = start_once(expected_host='sb16', hostname='sb16', session='job', run_dir=run, command=['/usr/bin/python3', 'job.py'], runner=invoke)
            self.assertTrue((run/'run-worker.sh').is_file())
            self.assertTrue((run/'startup.json').is_file())
            self.assertEqual(receipt['host'], 'sb16')
        self.assertEqual(sum(c[:3] == ['tmux', 'new-session', '-d'] for c in calls), 1)


if __name__ == '__main__':
    unittest.main()
