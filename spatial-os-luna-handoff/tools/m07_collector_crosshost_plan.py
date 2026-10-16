#!/usr/bin/env python3
"""Create the fixed M07 collector-continuation crosshost plan after accepted combined local evidence."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

CANDIDATE = "1e58e65dec132a0a16f004ca4527034dca081f77"
PIN_PATH = "/home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01/metadata/package-pin.json"
PIN_SHA = "e877bf02d54091f8aeaa7a60b8817b888a49c2c0e1618142d3107f06212c6fa4"
INNER_SHA = "07ff616e2d3560b1326438d640055cd3b25ff8c4c494d5e0e72b79618f7cd728"
OUTER_SHA = "67f1752a40681f23f91251f6bad692b4922b60732f3d1ab20cb8958f7f8e8462"
ARCHIVE_SHA = "05ac740d7b234e307c4fc69895b30244bb9d4a525698c6772afe115576682cc7"
INNER_PATH = '/home/dflanag3/spatial-artifacts/m07-fixture-repaired-gate-preparation-20261016/packages/1e58e65d-prepare-01/provenance.json'
DEPLOY = "/home/dflanag3/spatial-artifacts/deployments/m07-1e58e65d/runtime"
PROV_SHA = OUTER_SHA
RECEIPTS = {
    "sb16": "b96c62fd9a6ab0dec30e854f046b6201f89756fddf0ab72e291cdae7de22c75e",
    "sb16-2": "e61c19fdaf7a5ef8a75edd061f9d73ffd39ee1bd5b11c0c3d86fb07dcffabd65",
}
RUNNER_SHA = "7053d5ffe05e7050b035d6b715b794dd742b68ee29b9198a703979391323f537"
RUNNER_BASE = "/home/dflanag3/spatial-artifacts/deployments/m07-1e58e65d/deploy-input-01"
REPORTS = {
    "sb16": DEPLOY + "/evidence/1e58e65d-sb16-local-01/combined-local.json",
    "sb16-2": DEPLOY + "/evidence/1e58e65d-sb16-2-local-01/combined-local.json",
}
PROFILES = ("Debug", "ASan-UBSan")
HOSTS = ("sb16", "sb16-2")
GIB = 1024 ** 3


BASELINE = Path('/home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01/candidate')
BASELINE_PINS = {
    'prepare_crosshost_plan.py': 'fba21bd6bc7d658706b2094043eecb6248bba664d76cc9b4257d46abdec682e5',
    'execute_workspace_crosshost_serial.py': '3a1e43610fd6dd1d2b6c2571a88c6ef74faa6c02a8d8d496e3d48d1321ddbe3d',
    'launch_m06_crosshost.sh': '1d8a55904453d6c67723867573449ee730aea3166410c93553ac741b8b192b69',
}
CONTEXT_PATH = '/home/dflanag3/spatial-artifacts/m07-collector-continuation-inputs-20261016/attempt-01/inputs-sanitizer.json'
TOOL_PINS = {
    'm06_deployed_gate.py': 'd157bfa9e723b2924186338157bdf41f1ff1e3d771c163f9f5a4ddd0007fc298',
    'm07_deployed_gate.py': '15c6eb522bcbc5406b93c8d3d472c6c85a0c34f170ac6de4553fc70bf993580d',
    'm07_collector_continuation.py': '57b3941fcf204643963936f4d24ac0b0ddfd1d18954533daed180a27d3a4ddc2',
}

def verify_baseline():
    for name, expected in BASELINE_PINS.items():
        path = BASELINE / name
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise RuntimeError('frozen baseline dependency changed: ' + name)
    return {str(BASELINE / name): value for name, value in BASELINE_PINS.items()}

def exact_pairs(values, label):
    if len(values) != 2 or {key for key, value in values} != set(HOSTS):
        raise RuntimeError(label + ' must cover both hosts exactly once')
    return dict(values)

def proof_pins(values, label):
    import re
    result = exact_pairs(values, label)
    if any(not re.fullmatch('[0-9a-f]{64}', value) for value in result.values()):
        raise RuntimeError(label + ' requires lowercase SHA256 pins')
    return result

QUALIFIED_METADATA_CHECK = r'''
def validate_local_runner_dir(actual, output_root, host, attempt):
 expected=pathlib.Path(output_root)/'full-gate-runtime-runs'/host/attempt
 if pathlib.Path(actual) != expected: raise RuntimeError('canonical local runner path differs')
 return expected
def validate_local_attempt(report_path, report_obj, host, candidate):
 expected='1e58e65d-'+host+'-local-01'
 if report_path.parent.name != report_obj.get('attempt') or report_obj.get('attempt') != expected or report_obj.get('candidate') != candidate:
  raise RuntimeError('local attempt path/name/candidate mismatch')
 return expected
def qualified_metadata(root, provenance, report, report_path, profile, query=None):
 import hashlib,json,pathlib,re,subprocess
 fail=lambda text: (_ for _ in ()).throw(RuntimeError(text))
 digest=lambda raw: hashlib.sha256(raw).hexdigest()
 target=report_path.parent/profile;build=root/'build'/profile
 pins=report['profiles'][profile].get('artifact_sha256',{})
 retained={}
 for name in ('metadata-relocation.json','inventory.json','inventory.log'):
  path=target/name
  if path.is_symlink() or not path.is_file():fail('qualified local artifact absent: '+name)
  raw=path.read_bytes()
  if digest(raw)!=pins.get(name):fail('qualified local artifact hash changed: '+name)
  retained[name]=raw
 after=json.loads(retained['metadata-relocation.json']).get('after')
 expected=provenance['profiles'][profile]['ctest_metadata']
 actual={path.relative_to(build).as_posix() for path in build.rglob('CTestTestfile.cmake')}
 if not isinstance(after,dict) or set(after)!=set(expected) or set(after)!=actual:fail('qualified metadata closure differs')
 for rel,want in after.items():
  relative=pathlib.PurePosixPath(rel);path=build/relative
  if relative.is_absolute() or '..' in relative.parts or path.is_symlink() or not re.fullmatch('[0-9a-f]{64}',want):fail('unsafe qualified metadata pin')
  if not path.is_file() or digest(path.read_bytes())!=want:fail('qualified metadata bytes changed')
 inventory=json.loads(retained['inventory.json']);rows=json.loads(retained['inventory.log']).get('tests',[])
 names=[row.get('name') for row in rows]
 if len(names)!=199 or len(set(names))!=199 or inventory.get('names')!=sorted(names) or inventory.get('count')!=199 or inventory.get('sha256')!=digest(retained['inventory.log']):fail('qualified inventory receipt differs')
 if query is None:
  process=subprocess.run(['ctest','--test-dir',str(build),'--show-only=json-v1'],cwd=root/'source',stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
  if process.returncode or process.stderr:fail('current qualified inventory query failed')
  current=process.stdout
 else:current=query(root,profile)
 if current!=retained['inventory.log']:fail('current inventory differs from qualified local run')
 return {'metadata_sha256':digest(retained['metadata-relocation.json']),'inventory_sha256':digest(current),'registrations':199,'metadata_files':len(after)}
'''

CONTINUATION_CHECK = r'''
def _fixed_collector():
 import hashlib,importlib.util,pathlib,stat,types
 here=pathlib.Path('/home/dflanag3/spatial-artifacts/m07-collector-continuation-inputs-20261016/attempt-01/tools')
 pins={'m06_deployed_gate.py':'d157bfa9e723b2924186338157bdf41f1ff1e3d771c163f9f5a4ddd0007fc298','m07_deployed_gate.py':'15c6eb522bcbc5406b93c8d3d472c6c85a0c34f170ac6de4553fc70bf993580d','m07_collector_continuation.py':'57b3941fcf204643963936f4d24ac0b0ddfd1d18954533daed180a27d3a4ddc2'}
 for name,pin in pins.items():
  path=here/name;component=pathlib.Path(path.anchor)
  for part in path.parts[1:]:
   component/=part
   if stat.S_ISLNK(component.lstat().st_mode):raise RuntimeError('fixed auditor path is linked')
  before=path.lstat();raw=path.read_bytes();after=path.lstat()
  identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  if not stat.S_ISREG(before.st_mode) or identity(before)!=identity(after) or hashlib.sha256(raw).hexdigest()!=pin:raise RuntimeError('fixed auditor tool pin changed: '+name)
 def load(name):
  spec=importlib.util.spec_from_file_location('fixed_crosshost_'+name,here/(name+'.py'))
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
 return load('m07_collector_continuation'),load('m06_deployed_gate'),pins

def continuation_preflight(root,host,report_path,context_sha,report_sha,before_sha):
 import hashlib,json,pathlib
 c,g,tool_pins=_fixed_collector()
 fail=lambda message:(_ for _ in ()).throw(RuntimeError(message))
 require=lambda ok,message:None if ok else fail(message)
 require(host in ('sb16','sb16-2') and str(root)=='/home/dflanag3/spatial-artifacts/deployments/m07-1e58e65d/runtime','continuation deployment/host differs')
 expected=root/'evidence'/('1e58e65d-'+host+'-local-01')/'combined-local.json'
 require(report_path==expected,'combined report path differs')
 context_path=pathlib.Path('/home/dflanag3/spatial-artifacts/m07-collector-continuation-inputs-20261016/attempt-01/inputs-sanitizer.json')
 context=c.load_context(g,root,context_path,context_sha)
 require(context.get('tools')==tool_pins and context.get('host')==host,'continuation context/tool identity differs')
 provenance,receipt=c._source_closure(g,root,context)
 out,original,retained=c._origin(g,root,context)
 require(out==expected.parent and original.get('status')=='failed','preserved failed origin differs')
 report=c.pinned_json(report_path,report_sha);amendment=report.get('collector_amendment',{})
 require(report.get('report_kind')=='combined_collector_amendment' and report.get('status')=='local_passed_crosshost_pending'
  and report.get('host')==host and report.get('candidate')==context.get('candidate') and report.get('attempt')==out.name
  and report.get('phase_complete') is False and report.get('selected_profiles')==['Debug','ASan-UBSan']
  and set(report.get('profiles',{}))=={'Debug','ASan-UBSan'},'complete combined local report required')
 require(report.get('source_sha256_rechecked') is True and report.get('postrun_invariant_failures')==[],'combined source/postrun integrity failed')
 for key in c.CHECKS:
  require(set(report.get(key,{}))=={'Debug','ASan-UBSan'} and all(report[key].get(p) is True for p in ('Debug','ASan-UBSan')),'combined profile postcheck failed: '+key)
 require(amendment.get('context_path')==str(context_path) and amendment.get('context_sha256')==context_sha
  and amendment.get('tools')==tool_pins and amendment.get('original_failed_run')==str(out/'run.json')
  and amendment.get('original_failed_run_sha256')==retained['original_run_sha256']
  and amendment.get('batch_id')==context['batch_id'],'combined continuation chain differs')
 require(context['debug_audit']['path']==str(out/'debug-collector-audit.json') and context['debug_audit']['sha256']==amendment.get('debug_audit_sha256'),'accepted Debug audit path/pin differs')
 audit=c.pinned_json(context['debug_audit']['path'],amendment['debug_audit_sha256'])
 require(audit.get('status')=='retained_debug_collector_passed' and audit.get('host')==host
  and audit.get('candidate')==context['candidate'] and audit.get('context_binding')==c._binding(context)
  and audit.get('original_run_sha256')==retained['original_run_sha256'],'accepted Debug audit differs')
 batch=c.pinned_json(out/'sanitizer-batch.json',amendment.get('sanitizer_batch_sha256'))
 require(batch.get('status')=='profile_batch_passed' and batch.get('report_kind')=='profile_batch'
  and batch.get('selected_profiles')==['ASan-UBSan'] and set(batch.get('profiles',{}))=={'ASan-UBSan'}
  and batch.get('candidate')==context['candidate'] and batch.get('host')==host and batch.get('attempt')==out.name
  and batch.get('batch_id')==context['batch_id'] and batch.get('phase_complete') is False
  and batch.get('source_sha256_rechecked') is True and batch.get('postrun_invariant_failures')==[]
  and batch.get('continuation',{}).get('context_sha256')==context_sha,'complete actual sanitizer batch required')
 for key in c.CHECKS:require(batch.get(key,{}).get('ASan-UBSan') is True,'sanitizer postcheck failed: '+key)
 for key in ('requirement_suites','required_tests','dedicated_crosshost','bundle_sha256'):
  require(batch.get(key)==report.get(key)==original.get(key),'continuation requirement/bundle differs: '+key)
 terminal=c._batch_runner(g,amendment['batch_runner'],root,context,context_path,context_sha)
 require(terminal.get('path')==str(c.runner_directory(host,context['batch_id'])),'new sanitizer runner differs')
 before=c.pinned_json(out/'before-crosshost-artifact-audit.json',before_sha)
 require(before.get('schema')==c.SCHEMA and before.get('status')=='primary_artifact_recheck_passed'
  and before.get('phase')=='before-crosshost' and before.get('phase_complete') is False and before.get('host')==host
  and before.get('candidate')==context['candidate'] and before.get('combined_local_sha256')==report_sha
  and before.get('context_sha256')==context_sha and before.get('tools')==tool_pins
  and set(before.get('profiles',{}))=={'Debug','ASan-UBSan'},'accepted before-crosshost artifact audit differs')
 for profile in ('Debug','ASan-UBSan'):
  row=report['profiles'][profile]
  require(row.get('status')=='passed_needs_crosshost_and_review','combined profile incomplete: '+profile)
  expected_manifest=dict(sha256=row.get('artifact_sha256'),exclusions=row.get('artifact_exclusions'),artifact_links=row.get('artifact_links'))
  require(all(value is not None for value in expected_manifest.values()),'typed artifact manifest missing: '+profile)
  if profile=='Debug':require(expected_manifest==audit['manifest'],'accepted Debug full manifest differs')
  else:
   batch_row=batch['profiles'][profile]
   require(expected_manifest==dict(sha256=batch_row.get('artifact_sha256'),exclusions=batch_row.get('artifact_exclusions'),artifact_links=batch_row.get('artifact_links')),'sanitizer batch artifact manifest differs')
  _,actual=c.audit_profile(g,root,provenance,report,profile,expected_manifest)
  require(actual==expected_manifest,'fresh complete artifact manifest differs: '+profile)
  summary=dict(regular_files=len(actual['sha256']),artifact_links=actual['artifact_links'],manifest_sha256=hashlib.sha256(json.dumps(actual,sort_keys=True).encode()).hexdigest())
  require(before['profiles'][profile]==summary,'before audit full artifact/link manifest differs: '+profile)
 return dict(status='CONTINUATION_READY_FOR_CROSSHOST',context_path=str(context_path),context_sha256=context_sha,
  combined_sha256=report_sha,before_artifact_audit_sha256=before_sha,batch_id=context['batch_id'],runner_dir=terminal['path'],
  original_failed_run_sha256=retained['original_run_sha256'],debug_audit_sha256=amendment['debug_audit_sha256'],tools=tool_pins,
  after_crosshost_audit_required=str(out/'after-crosshost-artifact-audit.json'))
'''
REMOTE_VALIDATE = QUALIFIED_METADATA_CHECK + CONTINUATION_CHECK + r'''
import hashlib,json,pathlib,runpy,sys,fcntl,os
gate_lock=open('/run/user/'+str(os.getuid())+'/spatial-m06-gate.lock','a')
fcntl.flock(gate_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
host,candidate,report,deploy,runner_dir,runner_path,runner_sha,audit_path,context_sha,report_sha,before_sha,audit_sha=sys.argv[1:]
root=pathlib.Path(deploy); report_path=pathlib.Path(report)
continuation=continuation_preflight(root,host,report_path,context_sha,report_sha,before_sha)
m=runpy.run_path(str(root/'source/tools/m06_deployed_gate.py'))
sha=lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
prov=json.loads((root/'provenance.json').read_text()); receipt=json.loads((root/'deployment-receipt.json').read_text())
if prov.get('candidate')!=candidate or receipt.get('candidate')!=candidate or receipt.get('host')!=host or receipt.get('status')!='verified': raise SystemExit('deployment candidate/host/status mismatch')
if sha(root/'provenance.json')!='67f1752a40681f23f91251f6bad692b4922b60732f3d1ab20cb8958f7f8e8462' or sha(root/'deployment-receipt.json')!=({'sb16':'b96c62fd9a6ab0dec30e854f046b6201f89756fddf0ab72e291cdae7de22c75e','sb16-2':'e61c19fdaf7a5ef8a75edd061f9d73ffd39ee1bd5b11c0c3d86fb07dcffabd65'}[host]): raise SystemExit('deployment receipt pin mismatch')
actual={}
for p in (root/'source').rglob('*'):
 if p.is_symlink() or p.stat().st_mode & 0o222: raise SystemExit('deployed source is linked or writable: '+str(p))
 if p.is_file(): actual[p.relative_to(root/'source').as_posix()]=sha(p)
if actual!=prov['source_sha256'] or len(actual)!=1040: raise SystemExit('exact deployed source closure mismatch')
report_obj=json.loads(report_path.read_text())
validate_local_attempt(report_path,report_obj,host,candidate)
if report_obj.get('status')!='local_passed_crosshost_pending' or report_obj.get('phase_complete') is not False or report_obj.get('host')!=host or report_obj.get('candidate')!=candidate: raise SystemExit('local report is not the exact terminal pending candidate')
if report_obj.get('source_sha256_rechecked') is not True or report_obj.get('postrun_invariant_failures') not in ([],None): raise SystemExit('source/postrun integrity failed')
for key in ('profile_artifacts_rechecked','production_off_rechecked','runtime_rechecked','metadata_rechecked','storage_evidence_rechecked'):
 values=report_obj.get(key,{})
 if set(values)!=set(('Debug','ASan-UBSan')) or any(values.get(p) is not True for p in ('Debug','ASan-UBSan')): raise SystemExit('post-run check failed: '+key)
for profile in ('Debug','ASan-UBSan'):
 row=report_obj.get('profiles',{}).get(profile,{})
 if row.get('status')!='passed_needs_crosshost_and_review': raise SystemExit('profile did not pass: '+profile)
 if not row.get('artifact_sha256') or row.get('artifact_exclusions') is None: raise SystemExit('profile artifact evidence is incomplete: '+profile)
 commands={x.get('name'):x for x in row.get('commands',[])}
 for name in ('inventory','production-off-startup','harness','core','P10','bounded-storage-git'):
  c=commands.get(name,{})
  if c.get('exit_code')!=0 or c.get('assertions_passed') is not True: raise SystemExit('command assertion failed: '+profile+'/'+name)
 if commands['harness'].get('expected_51') is not True: raise SystemExit('harness assertion missing')
 core=commands['core']
 if core.get('all_registered_present') is not True or core.get('failed_or_skipped') is not False: raise SystemExit('CTest completeness assertion failed')
 p10=commands['P10'].get('acceptance',{})
 if (p10.get('status')!='passed' or p10.get('through')!='P10' or p10.get('selected')!=151 or p10.get('executed')!=151 or p10.get('total_baseline')!=151 or p10.get('not_selected')!=0 or p10.get('diagnostic_only') is not False or commands['P10'].get('acceptance_rows_valid') is not True): raise SystemExit('locked P10 assertion failed')
 bs=pathlib.Path(report_path.parent/profile/'bounded-storage-git/review.json')
 if not bs.is_file(): raise SystemExit('bounded storage review absent')
 b=json.loads(bs.read_text())
 if b.get('status')!='passed' or len(b.get('results',{}))!=5 or not all(x.get('passed') for x in b['results'].values()): raise SystemExit('bounded storage review failed')
# The production validator checks the complete profile inventory and all core rows.
m['_validate_local_run'](report_path,host,candidate)
qualified={profile:qualified_metadata(root,prov,report_obj,report_path,profile) for profile in ('Debug','ASan-UBSan')}
# The reviewed continuation helper rechecks both actual canonical runners and launch/PID pins.
rdir=pathlib.Path(runner_dir)
if str(rdir)!=continuation['runner_dir']: raise SystemExit('selected sanitizer runner path differs')
if (rdir/'stderr.log').read_bytes(): raise SystemExit('canonical sanitizer runner stderr is not empty')
if pathlib.Path(audit_path).is_symlink() or sha(audit_path)!=audit_sha: raise SystemExit('primary local audit hash changed')
audit=json.loads(pathlib.Path(audit_path).read_text())
if audit.get('status')!='PRIMARY_LOCAL_FULL_GATE_EVIDENCE_ACCEPTED' or audit.get('candidate')!=candidate or audit.get('host')!=host or audit.get('run_json_sha256')!=sha(report_path) or audit.get('deployment_receipt_sha256')!=sha(root/'deployment-receipt.json') or audit.get('crosshost')!='PENDING' or audit.get('phase_complete') is not False: raise SystemExit('primary local audit receipt does not pin this terminal run')
if set(audit.get('profiles',{}))!=set(('Debug','ASan-UBSan')): raise SystemExit('primary audit profiles incomplete')
for profile,summary in audit['profiles'].items():
 if summary.get('startup')!=5 or summary.get('harness')!=51 or summary.get('core')!=197 or summary.get('locked_P10')!=151 or summary.get('bounded_storage_scenarios')!=5: raise SystemExit('primary audit profile counts mismatch')
if hashlib.sha256(pathlib.Path(runner_path).read_bytes()).hexdigest()!=runner_sha: raise SystemExit('installed canonical runner hash mismatch')
# Refuse while any production local/full or crosshost gate process remains live.
for proc in pathlib.Path('/proc').iterdir():
 if not proc.name.isdigit(): continue
 try: cmd=(proc/'cmdline').read_bytes().replace(b'\\0',b' ').decode(errors='replace')
 except OSError: continue
 if ('m07_deployed_gate.py' in cmd and 'run-local' in cmd) or ('m06_deployed_gate.py' in cmd and 'run-local' in cmd): raise SystemExit('a local gate process remains live: '+cmd[:300])
import shutil
if shutil.disk_usage(root).free < 26*1024**3: raise SystemExit('less than 26 GiB free after local cleanup')
print(json.dumps({'host':host,'candidate':candidate,'report_sha256':sha(report_path),'runner_dir':str(rdir),'deployment_provenance_sha256':sha(root/'provenance.json'),'deployment_receipt_sha256':sha(root/'deployment-receipt.json'),'local_status':report_obj['status'],'profiles':{p:report_obj['profiles'][p]['status'] for p in ('Debug','ASan-UBSan')},'source_integrity':True,'postrun_integrity':True,'qualified_metadata':qualified,'runner_terminal':'COMPLETE','primary_audit_sha256':sha(audit_path),'free_bytes':shutil.disk_usage(root).free,'collector_continuation':continuation},sort_keys=True))
'''

def select_runtime_profiles(provenance, host):
    """Select only this host's per-profile runtime records."""
    hosts = provenance["runtime_manifest"]["hosts"]
    return {profile: hosts[host][profile] for profile in PROFILES}

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_pins(source):
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=CANDIDATE: raise RuntimeError('tool checkout is not the frozen candidate')
    if subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=no'],text=True).strip(): raise RuntimeError('tracked tool source changed')
    inner=Path(INNER_PATH)
    if digest(inner)!=INNER_SHA: raise RuntimeError('reviewed inner provenance hash changed')
    provenance=json.loads(inner.read_bytes());expected=provenance['source_sha256']
    if provenance.get('candidate')!=CANDIDATE or len(expected)!=1040: raise RuntimeError('candidate source closure differs')
    base=source/'spatial-os-luna-handoff'
    schemas={p.relative_to(base).as_posix():p for p in base.rglob('*.proto')}
    if set(schemas)!={name for name in expected if name.endswith('.proto')}: raise RuntimeError('versioned schema closure differs')
    paths={**schemas,**{name:base/name for name in ('tools/m06_deployed_gate.py','tools/m07_deployed_gate.py')}}
    if any(digest(path)!=expected[name] for name,path in paths.items()): raise RuntimeError('candidate tool/schema bytes differ')
    return {str(path):expected[name] for name,path in paths.items()}

def ssh_json(host, code, args):
    encoded = __import__('base64').b64encode(code.encode()).decode()
    py = 'import base64;exec(compile(base64.b64decode(' + repr(encoded) + '),"<offline-preflight>","exec"))'
    argv = ['python3','-B','-c',py,*map(str,args)]
    command = ['ssh','-i','/home/dflanag3/.ssh/id_ed25519_spatial_tests','-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=8',host,shlex.join(argv)]
    result = subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=180)
    if result.returncode:
        raise RuntimeError(host+' preflight refused: '+result.stderr[-2000:]+result.stdout[-1000:])
    return json.loads(result.stdout)

def parse_pair(value):
    if '=' not in value: raise argparse.ArgumentTypeError('expected HOST=VALUE')
    return tuple(value.split('=',1))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',type=Path,default=Path('/home/dflanag3/src/spacial-m07-fixture-repaired-crosshost-preparation-20261016'))
    p.add_argument('--artifact-root',type=Path,default=Path('/home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01'))
    p.add_argument('--attempt',required=True,help='fresh unique plan attempt label')
    p.add_argument('--runner-dir',action='append',type=parse_pair,required=True,metavar='HOST=REMOTE_DIR')
    p.add_argument('--audit-receipt',action='append',type=parse_pair,required=True,metavar='HOST=JSON_PATH')
    for name in ('context-sha256','combined-sha256','before-audit-sha256','audit-sha256'):
        p.add_argument('--'+name, action='append', type=parse_pair, required=True, metavar='HOST=SHA256')
    p.add_argument('--address',action='append',type=parse_pair,required=True,metavar='HOST=IPV4')
    a=p.parse_args()
    if re_fullhex(a.attempt) is False: raise RuntimeError('attempt must use safe [A-Za-z0-9._-] characters')
    source=a.source_root.resolve(strict=True)
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=CANDIDATE: raise RuntimeError('planning checkout is not the frozen candidate')
    if subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=no'],text=True).strip(): raise RuntimeError('tracked source changed')
    pin=Path(PIN_PATH)
    if digest(pin)!=PIN_SHA: raise RuntimeError('reviewed package pin hash changed')
    pin_obj=json.loads(pin.read_text())
    if pin_obj.get('candidate')!=CANDIDATE or pin_obj.get('inner_provenance_sha256')!=INNER_SHA or pin_obj.get('outer_provenance_sha256')!=OUTER_SHA or pin_obj.get('archive_sha256')!=ARCHIVE_SHA or pin_obj.get('status')!='PRIMARY_PACKAGE_CONTENT_REVIEW_PASS': raise RuntimeError('package pin candidate/provenance/status mismatch')
    baseline=verify_baseline()
    tools=source_pins(source)
    runner_dirs=exact_pairs(a.runner_dir,'runner directories'); addresses=exact_pairs(a.address,'addresses'); audits=exact_pairs(a.audit_receipt,'primary audits')
    contexts=proof_pins(a.context_sha256,'contexts'); combined=proof_pins(a.combined_sha256,'combined reports')
    before=proof_pins(a.before_audit_sha256,'before audits'); audit_pins=proof_pins(a.audit_sha256,'primary audits')
    if set(runner_dirs)!=set(HOSTS) or set(addresses)!=set(HOSTS) or set(audits)!=set(HOSTS): raise RuntimeError('runner dirs, primary audit receipts, and addresses must cover both hosts exactly')
    for host in HOSTS:
        ip=ipaddress.ip_address(addresses[host])
        if ip.version!=4 or ip.is_loopback or ip.is_unspecified: raise RuntimeError(host+' address must be a reachable non-loopback IPv4 address')
    checked={}
    for host in HOSTS:
        runner=RUNNER_BASE+'/run-command.sh'
        checked[host]=ssh_json(host,REMOTE_VALIDATE,[host,CANDIDATE,REPORTS[host],DEPLOY,runner_dirs[host],runner,RUNNER_SHA,audits[host],contexts[host],combined[host],before[host],audit_pins[host]])
        reply=checked[host]
        if reply.get('host')!=host or reply.get('candidate')!=CANDIDATE or reply.get('source_integrity') is not True or reply.get('postrun_integrity') is not True or reply.get('profiles')!={profile:'passed_needs_crosshost_and_review' for profile in PROFILES} or set(reply.get('qualified_metadata',{}))!=set(PROFILES) or any(reply['qualified_metadata'][profile].get('registrations')!=199 for profile in PROFILES):
            raise RuntimeError(host+' returned authority/inventory summary differs')
        if checked[host].get('report_sha256') != combined[host] or checked[host].get('primary_audit_sha256') != audit_pins[host]:
            raise RuntimeError(host+' returned proof pins differ')
        chain=checked[host].get('collector_continuation',{})
        if chain.get('status')!='CONTINUATION_READY_FOR_CROSSHOST' or chain.get('context_sha256')!=contexts[host] or chain.get('before_artifact_audit_sha256')!=before[host] or chain.get('combined_sha256')!=combined[host] or chain.get('runner_dir')!=runner_dirs[host]:
            raise RuntimeError(host+' continuation chain is incomplete')
        if checked[host].get('runner_terminal')!='COMPLETE' or checked[host].get('local_status')!='local_passed_crosshost_pending': raise RuntimeError(host+' is not terminal and ready')
    plan_path=a.artifact_root/('plan-'+a.attempt)/'m06-crosshost-plan.json'
    prepared=plan_path.parent
    if plan_path.exists() or plan_path.is_symlink() or prepared.exists() or prepared.is_symlink(): raise RuntimeError('plan output already exists')
    evidence_root=Path('/home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01/evidence')
    m06_output=evidence_root/('m06-crosshost-'+a.attempt)
    # The deployed gate creates this output on sb16. The coding host must not create it.
    for host in HOSTS:
        r=subprocess.run(['ssh','-i','/home/dflanag3/.ssh/id_ed25519_spatial_tests','-o','IdentitiesOnly=yes','-o','BatchMode=yes',host,'test','!','-e',str(m06_output)],capture_output=True)
        if r.returncode: raise RuntimeError('M06 evidence path exists on '+host)
        r=subprocess.run(['ssh','-i','/home/dflanag3/.ssh/id_ed25519_spatial_tests','-o','IdentitiesOnly=yes','-o','BatchMode=yes',host,'test','!','-L',str(m06_output)],capture_output=True)
        if r.returncode: raise RuntimeError('M06 evidence path is linked on '+host)
    dep_pins={h:{'path':DEPLOY,'provenance_sha256':PROV_SHA,'deployment_receipt_sha256':RECEIPTS[h]} for h in HOSTS}
    native={}
    remote_runtime={}
    for host in HOSTS:
        code='import json,pathlib,sys; p=json.load(open(sys.argv[1])); profiles={k:p["runtime_manifest"]["hosts"][sys.argv[2]][k] for k in ("Debug","ASan-UBSan")}; print(json.dumps(profiles))'
        runtime=ssh_json(host,code,[DEPLOY+'/provenance.json',host])
        remote_runtime[host]=runtime
    for profile in PROFILES:
        rows={}
        for host in HOSTS:
            short='debug' if profile=='Debug' else 'asan'
            rows[host]={
                'source':DEPLOY+'/source','bin':DEPLOY+'/build/'+profile,'python':DEPLOY+'/build/'+profile+'/python',
                'xserver':remote_runtime[host][profile]['xace_wrapper']['path'],
                'evidence':str(evidence_root/('native-'+a.attempt)/profile/host),
                'command_runner':RUNNER_BASE+'/run-command.sh','address':addresses[host],
            }
        native[profile]={'hosts':rows}
    plan={
        'schema':1,'candidate':CANDIDATE,'runner':'tools/run_geometry_native_crosshost.py',
        'directions':['sb16-to-sb16-2','sb16-2-to-sb16'],'profiles':list(PROFILES),
        'semantic_cases':[case+':'+direction for direction in ('sb16-to-sb16-2','sb16-2-to-sb16') for case in ('round-trip','private-denial','unavailable-destination')],
        'provisioning':'actor_transition_crosshost_fixture.py:production-private-two-world-issuer-mtls',
        'deployments':dep_pins,'local_runs':{h:{'path':REPORTS[h],'sha256':checked[h]['report_sha256']} for h in HOSTS},
        'native_geometry':native,'evidence':{'output':str(m06_output)},
    }
    module_path=source/'spatial-os-luna-handoff/tools/m06_deployed_gate.py'
    import runpy
    m=runpy.run_path(str(module_path))
    m['_validate_plan'](plan,plan_path)
    prepared.mkdir(parents=True)
    with plan_path.open('x', encoding='utf-8') as stream: stream.write(json.dumps(plan,indent=2,sort_keys=True)+'\n')
    roles_dir=prepared/'roles'; roles_dir.mkdir()
    role_files=[]
    workspace_root=evidence_root/('workspace-'+a.attempt)
    for profile in PROFILES:
        for direction in plan['directions']:
            authority,observer=direction.split('-to-')
            obs_runtime=remote_runtime[observer][profile]
            base=workspace_root/profile/direction
            roles={
              'SPATIAL_GEOMETRY_EVIDENCE':str(base/('authority-'+authority)),
              'SPATIAL_GEOMETRY_OBSERVER_HOST':observer,
              'SPATIAL_GEOMETRY_AUTHORITY_ADDRESS':addresses[authority],
              'SPATIAL_GEOMETRY_REMOTE_SOURCE':DEPLOY+'/source',
              'SPATIAL_GEOMETRY_REMOTE_BIN':DEPLOY+'/build/'+profile,
              'SPATIAL_GEOMETRY_REMOTE_PYTHON':DEPLOY+'/build/'+profile+'/python',
              'SPATIAL_GEOMETRY_REMOTE_XSERVER':obs_runtime['xace_wrapper']['path'],
              'SPATIAL_GEOMETRY_REMOTE_EVIDENCE':str(base/('observer-'+observer)),
            }
            if any(not str(v) for v in roles.values()): raise RuntimeError('empty role-map value')
            path=roles_dir/(profile+'-'+direction+'.json')
            path.write_text(json.dumps(roles,indent=2,sort_keys=True)+'\n',encoding='utf-8')
            role_files.append({'path':str(path),'sha256':digest(path),'profile':profile,'direction':direction,'authority':authority,'observer':observer})
    # Verify generated maps against the actual production role-map validator for both directions.
    for row in role_files:
        direction=row['direction']; authority=row['authority']; path=Path(row['path'])
        m07=runpy.run_path(str(source/'spatial-os-luna-handoff/tools/m07_deployed_gate.py'))
        m07['_role_map'](path,authority,direction)
    summary={'schema':1,'status':'READY_FOR_REVIEW','qualification':'UNQUALIFIED','runtime':'UNRUN','candidate':CANDIDATE,
      'package_pin_sha256':PIN_SHA,'inner_provenance_sha256':INNER_SHA,'outer_provenance_sha256':OUTER_SHA,'archive_sha256':ARCHIVE_SHA,'tools_and_schemas':tools,'deployments':dep_pins,'local_checks':checked,'primary_audits':{h:{'path':audits[h],'sha256':checked[h]['primary_audit_sha256']} for h in HOSTS},
      'plan':str(plan_path),'plan_sha256':digest(plan_path),'m06_output':str(m06_output),'roles':role_files,
      'baseline_dependencies':baseline,'collector_continuation':{h:checked[h]['collector_continuation'] for h in HOSTS},
      'required_after_crosshost_audits':{h:str(Path(REPORTS[h]).parent/'after-crosshost-artifact-audit.json') for h in HOSTS},
      'runtime_checks_run':False,'generated_utc_note':'timestamps intentionally omitted; hashes pin exact inputs'}
    (prepared/'preparation-receipt.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2,sort_keys=True))
    return 0

def re_fullhex(value):
    import re
    return re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}',value) is not None

if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as e:
        print('PLAN_REFUSED: '+repr(e),file=sys.stderr); raise SystemExit(2)
