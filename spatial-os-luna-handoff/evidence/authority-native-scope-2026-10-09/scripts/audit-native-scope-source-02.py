import ast,hashlib,json,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1]
s=r/'local/authority-native-scope-draft-01'
base=json.loads((r/'local/participant-fixtures-candidate-09.json').read_text(encoding='utf-8'))['files']
manifest=json.loads((r/'local/authority-native-scope-candidate-02.json').read_text(encoding='utf-8'))['files']
for n,h in manifest.items():assert hashlib.sha256((s/n).read_bytes()).hexdigest()==h,n
locked=json.loads((s/'acceptance.lock.json').read_text(encoding='utf-8'))['files']
for n,h in locked.items():assert manifest[n]==h,n
for n,h in base.items():
    if n.startswith(('tests/','harness/')):assert manifest[n]==h,n
with tarfile.open(r/'local/participant-fixtures-candidate-09.tar') as a:
    before=a.extractfile('src/workspace/native_launch.cpp').read().decode()
after=(s/'src/workspace/native_launch.cpp').read_text(encoding='utf-8')
old=before[before.index('    impl_->number ='):before.index('NativeLaunch::~NativeLaunch()')]
new=after[after.index('    number ='):after.index('NativeLaunch::NativeLaunch(int sealed_config_fd)')].rstrip()+'\n'
assert old.replace('impl_->','').replace('identity.identity().role() != native_security::BROKER','identity.identity().role() != role')==new
tail=before[before.index('NativeLaunch::~NativeLaunch()'):].rstrip()[:-1].strip()
same=after[after.index('NativeLaunch::~NativeLaunch()'):after.index('\nnamespace supervisor {')].strip()
assert tail==same,'Existing application issue, cancellation, and checkpoint behavior changed'
assert 'impl_->connect_server(configuration(sealed_config_fd), native_security::BROKER);' in after
assert 'impl_->native->connect_server(connection, native_security::SUPERVISOR);' in after
test=ast.parse((s/'tests/native/test_authority_native_scope.py').read_text(encoding='utf-8'))
cases=[m.name for c in test.body if isinstance(c,ast.ClassDef) and c.name=='NativeScopeTests'
       for m in c.body if isinstance(m,ast.FunctionDef) and m.name.startswith('test_')]
assert len(cases)==11
print(json.dumps(dict(candidate=2,source_files=len(manifest),locked_files=len(locked),
    existing_tests='unchanged',broker_connection_refactor='equivalent with same BROKER role',
    existing_application_methods='identical',new_cases=cases,integrated=False),indent=2))
