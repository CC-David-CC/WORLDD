"""Production storage transport and broker Git effects. Development check, not M03 gate."""
import array,hashlib,json,os,select,socket,sqlite3,struct,subprocess,sys,time,uuid
from pathlib import Path
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
build=base/'m03-storage-owner-build-01/build'
sys.path.insert(0,str(build/'python'))
import storage_pb2 as storage, core_pb2 as core, broker_pb2 as broker

if len(sys.argv)>1 and sys.argv[1]=='--raw':
 mode=sys.argv[2];fd=int(sys.argv[3]);os.read(0,1);channel=socket.socket(fileno=fd)
 request=storage.StorageOwnerRequest(protocol_version=1,request_id=1)
 request.repository_prepare.object.kind=storage.StorageObject.REPOSITORY;request.repository_prepare.object.id=uuid.uuid4().hex
 payload=request.SerializeToString();frame=struct.pack('!I',len(payload))+payload
 if mode=='rights':
  with open('/dev/null','rb') as source:channel.sendmsg([frame],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[source.fileno()]))])
 elif mode=='oversize':channel.sendall(struct.pack('!I',2**20+1))
 elif mode=='partial':channel.sendall(frame[:2]);channel.shutdown(socket.SHUT_WR)
 elif mode=='timeout':channel.sendall(frame[:1]);time.sleep(6)
 elif mode=='substitution':
  channel.sendall(frame[:4]);time.sleep(.1)
  child=os.fork()
  if child==0:channel.sendall(frame[4:]);os._exit(0)
  os.waitpid(child,0)
 else:channel.sendall(frame)
 time.sleep(.3);sys.exit(0)

assert os.getuid()==0 and socket.gethostname().split('.')[0] in ('sb16','sb16-2')
os.umask(0o077)
root=base/'m03-storage-transport-01';assert not root.exists();root.mkdir();os.chown(root,1000,1000)
volume=root/'volume';volume.mkdir();image=root/'volume.img'
record=dict(status='running',qualification=False,host=socket.gethostname().split('.')[0],profile='Debug',results={},commands=[],runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
loop=None;mounted=False;live=[];logs=[]
def save():
 path=root/'review.json';path.write_text(json.dumps(record,indent=2)+'\n');os.chown(path,1000,1000)
def run(command,actor=False):
 p=subprocess.run(list(map(str,command)),capture_output=True,text=True,timeout=60,preexec_fn=drop if actor else None)
 entry=dict(argv=list(map(str,command)),exit_code=p.returncode,stdout=p.stdout,stderr=p.stderr);record['commands'].append(entry);save();assert p.returncode==0,entry;return p

def drop():os.setgroups([]);os.setgid(1000);os.setuid(1000)
def exact(stream,size):
 result=b'';deadline=time.monotonic()+15
 while len(result)<size:
  assert select.select([stream],[],[],max(0,deadline-time.monotonic()))[0],'reply deadline'
  data=os.read(stream.fileno(),size-len(result));assert data,'response EOF';result+=data
 return result
counter=0

def framed(process,request,reply_type):
 global counter
 counter+=1;request.protocol_version=1;request.request_id=counter
 if isinstance(request,broker.Request) and request.HasField('management'):
  request.management.protocol_version=1;request.management.request_id=counter
 data=request.SerializeToString();process.stdin.write(struct.pack('!I',len(data))+data);process.stdin.flush()
 size=struct.unpack('!I',exact(process.stdout,4))[0];assert 0<size<=2**20
 reply=reply_type.FromString(exact(process.stdout,size));assert reply.request_id==counter
 if isinstance(reply,broker.Reply):
  assert not reply.HasField('error'),reply;reply=reply.management
 assert not reply.HasField('error'),reply
 return reply

config=None

def spawn(label,mode='client',wrong_pid=None):
 pair=socket.socketpair()
 for end in pair:end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
 actor_log=open(root/(label+'-actor.log'),'wb',buffering=0);owner_log=open(root/(label+'-owner.log'),'wb',buffering=0);logs.extend([actor_log,owner_log])
 if mode=='client':args=[str(build/'spatial-storage-client-probe'),str(pair[1].fileno()),str(volume)]
 elif mode=='broker':
  wrapper="import os,sys; assert os.read(0,1)==b'!'; os.execv(sys.argv[1],sys.argv[1:])"
  args=[sys.executable,'-c',wrapper,str(build/'workspaced'),'--state-dir',str(volume),'--storage-fd',str(pair[1].fileno())]
 else:args=[sys.executable,str(Path(__file__).resolve()),'--raw',mode,str(pair[1].fileno())]
 actor=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=actor_log,pass_fds=[pair[1].fileno()],preexec_fn=None if mode=='uid' else drop);live.append(actor)
 selected=storage.StorageOwnerConfig();selected.CopyFrom(config);selected.broker_pid=wrong_pid or actor.pid
 path=root/(label+'-operator.pb');path.write_bytes(selected.SerializeToString());os.chmod(path,0o600)
 owner=subprocess.Popen([str(build/'spatial-storage-owner'),'--channel-fd',str(pair[0].fileno()),'--config',str(path)],stdout=subprocess.DEVNULL,stderr=owner_log,pass_fds=[pair[0].fileno()]);live.append(owner)
 pair[0].close();pair[1].close();actor.stdin.write(b'!');actor.stdin.flush();return actor,owner

def stop(actor,owner):
 actor.stdin.close();assert actor.wait(timeout=20)==0
 assert owner.wait(timeout=20)==0
 live.remove(actor);live.remove(owner)

def object(kind):return storage.StorageObject(kind=kind,id=uuid.uuid4().hex)
def count():
 with sqlite3.connect(volume/'.storage-owner/catalog.db') as db:return db.execute('SELECT count(*) FROM objects').fetchone()[0]
try:
 assert int(subprocess.check_output(['df','--output=avail','-B1',str(root)]).decode().splitlines()[-1])>2*2**30
 run(['fallocate','-l',512*2**20,image]);run(['mkfs.btrfs','-f','-d','single','-m','single',image])
 loop=run(['losetup','--find','--show',image]).stdout.strip();assert loop.startswith('/dev/loop')
 record['recovery']=[['sudo','-n','umount',str(volume)],['sudo','-n','losetup','-d',loop]];save()
 run(['mount','-t','btrfs','-o','rw,nodev,nosuid,compress=no',loop,volume]);mounted=True
 fsid=uuid.UUID(run(['blkid','-s','UUID','-o','value',loop]).stdout.strip()).bytes
 config=storage.StorageOwnerConfig(protocol_version=1,volume_root=str(volume),filesystem_id=fsid,broker_uid=1000,volume_bytes=512*2**20,control_plane_bytes=64*2**20,filesystem_reserve_bytes=64*2**20)
 policy=config.default_policy;policy.total_bytes=64*2**20;policy.actor_state_default_bytes=32*2**20
 for profile in ('development','read-only'):policy.profiles.add(name=profile,state_bytes=24*2**20)
 actor,owner=spawn('positive')
 repo=object(storage.StorageObject.REPOSITORY);request=storage.StorageOwnerRequest();request.repository_prepare.object.CopyFrom(repo)
 reply=framed(actor,request,storage.StorageOwnerReply);assert reply.state.maximum_bytes==64*2**20
 stop(actor,owner);record['results']['authenticated_allocation']=dict(passed=True,reply=str(reply));save()
 for mode in ('uid','pid','rights','oversize','partial','timeout','substitution'):
  before=count();dummy=None
  if mode=='pid':dummy=subprocess.Popen(['sleep','20'],preexec_fn=drop);live.append(dummy)
  actor,owner=spawn('reject-'+mode,mode,wrong_pid=dummy.pid if dummy else None)
  actor.stdin.close();actor_result=actor.wait(timeout=15);owner_result=owner.wait(timeout=15)
  if dummy:dummy.terminate();dummy.wait();live.remove(dummy)
  live.remove(actor);live.remove(owner)
  after=count();assert owner_result!=0 and before==after,(mode,owner_result,before,after)
  record['results'][mode]=dict(passed=True,actor_exit=actor_result,owner_exit=owner_result,objects_before=before,objects_after=after);save()
 # Actual private broker transport, real Git clone, linked worktree and retained removal.
 source=root/'source';source.mkdir();os.chown(source,1000,1000)
 file=source/'README';file.write_text('storage integration\n');os.chown(file,1000,1000)
 run(['git','-C',source,'init','-b','main'],True);run(['git','-C',source,'add','README'],True)
 run(['git','-C',source,'-c','user.name=Storage witness','-c','user.email=witness@localhost','commit','-m','Initial'],True)
 actor,owner=spawn('broker',mode='broker')
 def management(field,message):
  request=broker.Request();getattr(request.management,field).CopyFrom(message);return framed(actor,request,broker.Reply)
 repository=management('git_repo_import',core.RepositoryImportRequest(id='repo-a',path=str(source))).repository
 project=management('git_worktree_create',core.WorktreeCreateRequest(repo='repo-a',id='tree-a',branch='work-a',start='main',operation_id='create-a')).worktree
 assert Path(project.path).joinpath('README').read_text()=='storage integration\n'
 status=management('git_status',core.WorktreeStatusRequest(worktree='tree-a')).git_status;assert status.clean
 removal=management('git_worktree_remove',core.WorktreeRemoveRequest(id='tree-a',operation_id='remove-a'));assert removal.worktree_removed.removed
 project2=management('git_worktree_create',core.WorktreeCreateRequest(repo='repo-a',id='tree-b',branch='work-b',start='main',operation_id='create-b')).worktree
 workspace=management('workspace_create',core.WorkspaceCreateRequest(id='workspace-a',worktree='tree-b',policy=[core.WORKSPACE_SELECT,core.FILES_READ,core.FILES_WRITE,core.PROCESS_LAUNCH])).workspace
 assert workspace.id=='workspace-a'
 stop(actor,owner)
 record['results']['broker_git']=dict(passed=True,repository=str(repository),project=str(project),workspace=str(workspace),objects=count());save()
 record['status']='passed';save()
except BaseException as error:
 record['status']='failed';record['failure']=repr(error);save();raise
finally:
 for process in reversed(live):
  if process.poll() is None:process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 for log in logs:log.close();os.chown(log.name,1000,1000)
 if mounted:
  cleanup=subprocess.run(['umount',str(volume)],capture_output=True,text=True);record['unmount']=dict(exit_code=cleanup.returncode,stderr=cleanup.stderr);mounted=cleanup.returncode!=0
 if loop and not mounted:
  cleanup=subprocess.run(['losetup','-d',loop],capture_output=True,text=True);record['detach']=dict(exit_code=cleanup.returncode,stderr=cleanup.stderr)
 record['cleanup']=dict(mounted=mounted);save()
