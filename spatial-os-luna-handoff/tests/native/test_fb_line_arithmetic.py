"""Independent X wire drawing exercises both framebuffer line-error paths."""
import struct
import unittest
import test_resource_isolation as resources


class FramebufferLineArithmeticTests(unittest.TestCase):
    setUp=resources.ResourceIsolationTests.setUp
    start=resources.ResourceIsolationTests.start
    connect=resources.ResourceIsolationTests.connect
    scene=resources.ResourceIsolationTests.scene

    def pixel(self,x,y):
        reply=self.owner.operation(73,self.owner.pack('IhhHHI',self.pixmap,x,y,1,1,0xffffffff),minor=2,reply=True)
        self.assertIsInstance(reply,tuple)
        _,data=reply;self.assertEqual(len(data),4)
        return struct.unpack('<I',data)[0]&0xffffff

    def draw(self,opcode):
        self.scene()
        self.assertIsNone(self.owner.operation(56,self.owner.pack('III',self.gc,4,0)))
        self.assertIsNone(self.owner.operation(70,self.owner.pack('IIhhHH',self.pixmap,self.gc,0,0,16,16)))
        self.assertEqual(self.pixel(9,9),0)
        self.assertIsNone(self.owner.operation(56,self.owner.pack('III',self.gc,4,0x789abc)))
        # PolyLine's coordinate mode is the request header's data byte.
        self.assertIsNone(self.owner.operation(opcode,self.owner.pack('IIhhhh',self.pixmap,self.gc,12,12,6,6)))
        self.assertEqual(self.pixel(9,9),0x789abc)
        self.assertEqual(self.pixel(1,1),0)
        self.assertEqual(self.owner.inspect().workspace,b'one')

    def test_polyline_uses_defined_negative_error_arithmetic(self):self.draw(65)
    def test_polysegment_uses_defined_negative_error_arithmetic(self):self.draw(66)


if __name__=='__main__':unittest.main(verbosity=2)
