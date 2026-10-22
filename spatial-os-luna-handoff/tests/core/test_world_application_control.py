"""Authenticated application input through real actor and native paths."""
import json
import os
import struct
import select
import signal
import time
import unittest
from unittest.mock import patch
import core_pb2 as core
import input_pb2 as inp
import security_pb2 as security
import grants_pb2 as grant
import issuer_pb2 as issuer
import broker_pb2 as pb
import world_session_pb2 as session
import test_application_observation as observation
import test_application_access as access
from harness.oracles import pose
from harness.wire import frame, make_request
from test_world_identity import receive


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


class DirectBroker:
    def __init__(self, broker):self.stream=broker.stream;self.proc=broker.proc
    def close(self):pass


class WorldApplicationControlTests(observation.ApplicationObservationTests):
    def setup_world(self,*args,**kwargs):
        control_acquire_barrier=kwargs.pop('control_acquire_barrier',False)
        if control_acquire_barrier:return super().setup_world(*args,**kwargs)
        with patch.object(observation,'BrokerRelay',DirectBroker):
            return super().setup_world(*args,**kwargs)

    def probe(self,position=(0,0,-2),profile='development'):
        self.probe_number+=1;name='controlled-'+str(self.probe_number)
        argv=['/project/probe','--id',name]
        code=("import subprocess,time\nfrom pathlib import Path\n"
              "out=open('/state/data/probe.jsonl','w')\n"
              f"p=subprocess.Popen({argv!r},stdin=subprocess.PIPE,stdout=out,stderr=out)\n"
              "sent=0\n"
              "while not Path('/state/data/release').exists():\n"
              " if p.poll() is not None:raise SystemExit(p.returncode or 1)\n"
              " commands=Path('/state/data/commands')\n"
              " if commands.exists():\n"
              "  data=commands.read_bytes();end=data.rfind(b'\\n')+1\n"
              "  if end>sent:p.stdin.write(data[sent:end]);p.stdin.flush();sent=end\n"
              " time.sleep(.01)\n"
              "p.terminate();p.wait();out.close()\n")
        result=self.peer.act(process_launch=core.ProcessLaunchRequest(operation_id=name,profile=profile,
            argv=['/usr/bin/python3','-c',code]))
        self.assertTrue(result.HasField('action'),result)
        app=pb.ApplicationRef(workspace=b'one',application=result.action.process.process_id.encode(),server_id=self.server_id)
        def retain():
            log=self.data(app)/'probe.jsonl'
            if log.exists() and os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=access.Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-'+name+'.log')).write_bytes(log.read_bytes())
        self.addCleanup(retain)
        try:
            value=self.until(lambda:self.world_process.management('surface.find',probe_id=name),lambda r:r['surface'] is not None)['surface']
        except AssertionError as error:
            log=self.data(app)/'probe.jsonl'
            self.fail(str(error)+'\n'+(log.read_text() if log.exists() else 'No native probe output'))
        self.world_process.management('surface.place',id=value['id'],room=self.world_info['entry_room'],pose=pose(position),size=[1,1])
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='ready' and not r.get('xi2') for r in rows))
        return app,value

    def probe_events(self,app):
        log=self.data(app)/'probe.jsonl'
        if not log.exists():return []
        return [json.loads(line) for line in log.read_text().split('\n')[:-1] if line.startswith('{')]

    def probe_command(self,app,name,*args):
        self.sequence+=1
        with (self.data(app)/'commands').open('a') as output:
            output.write(' '.join(map(str,[name,self.sequence,*args]))+'\n')
        return self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='ack' and r.get('seq')==self.sequence for r in rows))

    def m09_create_survivor(self,app):
        rows=self.probe_command(app,'survivor',160,120)
        created=next(r for r in rows if r.get('event')=='survivor')
        value=self.until(lambda:self.world_process.management('surface.find',probe_id=created['probe_id']),
                         lambda result:result['surface'] is not None)['surface']
        position=(.8,0,-2)
        self.world_process.management('surface.place',id=value['id'],room=self.world_info['entry_room'],
                                      pose=pose(position),size=[.6,.6])
        self.assertTrue(self.world_process.management('surface.get',id=value['id'])['mapped'])
        return value,position

    def native_target_generation(self,controller,window):
        name=b'SPATIAL-INPUT'
        head,_=controller.request(98,struct.pack('<HH',len(name),0)+name)
        self.assertTrue(head[8],'SPATIAL-INPUT extension is unavailable')
        if not hasattr(controller,'spatial_input_opcode'):controller.spatial_input_opcode=head[9]
        controller.spatial_input_request_id=getattr(controller,'spatial_input_request_id',0)+1
        message=inp.Request(protocol_version=1,request_id=controller.spatial_input_request_id,
                            inspect=inp.Inspect(xid=window))
        payload=message.SerializeToString()
        head,body=controller.request(controller.spatial_input_opcode,struct.pack('<I',len(payload))+payload)
        size=struct.unpack_from('<I',head,8)[0]
        reply=inp.Reply.FromString(body[:size])
        self.assertEqual(reply.protocol_version,1,reply)
        self.assertEqual(reply.request_id,message.request_id,reply)
        self.assertEqual(reply.code,inp.OK,reply)
        self.assertTrue(reply.HasField('target'),reply)
        return reply.target.generation

    def native_device(self,app):
        # Independent supervisor observation. Confined clients cannot enumerate XI devices.
        window=next(r['xid'] for r in self.probe_events(app) if r.get('event')=='ready')
        root=self.native.supervisor;name=b'XInputExtension'
        head,_=root.request(98,struct.pack('<HH',len(name),0)+name)
        self.assertTrue(head[8])
        head,_=root.request(head[9],struct.pack('<I',window),minor=45)
        self.assertTrue(head[8])
        return struct.unpack_from('<H',head,10)[0]

    def control_action(self,peer,**action):
        result=peer.act(**action);self.assertTrue(result.HasField('action'),result);return result.action

    def actor_aim(self,peer=None,x=0,y=0,z=-1):
        return self.control_action(peer or self.viewer,aim=core.ActorAimRequest(direction=core.Vec3(x=x,y=y,z=z))).aim

    def actor_pointer(self,pressed,peer=None):
        return self.control_action(peer or self.viewer,pointer=core.ActorPointerRequest(button=1,pressed=pressed))

    def send_key_event(self,name,pressed,peer=None):
        return self.control_action(peer or self.viewer,key=core.ActorKeyRequest(keysym=name,pressed=pressed))

    def grant_control(self,app,readonly=False):
        self.grant_viewer(access.READ if readonly else [grant.WORKSPACE_ACCESS,*access.workspace.PROJECT])
        observe=self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        control=self.grant_viewer([grant.APPLICATION_CONTROL],app)
        return observe,control

    def begin_control(self,profile='development',readonly=False):
        self.setup_world();app,surface=self.probe(profile=profile)
        observe,control=self.grant_control(app,readonly);self.actor_aim();self.actor_pointer(True)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='button' and r.get('pressed') for r in rows))
        return app,surface,observe,control

    def actor_state(self,peer=None):return (peer or self.viewer).call(inspect=session.WorldSessionInspect()).state

    def assert_released(self,app,inspect=True):
        def released(rows):
            return any(r.get('event')=='button' and not r.get('pressed') for r in rows) and any(r.get('event')=='key' and not r.get('pressed') for r in rows)
        self.until(lambda:self.probe_events(app),released)
        if inspect:
            value=self.actor_state();self.assertFalse(value.held_keys);self.assertFalse(value.held_buttons);self.assertFalse(value.HasField('focus'))

    def m09_hold_native_input(self,app):
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='focus' and r.get('focused') for r in rows))
        before=len(self.probe_events(app))
        self.send_key_event('Shift_L',True);self.send_key_event('a',True)
        self.actor_aim(x=.05,z=-1)
        rows=self.until(lambda:self.probe_events(app),lambda rows:
            any(r.get('event')=='key' and r.get('pressed') and r.get('keysym') in (ord('a'),ord('A')) for r in rows[before:]) and
            any(r.get('event')=='motion' for r in rows[before:]))
        presses=[r for r in rows[before:] if r.get('event')=='key' and r.get('pressed')]
        codes={r['keycode'] for r in presses if r.get('keysym') in (ord('a'),ord('A'),0xffe1)}
        self.assertEqual(len(codes),2,presses)
        self.assertTrue(any(r.get('event')=='button' and r.get('pressed') and r.get('button')==1 for r in rows[:before]))
        return before,codes

    def m09_assert_native_release(self,app,before,codes,inspect=True):
        def balanced(rows):
            tail=rows[before:]
            released={r.get('keycode') for r in tail if r.get('event')=='key' and not r.get('pressed')}
            return (codes <= released and
                any(r.get('event')=='button' and r.get('button')==1 and not r.get('pressed') for r in tail) and
                any(r.get('event')=='focus' and not r.get('focused') for r in tail))
        rows=self.until(lambda:self.probe_events(app),balanced)
        tail=rows[before:]
        key_releases={r.get('keycode') for r in tail if r.get('event')=='key' and not r.get('pressed')}
        self.assertTrue(codes <= key_releases,tail)
        self.assertTrue(any(r.get('event')=='button' and r.get('button')==1 and not r.get('pressed') for r in tail),tail)
        self.assertTrue(any(r.get('event')=='focus' and not r.get('focused') for r in tail),tail)
        if inspect:
            state=self.actor_state()
            self.assertFalse(state.held_keys);self.assertFalse(state.held_buttons);self.assertFalse(state.HasField('focus'))
        released_indexes=[index for index,row in enumerate(rows[before:],before)
            if (row.get('event')=='key' and not row.get('pressed') and row.get('keycode') in codes) or
               (row.get('event')=='button' and row.get('button')==1 and not row.get('pressed')) or
               (row.get('event')=='focus' and not row.get('focused'))]
        self.assertTrue(released_indexes,tail)
        return max(released_indexes)

    def m09_start_xi2_observer(self,app):
        ready=next(r for r in self.probe_events(app) if r.get('event')=='ready')
        self.assertGreater(ready['xid'],0)
        client=self.native.connect(self.native.cookie)
        self.addCleanup(client.close) # Own cleanup before extension lookup or negotiation.
        observer=NativeXI2Observer(client,ready['xid'])
        self.addCleanup(observer.close)
        return observer

    def m09_pin_pointer(self,observer,boundary):
        attribution=observer.controlled_devices() # Query only after successful control acquisition.
        events=observer.read_until(lambda es:any(e['kind']=='button' and e['pressed'] and e['detail']==1 for e in es[boundary:]),3.0)
        presses=[e for e in events[boundary:] if e['kind']=='button' and e['pressed'] and e['detail']==1]
        self.assertTrue(presses)
        for event in presses:
            self.assertEqual(event['device'],attribution['pointer'],presses)
            self.assertEqual(attribution['devices'].get(event['source']),(3,attribution['pointer']),presses)
            self.assertIn(event['source'],attribution['enabled_devices'])
        sources={e['source'] for e in presses};self.assertEqual(len(sources),1,presses)
        attribution['pointer_source']=sources.pop()
        return attribution

    def m09_begin_observed_control(self):
        app,surface,observe,control=self.begin_control()
        # The independent C application proves actual keycodes and core delivery first.
        before,codes=self.m09_hold_native_input(app)
        self.control_action(self.viewer,unfocus=core.ActorStopRequest())
        self.m09_assert_native_release(app,before,codes)
        self.probe_command(app,'sync')
        observer=self.m09_start_xi2_observer(app)
        self.addCleanup(self.m09_release_probe,app)
        # Establish the actual peer pair/source under control before the ordered revocation phase.
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer)
        peer_attribution=self.m09_pin_pointer(observer,0)
        self.actor_pointer(False,self.peer)
        self.control_action(self.peer,unfocus=core.ActorStopRequest())
        observer.read_until(lambda es:any(e['kind']=='button' and not e['pressed'] and e['device']==peer_attribution['pointer'] for e in es),3.0)
        boundary=len(observer.events)
        self.actor_aim();self.actor_pointer(True)
        attribution=self.m09_pin_pointer(observer,boundary)
        self.assertNotEqual(attribution['pointer'],peer_attribution['pointer'])
        self.assertNotEqual(attribution['pointer_source'],peer_attribution['pointer_source'])
        attribution['peer']=peer_attribution
        attribution['expected_keycodes']=codes
        # XI2-first delivery intentionally makes the privileged observer the sole recipient.
        return app,surface,observe,control,observer,attribution
    def m09_hold_observed_input(self,observer,attribution):
        boundary=len(observer.events);codes=attribution['expected_keycodes']
        self.send_key_event('Shift_L',True);self.send_key_event('a',True)
        self.actor_aim(x=.05,z=-1)
        events=observer.read_until(lambda rows:
            {r['detail'] for r in rows[boundary:] if r['kind']=='key' and r['pressed']}>=codes and
            any(r['kind']=='motion' and r['device']==attribution['pointer'] and
                r['source']==attribution['pointer_source'] and r['window']==attribution['window']
                for r in rows[boundary:]),3.0)
        self.assertTrue(any(r['kind']=='button' and r['pressed'] and
            r['device']==attribution['pointer'] for r in events[:boundary]),events)
        self.m09_wait_xi2_key_presses(observer,codes,attribution)
        return codes

    def m09_observed_handoff_then_core_delivery(self,app,observer,attribution,codes):
        # Submit before waiting for old releases. A BUSY reply proves no acquisition.
        result=self.peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        if result.HasField('error'):
            self.assertEqual(result.error.code,core.BUSY,result)
            observer.read_until(lambda rows:
                {r['detail'] for r in rows[attribution['boundary']:] if r['kind']=='key' and not r['pressed']}>=codes and
                any(r['kind']=='button' and not r['pressed'] for r in rows[attribution['boundary']:]),3.0)
            result=self.peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        self.assertTrue(result.HasField('action'),result)
        events=observer.read_until(lambda rows:any(r['kind']=='button' and r['pressed'] and
            r['device']==attribution['peer']['pointer'] for r in rows[attribution['boundary']:]),3.0)
        self.m09_assert_xi2_order(observer,attribution,events,codes)
        self.actor_pointer(False,self.peer)
        self.control_action(self.peer,unfocus=core.ActorStopRequest())
        observer.read_until(lambda rows:any(r['kind']=='button' and not r['pressed'] and r['detail']==1 and
            r['device']==attribution['peer']['pointer'] and r['source']==attribution['peer']['pointer_source'] and
            r['window']==attribution['window'] for r in rows[attribution['boundary']:]),3.0)
        observer.deselect();observer.close()
        # The privileged recipient is gone. Prove fresh input reaches the same C application.
        boundary=len(self.probe_events(app))
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer);self.actor_pointer(False,self.peer)
        self.send_key_event('b',True,self.peer);self.send_key_event('b',False,self.peer)
        self.probe_command(app,'sync')
        rows=self.probe_events(app)[boundary:]
        self.assertTrue(any(r.get('event')=='button' and r.get('pressed') for r in rows),rows)
        self.assertTrue(any(r.get('event')=='key' and r.get('pressed') and r.get('text_hex')=='62' for r in rows),rows)

    def m09_wait_xi2_key_presses(self,observer,codes,attribution):
        events=observer.read_until(lambda es:
            {e['detail'] for e in es if e['kind']=='key' and e['pressed']}>=codes,3.0)
        presses=[e for e in events if e['kind']=='key' and e['pressed'] and e['detail'] in codes]
        self.assertEqual({e['detail'] for e in presses},codes,presses)
        sources={}
        for event in presses:
            self.assertEqual(event['device'],attribution['keyboard'])
            self.assertEqual(event['window'],attribution['window'])
            self.assertEqual(attribution['devices'].get(event['source']),(4,attribution['keyboard']),presses)
            self.assertIn(event['source'],attribution['enabled_devices'])
            sources.setdefault(event['detail'],set()).add(event['source'])
        self.assertTrue(all(len(v)==1 and next(iter(v))>0 for v in sources.values()),presses)
        attribution['key_sources']={key:next(iter(value)) for key,value in sources.items()}
        attribution['boundary']=len(events)
    def m09_assert_xi2_order(self,observer,attribution,events,codes):
        tail=events[attribution['boundary']:]
        releases=[(index,event) for index,event in enumerate(tail)
                  if (event['kind']=='key' and not event['pressed'] and event['detail'] in codes) or
                     (event['kind']=='button' and not event['pressed'] and event['detail']==1)]
        peer=[(index,event) for index,event in enumerate(tail)
              if event['device'] in (attribution['peer']['pointer'],attribution['peer']['keyboard'])]
        self.assertTrue(releases,tail);self.assertTrue(peer,tail);first_peer=peer[0][0]
        self.assertTrue(any(event['kind']=='button' and event['pressed'] and event['detail']==1 for _,event in peer),tail)
        for _,event in peer:
            # This phase submits only a pointer press. Include preceding native motion in the order check.
            self.assertIn(event['kind'],('button','motion'),tail)
            self.assertEqual(event['device'],attribution['peer']['pointer'])
            self.assertEqual(event['source'],attribution['peer']['pointer_source'])
        self.assertTrue(any(event['kind']=='button' for _,event in releases),tail)
        self.assertTrue(all(index<first_peer for index,_ in releases),
                        'Old native release followed peer input: '+repr(tail))
        observed={}
        for _,event in releases:
            self.assertEqual(event['window'],attribution['window'])
            if event['kind']=='key':
                self.assertEqual(event['device'],attribution['keyboard'])
                observed.setdefault(event['detail'],set()).add(event['source'])
            else:
                self.assertEqual(event['device'],attribution['pointer'])
                self.assertEqual(event['source'],attribution['pointer_source'])
        self.assertEqual(set(observed),codes,tail)
        for key,sources in observed.items():self.assertEqual(sources,{attribution['key_sources'][key]},tail)
        self.assertTrue(all(event['window']==attribution['window'] for _,event in peer),tail)

    def m09_peer_first_native_event(self,app,peer,before,codes,x=0,z=-1,aim=True,observer=None,attribution=None):
        if aim:self.actor_aim(peer,x=x,z=z)
        result=peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        if result.HasField('error'):
            self.assertEqual(result.error.code,core.BUSY,result)
            # BUSY proves this request did not acquire the surface group.
            self.m09_assert_native_release(app,before,codes,inspect=False)
            result=peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        self.assertTrue(result.HasField('action'),result)
        rows=self.until(lambda:self.probe_events(app),lambda rows:
            any(r.get('event')=='button' and r.get('button')==1 and r.get('pressed') for r in rows[before:]))
        peer_press=next(index for index,row in enumerate(rows[before:],before)
            if row.get('event')=='button' and row.get('button')==1 and row.get('pressed'))
        releases=[index for index,row in enumerate(rows[before:],before)
            if (row.get('event')=='key' and not row.get('pressed') and row.get('keycode') in codes) or
               (row.get('event')=='button' and row.get('button')==1 and not row.get('pressed')) or
               (row.get('event')=='focus' and not row.get('focused'))]
        self.assertEqual(len({rows[index].get('keycode') for index in releases
            if rows[index].get('event')=='key'}),len(codes),rows[before:])
        self.assertTrue(any(rows[index].get('event')=='button' for index in releases),rows[before:])
        self.assertTrue(any(rows[index].get('event')=='focus' for index in releases),rows[before:])
        self.assertTrue(all(index<peer_press for index in releases),rows[before:])
        if observer is not None:
            observed=observer.read_until(lambda es:any(e['kind']=='button' and e['pressed'] and e['detail']==1 for e in es[attribution['boundary']:]),timeout=3.0)
            self.m09_assert_xi2_order(observer,attribution,observed,codes)
        self.send_key_event('b',True,peer);self.send_key_event('b',False,peer);self.actor_pointer(False,peer)
        rows=self.until(lambda:self.probe_events(app),lambda rows:
            any(r.get('event')=='key' and r.get('pressed') and r.get('text_hex')=='62' for r in rows[before:]))
        self.assertTrue(any(r.get('event')=='button' and r.get('pressed') and r.get('button')==1 for r in rows[before:]))
        return rows

    def m09_release_probe(self,app):
        (self.data(app)/'release').touch()
        result=self.world_process.management('process.wait',id=app.application.decode(),timeout_seconds=5)
        self.assertEqual(result['exit_code'],0)

    def m09_run_balanced_transfer(self,trigger):
        app,surface,observe,control=self.begin_control();before,codes=self.m09_hold_native_input(app)
        self.addCleanup(self.m09_release_probe,app)
        log_boundary=len(self.probe_events(app))
        if trigger=='voluntary':self.control_action(self.viewer,unfocus=core.ActorStopRequest())
        elif trigger=='disconnect':self.viewer.close()
        elif trigger=='revoke':self.revoke_grant(self.peer,control)
        elif trigger=='observation':self.revoke_grant(self.peer,observe)
        elif trigger=='actor':self.revoke_viewer_identity(issuer.ACTOR_TARGET)
        elif trigger in ('unmap','destroy'):
            self.probe_command(app,trigger)
            self.until(lambda:self.probe_events(app),lambda rows:
                any(r.get('event')==trigger and r.get('xid')==next(r['xid'] for r in rows if r.get('event')=='ready')
                    for r in rows[before:]))
        else:raise AssertionError('unknown M09 transfer trigger')
        if trigger in ('disconnect','revoke','observation','actor'):
            # These paths must make progress without a harness wait for release.
            self.m09_peer_first_native_event(app,self.peer,log_boundary,codes)
            self.m09_assert_native_release(app,before,codes,inspect=False)
        elif trigger=='destroy':
            self.m09_assert_native_release(app,before,codes,inspect=False)
            self.probe_command(app,'sync')
            other,other_surface=self.probe((.6,0,-2))
            self.addCleanup(self.m09_release_probe,other)
            self.actor_aim(self.peer,x=.6,z=-2)
            self.actor_pointer(True,self.peer)
            self.until(lambda:self.probe_events(other),lambda rows:
                any(r.get('event')=='button' and r.get('button')==1 and r.get('pressed') for r in rows))
        else:
            if trigger=='unmap':self.probe_command(app,'map')
            self.m09_peer_first_native_event(app,self.peer,log_boundary,codes)
            self.m09_assert_native_release(app,before,codes)

    def test_focused_application_exit_releases_control_through_direct_broker(self):
        app,surface,observe,control=self.begin_control()
        self.send_key_event('a',True)
        self.until(lambda:self.probe_events(app),lambda rows:any(
            r.get('event')=='key' and r.get('pressed') for r in rows))
        # Default setup uses DirectBroker. Keep input held until the real client exits.
        self.assertIsInstance(self.relay,DirectBroker)
        self.m09_release_probe(app)
        self.until(lambda:self.actor_state(),lambda value:
            not value.held_keys and not value.held_buttons and not value.HasField('focus'))
        result=self.world_process.management('surface.find',probe_id='controlled-1')
        self.assertIsNone(result['surface'])

    def test_two_idle_native_seats_reacquire_fresh_application_control(self):
        self.setup_world(control_acquire_barrier=True);app,surface=self.probe();self.grant_control(app)
        self.addCleanup(self.m09_release_probe,app)
        ordered=sorted(((self.actor_state(self.viewer).id,self.viewer),
                        (self.actor_state(self.peer).id,self.peer)),key=lambda item:item[0])
        pointers=[]
        for actor_id,peer in ordered:
            boundary=len(self.probe_events(app))
            self.actor_aim(peer);self.actor_pointer(True,peer)
            self.probe_command(app,'sync')
            rows=self.probe_events(app)[boundary:]
            self.assertTrue(any(r.get('event')=='button' and r.get('pressed') and
                r.get('button')==1 and r.get('xid')==surface['xid'] for r in rows),rows)
            pointers.append(self.native_device(app))
            self.actor_pointer(False,peer)
            self.control_action(peer,unfocus=core.ActorStopRequest())
            state=self.actor_state(peer)
            self.assertFalse(state.held_keys);self.assertFalse(state.held_buttons);self.assertFalse(state.HasField('focus'))
        self.assertNotEqual(*pointers)
        count=self.native_device_count();peer=ordered[0][1]
        self.actor_aim(peer);self.probe_command(app,'sync');boundary=len(self.probe_events(app))
        peer.sequence+=1;peer.action_sequence+=1
        request=session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,
            action=core.ActorActionRequest(seq=peer.action_sequence,
                pointer=core.ActorPointerRequest(button=1,pressed=True)))
        self.relay.hold_next_control_acquire()
        try:
            peer.stream.sendall(frame(request))
            self.assertTrue(self.relay.control_acquire_reply_seen.wait(5))
            record=self.relay.control_acquire_record()
            self.assertTrue(record.get('correlated_success'),record)
            self.assertEqual(record['application'],app.SerializeToString(),record)
            identity=self.second if peer is self.viewer else self.identity
            self.assertEqual(record['principal'],self.principal(identity).SerializeToString(),record)
            # BrokerClient::wait runs the production scheduler before sending acquisition.
            # Both seats exist and are idle throughout that callback, without XI2 selection.
        finally:self.relay.release_control_acquire()
        reply=receive(peer.stream.recv,peer.stream,session.WorldSessionReply)
        self.assertEqual(reply.request_id,request.request_id)
        self.assertTrue(reply.HasField('action'),reply)
        self.probe_command(app,'sync');rows=self.probe_events(app)[boundary:]
        self.assertTrue(any(r.get('event')=='button' and r.get('pressed') for r in rows),rows)
        self.assertEqual(self.native_device(app),pointers[0])
        self.assertEqual(self.native_device_count(),count)
        self.actor_pointer(False,peer)

    def test_target_generation_replacement_during_broker_authorization(self):
        self.setup_world(control_acquire_barrier=True);app,surface=self.probe();self.grant_control(app)
        self.actor_aim();self.actor_pointer(True)
        ready=self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='button' and
            r.get('button')==1 and r.get('pressed') for r in rows))
        self.assertTrue(any(r.get('event')=='button' and r.get('button')==1 and r.get('pressed')
                            for r in ready),'Authorized control did not reach the native application')
        self.actor_pointer(False);self.probe_command(app,'sync')
        baseline=self.probe_events(app)
        baseline_presses=[r for r in baseline if r.get('event')=='button' and r.get('button')==1 and r.get('pressed')]
        self.assertTrue(baseline_presses)
        xid=next(r['xid'] for r in baseline if r.get('event')=='ready')
        controller=self.native.connect(self.native.supervisor.issue(security.COMPOSITOR,b'',b'').cookie)
        before_generation=self.native_target_generation(controller,xid)
        logical_generation=self.world_process.management('surface.get',id=surface['id'])['generation']

        self.viewer.sequence+=1;self.viewer.action_sequence+=1
        request=session.WorldSessionRequest(protocol_version=1,request_id=self.viewer.sequence,
            action=core.ActorActionRequest(seq=self.viewer.action_sequence,
                pointer=core.ActorPointerRequest(button=1,pressed=True)))
        self.relay.hold_next_control_acquire()
        try:
            self.viewer.stream.sendall(frame(request))
            self.assertTrue(self.relay.control_acquire_reply_seen.wait(5),
                            'Broker did not return a control-acquire response')
            record=self.relay.control_acquire_record()
            self.assertTrue(record.get('correlated_success'),record)
            self.assertEqual(record['request_protocol'],1,record)
            self.assertEqual(record['reply_protocol'],1,record)
            self.assertEqual(record['request_id'],record['reply_id'],record)
            self.assertEqual(record['outcome'],'control_lease',record)
            self.assertEqual(record['application'],app.SerializeToString(),record)
            self.assertEqual(record['principal'],self.principal(self.second).SerializeToString(),record)
            self.assertEqual(len(record['lease_id']),32,record)
            self.assertTrue(self.relay.control_acquire_entered.is_set(),record)

            # The actor request blocks worldd's single serve loop. Use native X only here.
            self.probe_command(app,'unmap');self.probe_command(app,'map')
            replaced_generation=self.native_target_generation(controller,xid)
            self.assertGreater(replaced_generation,before_generation,
                               'Native remap did not advance the server-owned target generation')
        finally:
            self.relay.release_control_acquire()

        reply=receive(self.viewer.stream.recv,self.viewer.stream,session.WorldSessionReply)
        self.assertEqual(reply.request_id,request.request_id)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(reply.error.code,core.STALE_TARGET,reply)
        world_surface=self.world_process.management('surface.get',id=surface['id'])
        self.assertTrue(world_surface['mapped'])
        # Compositor handles and native server generations are independent domains.
        self.assertEqual(world_surface['xid'],xid)
        self.assertGreater(world_surface['generation'],logical_generation,
                           'worldd retained the old logical surface generation after remap')

        # xprobe sync performs XSync, drains pending native events, then acknowledges.
        self.probe_command(app,'sync')
        drained=self.probe_events(app)
        drained_presses=[r for r in drained if r.get('event')=='button' and r.get('button')==1 and r.get('pressed')]
        self.assertEqual(len(drained_presses),len(baseline_presses),
                         'Stale input reached the replacement before the native drain boundary')
        boundary=len(drained)
        fresh_aim=self.actor_aim()
        self.assertEqual(fresh_aim.hit.id,surface['id'])
        self.assertEqual(fresh_aim.hit.generation,world_surface['generation'])
        self.actor_pointer(True);self.probe_command(app,'sync')
        fresh=self.probe_events(app)[boundary:]
        fresh_presses=[r for r in fresh if r.get('event')=='button' and r.get('button')==1 and
                       r.get('pressed') and r.get('xid')==xid]
        self.assertTrue(fresh_presses,'Fresh authorized input did not reach the remapped native target')
        self.actor_pointer(False);self.probe_command(app,'sync')


    def test_authorized_native_input_without_workspace_selection(self):
        app,surface,observe,control=self.begin_control();self.assertFalse(self.actor_state().workspace)
        self.actor_pointer(False);self.send_key_event('a',True);self.send_key_event('a',False)
        rows=self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='key' and r.get('text_hex')=='61' for r in rows))
        self.assertGreater(self.native_device(app),0)

    def test_observation_does_not_grant_control(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.WORKSPACE_ACCESS]);self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        self.actor_aim();self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.FORBIDDEN)
        self.assertFalse(any(r.get('event')=='button' for r in self.probe_events(app)))

    def test_readonly_actor_cannot_control_writable_application(self):
        self.setup_world();app,surface=self.probe();self.grant_control(app,readonly=True);self.actor_aim()
        self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.FORBIDDEN)
        self.assertFalse(any(r.get('event')=='button' for r in self.probe_events(app)))

    def test_readonly_actor_can_control_readonly_application(self):
        app,surface,observe,control=self.begin_control(profile='read-only',readonly=True)
        self.actor_pointer(False);self.send_key_event('a',True);self.send_key_event('a',False)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='61' for r in rows))

    def test_one_application_has_one_actor_controller(self):
        app,surface,observe,control=self.begin_control();self.actor_aim(self.peer)
        self.denied(self.peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.BUSY)
        self.assertTrue(self.actor_state().held_buttons);self.assertFalse(self.actor_state(self.peer).held_buttons)

    def test_unfocus_releases_native_input_before_handoff(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('Shift_L',True)
        self.control_action(self.viewer,unfocus=core.ActorStopRequest());self.assert_released(app)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer);self.actor_pointer(False,self.peer);self.send_key_event('a',True,self.peer);self.send_key_event('a',False,self.peer)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='61' for r in rows))

    def test_idle_control_revocation_releases_input_and_preserves_execution(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_grant(self.peer,control);self.assert_released(app)
        self.assertTrue(self.world_process.management('surface.get',id=surface['id'])['mapped'])
        self.assertIn(surface['id'],self.visible())
        self.actor_aim();self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.FORBIDDEN)

    def test_observation_revocation_cancels_control(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_grant(self.peer,observe);self.assert_released(app)
        self.assertNotIn(surface['id'],self.visible());self.assertIn(surface['id'],self.visible(self.peer))

    def test_independent_control_grant_survives_other_revocation(self):
        app,surface,observe,control=self.begin_control();self.grant_viewer([grant.APPLICATION_CONTROL],app)
        self.revoke_grant(self.peer,control);self.send_key_event('a',True);self.send_key_event('a',False)
        self.assertTrue(self.actor_state().held_buttons);self.actor_pointer(False)

    def test_stop_releases_application_reservation(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.control_action(self.viewer,stop=core.ActorStopRequest());self.assert_released(app)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer);self.actor_pointer(False,self.peer)

    def test_scroll_uses_real_native_events(self):
        self.setup_world();app,surface=self.probe();self.grant_control(app);self.actor_aim()
        self.control_action(self.viewer,scroll=core.ActorScrollRequest(steps_y=2))
        rows=self.until(lambda:self.probe_events(app),lambda rows:sum(r.get('event')=='button' and r.get('button')==4 and not r.get('pressed') for r in rows)==2)
        self.assertEqual(sum(r.get('event')=='button' and r.get('button')==4 and r.get('pressed') for r in rows),2)
        self.assertFalse(self.actor_state().held_buttons)

    def test_independent_applications_allow_simultaneous_actors(self):
        self.setup_world();app,left=self.probe((-.6,0,-2));other,right=self.probe((.6,0,-2));self.grant_control(app)
        self.actor_aim(x=-.6,z=-2);self.actor_pointer(True)
        self.actor_aim(self.peer,x=.6,z=-2);self.actor_pointer(True,self.peer)
        self.assertEqual(self.actor_state().focus,left['id']);self.assertEqual(self.actor_state(self.peer).focus,right['id'])
        self.send_key_event('a',True);self.send_key_event('b',True,self.peer)
        a=self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='key' and r.get('pressed') for r in rows))
        b=self.until(lambda:self.probe_events(other),lambda rows:any(r.get('event')=='key' and r.get('pressed') for r in rows))
        self.assertNotEqual(self.native_device(app),self.native_device(other))
        self.assertFalse(any(r.get('text_hex')=='62' for r in a))
        self.assertFalse(any(r.get('text_hex')=='61' for r in b))

    def test_session_close_releases_before_handoff(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('Shift_L',True)
        self.assertTrue(self.viewer.call(close=session.WorldSessionClose()).HasField('closed'))
        self.assert_released(app,inspect=False)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer);self.actor_pointer(False,self.peer)
        self.send_key_event('a',True,self.peer)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='61' for r in rows))

    def test_controller_disconnect_releases_before_handoff(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.viewer.close();self.assert_released(app,inspect=False)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer)
        self.assertTrue(self.actor_state(self.peer).held_buttons)

    def test_restored_grant_does_not_restore_held_input(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_grant(self.peer,control);self.assert_released(app)
        self.grant_viewer([grant.APPLICATION_CONTROL],app)
        self.denied(self.viewer.act(key=core.ActorKeyRequest(keysym='b',pressed=True)),core.NO_FOCUS)
        self.actor_aim();self.actor_pointer(True);self.actor_pointer(False)
        self.send_key_event('b',True);self.send_key_event('b',False)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='62' for r in rows))

    def test_popup_uses_same_application_reservation(self):
        app,surface,observe,control=self.begin_control();self.actor_pointer(False)
        rows=self.probe_command(app,'popup',100,80)
        xid=next(r['xid'] for r in rows if r.get('event')=='popup')
        child=self.until(lambda:self.world_process.management('surface.children',id=surface['id']),
                         lambda result:any(r['xid']==xid for r in result['surfaces']))
        popup=next(r for r in child['surfaces'] if r['xid']==xid)
        point=popup['pose']['p']
        self.assertEqual(self.actor_aim(x=point[0],y=point[1],z=point[2]).hit.id,popup['id']);self.actor_pointer(True);self.actor_pointer(False);self.send_key_event('a',True)
        self.assertEqual(self.actor_state().focus,popup['id'])
        self.actor_aim(self.peer);self.denied(self.peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.BUSY)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='61' and r.get('xid')==xid for r in rows))

    def test_resized_aim_rejects_stale_pixels(self):
        self.setup_world();app,surface=self.probe();self.grant_control(app);self.actor_aim()
        self.probe_command(app,'resize',640,480)
        self.until(lambda:self.world_process.management('surface.get',id=surface['id']),lambda r:r['pixels']==[640,480])
        self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.STALE_TARGET)
        self.assertFalse(any(r.get('event')=='button' for r in self.probe_events(app)))

    def test_unmapped_capture_releases_reservation(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.probe_command(app,'unmap');self.until(lambda:self.actor_state(),lambda s:not s.held_keys and not s.held_buttons and not s.HasField('focus'))
        self.probe_command(app,'map');self.actor_aim(self.peer);self.actor_pointer(True,self.peer)
        self.assertTrue(self.actor_state(self.peer).held_buttons)

    def test_m09_voluntary_release_balances_input_before_peer_event(self):
        self.m09_run_balanced_transfer('voluntary')

    def test_m09_disconnect_balances_input_before_peer_event(self):
        self.m09_run_balanced_transfer('disconnect')

    def test_m09_authority_revocation_balances_input_before_peer_event(self):
        self.m09_run_balanced_transfer('revoke')

    def test_m09_unmap_balances_input_before_peer_event(self):
        self.m09_run_balanced_transfer('unmap')

    def test_m09_destroy_releases_input_and_preserves_probe(self):
        self.m09_run_balanced_transfer('destroy')

    def test_m09_destroy_reacquires_same_application_survivor(self):
        app,main,observe,control=self.begin_control()
        self.addCleanup(self.m09_release_probe,app)
        survivor,position=self.m09_create_survivor(app)
        hit=self.actor_aim(self.peer,x=position[0],y=position[1],z=position[2]).hit
        self.assertEqual(hit.id,survivor['id'])
        self.denied(self.peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.BUSY)
        self.m09_hold_native_input(app)
        log_boundary=len(self.probe_events(app))
        self.probe_command(app,'destroy')
        self.until(lambda:self.probe_events(app),
                   lambda rows:any(r.get('event')=='destroy' and r.get('xid')==main['xid'] for r in rows))
        # Reuse the peer's pre-destroy aim. The pointer request itself reaches cancellation first.
        result=self.peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        if result.HasField('error'):
            self.assertEqual(result.error.code,core.BUSY,result)
            # This logical-state wait schedules the retry. It proves no native device-state invariant.
            self.until(lambda:self.actor_state(),lambda value:
                not value.held_keys and not value.held_buttons and not value.HasField('focus'))
            result=self.peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        self.assertTrue(result.HasField('action'),result)
        rows=self.until(lambda:self.probe_events(app),lambda events:any(
            r.get('event')=='button' and r.get('pressed') and r.get('xid')==survivor['xid']
            for r in events[log_boundary:]))
        # Reacquisition and process continuity only. Destroyed-window releases can be absent.
        # Neither this peer press nor logical actor state proves old native devices were released.
        self.send_key_event('b',True,self.peer);self.send_key_event('b',False,self.peer)
        self.actor_pointer(False,self.peer)
        rows=self.until(lambda:self.probe_events(app),lambda events:any(
            r.get('event')=='key' and r.get('pressed') and r.get('text_hex')=='62' and r.get('xid')==survivor['xid']
            for r in events[log_boundary:]))
        current=self.world_process.management('surface.get',id=survivor['id'])
        self.assertTrue(current['mapped']);self.assertEqual(current['room'],self.world_info['entry_room'])
        self.assertEqual(self.world_process.management('surface.find',probe_id=next(
            r['probe_id'] for r in rows if r.get('event')=='survivor'))['surface']['id'],survivor['id'])
        self.probe_command(app,'sync') # The application process remains responsive after main-window destruction.
        state=self.actor_state()
        self.assertFalse(state.held_keys);self.assertFalse(state.held_buttons);self.assertFalse(state.HasField('focus'))

    def test_m09_observation_revocation_orders_core_application_input(self):
        self.m09_run_balanced_transfer('observation')

    def test_m09_actor_revocation_orders_core_application_input(self):
        self.m09_run_balanced_transfer('actor')

    def test_m09_query_only_attribution_preserves_core_delivery(self):
        app,surface,observe,control=self.begin_control()
        self.addCleanup(self.m09_release_probe,app)
        ready=next(r for r in self.probe_events(app) if r.get('event')=='ready')
        self.assertIs(ready['xi2'],False)
        client=self.native.connect(self.native.cookie);self.addCleanup(client.close)
        query=NativeXI2Observer(client,ready['xid'],select_events=False)
        self.addCleanup(query.close)
        self.assertEqual(query.controlled_devices()['pointer'],self.native_device(app))
        before=len(self.probe_events(app))
        self.send_key_event('a',True);self.send_key_event('a',False)
        self.actor_pointer(False);self.probe_command(app,'sync')
        rows=self.probe_events(app)[before:]
        self.assertTrue(any(r.get('event')=='key' and r.get('pressed') for r in rows),rows)
        self.assertTrue(any(r.get('event')=='key' and not r.get('pressed') for r in rows),rows)
        self.assertTrue(any(r.get('event')=='button' and not r.get('pressed') for r in rows),rows)

    def test_m09_observation_revocation_balances_input_before_peer_event(self):
        app,surface,observe,control,observer,attribution=self.m09_begin_observed_control()
        codes=self.m09_hold_observed_input(observer,attribution)
        self.actor_aim(self.peer)
        self.revoke_grant(self.peer,observe)
        self.m09_observed_handoff_then_core_delivery(app,observer,attribution,codes)
        self.assertNotIn(surface['id'],self.visible(self.viewer))
        self.assertIn(surface['id'],self.visible(self.peer))
        self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.FORBIDDEN)
        self.assertTrue(self.world_process.management('surface.get',id=surface['id'])['mapped'])
        self.probe_command(app,'sync') # Observation loss leaves the application process running.

    def test_m09_actor_revocation_balances_input_before_peer_event(self):
        app,surface,observe,control,observer,attribution=self.m09_begin_observed_control()
        codes=self.m09_hold_observed_input(observer,attribution)
        self.actor_aim(self.peer)
        self.revoke_viewer_identity(issuer.ACTOR_TARGET)
        self.m09_observed_handoff_then_core_delivery(app,observer,attribution,codes)
        self.denied(self.viewer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.denied(self.viewer.act(observe=core.ActorObserveRequest(width=128,height=128)),core.UNAUTHORIZED)
        self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.UNAUTHORIZED)
        self.assertTrue(self.world_process.management('surface.get',id=surface['id'])['mapped'])
        self.probe_command(app,'sync') # Actor revocation leaves valid application execution running.

    def revoke_viewer_identity(self,kind):
        target=self.second.actor_id if kind==issuer.ACTOR_TARGET else self.second.credential_id
        operation=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
            target=issuer.IdentityReference(kind=kind,id=target),expected_security_revision=0,reason='native control regression'))
        client=self.connect()
        result=self.execute(client,self.challenge(client,operation))
        self.assertTrue(result.HasField('identity_status'),result)

    def test_actor_revocation_releases_native_input(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_viewer_identity(issuer.ACTOR_TARGET);self.assert_released(app,inspect=False)
        self.denied(self.viewer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer)

    def test_credential_revocation_releases_native_input(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_viewer_identity(issuer.CREDENTIAL_TARGET);self.assert_released(app,inspect=False)
        self.denied(self.viewer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer)

    def revocation_during_broker_wait(self,kind):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        world=self.world_process;world.sequence+=1
        request=make_request('process.wait',dict(id=app.application.decode(),timeout_seconds=3));request.request_id=world.sequence
        world.proc.stdin.write(frame(request));world.proc.stdin.flush()
        self.assertFalse(select.select([world.proc.stdout],[],[],.1)[0])
        if kind is None:self.viewer.close()
        else:self.revoke_viewer_identity(kind)
        self.assert_released(app,inspect=False)
        # Cancellation must complete while the unrelated process wait is still pending.
        self.assertFalse(select.select([world.proc.stdout],[],[],0)[0])
        (self.data(app)/'release').touch()
        reply=receive(lambda n:os.read(world.proc.stdout.fileno(),n),world.proc.stdout,core.Reply)
        self.assertEqual(reply.request_id,request.request_id);self.assertTrue(reply.HasField('process_wait'),reply)
        self.assertEqual(reply.process_wait.exit_code,0)

    def test_actor_revocation_cancels_during_broker_wait(self):
        self.revocation_during_broker_wait(issuer.ACTOR_TARGET)

    def test_credential_revocation_cancels_during_broker_wait(self):
        self.revocation_during_broker_wait(issuer.CREDENTIAL_TARGET)

    def test_disconnect_cancels_during_broker_wait(self):
        self.revocation_during_broker_wait(None)

    def test_revocation_during_renderer_wait_does_not_replay_input(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        children=access.Path(f'/proc/{self.world_process.proc.pid}/task/{self.world_process.proc.pid}/children').read_text().split()
        compositor=next(int(pid) for pid in children if access.Path(f'/proc/{pid}/comm').read_text().strip()=='spatialwm')
        os.kill(compositor,signal.SIGSTOP)
        try:
            self.viewer.sequence+=1;self.viewer.action_sequence+=1
            request=session.WorldSessionRequest(protocol_version=1,request_id=self.viewer.sequence,
                action=core.ActorActionRequest(seq=self.viewer.action_sequence,key=core.ActorKeyRequest(keysym='b',pressed=True)))
            self.viewer.stream.sendall(frame(request))
            self.assertFalse(select.select([self.viewer.stream],[],[],.1)[0])
            self.revoke_viewer_identity(issuer.CREDENTIAL_TARGET)
            time.sleep(.2)
        finally:os.kill(compositor,signal.SIGCONT)
        reply=receive(self.viewer.stream.recv,self.viewer.stream,session.WorldSessionReply)
        self.assertTrue(reply.HasField('error'),reply)
        self.assert_released(app,inspect=False)
        self.assertFalse(any(r.get('text_hex')=='62' for r in self.probe_events(app)))
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer)


    def native_device_count(self):
        root=self.native.supervisor;name=b'XInputExtension'
        head,_=root.request(98,struct.pack('<HH',len(name),0)+name)
        self.assertTrue(head[8]);opcode=head[9]
        root.request(opcode,struct.pack('<HH',2,0),minor=47)
        head,_=root.request(opcode,struct.pack('<HH',0,0),minor=48)
        return struct.unpack_from('<H',head,8)[0]

    def test_revocation_during_native_cancel_closes_actor_devices(self):
        app,surface,observe,lease=self.begin_control();self.send_key_event('a',True)
        before=self.native_device_count()
        children=access.Path(f'/proc/{self.world_process.proc.pid}/task/{self.world_process.proc.pid}/children').read_text().split()
        compositor=next(int(pid) for pid in children if access.Path(f'/proc/{pid}/comm').read_text().strip()=='spatialwm')
        os.kill(compositor,signal.SIGSTOP)
        try:
            self.viewer.sequence+=1;self.viewer.action_sequence+=1
            request=session.WorldSessionRequest(protocol_version=1,request_id=self.viewer.sequence,
                action=core.ActorActionRequest(seq=self.viewer.action_sequence,unfocus=core.ActorStopRequest()))
            self.viewer.stream.sendall(frame(request))
            self.assertFalse(select.select([self.viewer.stream],[],[],.1)[0])
            self.revoke_viewer_identity(issuer.CREDENTIAL_TARGET)
            time.sleep(.2)
        finally:os.kill(compositor,signal.SIGCONT)
        receive(self.viewer.stream.recv,self.viewer.stream,session.WorldSessionReply)
        self.assert_released(app,inspect=False)
        self.until(self.native_device_count,lambda count:count==before-4)


def suite():
    return unittest.TestSuite(WorldApplicationControlTests(name) for name in vars(WorldApplicationControlTests) if name.startswith('test_'))


if __name__=='__main__':unittest.main(defaultTest='suite',verbosity=2)
