"""Review deliberate assertion failures and the restored production checkout."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
manifest=json.loads((base/'m02-phase-gate-01/manifest.json').read_bytes())
root=base/'m02-focused-mutations-04'
run=json.loads((root/'summary.json').read_bytes())
assert run['status']=='four_mutations_detected_clean_source_restored' and 'ended' in run
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
source=root/'worktree/spatial-os-luna-handoff'
assert all(sha(source/name)==digest for name,digest in manifest['files'].items())
assert all(sha(source/name)==digest for name,digest in manifest['locked_files'].items())
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==manifest['commit']
assert subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=source)==b''
assert len(run['mutations'])==4 and len({m['name'] for m in run['mutations']})==4
assert len(run['commands'])==25, len(run['commands'])
commands={c['label']:c for c in run['commands']}
results=[]
expected_assertions={
    'parent-revocation':'AssertionError: True is not false',
    'object-scope':'AssertionError: 0 != 13',
    'partial-write':"AssertionError: {'exit_code': 21} != {'exit_code': 0}",
    'premature-completion':'AssertionError: Tuples differ: (2, 2, True) != (2, 2, False)'
}
for mutation in run['selection']:
    name=mutation['name']
    for phase in ('baseline','mutant','restored'):
        label=name+'-'+phase;text=(root/(label+'.log')).read_text()
        assert re.search(r'^Ran 1 test in ',text,re.M) and 'skipped' not in text and 'ERROR:' not in text
        assert commands[label]['exit_code']==(1 if phase=='mutant' else 0)
        if phase=='mutant':
            assert expected_assertions[name] in text and 'FAILED (failures=1)' in text
        else:assert text.rstrip().endswith('OK')
    before=(source/mutation['file']).read_bytes();old=mutation['old'].encode()
    assert before.count(old)==1
    proof=next(m for m in run['mutations'] if m['name']==name)
    assert hashlib.sha256(before).hexdigest()==proof['before_sha256']
    assert hashlib.sha256(before.replace(old,mutation['new'].encode())).hexdigest()==proof['after_sha256']
    results.append(dict(name=name,test=mutation['test'],positive_before='passed',mutant='intended_assertion_failed',
        positive_after='passed',assertion=expected_assertions[name]))
for label,command in commands.items():
    assert sha(root/(label+'.log'))==command['sha256']
    assert command['exit_code']==(1 if label.endswith('-mutant') else 0)

attachments={}
def add(path):
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base)
    assert path.suffix in ('.json','.log','.patch','.py','.txt')
    data=path.read_bytes()
    assert b'-----BEGIN '+b'PRIVATE KEY-----' not in data
    assert not re.search(rb'Bearer '+rb'fleet-[A-Za-z0-9_-]{12,}',data)
    attachments[path.relative_to(base).as_posix()]=sha(path)
for tag in ('02','03','04'):
    attempt=base/('m02-focused-mutations-'+tag)
    for path in attempt.iterdir():
        if path.is_file():add(path)
        elif path.name.endswith('-native'):
            for log in path.rglob('*.log'):add(log)
    add(base/('run-m02-focused-mutations-'+tag+'.py'))
add(root/'build/CMakeCache.txt');add(Path(__file__))
for tag in ('02','03'):
    failed=json.loads((base/('m02-focused-mutations-'+tag)/'summary.json').read_bytes())
    assert failed['status']=='failed' and failed['mutations']==[]
report=dict(status='four_mutation_witnesses_verified',host='sb16',profile='Debug',source_commit=manifest['commit'],
    source_files=351,locked_files_unchanged=41,positive_before_passed=4,expected_mutation_failures=4,
    positive_after_passed=4,unexpected_test_failures=0,results=results,source_restored=True,
    phase_complete=False,historical_timeouts_resolved=False,attachments=attachments,
    restored_binaries={p.name:sha(p) for p in (root/'build').iterdir() if p.is_file() and os.access(p,os.X_OK)},
    setup_failures={'02':'SSH authentication denied before checkout or tests.',
                    '03':'Sparse worktree index was empty. No mutation tests ran. The preserved repair probe verified explicit population.'},
    limitation='Individual mutant executable hashes were not recorded. Source patches, build logs, exact commands, intended failures, and restored passes are retained.')
review=base/'m02-mutations-review-04.json'
with review.open('xb') as stream:stream.write((json.dumps(report,indent=2)+'\n').encode())
archive=base/'m02-mutations-evidence-04.tar.gz'
with tarfile.open(archive,'x:gz') as tar:
    for name in sorted(attachments):tar.add(base/name,arcname=name,recursive=False)
    tar.add(review,arcname='primary-review.json',recursive=False)
print(json.dumps(dict(status=report['status'],archive=str(archive),sha256=sha(archive),attachments=len(attachments))))
