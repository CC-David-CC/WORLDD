import hashlib,json,os,resource,socket,struct,subprocess,sys,time,tempfile,shutil,uuid
from pathlib import Path
assert os.getuid()==1000 and socket.gethostname().split('.')[0] in ('sb16','sb16-2')
assert len(sys.argv) in (2,3) and sys.argv[2:] in ([],['--sanitizers'])
probe=Path(sys.argv[1]).resolve();sanitizers=sys.argv[2:]==['--sanitizers']
import broker_pb2 as pb
temporary=tempfile.TemporaryDirectory(prefix='spatial-app-initializer-')
root=Path(temporary.name)
record=dict(status='running',qualification=False,cases=[],host=socket.gethostname().split('.')[0],runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),started=time.time())
env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
def save():(root/'review.json').write_text(json.dumps(record,indent=2)+'\n')
def events(data):
 result=[]
 while data:
  assert len(data)>=4;size=struct.unpack('!I',data[:4])[0];assert 0<size<=2**20 and len(data)>=4+size
  result.append(pb.AppEvent.FromString(data[4:4+size]));data=data[4+size:]
 return result

def no_core():resource.setrlimit(resource.RLIMIT_CORE,(0,0))
def case(name,request=None,payload=None,expected=0,exit_code=None,raw=None,leak=False):
 directory=root/name;directory.mkdir(mode=0o700)
 if payload is None:payload=request.SerializeToString()
 frame=raw if raw is not None else struct.pack('!I',len(payload))+payload
 command=[str(probe),str(directory)]+(['--leak-witness'] if leak else [])
 process=subprocess.run(command,input=frame,capture_output=True,timeout=15,env=env,preexec_fn=no_core)
 (directory/'diagnostic.log').write_bytes(process.stderr);(directory/'reply.pb').write_bytes(process.stdout)
 replies=events(process.stdout)
 receipt=dict(name=name,exit=process.returncode,events=[str(reply) for reply in replies],stderr_sha256=hashlib.sha256(process.stderr).hexdigest())
 record['cases'].append(receipt);save()
 if leak:assert process.returncode!=0 and b'LeakSanitizer' in process.stderr and b'73 byte' in process.stderr,receipt
 else:
  assert process.returncode==expected,receipt
  assert not any(value in process.stderr for value in (b'AddressSanitizer',b'LeakSanitizer',b'runtime error:')),receipt
  if expected==0:
   assert len(replies)==2 and replies[0].started and replies[1].HasField('exit_code') and replies[1].exit_code==exit_code,receipt
  else:assert len(replies)==1 and replies[0].HasField('error'),receipt
 return directory
try:
 valid=pb.AppStart(protocol_version=1,argv=['/usr/bin/python3','-c',"import os; print('actual stdout'); os.write(2,b'actual stderr'); raise SystemExit(7)"])
 directory=case('actual-output',valid,exit_code=7)
 assert (directory/'stdout').read_text()=='actual stdout\n' and (directory/'stderr').read_text()=='actual stderr'
 for index in range(32):case('lifecycle-'+str(index),pb.AppStart(protocol_version=1,argv=['/usr/bin/true']),exit_code=0)
 for name,request in [('version',pb.AppStart(protocol_version=2,argv=['/usr/bin/true'])),('missing-argv',pb.AppStart(protocol_version=1)),('too-many-argv',pb.AppStart(protocol_version=1,argv=['/usr/bin/true']+['x']*256)),('nul-argv',pb.AppStart(protocol_version=1,argv=['/usr/bin/true','x\0y'])),('exec-failure',pb.AppStart(protocol_version=1,argv=['/nonexistent-spatial-test-executable']))]:
  case(name,request,expected=2)
 case('malformed',payload=b'\xff',expected=2)
 case('unknown-field',payload=valid.SerializeToString()+b'\xf8\x07\x01',expected=2)
 case('truncated-prefix',payload=b'x',raw=b'\x00\x00',expected=2)
 case('excessive-frame',payload=b'x',raw=struct.pack('!I',2**20+1),expected=2)
 if sanitizers:case('positive-leak',pb.AppStart(protocol_version=1,argv=['/usr/bin/true']),leak=True)
 record['status']='passed';record['coverage']='Production shared initializer body with full ASan/UBSan/LSan. Hardened syscall wrapper requires separate real-runtime evidence.';save()
except BaseException as error:record['status']='failed';record['failure']=repr(error);save();raise
finally:
 destination=os.environ.get('SPATIAL_INITIALIZER_EVIDENCE')
 if destination:
  selected=Path(destination)/('initializer-'+uuid.uuid4().hex);selected.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(root,selected)
 temporary.cleanup()
print(json.dumps(dict(status=record['status'],cases=len(record['cases']))))
