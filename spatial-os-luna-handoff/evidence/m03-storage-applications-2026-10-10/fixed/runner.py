"""Production storage transport and broker Git effects. Development check, not M03 gate."""
import array,hashlib,json,os,select,socket,sqlite3,struct,subprocess,sys,time,uuid
from pathlib import Path
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
build=base/'m03-storage-owner-build-01/sanitize'
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
os.environ['ASAN_OPTIONS']='detect_leaks=1:abort_on_error=1'
os.environ['UBSAN_OPTIONS']='halt_on_error=1:print_stacktrace=1'
os.environ['HOME']='/home/dflanag3'
os.environ['GIT_CONFIG_NOSYSTEM']='1'
os.environ['GIT_CONFIG_GLOBAL']='/dev/null'
root=base/'m03-storage-applications-03';assert root.exists()
volume=root/'volume';image=root/'volume.img'
record=dict(status='running',qualification=False,host=socket.gethostname().split('.')[0],profile='runtime-sanitizers',results={},commands=[],runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
loop=None;mounted=False;live=[];logs=[];unit=None;scope=None;resource_config=None
def save():
 path=root/'fixed-review.json';path.write_text(json.dumps(record,indent=2)+'\n');os.chown(path,1000,1000)
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
  args=[sys.executable,'-c',wrapper,str(build/'workspaced'),'--state-dir',str(volume),'--storage-fd',str(pair[1].fileno()),'--resource-config',str(root/'resource.textproto')]
 else:args=[sys.executable,str(Path(__file__).resolve()),'--raw',mode,str(pair[1].fileno())]
 actor=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=actor_log,pass_fds=[pair[1].fileno()],preexec_fn=None if mode=='uid' else drop);live.append(actor)
 if mode=='broker':
  (scope/'cgroup.procs').write_text(str(actor.pid)+'\n')
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
 db=sqlite3.connect(volume/'.storage-owner/catalog.db')
 try:return db.execute('SELECT count(*) FROM objects').fetchone()[0]
 finally:db.close()
try:
 assert int(subprocess.check_output(['df','--output=avail','-B1',str(root)]).decode().splitlines()[-1])>2*2**30
 assert image.stat().st_size==512*2**20
 loop=run(['losetup','--find','--show',image]).stdout.strip();assert loop.startswith('/dev/loop')
 record['recovery']=[['sudo','-n','umount',str(volume)],['sudo','-n','losetup','-d',loop]];save()
 run(['mount','-t','btrfs','-o','rw,nodev,nosuid,compress=no',loop,volume]);mounted=True
 fsid=uuid.UUID(run(['blkid','-s','UUID','-o','value',loop]).stdout.strip()).bytes
 config=storage.StorageOwnerConfig(protocol_version=1,volume_root=str(volume),filesystem_id=fsid,broker_uid=1000,volume_bytes=512*2**20,control_plane_bytes=64*2**20,filesystem_reserve_bytes=64*2**20)
 policy=config.default_policy;policy.total_bytes=64*2**20;policy.actor_state_default_bytes=32*2**20
 for profile in ('development','read-only'):policy.profiles.add(name=profile,state_bytes=24*2**20)
 # Provision one disposable delegated hierarchy. The normal host sessions remain untouched.
 unit='spatial-m03-storage-apps-'+uuid.uuid4().hex+'.service'
 run(['systemd-run','--unit='+unit,'--collect','--service-type=exec','--uid=dflanag3','-p','Delegate=cpu memory pids io','-p','DelegateSubgroup=manager','-p','MemoryMax=1536M','-p','MemorySwapMax=0','-p','TasksMax=256','-p','CPUQuota=400%','-p','RuntimeMaxSec=120','-p','TimeoutStopSec=5s','--','/usr/bin/sleep','120'])
 group=run(['systemctl','show',unit,'-p','ControlGroup','--value']).stdout.strip()
 assert group.startswith('/system.slice/') and group.endswith('/'+unit)
 delegate=Path('/sys/fs/cgroup')/group.lstrip('/');assert delegate.stat().st_uid==1000
 scope=delegate/('broker-'+uuid.uuid4().hex)
 code="from pathlib import Path; d=Path("+repr(str(delegate))+"); (d/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\\n'); Path("+repr(str(scope))+").mkdir()"
 run([sys.executable,'-c',code],True)
 backing=(Path('/sys/dev/block')/f'{os.major(root.stat().st_dev)}:{os.minor(root.stat().st_dev)}').resolve()
 if (backing/'partition').exists():backing=backing.parent
 backing_major,backing_minor=map(int,(backing/'dev').read_text().strip().split(':'))
 loop_stat=Path(loop).stat();devices={(backing_major,backing_minor),(os.major(loop_stat.st_rdev),os.minor(loop_stat.st_rdev))}
 from google.protobuf import text_format
 import process_scope_pb2 as process_scope
 def budget(memory,processes,cpu):
  return process_scope.ProcessScopeBudget(memory_bytes=memory*2**20,swap_bytes=0,processes=processes,cpu_microseconds=cpu,io=[dict(major=major,minor=minor,read_bytes_per_second=64*2**20,write_bytes_per_second=16*2**20) for major,minor in sorted(devices)])
 resource_config=broker.BrokerResourceConfig(protocol_version=2,delegated_cgroup=str(scope),broker_budget=budget(1024,128,400000),manager_budget=budget(256,32,200000),default_policy=broker.WorkspaceResourcePolicy(total=budget(512,64,200000),actor_default=budget(256,32,100000),profiles=[broker.ApplicationResourceProfile(name=name,maximum=budget(128,16,100000)) for name in ('development','read-only')]))
 path=root/'resource.textproto';path.write_text(text_format.MessageToString(resource_config));os.chown(path,1000,1000)
 record['resource_policy']=text_format.MessageToString(resource_config);record['unit']=unit;record['scope']=str(scope);save()
 # Actual private broker transport, real Git clone, linked worktree and retained removal.
 source=root/'source'
 actor,owner=spawn('restart-broker-fixed',mode='broker')
 def management(field,message):
  request=broker.Request();getattr(request.management,field).CopyFrom(message);return framed(actor,request,broker.Reply)
 repository=management('git_repo_import',core.RepositoryImportRequest(id='repo-a',path=str(source))).repository
 workspace=management('workspace_get',core.WorkspaceIdRequest(id='workspace-a')).workspace
 db=sqlite3.connect(volume/'execution.db')
 try:path=db.execute("SELECT path FROM worktrees WHERE id='tree-b'").fetchone()[0]
 finally:db.close()
 project2=core.WorktreeReply(id='tree-b',repo='repo-a',path=path)
 project=project2
 management('workspace_member_set',core.WorkspaceMemberRequest(id='workspace-a',actor='actor-a',rights=[core.WORKSPACE_SELECT,core.FILES_READ,core.FILES_WRITE,core.PROCESS_LAUNCH]))
 request=broker.Request(protocol_version=1,request_id=1002)
 request.launch.workspace='workspace-a';request.launch.actor='actor-a'
 request.launch.command.operation_id='fixed-application';request.launch.command.profile='development'
 request.launch.command.argv.extend(['/usr/bin/python3','-c',"from pathlib import Path; Path('/project/launched').write_text('real application')"])
 data=request.SerializeToString();actor.stdin.write(struct.pack('!I',len(data))+data);actor.stdin.flush()
 size=struct.unpack('!I',exact(actor.stdout,4))[0];reply=broker.Reply.FromString(exact(actor.stdout,size))
 record['results']['first_application_reply']=dict(reply=str(reply));save()
 assert reply.HasField('launched'),reply
 completion=management('process_wait',core.ProcessWaitRequest(id=reply.launched.process_id,timeout_seconds=5));assert completion.process_wait.exit_code==0,completion
 assert Path(project2.path).joinpath('launched').read_text()=='real application'
 code=r"""import ctypes,errno,fcntl,json,os,time
from pathlib import Path
time.sleep(.5)
results={}
root=os.open('/project',os.O_RDONLY|os.O_DIRECTORY)
for name,command in [('subvolume',0x5000940e),('unknown-btrfs-control',0x947f)]:
 data=bytearray(4096);data[8:15]=b'escape\0'
 try:fcntl.ioctl(root,command,data);results[name]=dict(accepted=True)
 except OSError as error:results[name]=dict(errno=error.errno)
os.close(root)
Path('/project/clone-source').write_bytes(b'ordinary reflink copy')
source=os.open('/project/clone-source',os.O_RDONLY);target=os.open('/project/clone-target',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try:fcntl.ioctl(target,0x40049409,source);results['reflink']=Path('/project/clone-target').read_bytes().decode()
finally:os.close(source);os.close(target)
fd=os.open('/state/large',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);written=0
try:
 while written<64*2**20:
  try:written+=os.write(fd,b'x'*2**20)
  except OSError as error:results['quota']=dict(bytes_written=written,errno=error.errno);break
 os.fsync(fd)
finally:os.close(fd)
results['inherited']={}
for entry in Path('/proc/self/fd').iterdir():
 try:results['inherited'][entry.name]=os.readlink(entry)
 except FileNotFoundError:pass
with open('/project/application-witness.json','w') as report:json.dump(results,report);report.flush();os.fsync(report.fileno())
time.sleep(.5)
"""
 request=broker.Request(protocol_version=1,request_id=1001)
 request.launch.workspace='workspace-a';request.launch.actor='actor-a'
 request.launch.command.operation_id='quota-and-escape';request.launch.command.profile='development';request.launch.command.argv.extend(['/usr/bin/python3','-c',code])
 data=request.SerializeToString();actor.stdin.write(struct.pack('!I',len(data))+data);actor.stdin.flush()
 size=struct.unpack('!I',exact(actor.stdout,4))[0];reply=broker.Reply.FromString(exact(actor.stdout,size));assert reply.HasField('launched'),reply
 leaves=[path.parent for path in scope.rglob('cgroup.procs') if len(path.relative_to(scope).parts)==4]
 assert len(leaves)==1,leaves
 leaf=leaves[0];kernel={name:(leaf/name).read_text() for name in ('memory.max','pids.max','cpu.max','io.max','io.stat','cgroup.procs')}
 assert kernel['memory.max'].strip()==str(128*2**20) and kernel['pids.max'].strip()=='16',kernel
 record['results']['kernel_application_budget']=dict(passed=True,state=kernel);save()
 completion=management('process_wait',core.ProcessWaitRequest(id=reply.launched.process_id,timeout_seconds=5));assert completion.process_wait.exit_code==0,completion
 result=json.loads(Path(project2.path).joinpath('application-witness.json').read_text())
 assert result['subvolume']['errno']==1 and result['unknown-btrfs-control']['errno']==1,result
 assert result['reflink']=='ordinary reflink copy',result
 assert result['quota']['errno']==122 and result['quota']['bytes_written']<24*2**20,result
 assert not Path(project2.path).joinpath('escape').exists()
 assert not any('socket:[' in path or 'pidfd' in path or 'memfd:' in path for path in result['inherited'].values()),result
 record['results']['confined_application']=dict(passed=True,witness=result);save()
 stop(actor,owner)
 record['results']['broker_git']=dict(passed=True,repository=str(repository),project=str(project),workspace=str(workspace),objects=count());save()
 for path in root.glob('*.log'):
  text=path.read_text();assert not any(value in text for value in ('AddressSanitizer','LeakSanitizer','runtime error:')),(path,text)
 record['status']='passed';save()
except BaseException as error:
 record['status']='failed';record['failure']=repr(error);save();raise
finally:
 for process in reversed(live):
  if process.poll() is None:process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 for log in logs:log.close();os.chown(log.name,1000,1000)
 if unit:
  stopped=subprocess.run(['systemctl','stop',unit],capture_output=True,text=True);record['unit_stop']=dict(exit_code=stopped.returncode,stderr=stopped.stderr)
 if mounted:
  cleanup=subprocess.run(['umount',str(volume)],capture_output=True,text=True);record['unmount']=dict(exit_code=cleanup.returncode,stderr=cleanup.stderr);mounted=cleanup.returncode!=0
 if loop and not mounted:
  cleanup=subprocess.run(['losetup','-d',loop],capture_output=True,text=True);record['detach']=dict(exit_code=cleanup.returncode,stderr=cleanup.stderr)
 record['cleanup']=dict(mounted=mounted);save()
