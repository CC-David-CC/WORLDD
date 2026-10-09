import ast
import difflib
import hashlib
import json
from pathlib import Path
import tarfile

r=Path(__file__).resolve().parents[1];draft=r/'local/supervisor-fencing-draft-01'
base=json.loads((r/'local/authority-fencing-candidate-01.json').read_text())['files']
cmake=draft/'CMakeLists.txt';text=cmake.read_text(encoding='utf-8')
start=text.index('    add_test(NAME authority-world-handoff-regressions ')
end=text.index('\n',text.index('    set_tests_properties(authority-world-handoff-regressions PROPERTIES TIMEOUT 180)',start))+1
block=text[start:end].replace('authority-world-handoff-regressions','authority-supervisor-fencing').replace('spatial-world-handoff-regressions','spatial-supervisor-fencing').replace('test_supervisor_world_handoff_regressions.py','test_supervisor_fencing.py')
assert 'authority-supervisor-fencing' not in text
cmake.write_bytes((text[:end]+block+text[end:]).encode())
files=sorted(set(base)|{'tests/native/test_supervisor_fencing.py'})
manifest={n:hashlib.sha256((draft/n).read_bytes()).hexdigest() for n in files}
assert len(manifest)==293
locked=json.loads((draft/'acceptance.lock.json').read_text())['files']
assert len(locked)==41 and all(manifest[n]==v for n,v in locked.items())
assert all(manifest[n]==v for n,v in base.items() if n.startswith('tests/'))
changed=sorted(n for n in manifest if base.get(n)!=manifest[n]);assert len(changed)==7
tree=ast.parse((draft/'tests/native/test_supervisor_fencing.py').read_text(encoding='utf-8'))
count=sum(isinstance(v,ast.FunctionDef) and v.name.startswith('test_') for cls in tree.body if isinstance(cls,ast.ClassDef) for v in cls.body)
assert count==10
record=dict(files=manifest,source_files=293,changed=changed,base_files=292)
(r/'local/supervisor-fencing-candidate-01.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
with tarfile.open(r/'local/supervisor-fencing-candidate-01.tar','w') as archive:
    for n in files:archive.add(draft/n,arcname=n)
e=r/'evidence/supervisor-fencing-2026-10-09'
(e/'candidate-01-manifest.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
patch=[]
for n in changed:
    before=(r/'local/authority-fencing-draft-01'/n).read_text(encoding='utf-8').splitlines(True) if n in base else []
    after=(draft/n).read_text(encoding='utf-8').splitlines(True)
    patch.extend(difflib.unified_diff(before,after,fromfile='a/'+n if n in base else '/dev/null',tofile='b/'+n))
(e/'candidate-01.patch').write_bytes(''.join(patch).encode())
review=dict(reviewer='primary agent',status='frozen_unqualified',source_files=manifest,reviewed_files=changed,
    new_checks=10,existing_tests_unchanged=True,locked_files_unchanged=41,
    expected_full=dict(core_checks=1443,core_suites=98,harness_checks=51,P10_cases=151),
    scope='Retained launch authorization constructs exact issuer fence; local record validates and persists returned fence; private fence operation performs checked cleanup.',
    limits=['No automatic crash fencing in this increment.','No dependent control cleanup receipt or issuer retirement.','Reopened journals do not reconstruct retained ownership.'],
    unresolved_static_defects=[])
(e/'candidate-prepared-review-01.json').write_bytes((json.dumps(review,indent=2)+'\n').encode())
print('Frozen supervisor fencing293:7 changed files,10 new checks,41 locks preserved.')
