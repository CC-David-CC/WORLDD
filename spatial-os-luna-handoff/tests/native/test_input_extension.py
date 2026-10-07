"""Observe production server input through the independent XI2 C witness."""
import ctypes
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest

import input_pb2 as pb
from harness.probe import Probe

BRIDGE = str(Path(sys.argv.pop(1)).resolve())


class Relay:
    def __init__(self, folder, owner):
        self.sequence = 0
        self.log = (folder / ("owner.stderr" if owner else "observer.stderr")).open("wb")
        self.proc = subprocess.Popen([BRIDGE] + (["--owner"] if owner else []),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.receive()

    def receive(self):
        def exact(count):
            data = b""
            end = time.monotonic() + 5
            while len(data) < count:
                if not select.select([self.proc.stdout], [], [], max(0, end - time.monotonic()))[0]:
                    raise AssertionError("Native bridge reply timed out")
                part = os.read(self.proc.stdout.fileno(), count - len(data))
                if not part:
                    raise AssertionError("Native bridge exited: " + str(self.proc.poll()))
                data += part
            return data
        size = struct.unpack(">I", exact(4))[0]
        if size > 4096:
            raise AssertionError("Oversized reply")
        return pb.Reply.FromString(exact(size))

    def request(self, operation, code=pb.OK, **values):
        self.sequence += 1
        request = pb.Request(protocol_version=1, request_id=self.sequence)
        getattr(request, operation).CopyFrom({
            "open": pb.Empty, "inspect": pb.Inspect, "button": pb.Button,
            "key": pb.Key, "cancel": pb.Seat, "close": pb.Seat, "status": pb.Seat, "motion": pb.Motion
        }[operation](**values))
        data = request.SerializeToString()
        self.proc.stdin.write(struct.pack(">I", len(data)) + data)
        self.proc.stdin.flush()
        reply = self.receive()
        if reply.code != code or reply.request_id != self.sequence:
            raise AssertionError(f"Unexpected native reply: {reply}")
        return reply

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
                raise AssertionError("Native bridge did not stop")
        self.proc.stdout.close()
        self.log.close()


class NativeInputTests(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('SPATIAL_INPUT_EVIDENCE')
        self.temp = None if base else tempfile.TemporaryDirectory(prefix="spatial-native-input-")
        self.folder = Path(base) / self._testMethodName if base else Path(self.temp.name)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.owner = Relay(self.folder, True)
        self.probes = []
        self.xlib = ctypes.CDLL("libX11.so.6")
        self.xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.xlib.XOpenDisplay.restype = ctypes.c_void_p
        self.xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.xlib.XStringToKeysym.restype = ctypes.c_ulong
        self.xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.xlib.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.xlib.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
        self.xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.display = self.xlib.XOpenDisplay(os.environ["SPATIAL_TEST_DISPLAY"].encode())
        self.assertTrue(self.display)
        self.xi = ctypes.CDLL("libXi.so.6")
        self.xi.XIQueryVersion.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        self.xi.XIQueryVersion.restype = ctypes.c_int
        major, minor = ctypes.c_int(2), ctypes.c_int(2)
        self.assertEqual(self.xi.XIQueryVersion(self.display, ctypes.byref(major), ctypes.byref(minor)), 0)
        self.assertGreaterEqual((major.value, minor.value), (2, 0))
        self.xi.XIQueryDevice.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.xi.XIQueryDevice.restype = ctypes.c_void_p
        self.xi.XIFreeDeviceInfo.argtypes = [ctypes.c_void_p]

    def tearDown(self):
        self.owner.close()
        for probe in self.probes:
            probe.close()
        self.xlib.XCloseDisplay(self.display)
        if self.owner.proc.returncode:
            print((self.folder / "owner.stderr").read_text(), file=sys.stderr)
        self.assertEqual(self.owner.proc.returncode, 0)
        if self.temp:
            self.temp.cleanup()

    def probe(self, xi2=True):
        probe = Probe(self.folder, xi2=xi2, display=os.environ["SPATIAL_TEST_DISPLAY"])
        self.probes.append(probe)
        end = time.monotonic() + 3
        while True:
            try:
                target = self.owner.request("inspect", xid=probe.xid).target
                return probe, target
            except AssertionError:
                if time.monotonic() >= end:
                    raise
                time.sleep(.01)

    def button(self, seat, target, pressed, x=80, y=60, code=pb.OK):
        return self.owner.request("button", code=code, seat=seat, target=target,
                                  x=x, y=y, button=1, pressed=pressed, focus=True)

    def key(self, seat, symbol, pressed):
        code = self.xlib.XKeysymToKeycode(self.display, self.xlib.XStringToKeysym(symbol.encode()))
        self.assertGreater(code, 0)
        return self.owner.request("key", seat=seat, keycode=code, pressed=pressed)

    def device_count(self):
        count = ctypes.c_int()
        devices = self.xi.XIQueryDevice(self.display, 0, ctypes.byref(count))
        self.assertTrue(devices)
        self.xi.XIFreeDeviceInfo(devices)
        return count.value

    def test_seat_lifecycle_does_not_leak_native_devices(self):
        baseline = self.device_count()
        ids = set()
        for _ in range(40):
            seat = self.owner.request("open").seat
            self.assertNotIn(seat, ids)
            ids.add(seat)
            self.assertEqual(self.device_count(), baseline + 4)
            self.owner.request("close", id=seat)
            self.assertEqual(self.device_count(), baseline)
            self.owner.request("status", code=pb.NO_SEAT, id=seat)

    def test_replayed_release_does_not_change_native_state(self):
        probe, target = self.probe()
        seat = self.owner.request("open").seat
        self.button(seat, target, True)
        self.owner.sequence -= 1
        self.button(seat, target, False, code=pb.INVALID_ARGUMENT)
        self.assertEqual(self.owner.request("status", id=seat).held_buttons, 2)
        self.button(seat, target, False)
        events = [e["pressed"] for e in probe.input_events() if e["event"] == "button"]
        self.assertEqual(events, [True, False])

    def test_destroy_clears_focus_and_held_state(self):
        probe, target = self.probe()
        seat = self.owner.request("open").seat
        self.button(seat, target, True)
        self.key(seat, "Shift_L", True)
        probe.command("destroy")
        status = self.owner.request("status", id=seat)
        self.assertEqual(status.held_buttons, 0)
        self.assertFalse(any(status.held_keys))
        self.assertFalse(status.HasField("focus"))

    def test_overlapping_windows_receive_local_pixels_and_separate_devices(self):
        left, lt = self.probe()
        right, rt = self.probe()
        for probe in (left, right):
            geometry = probe.geometry()
            self.assertEqual((geometry["x"], geometry["y"]), (0, 0))
        a = self.owner.request("open")
        b = self.owner.request("open")
        for seat, target in ((a.seat, lt), (b.seat, rt)):
            self.button(seat, target, True)
            self.button(seat, target, False)
        for probe, seat in ((left, a), (right, b)):
            events = [e for e in probe.input_events() if e["event"] == "button"]
            self.assertEqual([e["pressed"] for e in events], [True, False])
            self.assertEqual([(e["x"], e["y"]) for e in events], [(80, 60), (80, 60)])
            self.assertEqual({e["device"] for e in events}, {seat.pointer_device})
        self.assertNotEqual(a.keyboard_device, b.keyboard_device)
        self.key(a.seat, "Shift_L", True)
        for _ in range(10):
            for seat, symbol in ((a.seat, "a"), (b.seat, "b")):
                self.key(seat, symbol, True)
                self.key(seat, symbol, False)
        self.key(a.seat, "Shift_L", False)
        for probe, seat, text in ((left, a, "41"), (right, b, "62")):
            events = [e for e in probe.input_events() if e["event"] == "key" and e["pressed"] and e["text_hex"]]
            self.assertEqual([e["text_hex"] for e in events], [text] * 10)
            self.assertEqual({e["device"] for e in events}, {seat.keyboard_device})

    def test_cancel_releases_native_keys_and_buttons(self):
        probe, target = self.probe()
        seat = self.owner.request("open").seat
        self.button(seat, target, True)
        self.key(seat, "Shift_L", True)
        status = self.owner.request("cancel", id=seat)
        self.assertEqual(status.held_buttons, 0)
        self.assertFalse(any(status.held_keys))
        self.assertFalse(status.HasField("focus"))
        events = probe.input_events()
        for kind in ("key", "button"):
            self.assertEqual([e["pressed"] for e in events if e["event"] == kind], [True, False])

    def test_ordinary_core_client_receives_input(self):
        probe, target = self.probe(xi2=False)
        seat = self.owner.request("open").seat
        self.button(seat, target, True)
        self.button(seat, target, False)
        self.key(seat, "a", True)
        self.key(seat, "a", False)
        events = probe.input_events()
        for kind in ("key", "button"):
            self.assertEqual([e["pressed"] for e in events if e["event"] == kind], [True, False])
        self.assertEqual([e["text_hex"] for e in events if e["event"] == "key" and e["pressed"]], ["61"])

    def test_owner_disconnect_releases_native_state(self):
        probe, target = self.probe()
        seat = self.owner.request("open").seat
        self.button(seat, target, True)
        self.key(seat, "Control_L", True)
        self.owner.close()
        events = probe.input_events()
        for kind in ("key", "button"):
            self.assertEqual([e["pressed"] for e in events if e["event"] == kind], [True, False])

    def test_resize_back_invalidates_server_generation(self):
        probe, old = self.probe()
        seat = self.owner.request("open").seat
        self.button(seat, old, True)
        probe.command("resize", 640, 480)
        probe.command("resize", 320, 240)
        # Synchronize through the owner so ConfigureRequest processing has completed.
        new = self.owner.request("inspect", xid=probe.xid).target
        self.assertNotEqual(old.generation, new.generation)
        self.button(seat, old, True, code=pb.STALE_TARGET)
        self.assertEqual(self.owner.request("status", id=seat).held_buttons, 0)

    def test_unprivileged_connection_cannot_open_or_inspect(self):
        probe, target = self.probe()
        observer = Relay(self.folder, False)
        try:
            observer.request("open", code=pb.FORBIDDEN)
            observer.request("inspect", code=pb.FORBIDDEN, xid=probe.xid)
        finally:
            observer.close()

    def test_offscreen_window_preserves_local_coordinates(self):
        probe, target = self.probe()
        self.xlib.XMoveWindow(self.display, probe.xid, -500, -500)
        self.xlib.XSync(self.display, False)
        end = time.monotonic() + 3
        while probe.geometry()["x"] != -500:
            self.assertLess(time.monotonic(), end)
            time.sleep(.01)
        target = self.owner.request("inspect", xid=probe.xid).target
        seat = self.owner.request("open").seat
        self.button(seat, target, True)
        self.button(seat, target, False)
        events = [e for e in probe.input_events() if e["event"] == "button"]
        self.assertEqual([(e["x"], e["y"]) for e in events], [(80, 60), (80, 60)])

    def test_captured_motion_keeps_signed_coordinates_and_original_target(self):
        probe, target = self.probe()
        other, other_target = self.probe()
        seat = self.owner.request("open").seat
        self.owner.request("motion", code=pb.INVALID_ARGUMENT, seat=seat, target=target, x=10, y=20)
        self.button(seat, target, True)
        self.owner.request("motion", seat=seat, target=target, x=-160, y=360)
        self.owner.request("motion", code=pb.BUSY, seat=seat, target=other_target, x=40, y=50)
        self.owner.request("motion", code=pb.INVALID_ARGUMENT, seat=seat, target=target, x=32768, y=50)
        self.owner.request("button", seat=seat, target=target, button=1, pressed=False, keep_capture_position=True)
        events = probe.input_events()
        motion = [event for event in events if event['event'] == 'motion']
        self.assertEqual([(event['x'], event['y']) for event in motion], [(-160, 360)])
        buttons = [event for event in events if event['event'] == 'button']
        self.assertEqual([event['pressed'] for event in buttons], [True, False])
        self.assertEqual((buttons[-1]['x'], buttons[-1]['y']), (-160, 360))
        self.assertEqual([event for event in other.input_events() if event['event'] in ('button', 'motion')], [])

    def test_cancel_at_captured_position_releases_real_client(self):
        probe, target = self.probe(xi2=False)
        seat = self.owner.request("open").seat
        self.button(seat, target, True)
        self.owner.request("motion", seat=seat, target=target, x=480, y=-120)
        self.owner.request("cancel", id=seat)
        events = probe.input_events()
        motion = [event for event in events if event['event'] == 'motion']
        self.assertEqual([(event['x'], event['y']) for event in motion], [(480, -120)])
        release = [event for event in events if event['event'] == 'button' and not event['pressed']]
        self.assertEqual([(event['x'], event['y']) for event in release], [(480, -120)])

    def test_explicit_grab_is_denied_without_stealing_actor_capture(self):
        probe, target = self.probe()
        other, _ = self.probe()
        opened = self.owner.request('open')
        self.button(opened.seat, target, True)
        # Use a checked XCB request. libXi maps a suppressed BadAccess to success.
        class Cookie(ctypes.Structure):
            _fields_ = [('sequence', ctypes.c_uint32)]
        xcb = ctypes.CDLL('libxcb.so.1')
        xi = ctypes.CDLL('libxcb-xinput.so.0')
        libc = ctypes.CDLL(None)
        libc.free.argtypes = [ctypes.c_void_p]
        xcb.xcb_connect.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
        xcb.xcb_connect.restype = ctypes.c_void_p
        xcb.xcb_connection_has_error.argtypes = [ctypes.c_void_p]
        xcb.xcb_disconnect.argtypes = [ctypes.c_void_p]
        xi.xcb_input_xi_query_version.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
        xi.xcb_input_xi_query_version.restype = Cookie
        xi.xcb_input_xi_query_version_reply.argtypes = [ctypes.c_void_p, Cookie, ctypes.POINTER(ctypes.c_void_p)]
        xi.xcb_input_xi_query_version_reply.restype = ctypes.c_void_p
        xi.xcb_input_xi_grab_device.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_uint16, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8,
            ctypes.c_uint16, ctypes.POINTER(ctypes.c_uint32)]
        xi.xcb_input_xi_grab_device.restype = Cookie
        xi.xcb_input_xi_grab_device_reply.argtypes = [ctypes.c_void_p, Cookie, ctypes.POINTER(ctypes.c_void_p)]
        xi.xcb_input_xi_grab_device_reply.restype = ctypes.c_void_p
        connection = xcb.xcb_connect(os.environ['SPATIAL_TEST_DISPLAY'].encode(), None)
        self.assertTrue(connection)
        try:
            self.assertEqual(xcb.xcb_connection_has_error(connection), 0)
            error = ctypes.c_void_p()
            version = xi.xcb_input_xi_query_version(connection, 2, 2)
            reply = xi.xcb_input_xi_query_version_reply(connection, version, ctypes.byref(error))
            try:
                self.assertTrue(reply); self.assertFalse(error.value)
            finally:
                libc.free(reply); libc.free(error)
            mask = (ctypes.c_uint32 * 1)((1 << 4) | (1 << 5) | (1 << 6))
            cookie = xi.xcb_input_xi_grab_device(connection, other.xid, 0, 0, opened.pointer_device, 1, 1, 0, 1, mask)
            error = ctypes.c_void_p()
            reply = xi.xcb_input_xi_grab_device_reply(connection, cookie, ctypes.byref(error))
            try:
                self.assertFalse(reply); self.assertTrue(error.value)
                packet = ctypes.string_at(error, 32)
                self.assertEqual(packet[:2], bytes((0, 10)))  # X error, BadAccess.
                self.assertEqual(struct.unpack('=H', packet[8:10])[0], 51)  # XIGrabDevice.
            finally:
                libc.free(reply); libc.free(error)
        finally:
            xcb.xcb_disconnect(connection)
        self.owner.request('motion', seat=opened.seat, target=target, x=420, y=-100)
        self.owner.request('cancel', id=opened.seat)
        events = probe.input_events()
        self.assertEqual([(e['x'], e['y']) for e in events if e['event'] == 'motion'], [(420, -100)])
        self.assertEqual([e['pressed'] for e in events if e['event'] == 'button'], [True, False])
        self.assertEqual([e for e in other.input_events() if e['event'] in ('button', 'motion')], [])


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False).result
    summary = {"executed": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "skipped": len(result.skipped),
               "passed": result.wasSuccessful() and not result.skipped}
    if folder := os.environ.get('SPATIAL_INPUT_EVIDENCE'):
        (Path(folder) / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    raise SystemExit(0 if summary['passed'] else 1)
