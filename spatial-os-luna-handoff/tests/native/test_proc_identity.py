"""Focused controls for bounded privileged /proc identity inspection."""
import json
import subprocess
import unittest
from unittest.mock import patch

import proc_identity


class ProcessIdentityTests(unittest.TestCase):
    def response(self, code, value):
        return subprocess.CompletedProcess([], code, json.dumps(value), '')

    def test_valid_identity_uses_bounded_sudo_argv(self):
        expected = dict(pid=41, ppid=9, starttime=1234,
                        exe='/build/spatial-authority-supervisor')
        with patch.object(proc_identity.subprocess, 'run',
                          return_value=self.response(0, expected)) as run:
            self.assertEqual(proc_identity.inspect_process_identity(41), expected)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:7], ['sudo', '-n', '/usr/bin/timeout',
            '--signal=TERM', '--kill-after=1s', '4s', '/usr/bin/python3'])
        self.assertEqual(argv[7:9], ['-c', proc_identity._INSPECT])
        self.assertEqual(argv[9], '41')
        self.assertEqual(run.call_args.kwargs['timeout'], 6)
        self.assertFalse(run.call_args.kwargs['check'])
        self.assertFalse(run.call_args.kwargs.get('shell', False))

    def test_malformed_output_fails_closed(self):
        for output in ('not-json', '[]'):
            with self.subTest(output=output), patch.object(
                    proc_identity.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 0, output, '')):
                with self.assertRaises(RuntimeError):
                    proc_identity.inspect_process_identity(41)

    def test_timeout_fails_closed(self):
        with patch.object(proc_identity.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired(['sudo'], 6)):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                proc_identity.inspect_process_identity(41)

    def test_disappeared_process_is_distinguished(self):
        with patch.object(proc_identity.subprocess, 'run',
                          return_value=self.response(3, {'error': 'process-disappeared'})):
            with self.assertRaises(ProcessLookupError):
                proc_identity.inspect_process_identity(41)

    def test_permission_and_identity_change_fail_closed(self):
        for code, reason in ((4, 'permission-denied'), (5, 'process-identity-changed')):
            with self.subTest(reason=reason), patch.object(
                    proc_identity.subprocess, 'run',
                    return_value=self.response(code, {'error': reason})):
                with self.assertRaisesRegex(RuntimeError, reason):
                    proc_identity.inspect_process_identity(41)


if __name__ == '__main__':
    unittest.main(verbosity=2)
