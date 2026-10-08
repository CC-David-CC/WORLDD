import os,sys,json,pathlib,struct,hashlib,socket,datetime
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
os.chdir(p)
record=json.loads((p/'local/m04-device-debug-01/commands.json').read_text())
binary=record['xserver']['path']
assert hashlib.sha256(pathlib.Path(binary).read_bytes()).hexdigest()==record['xserver']['sha256']
os.environ['SPATIAL_TEST_XSERVER']=binary
out=p/'local/m04-input-privacy-audit-01'
out.mkdir(exist_ok=True)
os.environ['SPATIAL_SECURITY_EVIDENCE']=str(out/'server')
sys.path[:0]=[str(p/'tests/native'),str(p/'build/python'),str(p)]
sys.argv=['privacy-audit',str(p/'build/spatial-security-auth')]
import test_resource_isolation as r
import input_pb2 as ip
results=[]
for endian,same_workspace,same_application in [('<',False,False),('>',False,False),('<',True,False),('<',True,True)]:
 case=r.ResourceIsolationTests()
 case.setUp()
 try:
  supervisor=case.scene(endian=endian,same_workspace=same_workspace,same_application=same_application)
  for client,win in [(case.owner,case.window),(case.other,case.other_window)]:
   assert client.operation(8,client.pack('I',win)) is None
  assert supervisor.operation(42,supervisor.pack('II',case.window,0)) is None
  focus=case.other.operation(43,reply=True)
  exposed=struct.unpack_from(endian+'I',focus[0],8)[0] if isinstance(focus,tuple) else None
  steal=case.other.operation(42,case.other.pack('II',case.other_window,0))
  owner_focus=case.owner.operation(43,reply=True)
  owner_seen=struct.unpack_from('<I',owner_focus[0],8)[0] if isinstance(owner_focus,tuple) else None
  assert supervisor.operation(41,supervisor.pack('IIhhHHhh',0,supervisor.root,0,0,0,0,77,83)) is None
  pointer=case.other.operation(38,case.other.pack('I',case.other_window),reply=True)
  pointer_xy=list(struct.unpack_from(endian+'hh',pointer[0],16)) if isinstance(pointer,tuple) else None
  results.append(dict(endian=endian,same_workspace=same_workspace,same_application=same_application,
   owner_window=case.window,other_window=case.other_window,other_observed_focus=exposed,
   foreign_focus_exposed=exposed==case.window,other_set_focus_result=steal,
   owner_observed_focus_after=owner_seen,shared_focus_changed=owner_seen==case.other_window,
   shared_pointer_warp=[77,83],other_observed_pointer=pointer_xy,shared_pointer_exposed=pointer_xy==[77,83]))
 finally:
  assert case.doCleanups(), 'Fixture cleanup failed'
case=r.ResourceIsolationTests(); case.setUp()
try:
 supervisor=case.scene()
 assert case.owner.operation(8,case.owner.pack('I',case.window)) is None
 assert supervisor.operation(42,supervisor.pack('II',0,0)) is None
 assert supervisor.operation(2,supervisor.pack('III',supervisor.root,1<<11,1<<20)) is None
 name=b'SPATIAL-INPUT'
 head,_=supervisor.operation(98,supervisor.pack('HH',len(name),0)+name,reply=True)
 assert head[8]
 opcode=head[9]
 seq=0
 def call(**op):
  global seq
  seq+=1
  data=ip.Request(protocol_version=1,request_id=seq,**op).SerializeToString()
  head,body=supervisor.operation(opcode,supervisor.pack('I',len(data))+data,reply=True)
  response=ip.Reply.FromString(body[:struct.unpack_from('<I',head,8)[0]])
  assert response.code==ip.OK,response
  return response
 seat=call(open=ip.Empty())
 target=call(inspect=ip.Inspect(xid=case.window)).target
 call(button=ip.Button(seat=seat.seat,target=target,x=3,y=4,button=1,pressed=True,focus=True))
 call(button=ip.Button(seat=seat.seat,target=target,x=3,y=4,button=1,pressed=False))
 state=call(status=ip.Seat(id=seat.seat))
 head,_=case.owner.operation(43,reply=True)
 app_focus=struct.unpack_from('<I',head,8)[0]
 results.append(dict(kind='actor_application_binding',actor_seat=seat.seat,actor_keyboard=seat.keyboard_device,
  target=case.window,actor_reported_focus=state.focus.xid,application_reported_focus=app_focus,
  application_sees_actor_focus=app_focus==case.window))
 call(close=ip.Seat(id=seat.seat))
finally:
 assert case.doCleanups(), 'Fixture cleanup failed'
report=dict(host=socket.gethostname(),date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
 source_base='f5594ee9c96dac6c39d8adb369c5fd2cfcf203fc',working_tree_changes=True,
 xserver=record['xserver'],diagnostic_only=True,results=results)
(out/'observations.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
