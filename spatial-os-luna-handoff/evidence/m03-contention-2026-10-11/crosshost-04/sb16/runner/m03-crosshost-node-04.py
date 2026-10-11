import argparse,hashlib,json,os,socket,subprocess,time
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('mode',choices=['controller','worker'])
parser.add_argument('profile',choices=['Debug','ASan-UBSan'])
parser.add_argument('scenario',choices=['delay','crash'])
parser.add_argument('direction',choices=['forward','reverse'])
parser.add_argument('--check-only',action='store_true')
args=parser.parse_args()
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m03-git-storage-20261011')
source=r/'source';cross=r/'m03-crosshost-01'
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
gate=json.loads((r/'m03-healthy-setup-qualification-04/review.json').read_text())
assert gate['status']=='selected_commands_passed_needs_review',gate['status']
assert all(p['status']=='passed' for p in gate['profiles'].values())
primary_path=r/'m03-healthy-setup-qualification-04/primary-reassessment.json'
primary=json.loads(primary_path.read_text())
assert primary['status']=='reviewed_local_M03_gate_pending_crosshost'
assert primary['source_hashes']==gate['source_hashes']
assert primary['profiles']==['Debug','ASan-UBSan']
gate['server']=gate['prior_gate']['server']
for name,digest in gate['source_hashes'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
build=r/('operator-storage-debug-01/build' if args.profile=='Debug' else 'sanitize')
pins=gate['profiles'][args.profile]['binary_hashes']
for name,digest in pins.items():assert hashlib.sha256((build/name).read_bytes()).hexdigest()==digest,name
bindings=json.loads((cross/'bindings-review.json').read_text())
assert bindings['exit_code']==0
assert hashlib.sha256((source/'tests/proto/crosshost_revocation.proto').read_bytes()).hexdigest()==bindings['schema_sha256']
assert hashlib.sha256((cross/'python/crosshost_revocation_pb2.py').read_bytes()).hexdigest()==bindings['binding_sha256']
assert hashlib.sha256(Path(gate['server']['path']).read_bytes()).hexdigest()==gate['server']['sha256']
for name,digest in gate['prior_gate']['installed_helpers'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,name
fixture=r/'m03-crosshost-production-02.py'
fixture_review=json.loads((r/'m03-crosshost-02-fixture.json').read_text())
assert hashlib.sha256(fixture.read_bytes()).hexdigest()==fixture_review['file_sha256']
if args.check_only:
 digest=hashlib.sha256(json.dumps(gate['source_hashes'],sort_keys=True).encode()).hexdigest()
 print(json.dumps(dict(host=host,status='local_gate_reviewed',source_digest=digest,fixture_sha256=fixture_review['file_sha256'])))
 raise SystemExit(0)
case=cross/'runs'/('-'.join([args.profile,args.scenario,args.direction])+'04')
case.mkdir(parents=True,exist_ok=True)
out=case/args.mode
assert not out.exists()
command=['python3',str(source/'tools/with_resource_delegate.py'),'--native','--','python3',
 str(source/'tools/with_adapter_resources.py'),'--','python3',str(fixture),args.mode,'--source',str(source),
 '--build',str(build),'--bindings',str(cross/'python'),'--scenario',args.scenario,'--output',str(out)]
if args.mode=='controller':
 assert host=='sb16'
 command+=['--address','10.0.7.49','--delayed-host','sb16-2' if args.direction=='forward' else 'sb16']
else:
 bundle=case/('controller/private-bundle' if host=='sb16' else 'private-bundle')
 assert bundle.is_dir()
 command+=['--bundle',str(bundle)]
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),
 SPATIAL_TEST_XSERVER=gate['server']['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'native'),
 SPATIAL_SCOPE_EVIDENCE=str(out/'scopes'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
 UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
record=dict(status='running',host=host,mode=args.mode,profile=args.profile,scenario=args.scenario,
 direction=args.direction,command=command,source_hashes=gate['source_hashes'],fixture_sha256=fixture_review['file_sha256'],
 primary_reassessment_sha256=hashlib.sha256(primary_path.read_bytes()).hexdigest(),
 gate_review_sha256=hashlib.sha256((r/'m03-healthy-setup-qualification-04/review.json').read_bytes()).hexdigest(),
 runner_pid=os.getpid(),started=time.time())
review=case/(args.mode+'-review.json')
def save():review.write_text(json.dumps(record,indent=2)+'\n')
save();print(json.dumps({k:record[k] for k in ['status','host','mode','profile','scenario','direction','runner_pid']}),flush=True)
try:
 with (case/(args.mode+'.log')).open('wb') as log:
  result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
 record['exit_code']=result.returncode
 if (out/'result.json').exists():record['result']=json.loads((out/'result.json').read_text())
 record['status']='passed' if result.returncode==0 and record.get('result',{}).get('status')=='passed' else 'failed'
 record['ended']=time.time();save()
 print(json.dumps({k:record[k] for k in ['status','host','mode','profile','scenario','direction','exit_code']}),flush=True)
 if record['status']!='passed':raise SystemExit(1)
except BaseException as error:
 if record['status']=='running':record.update(status='failed',failure=repr(error),ended=time.time());save()
 raise

