"""Measure real X input seat capacity and verify exhaustion cleanup."""
import json
import os
from pathlib import Path
import struct
import unittest

import core_pb2 as core
import input_pb2 as native_input
from test_scoped_input import ScopedInputTests


class NativeSeatCapacityTests(ScopedInputTests):
    def test_real_seat_capacity_releases_devices_and_recovers(self):
        process=self.launch()
        self.assertTrue(self.read(process,core.RenderResult).HasField('ready'))
        name=b'XInputExtension'
        header,_=self.supervisor.request(98,struct.pack('<HH',len(name),0)+name)
        self.assertTrue(header[8]);opcode=header[9]
        self.supervisor.request(opcode,struct.pack('<HH',2,0),minor=47)
        def devices():
            header,_=self.supervisor.request(opcode,struct.pack('<HH',0,0),minor=48)
            return struct.unpack_from('<H',header,8)[0]
        baseline=devices();sequence=0;seats=[];exhausted=False
        for _ in range(256):
            sequence+=1;reply=self.call(process,sequence,open=native_input.Empty())
            if reply.code==native_input.RESOURCE_LIMIT:
                exhausted=True;break
            self.assertEqual(reply.code,native_input.OK,reply)
            self.assertGreater(reply.seat,0);self.assertNotIn(reply.seat,seats)
            seats.append(reply.seat)
        self.assertGreaterEqual(len(seats),3,'Fewer than three simultaneous native seats')
        allocated=devices()
        self.assertEqual(allocated,baseline+4*len(seats))
        target=Path(os.environ.get('SPATIAL_SECURITY_EVIDENCE',str(self.root)))/'native-seat-capacity.json'
        target.write_text(json.dumps(dict(seats=len(seats),exhausted=exhausted,
            baseline_devices=baseline,allocated_devices=allocated),sort_keys=True,indent=2)+'\n')
        target.chmod(0o600)
        sequence+=1
        self.assertEqual(self.call(process,sequence,close=native_input.Seat(id=seats.pop())).code,native_input.OK)
        sequence+=1;replacement=self.call(process,sequence,open=native_input.Empty())
        self.assertEqual(replacement.code,native_input.OK)
        self.assertNotIn(replacement.seat,seats);seats.append(replacement.seat)
        for seat in seats:
            sequence+=1
            self.assertEqual(self.call(process,sequence,close=native_input.Seat(id=seat)).code,native_input.OK)
        self.assertEqual(devices(),baseline,'Seat exhaustion or cleanup leaked X devices')
        process.stdin.close();self.assertEqual(process.wait(timeout=5),0)


if __name__=='__main__':
    unittest.main(defaultTest='NativeSeatCapacityTests.test_real_seat_capacity_releases_devices_and_recovers',verbosity=2)
