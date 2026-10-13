"""Independent X11 wire witness for the disposable compositor output window."""
import struct
import select
import time
import test_security_identity as native


class GeometryPixelWitness(native.NativeClient):
    def __init__(self,display,cookie):
        self.reply_deadline=time.monotonic()+3
        super().__init__(display,cookie)
        vendor=struct.unpack_from(self.endian+'H',self.setup,16)[0]
        screen=32+(vendor+3)//4*4+self.setup[21]*8
        self.root_xid=struct.unpack_from(self.endian+'I',self.setup,screen)[0]
        self.window=None

    def exact(self,size):
        result=bytearray()
        while len(result)<size:
            remaining=self.reply_deadline-time.monotonic()
            if remaining<=0 or not select.select([self.stream],[],[],remaining)[0]:
                raise TimeoutError('Independent X witness absolute reply deadline')
            part=self.stream.recv(size-len(result))
            if not part:raise EOFError('Independent X witness closed')
            result.extend(part)
        return bytes(result)

    def request(self,opcode,payload=b'',minor=0):
        self.reply_deadline=time.monotonic()+3
        self.send(opcode,payload,minor)
        for _ in range(65):
            header=self.exact(32)
            if header[0]==34:
                if header[4] not in (0,1,2):raise AssertionError('Invalid MappingNotify')
                continue
            if header[0]!=1:raise AssertionError('Independent X witness error/event: '+str(header[:4]))
            if struct.unpack_from(self.endian+'H',header,2)[0]!=self.sequence%65536:
                raise AssertionError('Independent X witness sequence mismatch')
            size=struct.unpack_from(self.endian+'I',header,4)[0]*4
            if size>1048576:raise AssertionError('Independent X witness reply exceeds limit')
            return header,self.exact(size)
        raise AssertionError('Excessive MappingNotify events')

    def pack(self,format,*values):return struct.pack(self.endian+format,*values)

    def pixels(self):
        if self.window is None:
            name=b'Composite'
            head,_=self.request(98,self.pack('HH',len(name),0)+native.padded(name))
            if not head[8]:raise AssertionError('Composite extension is absent')
            head,_=self.request(head[9],self.pack('I',self.root_xid),7)
            overlay=struct.unpack_from(self.endian+'I',head,8)[0]
            head,children=self.request(15,self.pack('I',overlay))
            count=struct.unpack_from(self.endian+'H',head,16)[0]
            if count!=1 or len(children)!=4:raise AssertionError('Expected one compositor output child')
            self.window=struct.unpack(self.endian+'I',children)[0]
        head,_=self.request(3,self.pack('I',self.window))
        if head[26]!=2:return b''  # An unmapped/unviewable window presents no pixels.
        head,_=self.request(14,self.pack('I',self.window))
        width,height=struct.unpack_from(self.endian+'HH',head,16)
        sample_width,sample_height=min(width,512),min(height,384)
        _,pixels=self.request(73,self.pack('IhhHHI',self.window,
            (width-sample_width)//2,(height-sample_height)//2,sample_width,sample_height,0xffffffff),2)
        if len(pixels)!=sample_width*sample_height*4:raise AssertionError('Unexpected native pixel format')
        return pixels
