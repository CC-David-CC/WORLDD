"""Adversarial compositor replies against the production checked client."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import core_pb2 as core
from harness.wire import frame

PROBE = sys.argv.pop(1)
PEER = r'''#!/usr/bin/python3
import os,sys,struct
from pathlib import Path
import core_pb2 as core
def send(message):
    data=message.SerializeToString()
    sys.stdout.buffer.write(struct.pack('!I',len(data))+data);sys.stdout.buffer.flush()
send(core.RenderResult(protocol_version=1,ready=core.RendererReady(gl_version='protocol fixture')))
header=sys.stdin.buffer.read(4)
if not header:raise SystemExit(0)
size=struct.unpack('!I',header)[0]
command=core.RenderCommand.FromString(sys.stdin.buffer.read(size))
Path(sys.argv[sys.argv.index('--state-dir')+1],'command.pb').write_bytes(command.SerializeToString())
fault=os.environ.get('PIXEL_FIXTURE_FAULT','')
reply=core.RenderResult(protocol_version=1,request_id=command.request_id)
value=reply.visibility;value.frame_id=1;value.world_revision=command.scene.world_revision
if command.include_pixels or fault=='unsolicited':
    value.image.width=command.width;value.image.height=command.height
    value.image.rgb8=b'\x11\x22\x33'*(command.width*command.height)
if fault=='missing':value.ClearField('image')
elif fault=='width':value.image.width+=1
elif fault=='height':value.image.height+=1
elif fault=='zero':value.image.width=0
elif fault=='truncated':value.image.rgb8=value.image.rgb8[:-1]
elif fault=='extra':value.image.rgb8+=b'\0'
elif fault=='unknown':value.image.MergeFromString(b'\x20\x01')
send(reply)
sys.stdin.buffer.read()  # Keep the peer alive until the checked client closes it.
'''


class RenderObservationClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='spatial-pixel-client-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.peer = self.root/'peer.py'
        self.peer.write_text(PEER)
        self.peer.chmod(0o700)

    def call(self, fault='', pixels=True, width=3, height=5):
        request = core.RenderCommand(protocol_version=1, request_id=7, width=width, height=height, observe=True, include_pixels=pixels)
        request.scene.world_revision = 19
        request.scene.camera.position.SetInParent()
        request.scene.camera.orientation.w = 1
        request.scene.config.vertical_fov = 1.5707963267948966
        request.scene.config.near_clip = .01
        request.scene.config.far_clip = 1000
        request.scene.config.background.SetInParent()
        return subprocess.run([PROBE, str(self.peer), str(self.root)], input=frame(request), capture_output=True,
            env={**os.environ, 'PIXEL_FIXTURE_FAULT': fault}, timeout=8)

    def decode(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertGreaterEqual(len(result.stdout), 4)
        size = struct.unpack('!I', result.stdout[:4])[0]
        self.assertEqual(size, len(result.stdout)-4)
        reply = core.RenderResult.FromString(result.stdout[4:])
        self.assertEqual(reply.request_id, 7)
        return reply.visibility

    def rejected(self, result):
        self.assertEqual(result.returncode, 2)
        self.assertFalse(result.stdout)
        for marker in [b'AddressSanitizer', b'LeakSanitizer', b'runtime error:']:
            self.assertNotIn(marker, result.stderr)

    def test_valid_pixels_use_checked_frame_and_exact_dimensions(self):
        value = self.decode(self.call())
        self.assertEqual((value.frame_id, value.world_revision), (1, 19))
        self.assertEqual((value.image.width, value.image.height), (3, 5))
        self.assertEqual(value.image.rgb8, b'\x11\x22\x33'*15)
        sent = core.RenderCommand.FromString((self.root/'command.pb').read_bytes())
        self.assertTrue(sent.observe)
        self.assertTrue(sent.include_pixels)
        self.assertFalse(sent.capture)

    def test_metadata_only_reply_has_no_image(self):
        self.assertFalse(self.decode(self.call(pixels=False)).HasField('image'))

    def test_missing_requested_image_is_rejected(self):
        self.rejected(self.call('missing'))

    def test_unsolicited_image_is_rejected(self):
        self.rejected(self.call('unsolicited', pixels=False))

    def test_changed_or_zero_image_dimensions_are_rejected(self):
        for fault in ['width', 'height', 'zero']:
            with self.subTest(fault=fault):
                self.rejected(self.call(fault))

    def test_truncated_rgb_data_is_rejected(self):
        self.rejected(self.call('truncated'))

    def test_extra_rgb_data_is_rejected(self):
        self.rejected(self.call('extra'))

    def test_unknown_image_field_is_rejected(self):
        self.rejected(self.call('unknown'))

    def test_excessive_pixel_request_is_not_sent_to_compositor(self):
        self.rejected(self.call(width=513, height=512))
        self.assertFalse((self.root/'command.pb').exists())


if __name__ == '__main__':
    unittest.main(defaultTest='RenderObservationClientTests', verbosity=2)
