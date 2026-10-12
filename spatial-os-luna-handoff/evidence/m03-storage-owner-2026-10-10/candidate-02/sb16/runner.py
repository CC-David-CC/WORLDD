"""Production owner library on a test-only private stdin harness. Not M03 qualification."""
import errno,hashlib,json,os,select,socket,struct,subprocess,sys,time,uuid
from pathlib import Path
host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2') and os.getuid()==1000
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
build=base/'m03-storage-owner-build-01/build'
sys.path.insert(0,str(build/'python'))
import storage_pb2 as pb
root=base/'m03-storage-owner-runtime-02'
assert not root.exists()
root.mkdir(mode=0o700);volume=root/'volume';volume.mkdir();image=root/'volume.img'
record=dict(host=host,status='running',qualification=False,commands=[],results={},runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
loop=None;mounted=False;process=None;log=None

def save(): (root/'review.json').write_text(json.dumps(record,indent=2)+'\n')
def run(command,expected=0):
 p=subprocess.run(list(map(str,command)),capture_output=True,text=True,timeout=60)
 item=dict(argv=list(map(str,command)),exit_code=p.returncode,stdout=p.stdout,stderr=p.stderr)
 record['commands'].append(item);save()
 if expected is not None: assert p.returncode==expected,item
 return p

def admin(*args): return run(['sudo','-n','btrfs',*args])
def object(kind): return pb.StorageObject(kind=kind,id=uuid.uuid4().hex)
def start():
 global process,log
 log=open(root/'owner.log','ab',buffering=0)
 process=subprocess.Popen(['sudo','-n',str(build/'spatial-storage-owner-probe'),str(root/'operator.pb')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log)
def stop():
 global process,log
 if process:
  process.stdin.close();assert process.wait(timeout=20)==0,(root/'owner.log').read_text();process=None
 if log:log.close();log=None

def exact(n):
 data=b'';deadline=time.monotonic()+30
 while len(data)<n:
  assert time.monotonic()<deadline,'owner reply deadline'
  assert select.select([process.stdout],[],[],max(0,deadline-time.monotonic()))[0],'owner reply timeout'
  chunk=os.read(process.stdout.fileno(),n-len(data));assert chunk,(root/'owner.log').read_text();data+=chunk
 return data
counter=0

def call(operation,expected=None):
 global counter
 counter+=1;request=pb.StorageOwnerRequest(protocol_version=1,request_id=counter)
 getattr(request,operation[0]).CopyFrom(operation[1]);data=request.SerializeToString()
 process.stdin.write(struct.pack('!I',len(data))+data);process.stdin.flush()
 size=struct.unpack('!I',exact(4))[0];assert size<=2**20
 reply=pb.StorageOwnerReply.FromString(exact(size));assert reply.request_id==counter
 record['results'][str(counter)]=dict(operation=operation[0],reply=str(reply));save()
 if expected is None: assert not reply.HasField('error'),reply
 else: assert reply.HasField('error') and reply.error.code in expected,reply
 return reply

def fill(path,maximum):
 fd=os.open(path,os.O_CREAT|os.O_WRONLY|os.O_EXCL,0o600);written=0;error=None;sync_error=None
 try:
  while written<maximum:
   try:written+=os.write(fd,b'x'*min(2**20,maximum-written))
   except OSError as failure:error=failure.errno;break
  try:os.fsync(fd)
  except OSError as failure:sync_error=failure.errno
 finally:os.close(fd)
 return dict(bytes_written=written,write_errno=error,fsync_errno=sync_error)

try:
 assert int(subprocess.check_output(['df','--output=avail','-B1',str(root)]).decode().splitlines()[-1])>2*2**30
 run(['fallocate','-l',512*2**20,image]);run(['mkfs.btrfs','-f','-d','single','-m','single',image])
 loop=run(['sudo','-n','losetup','--find','--show',image]).stdout.strip();assert loop.startswith('/dev/loop')
 record['recovery']=[['sudo','-n','umount',str(volume)],['sudo','-n','losetup','-d',loop]];save()
 run(['sudo','-n','mount','-t','btrfs','-o','rw,nodev,nosuid,compress=no',loop,volume]);mounted=True
 fsid=uuid.UUID(run(['sudo','-n','blkid','-s','UUID','-o','value',loop]).stdout.strip()).bytes
 config=pb.StorageOwnerConfig(protocol_version=1,volume_root=str(volume),filesystem_id=fsid,broker_uid=os.getuid(),broker_pid=os.getpid(),volume_bytes=512*2**20,control_plane_bytes=64*2**20,filesystem_reserve_bytes=64*2**20)
 policy=config.default_policy;policy.total_bytes=64*2**20;policy.actor_state_default_bytes=32*2**20;policy.profiles.add(name='test',state_bytes=24*2**20)
 local=root/'configuration.pb';local.write_bytes(config.SerializeToString());run(['sudo','-n','install','-m','600',local,root/'operator.pb'])
 start();call(('hello',pb.StorageHello()))
 repo=object(pb.StorageObject.REPOSITORY);project=object(pb.StorageObject.PROJECT)
 call(('repository_prepare',pb.StorageRepositoryPrepare(object=repo)))
 state=call(('project_prepare',pb.StorageProjectPrepare(object=project,repository=repo))).state
 assert state.maximum_bytes==64*2**20
 call(('workspace_bind',pb.StorageWorkspaceBind(workspace='workspace-a',repository=repo,project=project)))
 app=object(pb.StorageObject.APPLICATION_STATE);app2=object(pb.StorageObject.APPLICATION_STATE)
 state=call(('application_prepare',pb.StorageApplicationPrepare(workspace='workspace-a',object=app,fixture_actor='actor-a',profile='test'))).state
 call(('application_prepare',pb.StorageApplicationPrepare(workspace='workspace-a',object=app2,fixture_actor='actor-a',profile='test')))
 retired=object(pb.StorageObject.RETIRED)
 call(('retirement_prepare',pb.StorageRetirementPrepare(object=retired,repository=repo,project=project)))
 sibling=object(pb.StorageObject.REPOSITORY);call(('repository_prepare',pb.StorageRepositoryPrepare(object=sibling)))
 stop();start();call(('inspect',pb.StorageInspect(object=app)))
 leaf='0/'+str(state.subvolume_id);group=str(state.quota_group>>48)+'/'+str(state.quota_group & ((1<<48)-1))
 admin('qgroup','limit',28*2**20,leaf,volume)
 call(('inspect',pb.StorageInspect(object=app)),(pb.StorageOwnerError.UNCERTAIN,))
 admin('qgroup','limit',24*2**20,leaf,volume)
 admin('qgroup','remove',leaf,group,volume)
 call(('inspect',pb.StorageInspect(object=app)),(pb.StorageOwnerError.UNCERTAIN,pb.StorageOwnerError.UNAVAILABLE))
 admin('qgroup','assign',leaf,group,volume);admin('quota','rescan','-w',volume)
 call(('inspect',pb.StorageInspect(object=app)))
 for label,path,maximum in [('application',volume/'applications'/app.id/'large',64*2**20),('actor',volume/'applications'/app2.id/'large',64*2**20),('workspace',volume/'worktrees'/project.id/'large',128*2**20)]:
  result=fill(path,maximum);record['results'][label]=result;save();assert result['write_errno']==errno.EDQUOT,result
 record['results']['sibling']=fill(volume/'repositories'/(sibling.id+'.git')/'independent',4*2**20)
 assert record['results']['sibling']['bytes_written']==4*2**20
 (volume/'worktrees'/project.id/'large').unlink();admin('filesystem','sync',volume)
 record['results']['recovered']=fill(volume/'worktrees'/project.id/'recovered',2**20)
 assert record['results']['recovered']['bytes_written']==2**20
 stop();record['status']='passed';save()
except BaseException as error:
 record['status']='failed';record['failure']=repr(error);save();raise
finally:
 if process:
  process.stdin.close()
  try:process.wait(timeout=20)
  except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
 if log:log.close()
 if mounted:
  result=run(['sudo','-n','umount',volume],expected=None);mounted=result.returncode!=0
 if loop and not mounted:run(['sudo','-n','losetup','-d',loop])
 record['cleanup']=dict(mounted=mounted,loop_attached=bool(loop and mounted));save()
