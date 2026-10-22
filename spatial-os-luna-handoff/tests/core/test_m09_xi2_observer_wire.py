"""Wire regressions for the privileged M09 witness; no production runtime imports.

Extract the actual observer and assertion methods to avoid starting native fixtures.
Run this source on qualification hosts. ROG assignments compile it without execution.
"""
import ast
from pathlib import Path
import select
import struct
import time
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).with_name('test_world_application_control.py')
TREE=ast.parse(SOURCE.read_text())
NAMESPACE={'select':select,'struct':struct,'time':time}
OBSERVER=next(node for node in TREE.body if isinstance(node,ast.ClassDef) and node.name=='NativeXI2Observer')
exec(compile(ast.Module(body=[OBSERVER],type_ignores=[]),str(SOURCE),'exec'),NAMESPACE)
NativeXI2Observer=NAMESPACE['NativeXI2Observer']
METHODS=next(node for node in TREE.body if isinstance(node,ast.ClassDef) and node.name=='WorldApplicationControlTests')
for name in ('m09_wait_xi2_key_presses','m09_assert_xi2_order','m09_pin_pointer'):
    node=next(node for node in METHODS.body if isinstance(node,ast.FunctionDef) and node.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),NAMESPACE)


def packet(kind=4,device=12,source=14,window=99,buttons=b'\x02\0\0\0',valuators=b'\x05\0\0\0',values=2):
    data=bytearray(80+len(buttons)+len(valuators)+values*8)
    struct.pack_into('<BBHIHH',data,0,35,131,1,(len(data)-32)//4,kind,device)
    struct.pack_into('<I',data,16,1)
    struct.pack_into('<I',data,24,window)
    struct.pack_into('<HHH',data,48,len(buttons)//4,len(valuators)//4,source)
    data[80:80+len(buttons)]=buttons
    data[80+len(buttons):80+len(buttons)+len(valuators)]=valuators
    # Conflicting bytes at the rejected variable source offset (60).
    struct.pack_into('<H',data,60,777)
    return bytes(data)


class Stream:
    def __init__(self,data=b''):
        self.data=data;self.closed=False;self.timeouts=[];self.fragment=100000
    def recv(self,size):
        size=min(size,self.fragment);result=self.data[:size];self.data=self.data[size:];return result
    def setblocking(self,value):pass
    def settimeout(self,value):self.timeouts.append(value)
    def close(self):self.closed=True


class Client:
    def __init__(self,version=(2,0),fail=None):
        self.stream=Stream();self.sequence=0;self.version=version;self.fail=fail;self.sent=[]
    def close(self):self.stream.close()
    def send(self,opcode,payload=b'',minor=0):
        self.sequence+=1;self.sent.append((opcode,minor,payload))
        if self.fail==minor:raise RuntimeError('setup failure')
        if minor==46:return
        head=bytearray(32);head[0]=1;struct.pack_into('<H',head,2,self.sequence)
        if opcode==98:head[8]=1;head[9]=131
        if minor==47:struct.pack_into('<HH',head,8,*self.version)
        self.stream.data+=head


def device_record(device,use,attachment,name,classes=()):
    # xXIDeviceInfo has a final byte of padding. Names precede the class records.
    header=struct.pack('<HHHHHBB',device,use,attachment,len(classes),len(name),1,0)
    return header+name+b'\0'*((-len(name))%4)+b''.join(classes)


def device_inventory():
    button=struct.pack('<HHHHII',1,4,14,1,0,0)
    key=struct.pack('<HHHHI',0,3,15,1,38)
    return b''.join((device_record(12,1,13,b'actor pointer',(button,)),
                     device_record(13,2,12,b'actor keyboard',(key,)),
                     device_record(14,3,12,b'actor pointer slave',(button,)),
                     device_record(15,4,13,b'actor keyboard slave',(key,))))


class DeviceReplyClient(Client):
    """Feed actual framed replies through the observer's request reader."""
    def __init__(self,inventory,count=4,pointer_set=True):
        super().__init__();self.inventory=inventory;self.count=count;self.pointer_set=pointer_set
    def send(self,opcode,payload=b'',minor=0):
        self.sequence+=1;self.sent.append((opcode,minor,payload))
        header=bytearray(32);header[0]=1;header[1]=minor
        struct.pack_into('<H',header,2,self.sequence)
        if minor==45:
            header[8]=self.pointer_set;struct.pack_into('<H',header,10,12);data=b''
        elif minor==48:
            struct.pack_into('<H',header,8,self.count);data=self.inventory
        else:raise AssertionError('Unexpected scripted request')
        if len(data)%4:raise AssertionError('Scripted reply is not aligned')
        struct.pack_into('<I',header,4,len(data)//4)
        self.stream.data+=bytes(header)+data


class XI2ObserverWireTests(unittest.TestCase):
    def setUp(self):
        self.select_patch=patch.object(select,'select',side_effect=lambda reads,*args:(reads,[],[]))
        self.select_patch.start();self.addCleanup(self.select_patch.stop)
    def observer(self,data):
        result=NativeXI2Observer.__new__(NativeXI2Observer)
        result.client=Client();result.client.stream.data=data
        result.window=99;result.opcode=131;result.events=[];result.closed=False
        return result
    def test_fixed_source_and_nonempty_masks_and_values(self):
        event=self.observer(packet())._packet(time.monotonic()+1)
        self.assertEqual((event['device'],event['source'],event['window']),(12,14,99))
    def test_truncated_fixed_header(self):
        data=bytearray(packet()[:56]);struct.pack_into('<I',data,4,6)
        with self.assertRaisesRegex(AssertionError,'device header'):self.observer(bytes(data))._packet(time.monotonic()+1)
    def test_truncated_masks(self):
        data=bytearray(packet());struct.pack_into('<H',data,48,200)
        with self.assertRaisesRegex(AssertionError,'masks'):self.observer(bytes(data))._packet(time.monotonic()+1)
    def test_missing_or_excess_valuator_values(self):
        for values in (1,3):
            with self.subTest(values=values),self.assertRaisesRegex(AssertionError,'valuator payload'):
                self.observer(packet(values=values))._packet(time.monotonic()+1)
    def test_wrong_window_and_reserved_ids(self):
        for kwargs in ({'window':100},{'source':1},{'device':0}):
            with self.subTest(kwargs=kwargs),self.assertRaises(AssertionError):
                self.observer(packet(**kwargs))._packet(time.monotonic()+1)
    def test_excessive_length_rejected_before_body_read(self):
        data=bytearray(packet()[:32]);struct.pack_into('<I',data,4,16385)
        with self.assertRaisesRegex(AssertionError,'Excessive'):self.observer(bytes(data))._packet(time.monotonic()+1)
    def test_version_negotiated_before_selection(self):
        client=Client();observer=NativeXI2Observer(client,99)
        self.assertEqual([minor for _,minor,_ in client.sent],[0,47,46,0])
        self.assertEqual(client.sent[1][2],struct.pack('<HH',2,0))
        observer.close();observer.close();self.assertTrue(client.stream.closed)
    def test_version_refusal_and_partial_setup_close_connection(self):
        for client in (Client(version=(1,5)),Client(version=(2,1)),Client(fail=47),Client(fail=46),Client(fail=0)):
            with self.subTest(client=client),self.assertRaises((AssertionError,RuntimeError)):
                NativeXI2Observer(client,99)
            self.assertTrue(client.stream.closed)
    def test_absolute_deadline_survives_fragmentation(self):
        observer=self.observer(b'abcdefgh');observer.client.stream.fragment=1
        with patch.object(time,'monotonic',side_effect=[0,.4,.8,1.2]):
            with self.assertRaisesRegex(AssertionError,'deadline'):observer._exact(8,1)
        self.assertEqual(observer.client.stream.data,b'defgh')
    def test_closed_stream_and_empty_select_are_bounded(self):
        with self.assertRaisesRegex(AssertionError,'closed'):self.observer(b'')._exact(1,time.monotonic()+1)
        with patch.object(select,'select',return_value=([],[],[])):
            with self.assertRaisesRegex(AssertionError,'deadline'):self.observer(b'x')._exact(1,time.monotonic()+1)
    def test_request_demultiplexes_events_and_checks_reply_sequence(self):
        observer=self.observer(packet());observer.client.sequence=0
        head,_=observer.request(43)
        self.assertEqual(len(observer.events),1);self.assertEqual(struct.unpack_from('<H',head,2)[0],1)
        observer=self.observer(bytes(32))
        with self.assertRaisesRegex(AssertionError,'error/event'):observer.request(43)
        bad=bytearray(32);bad[0]=1;struct.pack_into('<H',bad,2,77)
        observer=self.observer(bytes(bad))
        with self.assertRaisesRegex(AssertionError,'sequence mismatch'):observer.request(43)
    def test_key_wait_invokes_predicate_with_events(self):
        observer=self.observer(packet(kind=2,device=13,source=15))
        attribution={'window':99,'keyboard':13,'devices':{15:(4,13)},'enabled_devices':{15}}
        NAMESPACE['m09_wait_xi2_key_presses'](self,observer,{1},attribution)
        self.assertEqual(attribution['key_sources'],{1:15})
    def test_pointer_pin_rejects_conflicting_source_attachment(self):
        observer=self.observer(packet())
        observer.controlled_devices=lambda:{'window':99,'pointer':12,'devices':{14:(3,22)},'enabled_devices':{14}}
        with self.assertRaises(AssertionError):NAMESPACE['m09_pin_pointer'](self,observer,0)
    def test_controlled_devices_parses_framed_padded_inventory(self):
        observer=self.observer(b'');observer.client=DeviceReplyClient(device_inventory())
        attribution=observer.controlled_devices()
        self.assertEqual(attribution['pointer'],12);self.assertEqual(attribution['keyboard'],13)
        self.assertEqual(attribution['devices'],{12:(1,13),13:(2,12),14:(3,12),15:(4,13)})
        self.assertEqual(attribution['enabled_devices'],{12,13,14,15})
        self.assertEqual([(opcode,minor,payload) for opcode,minor,payload in observer.client.sent],
                         [(131,45,struct.pack('<I',99)),(131,48,struct.pack('<HH',0,0))])
        self.assertFalse(observer.client.stream.data)
    def test_controlled_devices_rejects_malformed_reply_at_intended_boundary(self):
        bad_name=struct.pack('<HHHHHBB',12,1,13,0,20,1,0)+b'abcd'
        bad_class=device_record(12,1,13,b'p',(struct.pack('<HHHH',1,1,14,1),))
        duplicate=device_record(12,1,13,b'p')*2
        no_pair=device_record(12,1,13,b'p')
        for data,count,pointer_set,reason in (
                (device_inventory(),4,False,'XIGetClientPointer failed'),
                (b'\0'*8,1,True,'Truncated XI device info'),
                (bad_name,1,True,'Truncated XI device name'),
                (bad_class,1,True,'Invalid XI class length'),
                (duplicate,2,True,'Duplicate XI device'),
                (device_inventory()+b'\0'*4,4,True,'Trailing XI device data'),
                (no_pair,1,True,'Controlled pointer has no unique enabled keyboard')):
            observer=self.observer(b'');observer.client=DeviceReplyClient(data,count,pointer_set)
            with self.subTest(reason=reason),self.assertRaisesRegex(AssertionError,reason):
                observer.controlled_devices()
    def test_equal_duplicate_release_after_peer_motion_is_rejected(self):
        def event(kind,pressed,device,source,detail):
            return dict(kind=kind,pressed=pressed,device=device,source=source,detail=detail,window=99)
        attribution=dict(boundary=0,window=99,pointer=12,keyboard=13,pointer_source=14,
                         key_sources={38:15,50:15},peer={'pointer':22,'keyboard':23,'pointer_source':24})
        releases=[event('key',False,13,15,50),event('key',False,13,15,38),event('button',False,12,14,1)]
        motion=event('motion',False,22,24,0);press=event('button',True,22,24,1)
        check=NAMESPACE['m09_assert_xi2_order']
        check(self,None,attribution,releases+[motion,press],{38,50})
        # Equal dictionaries must retain distinct wire positions.
        late=releases+[motion,dict(releases[1]),press]
        with self.assertRaisesRegex(AssertionError,'Old native release followed peer input'):
            check(self,None,attribution,late,{38,50})
    def test_order_requires_all_releases_and_exact_peer_identity(self):
        def event(kind,pressed,device,source,detail):
            return dict(kind=kind,pressed=pressed,device=device,source=source,detail=detail,window=99)
        attribution=dict(boundary=0,window=99,pointer=12,keyboard=13,pointer_source=14,
                         key_sources={38:15,50:15},peer={'pointer':22,'keyboard':23,'pointer_source':24})
        events=[event('key',False,13,15,38),event('key',False,13,15,50),
                event('button',False,12,14,1),event('button',True,22,24,1)]
        check=NAMESPACE['m09_assert_xi2_order']
        check(self,None,attribution,events,{38,50})
        check(self,None,attribution,events[:3]+[event('motion',False,22,24,0)]+events[3:],{38,50})
        for bad in (events[:2]+events[3:],events[1:],events[3:]+events[:3],
                    events[:3]+[event('button',True,32,34,1)],
                    events[:3]+[event('button',True,22,34,1)],
                    [event('motion',False,22,24,0)]+events):
            with self.subTest(bad=bad),self.assertRaises(AssertionError):check(self,None,attribution,bad,{38,50})


if __name__=='__main__':unittest.main()
