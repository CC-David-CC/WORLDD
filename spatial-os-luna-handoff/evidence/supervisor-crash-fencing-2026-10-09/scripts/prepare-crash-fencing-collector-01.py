import ast
from pathlib import Path
r=Path(__file__).resolve().parents[1]
value=(r/'local/collect-supervisor-fencing-01.py').read_text(encoding='utf-8')
value=value.replace('supervisor-fencing','supervisor-crash-fencing').replace('293','294').replace('1443','1453').replace('(98 if full else 1)','(99 if full else 1)')
old="assert schema['existing_descriptors_unchanged'] and schema['existing_files']==13\nassert schema['added_record_field']==10 and schema['added_request_field']==20"
assert old in value
value=value.replace(old,"assert schema['existing_schema_sources_unchanged'] and schema['existing_tests_unchanged'] and schema['locked_files']==41")
value=value.replace("('before.pb','after.pb','schema-audit.json')","('schema-audit.json',)")
ast.parse(value)
name='collect-supervisor-crash-fencing-01.py'
(r/'local'/name).write_bytes(value.encode())
e=r/'evidence/supervisor-crash-fencing-2026-10-09/scripts'
(e/name).write_bytes(value.encode())
(e/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
print('Prepared guarded crash-fencing collector:294source,41locks,1453core,99suites,51harness,151P10.')
