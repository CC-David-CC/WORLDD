import ast
import json
from pathlib import Path
r = Path(__file__).resolve().parents[1]
value = (r/'local/qualify-supervisor-fencing-01.py').read_text(encoding='utf-8')
value = value.replace('supervisor-fencing', 'supervisor-crash-fencing').replace('==293', '==294').replace('source_files=293', 'source_files=294')
old = "assert all(v['passed'] and v['exit_code']==1 and v['ran']==1 and not v['sanitizer_marker'] for v in baseline['profiles'].values())"
assert value.count(old) == 1
value = value.replace(old, "assert len(baseline['profiles']) == 2 and all(v['expected_missing_feature'] and not v['sanitizer_marker'] for v in baseline['profiles'].values())")
start = value.index('def descriptors(tree,label):')
end = value.index("server=json.loads(", start)
value = value[:start]+'''oldsource=r/'local/m02-supervisor-fencing-01/source'
oldmanifest=json.loads((r/'local/supervisor-fencing-candidate-01.json').read_text())['files']
assert all(hashlib.sha256((oldsource/n).read_bytes()).hexdigest()==v for n,v in oldmanifest.items())
assert all(manifest[n]==v for n,v in oldmanifest.items() if n.startswith(('proto/','tests/')))
(out/'schema-audit.json').write_text(json.dumps(dict(existing_schema_sources_unchanged=True,
    existing_tests_unchanged=True,locked_files=41),indent=2)+'\\n')
'''+value[end:]
ast.parse(value)
(r/'local/qualify-supervisor-crash-fencing-01.py').write_bytes(value.encode())
value = (r/'local/start-supervisor-fencing-01.py').read_text(encoding='utf-8').replace('supervisor-fencing','supervisor-crash-fencing')
ast.parse(value)
(r/'local/start-supervisor-crash-fencing-01.py').write_bytes(value.encode())
e=r/'evidence/supervisor-crash-fencing-2026-10-09'
manifest=json.loads((r/'local/supervisor-crash-fencing-candidate-01.json').read_text())
review=dict(reviewer='primary agent', status='frozen_unqualified', reviewed_files=manifest['changed'],
    source_files=294, new_checks=10, existing_tests_unchanged=True, locked_files_unchanged=41,
    schema_sources_unchanged=True, expected_full=dict(core_checks=1453,core_suites=99,harness_checks=51,P10_cases=151),
    decisions=[
        'Fatal handler requests a fence before the existing cleanup; it preserves original failure exit2.',
        'Explicit stop/fence paths suppress implicit fencing and avoid repeating an explicit failed operation.',
        'Checked graceful release excludes automatic fencing; retained ownership is required.',
        'Issuer failure and persistence failure still reach checked cleanup. Fence alone never retires obligations.',
        'The issuer verifies the consumed generation. Local start-attempt state is only eligibility.'],
    limits=['No dependent control cleanup receipt or issuer retirement.', 'No ownership reconstruction after supervisor death.',
        'World bootstrap failure still prevents integration of the candidate chain.'])
(e/'candidate-prepared-review-01.json').write_bytes((json.dumps(review,indent=2)+'\n').encode())
for name in ('prepare-supervisor-crash-fencing-01.py','prepare-crash-fencing-driver-01.py',
             'qualify-supervisor-crash-fencing-01.py','start-supervisor-crash-fencing-01.py'):
    data=(r/'local'/name).read_bytes();ast.parse(data);(e/'scripts'/name).write_bytes(data)
print('Prepared reviewed crash-fencing qualification driver; builds limited to two jobs per host.')
