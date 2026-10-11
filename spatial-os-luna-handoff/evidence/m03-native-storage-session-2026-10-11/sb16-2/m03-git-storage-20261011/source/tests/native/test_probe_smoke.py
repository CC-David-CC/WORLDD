"""Tests of the independent witness itself on an owned Xvfb, not the Spatial OS."""
import ctypes,ctypes.util,os,secrets,tempfile,unittest
from pathlib import Path
from harness.probe import Probe
from harness.oracles import PPM

class ProbeSmokeTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("SPATIAL_SMOKE_OWNED_DISPLAY")!="1":raise RuntimeError("Use tools/probe_smoke.py; do not inject into an arbitrary desktop")
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.p=Probe(self.root,xi2=os.environ.get("SPATIAL_PROBE_XI2")=="1");self.addCleanup(self.p.close)
    def test_ready_and_geometry(self):
        self.assertEqual(self.p.ready["width"],320);g=self.p.geometry();self.assertEqual((g["width"],g["height"]),(320,240))
    def test_quadrants_are_correct(self):
        self.p.command("quadrants");path=self.root/"frame.ppm";self.p.capture(self.p.xid,path);im=PPM(path)
        self.assertEqual(im.pixel(80,60),(255,0,0));self.assertEqual(im.pixel(240,60),(0,255,0))
        self.assertEqual(im.pixel(80,180),(0,0,255));self.assertEqual(im.pixel(240,180),(255,255,255))
    def test_random_paint_roundtrip(self):
        for _ in range(8):
            color=tuple(secrets.randbelow(256) for _ in range(3));self.p.command("paint",*color)
            path=self.root/"frame.ppm";self.p.capture(self.p.xid,path);self.assertEqual(PPM(path).pixel(160,120),color)
    def test_odd_resize_redraw(self):
        self.p.command("resize",641,479);self.p.wait(lambda e:e["event"]=="configure" and e["width"]==641)
        self.p.command("quadrants");path=self.root/"frame.ppm";self.p.capture(self.p.xid,path);im=PPM(path)
        self.assertEqual((im.width,im.height),(641,479));self.assertEqual(im.pixel(600,400),(255,255,255))
    def test_unmap_remap(self):
        self.p.command("unmap");self.p.wait(lambda e:e["event"]=="unmap")
        mark=len(self.p.events);self.p.command("map");self.p.wait(lambda e:e["event"]=="map",after=mark)
    def test_popup_records_parent(self):
        self.p.command("popup",80,60);e=self.p.wait(lambda e:e["event"]=="popup")
        self.assertEqual(e["parent_xid"],self.p.xid);self.assertNotEqual(e["xid"],self.p.xid)
    def test_destroy_is_observed(self):
        self.p.command("destroy");self.p.wait(lambda e:e["event"]=="destroy" and e["xid"]==self.p.xid)
    def test_real_server_injected_input_is_observed(self):
        lib=ctypes.CDLL(ctypes.util.find_library("X11"));xt=ctypes.CDLL(ctypes.util.find_library("Xtst"))
        lib.XOpenDisplay.argtypes=[ctypes.c_char_p];lib.XOpenDisplay.restype=ctypes.c_void_p
        lib.XSync.argtypes=[ctypes.c_void_p,ctypes.c_int];lib.XCloseDisplay.argtypes=[ctypes.c_void_p]
        lib.XSetInputFocus.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
        lib.XKeysymToKeycode.argtypes=[ctypes.c_void_p,ctypes.c_ulong];lib.XKeysymToKeycode.restype=ctypes.c_ubyte
        xt.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
        xt.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
        xt.XTestFakeKeyEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
        d=lib.XOpenDisplay(None);self.assertTrue(d)
        try:
            mark=len(self.p.events);lib.XSetInputFocus(d,self.p.xid,1,0)
            xt.XTestFakeMotionEvent(d,0,80,60,0);xt.XTestFakeButtonEvent(d,1,1,0);xt.XTestFakeButtonEvent(d,1,0,0)
            code=lib.XKeysymToKeycode(d,ord("a"));xt.XTestFakeKeyEvent(d,code,1,0);xt.XTestFakeKeyEvent(d,code,0,0);lib.XSync(d,0)
            e=self.p.wait(lambda e:e["event"]=="key" and e["pressed"],after=mark);self.assertEqual(e["text_hex"],"61")
            click=self.p.wait(lambda e:e["event"]=="button" and e["pressed"],after=mark);self.assertEqual((click["x"],click["y"]),(80,60))
        finally:lib.XCloseDisplay(d)
