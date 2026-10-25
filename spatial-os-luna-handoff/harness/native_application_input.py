"""Independent native XI2 test observer, reused from world application control.

Only trusted test fixtures receive this connection. Production actors do not.
"""
import select
import struct
import time

class NativeXI2Observer:
    """Privileged XI 2.0 witness. All reads share an absolute request deadline."""
    GENERIC_EVENT=35
    EVENT_NAMES={2:'key',3:'key',4:'button',5:'button',6:'motion'}
    def __init__(self,client,window,select_events=True):
        self.client=client;self.window=window;self.events=[];self.closed=False
        self.opcode=None
        try:
            client.stream.setblocking(False)
            name=b'XInputExtension'
            ext,_=self.request(98,struct.pack('<HH',len(name),0)+name)
            if not ext[8]:raise AssertionError('XInputExtension is missing')
            self.opcode=ext[9]
            version,extra=self.request(self.opcode,struct.pack('<HH',2,0),minor=47)
            if extra or struct.unpack_from('<HH',version,8)!=(2,0):
                raise AssertionError('XI 2.0 negotiation refused')
            if select_events:
                mask=bytearray(4)
                for kind in self.EVENT_NAMES:mask[kind//8]|=1<<(kind%8)
                # XIAllMasterDevices=1. Only device events use the 80-byte layout.
                client.send(self.opcode,struct.pack('<IHHHH',window,1,0,1,1)+bytes(mask),minor=46)
                self.request(43) # Surface selection errors before actor input starts.
        except BaseException:
            self.close();raise
    def deselect(self):
        # Fence selection removal on this connection before another client sends input.
        self.client.stream.settimeout(3.0)
        try:self.client.send(self.opcode,struct.pack('<IHHHH',self.window,1,0,1,1)+bytes(4),minor=46)
        finally:self.client.stream.setblocking(False)
        self.request(43)
    def close(self):
        if not self.closed:self.closed=True;self.client.close()
    def _exact(self,size,deadline):
        result=bytearray()
        while len(result)<size:
            remaining=deadline-time.monotonic()
            if remaining<=0 or not select.select([self.client.stream],[],[],remaining)[0]:raise AssertionError('XI2 deadline expired during packet read')
            try:chunk=self.client.stream.recv(size-len(result))
            except BlockingIOError:continue
            if not chunk:raise AssertionError('XI2 observer connection closed')
            result.extend(chunk)
        return bytes(result)
    def request(self,opcode,payload=b'',minor=0,timeout=3.0):
        deadline=time.monotonic()+timeout
        # NativeClient.send uses sendall. Bound writes too, then restore nonblocking reads.
        self.client.stream.settimeout(max(0.001,deadline-time.monotonic()))
        try:self.client.send(opcode,payload,minor)
        finally:self.client.stream.setblocking(False)
        while True:
            header=self._exact(32,deadline)
            if header[0]==self.GENERIC_EVENT:
                event=self._packet(deadline,header)
                if event is not None:self.events.append(event)
                continue
            if header[0]!=1:raise AssertionError('Native XI request returned error/event: '+repr(header[:4]))
            if struct.unpack_from('<H',header,2)[0]!=self.client.sequence%65536:
                raise AssertionError('Native XI reply sequence mismatch')
            size=struct.unpack_from('<I',header,4)[0]*4
            if size>65536:raise AssertionError('Excessive XI reply')
            return header,self._exact(size,deadline)
    def _packet(self,deadline,header=None):
        if header is None:header=self._exact(32,deadline)
        if header[0]!=self.GENERIC_EVENT or header[1]!=self.opcode:raise AssertionError('Unexpected XI2 observer packet '+repr(header[:12]))
        size=struct.unpack_from('<I',header,4)[0]*4
        if size>65536:raise AssertionError('Excessive XI2 packet')
        packet=header+self._exact(size,deadline)
        kind=struct.unpack_from('<H',packet,8)[0]
        if kind not in self.EVENT_NAMES:raise AssertionError('Unselected XI2 event')
        # Pinned XI2proto.h: sourceid at 52, fixed header 80, then masks and FP3232 values.
        if len(packet)<80:raise AssertionError('Truncated XI2 device header')
        blen,vlen=struct.unpack_from('<HH',packet,48)
        mask_end=80+4*(blen+vlen)
        if mask_end>len(packet):raise AssertionError('Truncated XI2 masks')
        valuators=packet[80+4*blen:mask_end]
        values=sum(byte.bit_count() for byte in valuators)
        if len(packet)!=mask_end+8*values:raise AssertionError('Invalid XI2 valuator payload length')
        window=struct.unpack_from('<I',packet,24)[0]
        device=struct.unpack_from('<H',packet,10)[0]
        source=struct.unpack_from('<H',packet,52)[0]
        if window!=self.window:raise AssertionError('Wrong XI2 event window')
        if device<=1 or source<=1:raise AssertionError('Invalid XI2 device/source')
        return {'kind':self.EVENT_NAMES[kind],'type':kind,'pressed':kind in (2,4),
                'window':window,'device':device,'source':source,'detail':struct.unpack_from('<I',packet,16)[0]}
    def controlled_devices(self):
        head,extra=self.request(self.opcode,struct.pack('<I',self.window),minor=45)
        if not head[8] or extra:raise AssertionError('XIGetClientPointer failed')
        pointer=struct.unpack_from('<H',head,10)[0]
        head,data=self.request(self.opcode,struct.pack('<HH',0,0),minor=48) # XIAllDevices
        count=struct.unpack_from('<H',head,8)[0];devices={};enabled_devices=set();offset=0
        for _ in range(count):
            if offset+12>len(data):raise AssertionError('Truncated XI device info')
            device,use,attachment,classes,nlen,enabled=struct.unpack_from('<HHHHHBx',data,offset)
            if device in devices:raise AssertionError('Duplicate XI device')
            devices[device]=(use,attachment)
            if enabled:enabled_devices.add(device)
            offset+=12+((nlen+3)//4)*4
            if offset>len(data):raise AssertionError('Truncated XI device name')
            for _class in range(classes):
                if offset+4>len(data):raise AssertionError('Truncated XI class header')
                length=struct.unpack_from('<H',data,offset+2)[0]*4
                if length<8 or offset+length>len(data):raise AssertionError('Invalid XI class length')
                offset+=length
        if offset!=len(data):raise AssertionError('Trailing XI device data')
        if pointer not in enabled_devices or devices.get(pointer,(None,None))[0]!=1:raise AssertionError('Controlled pointer is not a master')
        paired=[d for d,(use,attachment) in devices.items() if use==2 and attachment==pointer]
        if len(paired)!=1 or paired[0] not in enabled_devices:raise AssertionError('Controlled pointer has no unique enabled keyboard')
        return {'window':self.window,'pointer':pointer,'keyboard':paired[0],'devices':devices,'enabled_devices':enabled_devices}
    def drain(self,timeout=.05):
        end=time.monotonic()+timeout
        while select.select([self.client.stream],[],[],0)[0]:
            event=self._packet(end)
            if event is not None:self.events.append(event)
        return list(self.events)
    def read_until(self,predicate,timeout):
        end=time.monotonic()+timeout
        while not predicate(self.events):
            event=self._packet(end)
            if event is not None:self.events.append(event)
        return list(self.events)
