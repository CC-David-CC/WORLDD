#!/usr/bin/env python3
"""Journal and resume four globally serial M07 cells. Never relaunch an ambiguous job."""

# Adapted from the reviewed external baseline below. Preserve its immutable receipt,
# serialization, retired-process, and no-duplicate-dispatch checks.
BASELINE_PATH = '/home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01/candidate/execute_workspace_crosshost_serial.py'
BASELINE_SHA256 = '3a1e43610fd6dd1d2b6c2571a88c6ef74faa6c02a8d8d496e3d48d1321ddbe3d'
M06_REMOTE_TOOL = '/home/dflanag3/spatial-artifacts/m07-crosshost-fixture-continuation-20261016/review02/tools/m06_deployed_gate.py'
M07_TOOL_NAME = 'm07_workspace_collector_continuation.py'
M06_SIBLING_SHA256 = 'f765b2bec2d0faeb6bec43acddf806124daf2b8f7acc7ac4437040f575f69735'
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
import uuid

CANDIDATE = '1e58e65dec132a0a16f004ca4527034dca081f77'
ROOT = Path('/home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01')
BASELINE_CANDIDATE = ROOT / 'candidate'
BASELINE_PREPARE = BASELINE_CANDIDATE / 'prepare_crosshost_plan.py'
BASELINE_PREPARE_SHA256 = 'fba21bd6bc7d658706b2094043eecb6248bba664d76cc9b4257d46abdec682e5'
BASELINE_LAUNCH = BASELINE_CANDIDATE / 'launch_m06_crosshost.sh'
BASELINE_LAUNCH_SHA256 = '1d8a55904453d6c67723867573449ee730aea3166410c93553ac741b8b192b69'
GLOBAL_LOCK = Path('/home/dflanag3/spatial-artifacts/coordination/m07-crosshost-workflow.lock')
SOURCE = Path('/home/dflanag3/src/spacial-m07-fixture-repaired-crosshost-preparation-20261016')
DEPLOY = '/home/dflanag3/spatial-artifacts/deployments/m07-1e58e65d/runtime'
DEPLOYMENT_PINS = {host: {'path': DEPLOY, 'provenance_sha256': '67f1752a40681f23f91251f6bad692b4922b60732f3d1ab20cb8958f7f8e8462',
                        'deployment_receipt_sha256': receipt} for host,receipt in {
    'sb16':'b96c62fd9a6ab0dec30e854f046b6201f89756fddf0ab72e291cdae7de22c75e',
    'sb16-2':'e61c19fdaf7a5ef8a75edd061f9d73ffd39ee1bd5b11c0c3d86fb07dcffabd65'}.items()}
RUNNER_BASE = '/home/dflanag3/spatial-artifacts/deployments/m07-1e58e65d/deploy-input-01'
RUNNER_SHA = '7053d5ffe05e7050b035d6b715b794dd742b68ee29b9198a703979391323f537'
HOSTS = ('sb16', 'sb16-2')
PROFILES = ('Debug', 'ASan-UBSan')
DIRECTIONS = ('sb16-to-sb16-2', 'sb16-2-to-sb16')
KEY = '/home/dflanag3/.ssh/id_ed25519_spatial_tests'
RECEIPTS = {'command': 'command.exit', 'wrapper': 'wrapper.exit', 'terminal': 'terminal-status.txt'}

DISPATCH_RECEIPT = 'dispatch-receipt.json'
REMOTE_DISPATCH = r"""
import hashlib,json,os,pathlib,re,subprocess,sys,uuid
payload=json.loads(__import__("base64").b64decode(sys.argv[1])); session=payload["session"]; command=payload["command"]; run_dir=pathlib.Path(payload["run_dir"])
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}",session): raise SystemExit("unsafe tmux session")
run_dir.parent.mkdir(parents=True,exist_ok=True)
receipt=run_dir.with_name(run_dir.name+".dispatch.json")
if receipt.exists() or receipt.is_symlink(): raise SystemExit("dispatch receipt collision; outcome uncertain")
import shlex
tmux=["tmux","new-session","-d","-P","-F","#{pane_pid}","-s",session,("exec "+shlex.join(command))]
result=subprocess.run(tmux,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
if result.returncode: raise SystemExit("tmux dispatch failed: "+result.stderr.decode(errors="replace")[-1000:])
raw=result.stdout.decode("ascii","strict").strip()
if not re.fullmatch(r"[1-9][0-9]*",raw): raise SystemExit("tmux pane PID output missing or malformed; dispatch outcome uncertain")
pid=int(raw)
record={"schema":1,"session":session,"pane_pid":pid,"argv":command}
body=(json.dumps(record,sort_keys=True,indent=2)+"\n").encode()
tmp=receipt.with_name(receipt.name+".tmp-"+uuid.uuid4().hex)
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,"wb") as f: f.write(body); f.flush(); os.fsync(f.fileno())
try: os.link(tmp,receipt)
except FileExistsError: raise SystemExit("dispatch receipt collision after tmux acceptance; do not redispatch")
finally:
 try: tmp.unlink()
 except FileNotFoundError: pass
dfd=os.open(run_dir.parent,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(dfd)
finally: os.close(dfd)
print(json.dumps(record,sort_keys=True))
"""


def raw_sha256(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    return raw_sha256(Path(path).read_bytes())


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def atomic_json(path, value):
    """Persist each transition before the next external side effect."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temp.open('xb') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temp.exists():
            temp.unlink()


def stable(path, identity):
    """Keep immutable identity separate from mutable observations."""
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError('linked journal refused: ' + str(path))
    if path.exists():
        record = json.loads(path.read_bytes())
        if record.get('identity') != identity:
            raise RuntimeError('journal identity mismatch: ' + str(path))
        return record, False
    record = {'identity': identity, 'observations': {}}
    atomic_json(path, record)
    return record, True


# Match argv tokens, not substrings embedded in an observer's shell/Python command.
# The same policy runs in remote preflight and injected offline process inventories.
PROCESS_POLICY = r'''
def foreign_jobs(processes, owned):
 def normalized(argv):
  return [argv[0].rsplit('/',1)[-1],*argv[1:]] if argv else []
 allowed=[normalized(argv) for argv in owned]
 bad=[]
 for row in processes:
  argv=row['argv']; kind=None
  for index,token in enumerate(argv[:-1]):
   tool=token.rsplit('/',1)[-1]; verb=argv[index+1]
   if tool=='m06_deployed_gate.py' and verb in ('run-local','crosshost-handoff'): kind='exclusive'
   if tool=='m07_deployed_gate.py' and verb in ('run-local','workspace-crosshost','reconcile'):
    kind='exclusive' if verb=='run-local' else 'resumable'
   if tool=='m07_workspace_collector_continuation.py' and verb in ('workspace-crosshost','reconcile'):
    kind='resumable'
  if kind and (kind=='exclusive' or normalized(argv) not in allowed): bad.append(row)
 return bad
'''

# Tests inject this filesystem/process boundary. They run the actual main below.
REMOTE_FS = PROCESS_POLICY + r'''
import base64,hashlib,json,os,pathlib,shutil,stat,sys,uuid
request=json.loads(base64.b64decode(sys.argv[1])); op=request['op']; a=request['args']
def path(s):
 p=pathlib.Path(s)
 if not p.is_absolute() or '..' in p.parts: raise RuntimeError('unsafe absolute path')
 for q in (p,*p.parents):
  if q.is_symlink(): raise RuntimeError('symlink refused: '+str(q))
 return p
def syncdir(p):
 fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY)
 try: os.fsync(fd)
 finally: os.close(fd)
def immutable(p,data):
 p=path(p)
 if p.exists():
  if not p.is_file() or p.read_bytes()!=data: raise RuntimeError('existing bytes differ: '+str(p))
  return
 p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_name(p.name+'.incomplete-'+uuid.uuid4().hex)
 with tmp.open('xb') as f: f.write(data);f.flush();os.fsync(f.fileno())
 try: os.link(tmp,p);syncdir(p.parent)
 except FileExistsError:
  if p.read_bytes()!=data: raise RuntimeError('concurrent bytes differ')
 finally: tmp.unlink()
def manifest(p):
 p=path(p)
 if not p.is_dir(): raise RuntimeError('tree absent: '+str(p))
 result={}
 for f in sorted(p.rglob('*')):
  path(f)
  if f.is_file(): result[f.relative_to(p).as_posix()]=hashlib.sha256(f.read_bytes()).hexdigest()
  elif not f.is_dir(): raise RuntimeError('nonregular evidence')
 return result
if op=='exists': result=path(a[0]).exists()
elif op=='mkdir': path(a[0]).mkdir(parents=True,exist_ok=True);result=True
elif op=='put': immutable(a[0],base64.b64decode(a[1]));result=True
elif op=='sha': result=hashlib.sha256(path(a[0]).read_bytes()).hexdigest()
elif op=='verify_file':
 p=path(a[0]); s=p.lstat()
 if not stat.S_ISREG(s.st_mode): raise RuntimeError('pinned input is not a regular file')
 result=hashlib.sha256(p.read_bytes()).hexdigest()
elif op=='manifest': result=manifest(a[0])
elif op=='owned':
 p=path(a[0]); owner=base64.b64decode(a[1]); marker=p/'.workflow-owner.json'
 if p.exists():
  if not marker.is_file() or marker.read_bytes()!=owner: raise RuntimeError('unowned staging directory')
 else:
  p.parent.mkdir(parents=True,exist_ok=True)
  temp=p.with_name(p.name+'.incomplete-'+uuid.uuid4().hex);temp.mkdir()
  immutable(temp/'.workflow-owner.json',owner)
  os.rename(temp,p);syncdir(p.parent)
 result=True
elif op=='publish':
 src=path(a[0]); dst=path(a[1]); expected=a[2]
 if dst.exists():
  if manifest(dst)!=expected: raise RuntimeError('published tree differs')
 elif manifest(src)!=expected: raise RuntimeError('partial mirror differs; preserve and investigate')
 else: os.rename(src,dst);syncdir(dst.parent)
 result=True
elif op=='preflight':
 runner=path(a[0]); expected=a[1]
 if hashlib.sha256(runner.read_bytes()).hexdigest()!=expected: raise RuntimeError('runner hash mismatch')
 if shutil.disk_usage('/home/dflanag3/spatial-artifacts').free<26*1024**3: raise RuntimeError('less than 26 GiB free')
 processes=[]
 for proc in pathlib.Path('/proc').iterdir():
  if proc.name.isdigit():
   try: argv=[x.decode(errors='replace') for x in (proc/'cmdline').read_bytes().split(b'\0') if x]
   except OSError: continue
   processes.append({'pid':int(proc.name),'argv':argv})
 live=foreign_jobs(processes,a[2])
 if live: raise RuntimeError('overlapping gate: '+repr(live))
 result=True
else: raise RuntimeError('unknown filesystem operation')
print(json.dumps(result,sort_keys=True))
'''


class Transport:
    def remote(self, host, argv, timeout=30):
        result = subprocess.run(['ssh', '-i', KEY, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
                                 '-o', 'ConnectTimeout=8', host, shlex.join(list(map(str, argv)))],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if result.returncode:
            raise RuntimeError(host + ' remote error: ' + result.stderr[-2000:].decode(errors='replace'))
        return result.stdout

    def fs(self, host, op, *args):
        code = 'import base64;exec(compile(base64.b64decode(' + repr(base64.b64encode(REMOTE_FS.encode()).decode()) + '),"<coordinator-fs>","exec"))'
        request = base64.b64encode(json.dumps({'op': op, 'args': args}).encode()).decode()
        return json.loads(self.remote(host, ['python3', '-B', '-c', code, request], timeout=120))

    def read(self, host, path):
        # Never decode and re-encode source bytes before hashing them.
        return self.remote(host, ['cat', str(path)])

    def create(self, host, session, command, run_dir):
        payload = base64.b64encode(json.dumps({'session': session, 'command': command, 'run_dir': str(run_dir)}).encode()).decode()
        code = 'import base64;exec(compile(base64.b64decode(' + repr(base64.b64encode(REMOTE_DISPATCH.encode()).decode()) + '),"<tmux-dispatch>","exec"))'
        raw = self.remote(host, ['python3', '-B', '-c', code, payload], timeout=45)
        try:
            record = json.loads(raw)
            pid = record['pane_pid']
            if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                    or record.get('session') != session or record.get('argv') != command):
                raise ValueError('dispatch receipt identity/PID differs')
            return record
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError('tmux dispatch receipt missing or malformed; outcome uncertain; refusing redispatch') from error

    def pid_alive(self, host, pid):
        result = self.remote(host, ['python3', '-B', '-c', 'import os,sys;sys.stdout.write("1" if os.path.exists("/proc/"+sys.argv[1]) else "0")', str(pid)])
        value = result
        if value not in (b'0', b'1'):
            raise RuntimeError('tmux pane PID liveness response malformed; outcome uncertain')
        return value == b'1'

    def observe_session(self, host, session):
        return self.remote(host, ['tmux', 'has-session', '-t', session])

    def copy_tree(self, host, source, target_host, target):
        # Copy only into a journal-owned unique incomplete directory.
        subprocess.run(['scp', '-i', KEY, '-o', 'IdentitiesOnly=yes', '-3', '-r',
                        f'dflanag3@{host}:{source}/.', f'dflanag3@{target_host}:{target}/'],
                       check=True, timeout=300)

    def sleep(self, seconds):
        time.sleep(seconds)

    def monotonic(self):
        return time.monotonic()


def validate_canonical_receipts(values, label='canonical runner'):
    if values != {'command': '0', 'wrapper': '0', 'terminal': 'COMPLETE'}:
        raise RuntimeError(label + ' terminal failure: ' + repr(values))


def terminal(transport, host, run_dir, command=None, session=None):
    dispatch_raw = transport.read(host, Path(run_dir).with_name(Path(run_dir).name + '.dispatch.json'))
    try:
        dispatch = json.loads(dispatch_raw)
    except (ValueError, TypeError) as error:
        raise RuntimeError('dispatch receipt absent or malformed') from error
    pid = dispatch.get('pane_pid')
    if (dispatch.get('schema') != 1 or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
            or dispatch.get('argv') != command or (session is not None and dispatch.get('session') != session)):
        raise RuntimeError('dispatch receipt identity/PID differs')
    if transport.pid_alive(host, pid):
        raise RuntimeError('original tmux pane PID remains live')
    values = {key: transport.read(host, Path(run_dir) / name).decode('ascii').strip()
              for key, name in RECEIPTS.items()}
    if values['terminal'] in ('RUNNING', 'NOT_STARTED'):
        raise RuntimeError('runner has not reached terminal state')
    validate_canonical_receipts(values)
    if command is not None:
        actual = transport.read(host, Path(run_dir) / 'argv.nul')
        expected = b''.join(arg.encode() + b'\0' for arg in command[4:])
        if actual != expected:
            raise RuntimeError('canonical runner terminal failure: exact argv differs')
    filenames = list(RECEIPTS.values()) + ['argv.nul', 'command-receipt.txt', 'stdout.log', 'stderr.log']
    return {filename: transport.fs(host, 'sha', str(Path(run_dir) / filename)) for filename in filenames} | {'dispatch.json': transport.fs(host, 'sha', str(Path(run_dir).with_name(Path(run_dir).name + '.dispatch.json')))}


def dispatch_and_observe(transport, identity, journal, fresh_paths, timeout=2100):
    """Persist before one create. Later invocations observe the same handle."""
    journal = Path(journal)
    if not journal.exists():
        for path in fresh_paths:
            if transport.fs(identity['host'], 'exists', str(path)):
                raise RuntimeError('unrelated existing job/evidence: ' + str(path))
    record, created = stable(journal, identity)
    host, session = identity['host'], identity['session']
    if created:
        try:
            created_receipt = transport.create(host, session, identity['command'], identity['run_dir'])
            record['observations']['dispatch_receipt_sha256'] = raw_sha256(encoded(created_receipt))
            record['observations']['pane_pid'] = created_receipt['pane_pid']
            atomic_json(journal, record)
        except Exception as error:
            # An SSH exception cannot tell us whether tmux accepted the job.
            record['observations']['create_error'] = repr(error)
            atomic_json(journal, record)
    deadline = transport.monotonic() + timeout
    last_error = None
    while transport.monotonic() < deadline:
        try:
            values = terminal(transport, host, identity['run_dir'], identity['command'], session)
            old = record['observations'].get('terminal_sha256')
            if old is not None and old != values:
                raise RuntimeError('completed runner receipt bytes changed')
            record['observations']['terminal_sha256'] = values
            record['observations']['status'] = 'COMPLETE'
            atomic_json(journal, record)
            return values
        except RuntimeError as error:
            if 'terminal failure' in str(error) or 'bytes changed' in str(error):
                raise
            last_error = error
        except (OSError, subprocess.TimeoutExpired) as error:
            last_error = error
        # has-session is an observation. Transport failure proves no job death.
        try:
            transport.observe_session(host, session)
        except Exception as error:
            last_error = error
        transport.sleep(1)
    raise RuntimeError('observation deadline; exact dispatch remains resumable; refusing relaunch: ' + repr(last_error))


def validate_final_reconciliation(m06_raw, m06_sha, plan_raw, final_raw, m06_path, candidate, cells, m07_tool_path, m07_tool_sha256):
    m06, plan, final = map(json.loads, (m06_raw, plan_raw, final_raw))
    if raw_sha256(m06_raw) != m06_sha:
        raise RuntimeError('M06 raw hash differs')
    if (m06.get('candidate') != candidate or m06.get('status') != 'passed_needs_primary_review'
            or m06.get('phase_complete') is not False):
        raise RuntimeError('M06 status/candidate differs')
    if plan.get('candidate') != candidate or plan.get('schema') != 1 or set(plan.get('deployments', {})) != set(HOSTS):
        raise RuntimeError('M06 plan differs')
    if set(m06.get('deployments', {})) != set(HOSTS):
        raise RuntimeError('M06 deployment reports absent')
    for host, row in m06['deployments'].items():
        if (row.get('candidate') != candidate or row.get('receipt_candidate') != candidate
                or row.get('status') != 'verified' or row.get('source_ok') is not True):
            raise RuntimeError('M06 source report differs: ' + host)
    if (final.get('schema') != 1 or final.get('candidate') != candidate
            or final.get('status') != 'passed_needs_primary_review' or final.get('qualification') != 'UNQUALIFIED'
            or final.get('phase_complete') is not False or final.get('m06_reconciliation') != str(m06_path)
            or final.get('m06_reconciliation_sha256') != m06_sha):
        raise RuntimeError('M07 reconciliation identity/status differs')
    continuation = final.get('m07_tool_continuation', {})
    if continuation.get('path') != m07_tool_path or continuation.get('sha256') != m07_tool_sha256:
        raise RuntimeError('M07 reconciliation continuation tool pin differs')
    expected = [{'matrix': row['matrix'], 'receipt': row['receipt'], 'receipt_sha256': row['receipt_sha256'],
                 'log_sha256': row['log_sha256']} for row in cells]
    rows = final.get('workspace_crosshost')
    if not isinstance(rows, list) or len(rows) != 4 or sorted(rows, key=lambda r: r['matrix']) != sorted(expected, key=lambda r: r['matrix']):
        raise RuntimeError('M07 four-cell receipt/log/path pins differ')
    return {'m07_reconciliation_sha256': raw_sha256(final_raw), 'workspace_cells': 4}


def safe_path(path, root):
    path, root = Path(path), Path(root)
    if not path.is_absolute() or '..' in path.parts or path == root or root not in path.parents:
        raise RuntimeError('path escapes owned artifact root: ' + str(path))
    return path


def input_file(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise RuntimeError('input must be an absolute regular file: ' + str(path))
    return path.read_bytes()


def _validate_continuation_inputs(args, transport):
    local_gate = Path(__file__).with_name('m06_deployed_gate.py')
    if local_gate.is_symlink() or not local_gate.is_file() or sha(local_gate) != args.m06_tool_sha256:
        raise RuntimeError('maintained M06 continuation tool pin differs')
    if not re.fullmatch(r'[0-9a-f]{64}', args.m06_tool_sha256):
        raise RuntimeError('M06 continuation tool requires lowercase SHA256')
    remote_tool_sha = transport.fs('sb16', 'sha', M06_REMOTE_TOOL)
    if remote_tool_sha != args.m06_tool_sha256:
        raise RuntimeError('remote M06 continuation tool pin differs')
    manifest_path = Path(args.fixture_continuation_manifest)
    if (not manifest_path.is_absolute() or '..' in manifest_path.parts
            or str(manifest_path) != args.fixture_continuation_manifest
            or not re.fullmatch(r'[0-9a-f]{64}', args.fixture_continuation_manifest_sha256)):
        raise RuntimeError('fixture continuation manifest path/SHA256 is malformed')
    actual_remote_sha = transport.fs('sb16', 'sha', str(manifest_path))
    if actual_remote_sha != args.fixture_continuation_manifest_sha256:
        raise RuntimeError('remote fixture continuation manifest pin differs')
    raw = transport.read('sb16', manifest_path)
    if raw_sha256(raw) != args.fixture_continuation_manifest_sha256:
        raise RuntimeError('fixture continuation manifest raw-byte hash differs')
    manifest = json.loads(raw)
    if (set(manifest) != {'schema', 'candidate', 'semantic_fixture', 'host_helper'}
            or manifest.get('schema') != 1 or manifest.get('candidate') != CANDIDATE):
        raise RuntimeError('fixture continuation proof schema/candidate differs')
    for key in ('semantic_fixture', 'host_helper'):
        row = manifest.get(key)
        if (not isinstance(row, dict) or set(row) != {'path', 'sha256'}
                or not Path(row.get('path', '')).is_absolute()
                or '..' in Path(row.get('path', '')).parts
                or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256', '')))):
            raise RuntimeError('fixture continuation proof pin is malformed: ' + key)
        if str(row['path']).startswith(DEPLOY + '/source/'):
            raise RuntimeError('fixture continuation proof points inside frozen deployment source')
    return raw, manifest


def _validate_role_output_paths(root, attempt, profile, direction, host, output, remote_output):
    scope = root / 'evidence' / ('workspace-' + attempt) / profile / direction
    local = scope / ('authority-' + host)
    remote = scope / ('observer-' + direction.split('-to-')[1])
    if output != local or remote_output != remote:
        raise RuntimeError('role output paths differ from the exact attempt authority/observer scope')


def _workspace_command(args, host, profile, direction, stage_dir, run_dir, role_path, plan_path, output):
    return ['bash', RUNNER_BASE + '/run-command.sh', stage_dir, run_dir,
            'python3', '-B', args.m07_tool_path, 'workspace-crosshost',
            '--deployment', DEPLOY, '--candidate', CANDIDATE, '--profile', profile,
            '--direction', direction, '--roles', role_path, '--m06-plan', plan_path,
            '--output', output, '--continuation-sha256', args.m07_tool_sha256]


def _reconcile_command(args, run_dir, output, index):
    return ['bash', RUNNER_BASE + '/run-command.sh', str(ROOT / 'staging-sb16'), run_dir,
            'python3', '-B', args.m07_tool_path, 'reconcile', '--candidate', CANDIDATE,
            '--m06-reconciliation', str(args.m06_reconciliation), '--workspace-results', str(index),
            '--output', output, '--continuation-sha256', args.m07_tool_sha256]


def _validate_m07_tool(args, transport):
    path = Path(args.m07_tool_path)
    if (not path.is_absolute() or '..' in path.parts or str(path) != args.m07_tool_path
            or path.name != M07_TOOL_NAME
            or not re.fullmatch(r'[0-9a-f]{64}', args.m07_tool_sha256)):
        raise RuntimeError('M07 continuation path/SHA256 is malformed')
    if str(path).startswith(DEPLOY + '/source/'):
        raise RuntimeError('M07 continuation must remain an external pinned tool')
    verified = {}
    for host in HOSTS:
        actual = transport.fs(host, 'verify_file', str(path))
        if actual != args.m07_tool_sha256:
            raise RuntimeError('M07 continuation tool pin differs on ' + host)
        sibling = transport.fs(host, 'verify_file', M06_REMOTE_TOOL)
        if sibling != M06_SIBLING_SHA256:
            raise RuntimeError('approved M06 sibling pin differs on ' + host)
        verified[host] = {'path': str(path), 'sha256': actual,
                          'm06_sibling_path': M06_REMOTE_TOOL, 'm06_sibling_sha256': sibling}
    return verified


def _m06_command(args):
    return ['bash', RUNNER_BASE + '/run-command.sh', str(ROOT / 'staging-sb16'), str(args.m06_runner_dir),
            'python3', '-B', M06_REMOTE_TOOL, 'crosshost-handoff', '--plan', str(args.plan),
            '--fixture-continuation-manifest', args.fixture_continuation_manifest,
            '--fixture-continuation-manifest-sha256', args.fixture_continuation_manifest_sha256]


def _validate_m06_command(command, args):
    expected = _m06_command(args)
    if list(command) != expected:
        raise RuntimeError('M06 continuation argv differs from its pinned manifest/tool contract')
    return expected


def _validate_tool_dependencies(dependencies=None):
    dependencies = dependencies or {
        'prepare_crosshost_plan.py': (BASELINE_PREPARE, BASELINE_PREPARE_SHA256),
        'launch_m06_crosshost.sh': (BASELINE_LAUNCH, BASELINE_LAUNCH_SHA256),
    }
    verified = {}
    for name, (path, expected) in dependencies.items():
        path = Path(path)
        if not path.is_absolute() or path.is_symlink():
            raise RuntimeError('pinned tool dependency must be an absolute non-symlink file: ' + name)
        try:
            before = path.lstat()
        except OSError as error:
            raise RuntimeError('pinned tool dependency is absent: ' + name) from error
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError('pinned tool dependency is not a regular file: ' + name)
        actual = sha(path)
        after = path.lstat()
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if identity(before) != identity(after):
            raise RuntimeError('pinned tool dependency changed while hashing: ' + name)
        if actual != expected:
            raise RuntimeError('pinned tool dependency SHA256 differs: ' + name)
        verified[name] = (path, actual)
    return verified


def _tool_pin_paths(dependencies):
    return [Path(__file__).resolve(),
            dependencies['prepare_crosshost_plan.py'][0],
            dependencies['launch_m06_crosshost.sh'][0],
            Path(__file__).with_name('m06_deployed_gate.py'),
            SOURCE / 'spatial-os-luna-handoff/tools/m06_deployed_gate.py',
            SOURCE / 'spatial-os-luna-handoff/tools/m07_deployed_gate.py']


def tool_pins():
    dependencies = _validate_tool_dependencies()
    import runpy
    generator = runpy.run_path(str(dependencies['prepare_crosshost_plan.py'][0]))
    if generator['CANDIDATE'] != CANDIDATE:
        raise RuntimeError('generator candidate differs')
    production = generator['source_pins'](SOURCE)
    paths = _tool_pin_paths(dependencies)
    return {**production, **{str(path): sha(path) for path in paths}}


def staged(transport, host, directory, owner, files):
    transport.fs(host, 'owned', str(directory), base64.b64encode(encoded(owner)).decode())
    expected = {name: raw_sha256(raw) for name, raw in files.items()}
    expected['.workflow-owner.json'] = raw_sha256(encoded(owner))
    actual = transport.fs(host, 'manifest', str(directory))
    if any(name not in expected or expected[name] != digest for name, digest in actual.items()):
        raise RuntimeError('staged input bytes differ: ' + host)
    for name, raw in files.items():
        transport.fs(host, 'put', str(directory / name), base64.b64encode(raw).decode())
    if transport.fs(host, 'manifest', str(directory)) != expected:
        raise RuntimeError('staged input verification differs: ' + host)


def observe_cell(transport, host, profile, direction, output, role, plan, m07_tool_path, m07_tool_sha256):
    receipt_path = output / 'workspace-crosshost-receipt.json'
    raw = transport.read(host, receipt_path)
    receipt = json.loads(raw)
    expected = {'schema': 1, 'candidate': CANDIDATE, 'host': host, 'profile': profile, 'direction': direction,
                'status': 'passed', 'exit_code': 0, 'qualification': 'UNQUALIFIED', 'phase_complete': False}
    if (any(receipt.get(key) != value for key, value in expected.items()) or receipt.get('roles') != role
            or receipt.get('m07_tool_continuation', {}).get('path') != m07_tool_path
            or receipt.get('m07_tool_continuation', {}).get('sha256') != m07_tool_sha256):
        raise RuntimeError('workspace receipt identity/status/roles differ')
    pins = plan['deployments']
    observer = direction.split('-to-')[1]
    if (receipt.get('m06_deployment_pins') != pins
            or receipt.get('deployment_provenance_sha256') != pins[host]['provenance_sha256']
            or receipt.get('deployment_receipt_sha256') != pins[host]['deployment_receipt_sha256']
            or receipt.get('observer_deployment', {}).get('provenance_sha256') != pins[observer]['provenance_sha256']
            or receipt.get('observer_deployment', {}).get('deployment_receipt_sha256') != pins[observer]['deployment_receipt_sha256']):
        raise RuntimeError('workspace deployment pins differ')
    log = output / 'workspace-geometry-native-crosshost.log'
    if receipt.get('test_log') != str(log) or transport.fs(host, 'sha', str(log)) != receipt.get('test_output_sha256'):
        raise RuntimeError('workspace log pin differs')
    if (not receipt.get('cleanup', {}).get('adapter_scope_removed') or not receipt.get('cleanup', {}).get('cgroup_absent')
            or not receipt.get('local_log_inventory') or not receipt.get('remote_log_inventory', {}).get('files')):
        raise RuntimeError('workspace cleanup/log evidence absent')
    manifest = transport.fs(host, 'manifest', str(output))
    if manifest.get(receipt_path.name) != raw_sha256(raw):
        raise RuntimeError('workspace receipt raw hash differs')
    return {'matrix': [host, profile, direction], 'receipt': str(receipt_path), 'receipt_sha256': raw_sha256(raw),
            'log_sha256': receipt['test_output_sha256'], 'manifest': manifest}


def mirror(transport, source, journal, row):
    target = str(source)
    identity = {'source_host': 'sb16-2', 'target_host': 'sb16', 'source': target, 'manifest': row['manifest']}
    if not Path(journal).exists() and transport.fs('sb16', 'exists', target):
        raise RuntimeError('unrelated mirror target exists')
    record, _ = stable(journal, identity)
    if transport.fs('sb16', 'exists', target):
        if transport.fs('sb16', 'manifest', target) != row['manifest']:
            raise RuntimeError('existing mirror bytes differ')
    else:
        temporary = record['observations'].get('incomplete')
        if temporary is not None:
            # Never merge another copy into a partial tree or run another test.
            if (not transport.fs('sb16', 'exists', temporary)
                    or transport.fs('sb16', 'manifest', temporary) != row['manifest']):
                raise RuntimeError('incomplete mirror requires explicit bounded recovery: ' + temporary)
        else:
            temporary = target + '.incomplete-' + uuid.uuid4().hex
            record['observations']['incomplete'] = temporary
            atomic_json(journal, record)
            transport.fs('sb16', 'mkdir', temporary)
            transport.copy_tree('sb16-2', target, 'sb16', temporary)
        transport.fs('sb16', 'publish', temporary, target, row['manifest'])
    record['observations']['status'] = 'COMPLETE'
    atomic_json(journal, record)


def main(argv=None, transport=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--roles', type=Path, required=True)
    parser.add_argument('--m06-reconciliation', type=Path, required=True)
    parser.add_argument('--m06-runner-dir', type=Path, required=True)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--m06-tool-sha256', required=True)
    parser.add_argument('--fixture-continuation-manifest', required=True)
    parser.add_argument('--fixture-continuation-manifest-sha256', required=True)
    parser.add_argument('--m07-tool-path', required=True)
    parser.add_argument('--m07-tool-sha256', required=True)
    args = parser.parse_args(argv)
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}', args.attempt)
            or args.attempt == 'collector-owner-fixed-02'):
        raise RuntimeError('unsafe or retired attempt label')
    if not os.environ.get('TMUX'):
        raise RuntimeError('run coordinator through canonical runner in persistent G164 tmux')
    for path in (args.plan, args.roles, args.m06_reconciliation, args.m06_runner_dir):
        if not path.is_absolute() or '..' in path.parts:
            raise RuntimeError('absolute pinned paths required')
    _validate_tool_dependencies()  # Fail before connecting or dispatching any runtime work.
    transport = transport or Transport()
    # This path stays fixed across attempts and artifact versions on the controller.
    # Do not reuse the host gate lock: each dispatched production gate owns its own lock.
    GLOBAL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    if GLOBAL_LOCK.is_symlink():
        raise RuntimeError('linked global coordination lock refused')
    global_lock = GLOBAL_LOCK.open('a')
    try:
        try:
            fcntl.flock(global_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('another cross-host coordinator holds the controller global lock') from error
        workflow = ROOT / 'workflows' / args.attempt
        workflow.mkdir(parents=True, exist_ok=True)
        lock = (workflow / 'coordinator.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return execute(args, transport, workflow)
        finally:
            lock.close()
    finally:
        global_lock.close()


def execute(args, transport, workflow):
    plan_raw = input_file(args.plan)
    plan = json.loads(plan_raw)
    if plan.get('schema') != 1 or plan.get('candidate') != CANDIDATE or set(plan.get('deployments', {})) != set(HOSTS):
        raise RuntimeError('M06 plan candidate/schema/deployments differ')
    if plan['deployments']!=DEPLOYMENT_PINS:
        raise RuntimeError('M06 plan differs from frozen candidate deployment pins')
    m07_tool_pins = _validate_m07_tool(args, transport)
    role_files = {profile + '-' + direction + '.json': input_file(args.roles / (profile + '-' + direction + '.json'))
                  for profile in PROFILES for direction in DIRECTIONS}
    m06_raw = transport.read('sb16', args.m06_reconciliation)
    m06_sha = transport.fs('sb16', 'sha', str(args.m06_reconciliation))
    if raw_sha256(m06_raw) != m06_sha:
        raise RuntimeError('M06 raw-byte transport differs')
    m06 = json.loads(m06_raw)
    manifest_raw, manifest = _validate_continuation_inputs(args, transport)
    continuation = m06.get('fixture_continuation', {})
    expected_manifest = {'path': args.fixture_continuation_manifest,
                         'sha256': args.fixture_continuation_manifest_sha256}
    if (continuation.get('manifest') != expected_manifest
            or continuation.get('semantic_fixture') != manifest['semantic_fixture']
            or continuation.get('host_helper') != manifest['host_helper']
            or continuation.get('m06_tool') != {'path': M06_REMOTE_TOOL, 'sha256': args.m06_tool_sha256}):
        raise RuntimeError('M06 reconciliation lacks the exact pinned fixture/tool continuation proof')
    if (m06.get('candidate') != CANDIDATE or m06.get('status') != 'passed_needs_primary_review'
            or m06.get('phase_complete') is not False):
        raise RuntimeError('M06 reconciliation has not passed')
    if set(m06.get('deployments', {})) != set(HOSTS):
        raise RuntimeError('M06 deployment reports absent')
    for host, row in m06['deployments'].items():
        if (row.get('candidate') != CANDIDATE or row.get('receipt_candidate') != CANDIDATE
                or row.get('status') != 'verified' or row.get('source_ok') is not True):
            raise RuntimeError('M06 source report differs: ' + host)
    m06_command = _validate_m06_command(_m06_command(args), args)
    m06_terminal = terminal(transport, 'sb16', args.m06_runner_dir, m06_command)
    recorded = transport.read('sb16', args.m06_reconciliation.parent / 'plan.json')
    if recorded != plan_raw:
        raise RuntimeError('M06 used different raw plan bytes')
    files = {'m06-crosshost-plan.json': plan_raw,
             'm06-fixture-continuation-manifest.json': manifest_raw, **role_files}
    identity = {'schema': 3, 'candidate': CANDIDATE, 'attempt': args.attempt,
                'coordinator_argv': ['--plan', str(args.plan), '--roles', str(args.roles),
                                     '--m06-reconciliation', str(args.m06_reconciliation),
                                     '--m06-runner-dir', str(args.m06_runner_dir), '--attempt', args.attempt,
                                     '--m06-tool-sha256', args.m06_tool_sha256,
                                     '--fixture-continuation-manifest', args.fixture_continuation_manifest,
                                     '--fixture-continuation-manifest-sha256', args.fixture_continuation_manifest_sha256,
                                     '--m07-tool-path', args.m07_tool_path,
                                     '--m07-tool-sha256', args.m07_tool_sha256],
                'input_sha256': {name: raw_sha256(raw) for name, raw in files.items()},
                'm06_sha256': m06_sha, 'm06_terminal_sha256': m06_terminal, 'tools_and_schemas': tool_pins(),
                'runner_sha256': RUNNER_SHA, 'deployment': DEPLOY, 'controller_global_lock': str(GLOBAL_LOCK),
                'm07_tool_continuation': {'path': args.m07_tool_path, 'sha256': args.m07_tool_sha256,
                                         'host_pins': m07_tool_pins},
                'fixture_continuation': {'manifest_path': args.fixture_continuation_manifest,
                                         'manifest_sha256': args.fixture_continuation_manifest_sha256,
                                         'm06_tool_path': M06_REMOTE_TOOL,
                                         'm06_tool_sha256': args.m06_tool_sha256,
                                         'baseline_path': BASELINE_PATH,
                                         'baseline_sha256': BASELINE_SHA256}}
    record, _ = stable(workflow / 'workflow.json', identity)
    owned = {host: [] for host in HOSTS}
    # Only exact commands from this pinned workflow can pass the resume preflight.
    for journal in sorted(workflow.glob('*-dispatch.json')):
        dispatch = json.loads(journal.read_bytes()).get('identity', {})
        if dispatch.get('workflow_sha256') != raw_sha256(encoded(identity)) or dispatch.get('host') not in HOSTS:
            raise RuntimeError('foreign dispatch journal in workflow')
        command = dispatch['command']
        owned[dispatch['host']].extend([command, command[4:]])
    for host in HOSTS:
        transport.fs(host, 'preflight', RUNNER_BASE + '/run-command.sh', RUNNER_SHA, owned[host])
    staging = {host: ROOT / 'inputs' / args.attempt / host for host in HOSTS}
    for host in HOSTS:
        staged(transport, host, staging[host], identity, files)
        transport.fs(host, 'mkdir', str(ROOT / ('staging-' + host)))
    cells = []
    for profile in PROFILES:
        for direction in DIRECTIONS:
            host = direction.split('-to-')[0]
            key = profile + '-' + direction
            role = json.loads(role_files[key + '.json'])
            if role.get('SPATIAL_GEOMETRY_OBSERVER_HOST') != direction.split('-to-')[1]:
                raise RuntimeError('role direction differs')
            output = safe_path(role['SPATIAL_GEOMETRY_EVIDENCE'], ROOT)
            remote_output = safe_path(role['SPATIAL_GEOMETRY_REMOTE_EVIDENCE'], ROOT)
            _validate_role_output_paths(ROOT, args.attempt, profile, direction, host, output, remote_output)
            if any(output == Path(cell['receipt']).parent or output in Path(cell['receipt']).parents
                   or Path(cell['receipt']).parent in output.parents for cell in cells):
                raise RuntimeError('overlapping cell evidence')
            run_dir = ROOT / 'runner-runs/workspace' / profile / (direction + '-' + args.attempt)
            command = _workspace_command(
                args, host, profile, direction, str(ROOT / ('staging-' + host)), str(run_dir),
                str(staging[host] / (key + '.json')), str(staging[host] / 'm06-crosshost-plan.json'),
                str(output))
            dispatch_identity = {'host': host, 'session': 'm07-' + raw_sha256((str(ROOT) + '|' + args.attempt + '|' + key).encode())[:20],
                                 'command': command, 'run_dir': str(run_dir), 'workflow_sha256': raw_sha256(encoded(identity))}
            dispatch_and_observe(transport, dispatch_identity, workflow / (key + '-dispatch.json'), [run_dir, output])
            row = observe_cell(transport, host, profile, direction, output, role, plan, args.m07_tool_path, args.m07_tool_sha256)
            stable(workflow / (key + '-result.json'), row)
            if host == 'sb16-2':
                mirror(transport, output, workflow / (key + '-mirror.json'), row)
            cells.append(row)
    index = ROOT / 'results' / args.attempt / 'workspace-results.json'
    index_raw = encoded([{'path': row['receipt']} for row in cells])
    stable(workflow / 'index.json', {'path': str(index), 'sha256': raw_sha256(index_raw)})
    if index.exists():
        if input_file(index) != index_raw:
            raise RuntimeError('local index bytes differ')
    else:
        atomic_json(index, [{'path': row['receipt']} for row in cells])
    transport.fs('sb16', 'put', str(index), base64.b64encode(index_raw).decode())
    if transport.fs('sb16', 'sha', str(index)) != raw_sha256(index_raw):
        raise RuntimeError('remote index bytes differ')
    recon_out = ROOT / 'evidence' / ('m07-reconcile-' + args.attempt)
    recon_run = ROOT / 'runner-runs/reconcile' / args.attempt
    command = _reconcile_command(args, str(recon_run), str(recon_out), str(index))
    dispatch_identity = {'host': 'sb16', 'session': 'm07-reconcile-' + raw_sha256((str(ROOT) + '|' + args.attempt).encode())[:16],
                         'command': command, 'run_dir': str(recon_run), 'workflow_sha256': raw_sha256(encoded(identity))}
    dispatch_and_observe(transport, dispatch_identity, workflow / 'reconcile-dispatch.json', [recon_run, recon_out], timeout=900)
    recon_path = recon_out / 'm07-reconciliation.json'
    recon_raw = transport.read('sb16', recon_path)
    recon_sha = transport.fs('sb16', 'sha', str(recon_path))
    proof = validate_final_reconciliation(m06_raw, m06_sha, plan_raw, recon_raw, args.m06_reconciliation, CANDIDATE, cells, args.m07_tool_path, args.m07_tool_sha256)
    if proof['m07_reconciliation_sha256'] != recon_sha:
        raise RuntimeError('M07 raw-byte hash differs')
    summary = {'candidate': CANDIDATE, 'qualification': 'UNQUALIFIED', 'phase_complete': False,
               'm06_reconciliation': str(args.m06_reconciliation), 'm06_reconciliation_sha256': m06_sha,
               'workspace_results_index': str(index), 'm07_reconciliation': str(recon_path),
               'm07_reconciliation_sha256': recon_sha, 'm07_tool_continuation': {'path': args.m07_tool_path, 'sha256': args.m07_tool_sha256},
               'cells': cells, 'strict_reconciliation_validation': proof}
    stable(workflow / 'complete.json', summary)
    record['observations']['status'] = 'COMPLETE'
    atomic_json(workflow / 'workflow.json', record)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('WORKFLOW_REFUSED/RESUMABLE: ' + repr(error), file=sys.stderr)
        raise SystemExit(2)
