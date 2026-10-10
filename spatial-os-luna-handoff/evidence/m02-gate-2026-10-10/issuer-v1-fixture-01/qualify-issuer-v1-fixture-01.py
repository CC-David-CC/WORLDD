"""Check the corrected v1 fixture against unchanged, pinned issuer binaries."""
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time

base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
gate = base/'m02-phase-gate-01'
host = socket.gethostname().split('.')[0]
assert host == 'sb16'
lock = (base/'m02-phase-qualification.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
run = json.loads((gate/'summary.json').read_bytes())
assert 'ended' in run
assert (gate/'primary-review.json').exists(), 'Preserve the original gate evidence first'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = json.loads((gate/'manifest.json').read_bytes())
changed = 'tests/core/test_identity_enrollment.py'
patch = base/'issuer-v1-fixture-01.py'
assert sha(patch) == sys.argv[1]
root = base/'issuer-v1-fixture-01'
root.mkdir(exist_ok=False)
source = root/'source'
for name, digest in manifest['files'].items():
    old = gate/'source'/name
    assert sha(old) == digest
    new = source/name
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(patch if name == changed else old, new)
assert changed not in manifest['locked_files']
assert all(sha(source/name) == digest for name, digest in manifest['locked_files'].items())
result = dict(host=host, status='running', started=time.time(), production_changed=False,
    source_commit=manifest['commit'], original_source=manifest['files'],
    changed_file=changed, changed_sha256=sha(patch), locked_files_unchanged=41,
    selection={'identity-enrollment':'Correct v1 schema and preserve enrollment assertions.',
               'identity-issuer':'Retain initialization and malformed database rejection.',
               'identity-authority-retirement':'Retain v9 migration and collision rollback.'},
    commands=[], binaries={})

def save():
    (root/'summary.json').write_text(json.dumps(result, indent=2)+'\n')

def execute(label, command, env, expected=0, count=False):
    processes=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try: name=(p/'comm').read_text().strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError): continue
        if name in ('cc1','cc1plus','clang','clang++','ninja','ctest'): processes.append((p.name,name))
    assert not processes, processes
    entry=dict(label=label,argv=command,started=time.time())
    result['commands'].append(entry);save()
    path=root/(label+'.log')
    with path.open('xb') as stream:
        completed=subprocess.run(command,cwd=source,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=240)
    entry.update(exit_code=completed.returncode,ended=time.time(),sha256=sha(path))
    log=path.read_text(errors='replace');save()
    assert completed.returncode == expected, log[-3000:]
    if count:
        counts=re.findall(r'^Ran (\d+) tests? in ',log,re.M)
        assert len(counts)==1 and log.rstrip().endswith('OK') and 'skipped' not in log
        assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',log)
        entry['passed']=int(counts[0]);save()
    print(label,completed.returncode,entry.get('passed'),flush=True)

save()
try:
    for profile in ('debug','runtime-sanitize'):
        build=gate/profile/'build'
        assert all(sha(build/name)==digest for name,digest in run['profiles'][profile]['binaries'].items())
        result['binaries'][profile]=run['profiles'][profile]['binaries']
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(build/'python')+':'+str(source),
                 ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',
                 SPATIAL_SECURITY_EVIDENCE=str(root/profile/'native'))
        env.pop('LSAN_OPTIONS',None)
        inventory=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1'],timeout=30))
        if profile=='debug':
            diagnostic=root/'reproduce-invalid-v1.py'
            diagnostic.write_text('''import ast,json,sqlite3,subprocess,sys
from pathlib import Path
old=Path(sys.argv.pop(1));sys.path.insert(0,str(old/'tests/core'))
from test_identity_issuer import IssuerFixture,BINARY
tree=ast.parse((old/'tests/core/test_identity_enrollment.py').read_text())
method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='test_explicit_schema_migration_preserves_issuer_key_owner_and_policy')
sql=next(ast.literal_eval(n.args[0]) for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='executescript')
fixture=IssuerFixture();fixture.setUp()
try:
 fixture.init()
 with sqlite3.connect(fixture.state/'identity.db') as db:
  db.executescript(sql)
  tables=db.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name").fetchall()
  assert tables==[('authority_retirement',),('issuer',)],tables
  try:db.execute('CREATE TABLE authority_retirement(marker INTEGER)')
  except sqlite3.OperationalError as error:collision=str(error)
  else:raise AssertionError('Expected a table collision')
 before=(fixture.state/'identity.db').read_bytes()
 p=subprocess.run([str(BINARY),'--state-dir',str(fixture.state),'--migrate'],input=b'',capture_output=True,timeout=5)
 assert p.returncode==2 and (fixture.state/'identity.db').read_bytes()==before
 print(json.dumps(dict(tables=tables,collision=collision,migration_exit=p.returncode,stderr=p.stderr.decode(),database_unchanged=True)))
finally:fixture.doCleanups()
''')
            execute('baseline-invalid-v1',['python3',str(diagnostic),str(gate/'source'),str(build/'identityd')],env)
        for name in result['selection']:
            test=next(t for t in inventory['tests'] if t['name']==name)
            command=[s.replace(str(gate/'source'),str(source)) for s in test['command']]
            execute(profile+'-'+name,command,env,count=True)
    assert all(sha(source/name)==(sha(patch) if name==changed else digest) for name,digest in manifest['files'].items())
    result['status']='focused_checks_passed_phase_pending'
except BaseException as error:
    result['status']='failed';result['failure']=type(error).__name__+': '+str(error)
    raise
finally:
    result['ended']=time.time();save()
    paths=[p for p in root.rglob('*') if p.is_file() and (p.parent==root or p.suffix=='.log')]
    attachments={p.relative_to(root).as_posix():sha(p) for p in paths}
    (root/'attachments.json').write_text(json.dumps(attachments,indent=2)+'\n')
    archive=base/'issuer-v1-fixture-evidence-01.tar.gz'
    with tarfile.open(archive,'x:gz') as tar:
        for p in paths+[root/'attachments.json',Path(__file__)]:
            data=p.read_bytes()
            assert b'-----BEGIN '+b'PRIVATE KEY-----' not in data
            assert not re.search(rb'Bearer '+rb'fleet-[A-Za-z0-9_-]{12,}',data)
            tar.add(p,arcname=p.relative_to(root).as_posix() if p.is_relative_to(root) else p.name,recursive=False)
        tar.add(patch,arcname='candidate-test.py',recursive=False)
    print(json.dumps(dict(status=result['status'],archive=str(archive),sha256=sha(archive))),flush=True)
