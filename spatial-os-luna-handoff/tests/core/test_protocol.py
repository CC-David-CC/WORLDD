"""Additional malformed-wire cases. The locked acceptance suite is separate."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BINARY = str(Path(sys.argv.pop(1)).resolve())


class ProtocolBoundaryTests(unittest.TestCase):
    def exchange(self, frames):
        with tempfile.TemporaryDirectory() as root:
            run = subprocess.run([BINARY, '--state-dir', root], input=frames,
                                 capture_output=True, timeout=5)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr, b'')
        return [json.loads(line) for line in run.stdout.splitlines()]

    def test_invalid_frames_recover_at_next_line(self):
        bad = [b'', b'{', b'[]', b'{"id":1,"method":"system.hello","params":{"x":NaN}}',
               b'{"id":1,"method":"system.hello","params":{"x":1e999}}',
               b'{"id":1,"id":2,"method":"system.hello","params":{}}',
               b'{"id":1,"method":"system.hello","params":{"x":{"a":1,"a":2}}}',
               b'{"id":1,"method":"system.hello","params":{"x":"\xff"}}',
               b'{"id":1,"method":"system.hello","params":{}} trailing',
               b'[' * 100 + b'0' + b']' * 100]
        hello = b'{"id":29,"method":"system.hello","params":{}}\n'
        for frame in bad:
            with self.subTest(frame=frame[:100]):
                rows = self.exchange(frame + b'\n' + hello)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]['error']['code'], 'INVALID_ARGUMENT')
                self.assertNotIn('result', rows[0])
                self.assertEqual(rows[1]['id'], 29)
                self.assertEqual(rows[1]['result']['implementation'], 'production')

    def test_envelope_types_and_unknown_fields(self):
        good = {'id': 7, 'method': 'system.hello', 'params': {}}
        requests = [dict(good, id=True), dict(good, id=1.0), dict(good, id='7'),
                    dict(good, id=None), dict(good, method=[]), dict(good, params=[]),
                    dict(good, extra=1), dict(good, params={'extra': 1})]
        requests += [{k: v for k, v in good.items() if k != field} for field in good]
        for request in requests:
            with self.subTest(request=request):
                row, = self.exchange((json.dumps(request) + '\n').encode())
                self.assertEqual(row['error']['code'], 'INVALID_ARGUMENT')

    def test_integer_ids_preserve_sign_and_precision(self):
        ids = [0, -3, 2**63 - 1, 2**64 - 1, -(2**63)]
        frames = ''.join(json.dumps({'id': i, 'method': 'system.hello', 'params': {}}) + '\n' for i in ids)
        self.assertEqual([row['id'] for row in self.exchange(frames.encode())], ids)

    def test_oversized_frame_does_not_consume_following_request(self):
        rows = self.exchange(b'x' * (1024 * 1024 + 1) + b'\n' +
                             b'{"id":3,"method":"system.hello","params":{}}\n')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(rows[1]['id'], 3)

    def test_eof_does_not_execute_incomplete_frame(self):
        row, = self.exchange(b'{"id":1,"method":"system.hello","params":{}}')
        self.assertEqual(row['error']['code'], 'INVALID_ARGUMENT')

    def test_world_get_has_no_fake_success(self):
        for params, code in [({}, 'INVALID_ARGUMENT'), ({'id': True}, 'INVALID_ARGUMENT'),
                             ({'id': 'room-a'}, 'UNIMPLEMENTED')]:
            request = {'id': 1, 'method': 'world.get', 'params': params}
            row, = self.exchange((json.dumps(request) + '\n').encode())
            self.assertEqual(row['error']['code'], code)

    def test_state_directory_must_be_absolute(self):
        run = subprocess.run([BINARY, '--state-dir', 'relative'], capture_output=True, timeout=5)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, b'')


if __name__ == '__main__':
    unittest.main(verbosity=2)
