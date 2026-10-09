"""Compare existing test assertions before the cumulative supervisor integration."""
import ast
import hashlib
import json
from pathlib import Path

r = Path(__file__).resolve().parents[1]
draft = r / 'local/authority-supervisor-draft-02'
base = json.loads((r / 'evidence/process-termination-2026-10-09/integration-applied-02.json').read_text(encoding='utf-8'))['files']
candidate = json.loads((r / 'local/authority-supervisor-candidate-02.json').read_text(encoding='utf-8'))['files']
assert len(base) == 242 and len(candidate) == 281 and set(base) <= set(candidate)
for tree, manifest in ((r, base), (draft, candidate)):
    for name, digest in manifest.items():
        assert hashlib.sha256((tree / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest, name
locked = json.loads((r / 'acceptance.lock.json').read_text(encoding='utf-8'))['files']
for name, digest in locked.items():
    assert hashlib.sha256((draft / name).read_bytes()).hexdigest() == digest, name

def methods(path):
    result = {}
    for cls in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(cls, ast.ClassDef):
            for method in cls.body:
                if isinstance(method, ast.FunctionDef) and method.name.startswith('test_'):
                    key = cls.name + '.' + method.name
                    assert key not in result
                    result[key] = method
    return result

def assertions(method):
    return [ast.unparse(call) for call in ast.walk(method)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        and call.func.attr.startswith('assert')]

migrations = {
    'BootstrapTests.test_explicit_v3_migration_preserves_credentials_and_is_repeatable',
    'BarrierTests.test_v5_migration_preserves_state_without_inventing_historical_completion',
    'ParticipantTests.test_v4_migration_preserves_credential_and_issuer_state',
    'RevocationTests.test_explicit_v2_migration_preserves_all_existing_credential_bytes',
}
refactored = {
    'NativeLaunchTests.test_broker_loss_revokes_native_access': 'Native access survived broker death',
    'NativeLaunchTests.test_exit_revokes_credentials_without_a_wait_request': 'Exited application retained its native credential',
}
seen = set()
total = identical = 0
changes = []
for name in sorted(base):
    if not name.startswith('tests/') or not name.endswith('.py'):
        continue
    before, after = methods(r / name), methods(draft / name)
    assert before.keys() <= after.keys(), name
    for key, original in before.items():
        total += 1
        updated = after[key]
        old, new = assertions(original), assertions(updated)
        if old == new:
            identical += 1
            continue
        seen.add(key)
        if key in migrations:
            expected = [item.replace("db.execute('PRAGMA user_version').fetchone()[0], 6)",
                "db.execute('PRAGMA user_version').fetchone()[0], 8)") for item in old]
            assert new == expected and new != old, (name, key)
            reason = 'Current issuer schema assertion advances from 6 to 8. All other assertions remain identical.'
        else:
            assert key in refactored, (name, key)
            message = refactored[key]
            removed = "self.assertLess(time.monotonic(), deadline, " + repr(message) + ")"
            assert new == [item for item in old if item != removed] and removed in old
            call = "self.wait_native_denied(bytes.fromhex(record['cookie']), " + repr(message) + ")"
            assert call in ast.unparse(updated)
            reason = 'The same deadline assertion moved into wait_native_denied. It still requires explicit protocol denial.'
        changes.append(dict(file=name, method=key, reason=reason))
assert seen == migrations | set(refactored)
tree = ast.parse((draft / 'tests/native/test_native_launch.py').read_text(encoding='utf-8'))
cls = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'NativeLaunchTests')
helper = next(item for item in cls.body if isinstance(item, ast.FunctionDef) and item.name == 'wait_native_denied')
text = ast.unparse(helper)
for required in ('deadline = time.monotonic() + 3', 'except native.Denied:',
                 'except (ConnectionError, EOFError):', 'self.assertLess(time.monotonic(), deadline, message)'):
    assert required in text, required
assert 'return' not in {type(node).__name__.lower() for node in ast.walk(helper)}
assert 'test_denial_wait_does_not_accept_a_live_credential' in {item.name for item in cls.body if isinstance(item, ast.FunctionDef)}
report = dict(status='Static test-integrity audit passed. Full supervisor qualification and integration review remain required.',
    base_files=len(base), candidate_files=len(candidate), locked_files=len(locked), existing_test_methods=total,
    identical_assertion_lists=identical, reviewed_assertion_changes=changes,
    changed_files=sorted(name for name in candidate if base.get(name) != candidate[name]),
    production_changed=False, integrated=False,
    migration_review='evidence/launch-authorization-2026-10-09/candidate-source-review.json',
    limitation='This AST audit does not prove fixture equivalence or replace runtime qualification and production code review.')
out = r / 'evidence/authority-supervisor-2026-10-09/integration-test-audit-02.json'
out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps({key: value for key, value in report.items() if key != 'changed_files'}))
