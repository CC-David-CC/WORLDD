"""Exercise the fixture's actual path guards without privileged filesystem operations."""
import ast
from pathlib import Path
import sys
import tempfile

helper = Path(sys.argv[1])
tree = ast.parse(helper.read_text())
guards = []
for statement in tree.body:
    text = ast.unparse(statement)
    if (isinstance(statement, ast.Assign) and text.startswith('fixture_local =')) or (
            isinstance(statement, ast.Assert) and
            (text.startswith('assert root.name.startswith') or text.startswith('assert root.is_relative_to'))):
        guards.append(statement)
assert len(guards) == 3
code = compile(ast.Module(body=guards, type_ignores=[]), str(helper), 'exec')
with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    source = base / 'frozen-checkout'
    local = source / 'local'
    local.mkdir(parents=True)
    script = source / 'tests/native/storage_session_volume.py'
    cases = [
        ('frozen-worktree', local / 'gate/native/test_native_storage_valid', True),
        ('sibling-worktree', base / 'other-checkout/local/test_native_storage_foreign', False),
        ('outside-local', source / 'test_native_storage_outside', False),
        ('wrong-test-prefix', local / 'unrelated', False),
        ('local-root', local, False),
    ]
    for name, path, expected in cases:
        path.mkdir(parents=True, exist_ok=True)
        try:
            exec(code, {'Path': Path, '__file__': str(script), 'root': path.resolve()})
            accepted = True
        except AssertionError:
            accepted = False
        assert accepted == expected, name
        print(name, 'passed')
    escape = local / 'test_native_storage_escape'
    escape.symlink_to(base / 'other-checkout/local/test_native_storage_foreign', target_is_directory=True)
    try:
        exec(code, {'Path': Path, '__file__': str(script), 'root': escape.resolve(strict=True)})
    except AssertionError:
        print('symlink-escape passed')
    else:
        raise AssertionError('Resolved symlink escaped fixture local root')
