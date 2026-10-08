import os,sys,json,pathlib,struct,hashlib,socket,datetime,time
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');os.chdir(p)
record=json.loads((p/'local/m04-device-debug-01/commands.json').read_text())
binary=record['xserver']['path']
assert hashlib.sha256(pathlib.Path(binary).read_bytes()).hexdigest()==record['xserver']['sha256']
out=p/'local/m04-input-privacy-audit-01';out.mkdir(exist_ok=True)
os.environ['SPATIAL_TEST_XSERVER']=binary;os.environ['SPATIAL_SECURITY_EVIDENCE']=str(out/'additional-server')
sys.path[:0]=[str(p/'tests/native'),str(p/'build/python'),str(p)]
sys.argv=['privacy-extra',str(p/'build/spatial-security-auth')]
import test_resource_isolation as r
case=r.ResourceIsolationTests();case.setUp();results={}
try:
 supervisor=case.scene()
 mapping_events=[]
 for client in case.clients:
  original=client.exact
  def receive(size,read=original):
   data=read(size)
   while size==32 and data[0]==34:
    mapping_events.append(data.hex())
    data=read(size)
   return data
  client.exact=receive
 for client,win in [(case.owner,case.window),(case.other,case.other_window)]:
  assert client.operation(8,client.pack('I',win)) is None
 assert supervisor.operation(42,supervisor.pack('II',case.window,0)) is None
 name=b'XTEST';head,_=supervisor.operation(98,supervisor.pack('HH',len(name),0)+name,reply=True)
 assert head[8];opcode=head[9];keycode=38
 def held(client):
  head,body=client.operation(44,reply=True);bitmap=head[8:]+body
  assert len(bitmap)==32
  return bool(bitmap[keycode//8] & (1<<(keycode%8)))
 def inject(kind):
  payload=supervisor.pack('BBHIIIIhhIHBB',kind,keycode,0,0,supervisor.root,0,0,0,0,0,0,0,0)
  assert supervisor.operation(opcode,payload,minor=2) is None
 before=held(case.other)
 inject(2)
 try:
  deadline=time.monotonic()+1
  while not held(supervisor) and time.monotonic()<deadline:time.sleep(.01)
  assert held(supervisor),'Synthetic diagnostic key did not reach server'
  during=held(case.other)
 finally:inject(3)
 after=held(case.other)
 results['shared_keymap']=dict(keycode=keycode,before=before,during=during,after=after,foreign_key_exposed=not before and during and not after)
 for kind,grab,ungrab in [('keyboard',31,32),('pointer',26,27)]:
  def payload(client,win):
   return client.pack('IIBB2x',win,0,1,1) if kind=='keyboard' else client.pack('IHBBIII',win,0,1,1,0,0,0)
  attack=case.other.operation(grab,payload(case.other,case.other_window),reply=True)
  competing=case.owner.operation(grab,payload(case.owner,case.window),reply=True)
  results[kind+'_grab']=dict(other_status=attack[0][1] if isinstance(attack,tuple) else 'error-'+str(attack),owner_status=competing[0][1] if isinstance(competing,tuple) else 'error-'+str(competing))
  assert case.other.operation(ungrab,case.other.pack('I',0)) is None
  assert case.owner.operation(ungrab,case.owner.pack('I',0)) is None
 warp=case.other.operation(41,case.other.pack('IIhhHHhh',0,case.other_window,0,0,0,0,13,17))
 head,_=supervisor.operation(38,supervisor.pack('I',supervisor.root),reply=True)
 results['shared_pointer_warp']=dict(other_request_result=warp,supervisor_observed_coordinates=list(struct.unpack_from('<hh',head,16)))
finally:assert case.doCleanups(),'Fixture cleanup failed'
report=dict(host=socket.gethostname(),date=datetime.datetime.now(datetime.timezone.utc).isoformat(),diagnostic_only=True,ignored_mapping_notify=mapping_events,xserver=record['xserver'],results=results)
(out/'additional-observations.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
