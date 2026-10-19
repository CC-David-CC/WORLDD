#!/usr/bin/env python3
import argparse,fcntl,hashlib,json,os,pwd,re,signal,socket,stat,subprocess,sys,time,uuid
from pathlib import Path
BASE='731106ee55c4238deac6bd7daacb380caaf58ef983041a89040355e2c549966e'
TEST='25237c0a754693884812e8e2ccca53cc3d840575fbc2b25eeddf034e62e1efa3'
PACKAGED_TEST='d379fcc65c0a127a1ed4f639b3eb8c38134474bf9ce53d4be22de885e94ea2e0'
BOOT='c8e575549593d38d2c80e47cfe496aee1d36f67ac05b228f3651741ba1233c6d'
RUNNER='7053d5ffe05e7050b035d6b715b794dd742b68ee29b9198a703979391323f537'
D='/home/dflanag3/spatial-artifacts/m08-copy-cleanup-native-repair-runtime-20261018'
S='/home/dflanag3/spatial-artifacts/m08-copy-cleanup-native-asan-ubsan-1a1da1f-runtime-20261018'
SRC=D+'/fixture-overlays/fixture-storage-setup-02/spatial-os-luna-handoff'
P={
'debug':dict(payload=D+'/payload/m08-copy-cleanup-native-debug-final-v7',pre=D+'/launchers/native-preflight-v7.primary.py',pre_sha='98acae10d517b39e4321506aed6c9f95ddc3c3eecf9f66727a1228fef262dd38',inv='deb8b74914d9e12491b84fdc972467a8d59afdffc410d6a651b50c6326460b4e',owner='47698e26f1b6ce032ec27a98bc8023ce434b42b34b120c1f9fa40c4e892cac0b',size=13842120,runner=D+'/launchers/run-command.sh'),
'san':dict(payload=S+'/payload/m08-copy-cleanup-native-asan-ubsan-1a1da1f-v1',pre=S+'/launchers/native-preflight-v1-san.primary.py',pre_sha='307910c3a9a0775304ab21a7ee5aa65e70e81a43714743dd06bfe7052670ced2',inv='49f9d68dfb8ff9936bf7a7cc0ce67a43917a197739778e140dddc0188d0603c7',owner='f2414d86a92b555c3c8d73541d43cc87ea40c7a28401e2cb2447042f4310db7e',size=67002872,runner=S+'/launchers/run-command.sh')}
# correct the SAN payload root from its documented artifact root
P['san']['payload']=S+'/payload/m08-copy-cleanup-native-asan-ubsan-1a1da1f-v1'
M=('test_native_storage_session_retains_owner_and_confined_application','test_cold_start_cleanup_binding_smoke','test_failed_cleanup_retains_owner_for_explicit_retry','test_cleanup_parent_disconnect_preserves_pending_without_receipt','test_pending_supervisor_signal_exits_without_receipt_or_retirement','test_cleanup_retry_exhaustion_stays_pending_and_rejects_stop','test_native_storage_owner_loss_stops_execution_and_records_cleanup')
B=('spatial-authority-launch-probe','spatial-security-auth','identityd','spatial-issuer-client')
T=Path('/usr/local/libexec/spatial-storage-owner'); L=Path('/run/user/1000/spatial-m08-runtime.lock')
ACTIVE=None; INTERRUPTED=None
class Refusal(RuntimeError): pass
def h(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 p=Path(p);q=p.with_name(p.name+'.tmp-'+uuid.uuid4().hex);q.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');os.replace(q,p)
def root(argv,secs=30):
 try:return subprocess.run(['sudo','-n','/usr/bin/timeout','--signal=TERM','--kill-after=3s',f'{secs-5}s',*argv],text=True,capture_output=True,timeout=secs)
 except subprocess.TimeoutExpired as e:raise Refusal('bounded sudo command timed out') from e
def ident():
 s=T.lstat()
 if not stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode):raise Refusal('installed helper is not regular')
 return dict(sha=h(T),uid=s.st_uid,gid=s.st_gid,mode=stat.S_IMODE(s.st_mode),size=s.st_size)

def conflicts():
 # Root can inspect executable identities for root-owned storage helpers.
 code = """import json,os
from pathlib import Path
rows=[]
for p in Path('/proc').iterdir():
 if not p.name.isdecimal(): continue
 try:
  exe=os.readlink(p/'exe').removesuffix(' (deleted)')
  argv=(p/'cmdline').read_bytes().split(b'\\0')
 except (FileNotFoundError,ProcessLookupError): continue
 if Path(exe).name=='spatial-storage-owner' or any(Path(os.fsdecode(a)).name=='test_storage_session.py' for a in argv if a):
  rows.append(dict(pid=int(p.name),executable=exe))
print(json.dumps(rows))
"""
 r=root(['/usr/bin/python3','-c',code],20)
 if r.returncode:raise Refusal('root process identity inspection failed: '+r.stderr)
 return json.loads(r.stdout)

def group_members(pgid):
 rows=[]
 for entry in Path('/proc').iterdir():
  if not entry.name.isdecimal():continue
  try:
   fields=(entry/'stat').read_text().rsplit(')',1)[1].split()
   if int(fields[2])==pgid:rows.append(dict(pid=int(entry.name),state=fields[0]))
  except (FileNotFoundError,ProcessLookupError):continue
 return rows

def sig_handler(sig,_):
 global INTERRUPTED
 INTERRUPTED=sig
 if ACTIVE is not None:
  try:os.killpg(ACTIVE.pid,signal.SIGTERM)
  except ProcessLookupError:pass

def kill_group(proc,grace=5):
 if proc is None:return dict(live=[],zombies=[],forced=False)
 members=group_members(proc.pid)
 live=[r for r in members if r['state']!='Z']
 forced=bool(live)
 if live:
  try:os.killpg(proc.pid,signal.SIGTERM)
  except ProcessLookupError:pass
 deadline=time.monotonic()+grace
 while live and time.monotonic()<deadline:
  proc.poll();time.sleep(.05)
  live=[r for r in group_members(proc.pid) if r['state']!='Z']
 if live:
  try:os.killpg(proc.pid,signal.SIGKILL)
  except ProcessLookupError:pass
 try:proc.wait(timeout=5)
 except subprocess.TimeoutExpired:pass
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  members=group_members(proc.pid)
  if not any(r['state']!='Z' for r in members):break
  time.sleep(.05)
 return dict(live=[r for r in members if r['state']!='Z'],zombies=[r for r in members if r['state']=='Z'],forced=forced)

def owned_units(out):
 units=set()
 for p in out.rglob('*'):
  if p.is_file() and (p.suffix=='.log' or p.name=='owner-kernel.json'):
   text=p.read_text(errors='replace')
   units.update(re.findall(r'spatial-m03-resources-[0-9a-f]{32}\.service',text))
   units.update(re.findall(r'Storage owner unit: (spatial-storage-owner-[0-9a-f]{32}\.service)',text))
   if p.name=='owner-kernel.json':
    value=json.loads(text).get('unit','')
    if re.fullmatch(r'spatial-storage-owner-[0-9a-f]{32}\.service',value):units.add(value)
 return sorted(units)

def stop_units(out):
 result=dict(units=owned_units(out),forced=[],unresolved=[])
 for unit in result['units']:
  query=root(['systemctl','show',unit,'-p','LoadState','-p','ActiveState','-p','MainPID'],15)
  fields=dict(line.split('=',1) for line in query.stdout.splitlines() if '=' in line)
  if fields.get('LoadState')=='not-found':continue
  if query.returncode:result['unresolved'].append(unit);continue
  if fields.get('ActiveState') not in ('inactive','failed') or fields.get('MainPID','0')!='0':
   result['forced'].append(unit)
   stopped=root(['systemctl','stop',unit],20)
   query=root(['systemctl','show',unit,'-p','LoadState','-p','ActiveState','-p','MainPID'],15)
   fields=dict(line.split('=',1) for line in query.stdout.splitlines() if '=' in line)
   if stopped.returncode or (fields.get('LoadState')!='not-found' and (query.returncode or fields.get('ActiveState') not in ('inactive','failed') or fields.get('MainPID','0')!='0')):
    result['unresolved'].append(unit)
 save(out/'transient-service-cleanup.json',result)
 return result

def inspect_logs(out):
 cr=out/'canonical-run'
 logs=''.join((cr/name).read_text(errors='replace') for name in ('stdout.log','stderr.log'))
 passed=[]
 for method in M:
  pattern=r'^'+re.escape(method)+r' \((?:__main__\.)?NativeStorageSessionTests(?:\.'+re.escape(method)+r')?\) \.\.\. ok$'
  if len(re.findall(pattern,logs,re.M))==1:passed.append(method)
 diagnostics=[]
 pattern=re.compile(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer)|AddressSanitizer:DEADLYSIGNAL|runtime error:')
 for path in out.rglob('*.log'):
  for number,line in enumerate(path.read_text(errors='replace').splitlines(),1):
   if pattern.search(line):diagnostics.append(dict(path=str(path),line=number,text=line[:600]))
 return dict(passed_methods=passed,unittest_ok=bool(re.search(r'Ran 7 tests in [\d.]+s\s+OK\s*$',logs)),sanitizer_findings=diagnostics)

def successful(result):
 return (result.get('test_exit')==0 and result.get('command_exit')=='0' and result.get('wrapper_exit')=='0'
  and result.get('terminal_status')=='COMPLETE' and result.get('unittest_ok') is True
  and result.get('passed_methods')==list(M) and result.get('sanitizer_findings')==[]
  and result.get('volume_cleanup_ok') is True and result.get('descriptor_cleanup_ok') is True
  and result.get('active_conflicts_after')==[] and result.get('process_stop',{}).get('live')==[]
  and result.get('process_stop',{}).get('forced') is False and result.get('transient_service',{}).get('unresolved')==[]
  and result.get('transient_service',{}).get('forced')==[] and not result.get('errors')
  and result.get('restored_owner_sha256')==BASE and result.get('interrupted') is None)

def validate_debug(record,host):
 if (record.get('status')!='FOCUSED_PASS_UNQUALIFIED' or record.get('host')!=host or record.get('profile')!='debug'
     or record.get('source')!=SRC or record.get('fixture_sha256')!=TEST or record.get('launcher_sha256')!=h(__file__)
     or record.get('inventory_sha256')!=P['debug']['inv'] or not successful(record)):
  raise Refusal('SAN needs same-host reviewed Debug pass with complete cleanup')
def verify_fixture_source(overlay,expected_sha,commit):
 manifest=overlay/'manifest.json'
 if h(manifest)!=expected_sha:raise Refusal('source manifest mismatch')
 record=json.loads(manifest.read_text())
 if record.get('source_commit')!=commit:raise Refusal('fixture commit mismatch')
 seen=set()
 for row in record['files']:
  rel=row['path']
  if not isinstance(rel,str) or Path(rel).is_absolute() or '..' in Path(rel).parts or rel in seen:raise Refusal('unsafe source inventory path')
  seen.add(rel);path=overlay/rel
  if path.is_symlink() or not path.is_file() or path.stat().st_mode&0o222:raise Refusal('fixture source is not frozen')
  data=path.read_bytes()
  if hashlib.sha256(data).hexdigest()!=row['sha256'] or len(data)!=row['bytes']:raise Refusal('fixture source bytes mismatch')
  if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=row['git_oid']:raise Refusal('fixture Git blob mismatch')
 actual=set()
 for path in overlay.rglob('*'):
  if path.is_symlink() or path.stat().st_mode&0o222:raise Refusal('fixture contains writable path or symlink')
  if path.is_file() and path!=manifest:actual.add(path.relative_to(overlay).as_posix())
 if actual!=seen or overlay.stat().st_mode&0o222:raise Refusal('fixture source coverage mismatch')


def verify(a,p,host):
 payload=Path(p['payload']).resolve(strict=True);src=Path(SRC).resolve(strict=True)
 verify_fixture_source(src.parent,a.source_manifest_sha256,a.fixture_commit)
 pre=Path(p['pre']).resolve(strict=True);runner=Path(p['runner']).resolve(strict=True);receipt=a.preflight_receipt.resolve(strict=True)
 if h(pre)!=p['pre_sha'] or h(receipt)!=a.preflight_sha256:raise Refusal('preflight script or receipt hash mismatch')
 x=json.loads(receipt.read_text())
 if (x.get('status')!='preflight-pass' or x.get('qualification')!='UNQUALIFIED' or x.get('tests')!='UNRUN' or x.get('host')!=host or Path(x.get('overlay',''))!=payload or x.get('inventory_sha256')!=p['inv'] or x.get('source_commit')!='1a1da1f60b491992f7cb1788ce5da4d8dc7302cb' or x.get('source_tree')!='415b3f04ece25a9510c769e7c51cce69089339a5'):
  raise Refusal('preflight receipt does not pin the frozen unqualified payload')
 b=payload/'build';pack=payload/'source/spatial-os-luna-handoff';owner=b/'spatial-storage-owner'
 if h(payload/'inventory.json')!=p['inv'] or h(runner)!=RUNNER:raise Refusal('inventory/canonical runner hash mismatch')
 if h(owner)!=p['owner'] or owner.stat().st_size!=p['size'] or not os.access(owner,os.X_OK):raise Refusal('owner profile ELF mismatch')
 if not (b/'python').is_dir():raise Refusal('generated Python bindings missing')
 if h(src/'tests/native/test_storage_session.py')!=TEST or h(pack/'tests/native/test_storage_session.py')!=PACKAGED_TEST:raise Refusal('fixture test source hash mismatch')
 if h(src/'tools/bootstrap_storage.py')!=BOOT or h(pack/'tools/bootstrap_storage.py')!=BOOT:raise Refusal('bootstrap source hash mismatch')
 for rel in ('tools/with_resource_delegate.py','tools/with_adapter_resources.py'):
  if h(src/rel)!=h(pack/rel):raise Refusal('fixture source/package mismatch: '+rel)
 for n in B:
  if not os.access(b/n,os.X_OK):raise Refusal('registered helper missing: '+n)
 wrapper=x.get('xace',{}).get('wrapper',{})
 server=wrapper.get('path')
 if not server or not wrapper.get('executable') or not os.access(server,os.X_OK) or h(server)!=wrapper.get('sha256'):raise Refusal('preflight XACE wrapper identity mismatch')
 return payload,src,b,owner,runner,receipt,x,server
def registration(src,b,server,evidence,graphics):
 py='/usr/bin/python3'
 env=['PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME=/home/dflanag3','USER=dflanag3','LOGNAME=dflanag3','XDG_RUNTIME_DIR=/run/user/1000',
 'PYTHONPATH='+str(b/'python')+':'+str(src),'SPATIAL_TEST_XSERVER='+str(server),'SPATIAL_SECURITY_EVIDENCE='+str(evidence),
 'PYTHONDONTWRITEBYTECODE=1','LSAN_OPTIONS=','ASAN_OPTIONS=detect_leaks=1:abort_on_error=1','UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1']
 return ['env','-i',*env,py,str(src/'tools/with_resource_delegate.py'),'--native','--',py,str(src/'tools/with_adapter_resources.py'),'--','env',*[k+'='+v for k,v in sorted(graphics.items())],py,str(src/'tests/native/test_storage_session.py'),str(src),*(str(b/n) for n in B)]
def fixture(evidence,m):
 d=evidence/m;r=dict(method=m,directory=d.is_dir(),descriptor_receipt=False,volume_receipt=False,mount_source=None,loop_associations=[],policy_directory_exists=None)
 if not d.is_dir():return r
 dp=d/'fixture-descriptors.json';desc=None
 if dp.is_file():
  desc=json.loads(dp.read_text())
  if set(desc)!={'before_collection','collected','after_collection'} or not isinstance(desc['after_collection'],list):raise Refusal('fixture-descriptors schema mismatch')
  r.update(descriptor_receipt=True,open_descriptors=len(desc['after_collection']))
 sp=d/'volume-setup.json';rec=None;vol=d/'volume';img=d/'volume.img'
 if sp.is_file():
  rec=json.loads(sp.read_text())
  if not {'status','mounted','loop','policy_directory','commands'}.issubset(rec) or not isinstance(rec['commands'],list):raise Refusal('volume-setup schema mismatch')
  r.update(volume_receipt=True,receipt_status=rec['status'],receipt_mounted=rec['mounted'],receipt_cleanup_confirmed=rec.get('cleanup_confirmed'),receipt_loop_attached=rec.get('loop_attached'))
  if rec.get('loop') is not None and not re.fullmatch(r'/dev/loop[0-9]+',rec['loop']):raise Refusal('invalid loop receipt')
  policy=rec.get('policy_directory')
  if policy is not None:
   if not re.fullmatch(r'/run/spatial-m03-native-storage-[a-f0-9]{32}',policy):raise Refusal('invalid policy path')
   r['policy_directory_exists']=Path(policy).exists()
 if vol.exists():
  q=root(['findmnt','--mountpoint',str(vol),'-n','-o','SOURCE'],20)
  if q.returncode==0:r['mount_source']=q.stdout.strip()
  elif q.returncode!=1:raise Refusal('mountpoint inspection failed')
 if img.exists():
  q=root(['losetup','-j',str(img)],20)
  if q.returncode not in (0,1):raise Refusal('loop inspection failed')
  r['loop_associations']=[v.split(':',1)[0] for v in q.stdout.splitlines() if v.strip()]
 r['descriptor_cleanup_ok']=bool(desc is not None and desc.get('after_collection')==[])
 r['volume_cleanup_ok']=bool(rec is not None and rec.get('cleanup_confirmed') is True and rec.get('mounted') is False and rec.get('loop_attached') is False and r['mount_source'] is None and not r['loop_associations'] and r['policy_directory_exists'] is False)
 return r

def restore_original(src,profile_sha,backup,baseline,out):
 current=ident()
 if current['sha']==profile_sha:
  if not backup.is_file() or h(backup)!=BASE:raise Refusal('verified original helper backup missing')
  restore=root(['/usr/bin/python3',str(src/'tools/bootstrap_storage.py'),'--owner-binary',str(backup),'--apply','--replace-known-sha256',profile_sha],35)
  (out/'restore.stdout').write_text(restore.stdout);(out/'restore.stderr').write_text(restore.stderr)
  if restore.returncode:raise Refusal('helper restoration failed')
 if ident()!=baseline:raise Refusal('restored helper identity mismatch')


def main():
 global ACTIVE,INTERRUPTED
 ap=argparse.ArgumentParser();ap.add_argument('--profile',choices=P,required=True);ap.add_argument('--preflight-receipt',type=Path,required=True);ap.add_argument('--preflight-sha256',required=True);ap.add_argument('--run-dir',type=Path,required=True);ap.add_argument('--debug-result',type=Path);ap.add_argument('--source-manifest-sha256',required=True);ap.add_argument('--fixture-commit',required=True);a=ap.parse_args()
 host=socket.gethostname().split('.')[0]
 if host not in ('sb16','sb16-2') or os.getuid()!=pwd.getpwnam('dflanag3').pw_uid or os.getuid()!=1000:raise Refusal('only dflanag3 UID 1000 on test hosts')
 if a.profile=='san':
  if not a.debug_result:raise Refusal('SAN requires Debug result')
  debug=json.loads(a.debug_result.read_text());validate_debug(debug,host)
  if debug.get('fixture_commit')!=a.fixture_commit or debug.get('source_manifest_sha256')!=a.source_manifest_sha256:raise Refusal('SAN fixture identity differs from Debug')
 p=P[a.profile]
 payload,src,b,owner,runner,receipt,pre,server=verify(a,p,host)
 graphics=pre['xace'].get('wrapper_environment',{})
 if not isinstance(graphics,dict) or any(k not in ('LD_LIBRARY_PATH','__EGL_VENDOR_LIBRARY_FILENAMES','LIBGL_DRIVERS_PATH') or not isinstance(v,str) for k,v in graphics.items()):raise Refusal('unexpected graphics environment')
 out=a.run_dir.absolute();art=Path('/home/dflanag3/spatial-artifacts').resolve(strict=True)
 if out.exists() or out.is_symlink() or art not in out.resolve().parents or payload in out.resolve().parents:raise Refusal('run path must be new and outside payload')
 if __import__('shutil').disk_usage(art).free<6*1024**3:raise Refusal('less than 6 GiB for seven temporary volumes')
 out.mkdir(mode=0o700,parents=True);evidence=out/'spatial_security_evidence';evidence.mkdir(mode=0o700)
 cmd=registration(src,b,server,evidence,graphics)
 direct=['bash',str(runner),str(b),str(out/'canonical-run'),'timeout','--signal=TERM','--kill-after=20s','180s',*cmd]
 result=dict(status='UNRUN',host=host,profile=a.profile,source=SRC,fixture_sha256=TEST,launcher_sha256=h(__file__),inventory_sha256=p['inv'],fixture_commit=a.fixture_commit,source_manifest_sha256=a.source_manifest_sha256,errors=[])
 save(out/'test-plan.json',dict(**result,methods=list(M),runner_argv=direct,preflight=str(receipt),preflight_sha256=a.preflight_sha256,purpose='Seven session-storage-owner controls; require terminal assertions, no sanitizer diagnostics, process/volume cleanup and exact helper restoration. Not M08 qualification.'))
 fd=os.open(L,os.O_CREAT|os.O_RDWR,0o600)
 try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BaseException:os.close(fd);raise
 backup=T.with_name(T.name+'.previous-'+BASE);proc=None;baseline=dict(sha=BASE,uid=0,gid=0,mode=0o755,size=13841840)
 for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,sig_handler)
 try:
  if ident()!=baseline:raise Refusal('installed helper baseline mismatch')
  if conflicts():raise Refusal('another storage owner or suite is active')
  if backup.exists() and (h(backup)!=BASE or backup.stat().st_uid!=0 or stat.S_IMODE(backup.stat().st_mode)!=0o755):raise Refusal('baseline recovery backup mismatch')
  inst=root(['/usr/bin/python3',str(src/'tools/bootstrap_storage.py'),'--owner-binary',str(owner),'--apply','--replace-known-sha256',BASE],35)
  (out/'install.stdout').write_text(inst.stdout);(out/'install.stderr').write_text(inst.stderr)
  if inst.returncode or not backup.is_file() or h(backup)!=BASE:raise Refusal('installation or baseline backup failed')
  if ident()!=dict(sha=p['owner'],uid=0,gid=0,mode=0o755,size=p['size']):raise Refusal('profile helper identity mismatch')
  if INTERRUPTED is not None:raise Refusal('interrupted before test start')
  with (out/'launcher.stdout.log').open('wb') as so,(out/'launcher.stderr.log').open('wb') as se:
   proc=subprocess.Popen(direct,cwd=b,start_new_session=True,stdout=so,stderr=se);ACTIVE=proc
   result['test_pid']=proc.pid;save(out/'started.json',result)
   result['test_exit']=proc.wait(timeout=210)
 except BaseException as error:
  result['errors'].append(repr(error))
 finally:
  # Never restore a helper while an owned runtime process may still use it.
  try:
   result['process_stop']=kill_group(proc);ACTIVE=None
   result['transient_service']=stop_units(out)
   result['active_conflicts_after']=conflicts()
   if result['process_stop']['live'] or result['transient_service']['unresolved'] or result['active_conflicts_after']:
    raise Refusal('runtime cleanup uncertain; helper retained and subsequent runs blocked')
   for method in M:
    row=evidence/method
    if (row/'volume-setup.json').exists():
     state=json.loads((row/'volume-setup.json').read_text())
     if state.get('cleanup_confirmed') is not True:
      cleanup=root(['/usr/bin/python3','-B',str(src/'tests/native/storage_session_volume.py'),'cleanup',str(b),str(row),str(evidence)],60)
      (row/'launcher-cleanup.log').write_text(cleanup.stdout+cleanup.stderr)
      result['errors'].append('Fixture needed external cleanup: '+method)
      if cleanup.returncode:raise Refusal('fixture recovery cleanup failed')
   restore_original(src,p['owner'],backup,baseline,out)
   result['restored_owner_sha256']=BASE
   save(out/'restoration.json',dict(status='VERIFIED',**baseline))
  except BaseException as error:
   result['errors'].append(repr(error));save(out/'restoration.json',dict(status='FAILED',error=repr(error)))
  finally:
   ACTIVE=None;result['interrupted']=INTERRUPTED
   try:
    cr=out/'canonical-run'
    for name,key in [('command.exit','command_exit'),('wrapper.exit','wrapper_exit'),('terminal-status.txt','terminal_status')]:result[key]=(cr/name).read_text().strip()
    result.update(inspect_logs(out))
    rows=[fixture(evidence,method) for method in M]
    save(out/'fixture-cleanup.json',rows)
    result['volume_cleanup_ok']=all(x.get('volume_cleanup_ok',False) for x in rows)
    result['descriptor_cleanup_ok']=all(x.get('descriptor_cleanup_ok',False) for x in rows)
   except BaseException as error:result['errors'].append(repr(error))
   result['status']='FOCUSED_PASS_UNQUALIFIED' if successful(result) else 'FAILED_OR_INCOMPLETE'
   save(out/'launcher-result.json',result);os.close(fd)
 return 0 if result['status']=='FOCUSED_PASS_UNQUALIFIED' else 1

if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as error:print('launcher error:',repr(error),file=sys.stderr);raise SystemExit(1)
