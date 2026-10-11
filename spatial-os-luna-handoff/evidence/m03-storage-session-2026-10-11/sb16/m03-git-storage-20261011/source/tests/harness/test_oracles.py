import math,tempfile,unittest
from pathlib import Path
from harness.oracles import *

class OracleTests(unittest.TestCase):
    def test_known_yaw(self):self.assertTrue(close_vec(rotate(axis_angle([0,1,0],math.pi/2),[0,0,-1]),[-1,0,0]))
    def test_known_roll(self):self.assertTrue(close_vec(rotate(axis_angle([0,0,1],math.pi/2),[1,0,0]),[0,1,0]))
    def test_known_pitch(self):self.assertTrue(close_vec(rotate(axis_angle([1,0,0],math.pi/2),[0,1,0]),[0,0,1]))
    def test_transform_known(self):self.assertTrue(close_vec(transform(pose([2,3,4]),[1,2,3]),[3,5,7]))
    def test_negative_q_same_rotation(self):self.assertTrue(same_rotation([0,0,0,1],[0,0,0,-1]))
    def test_nonfinite_not_close(self):self.assertFalse(close_vec([float("nan")],[0]))
    def test_axis_zero_rejected(self):
        with self.assertRaises(ValueError):axis_angle([0,0,0],1)
    def test_half_open_edge(self):self.assertIsNone(uv_pixel([1,.5],[320,240]))
    def test_floor_not_round(self):self.assertEqual(uv_pixel([.4999,.4999],[320,240]),[159,119])
    def test_negative_uv_not_clamped(self):self.assertIsNone(uv_pixel([-.001,.5],[320,240]))
    def test_p6_binary_whitespace_pixel_is_not_eaten(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"a.ppm";write_ppm(p,2,2,[10,13,32]);im=PPM(p)
            self.assertEqual(im.pixel(0,0),(10,13,32));self.assertEqual(im.pixel(1,1),(10,13,32))
    def test_p6_comments(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"a.ppm";p.write_bytes(b"P6\n# hi\n1 1\n255\n\xff\x00\x80")
            self.assertEqual(PPM(p).pixel(0,0),(255,0,128))
    def test_p6_truncation_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"a.ppm";p.write_bytes(b"P6\n2 2\n255\n\x00")
            with self.assertRaises(ValueError):PPM(p)
    def test_p6_wrong_format_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"a.ppm";p.write_bytes(b"P3\n1 1\n255\n0 0 0\n")
            with self.assertRaises(ValueError):PPM(p)
    def test_mutation_wrong_axis_is_detectable(self):self.assertFalse(close_vec([1,0,0],[-1,0,0]))
    def test_mutation_uv_flip_is_detectable(self):self.assertNotEqual(uv_pixel([.25,.75],[320,240]),[80,60])
