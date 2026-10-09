import ast
from pathlib import Path
import shutil
r = Path(__file__).resolve().parents[1]
text = (r / 'local/qualify-supervisor-broker-handoff-02.py').read_text()
old = """source.mkdir(parents=True)
with tarfile.open(r / 'local/supervisor-broker-handoff-candidate-02.tar') as archive:
    archive.extractall(source, filter='data')
"""
assert old in text
text = text.replace(old, "assert source.is_dir(), 'Continuation requires the preserved candidate source'\n")
text = text.replace("    target.mkdir()", "    target.mkdir(exist_ok=True)")
text = text.replace("    results = []\n    for name, command in commands:\n", """    prior = target / 'run.json'
    results = json.loads(prior.read_text())['results'] if prior.exists() else []
    assert len(results) <= len(commands)
    for index, result in enumerate(results):
        assert result == dict(name=commands[index][0], command=commands[index][1], exit_code=0)
    for name, command in commands[len(results):]:
        assert not (target / (name + '.log')).exists(), 'Unrecorded command requires diagnosis: ' + name
""")
guard = """for path in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        if int(path.parent.name) == os.getpid() or path.parent.stat().st_uid != os.geteuid():
            continue
        args = path.read_bytes().split(b'\\0')
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        continue
    if len(args) >= 2 and Path(os.fsdecode(args[0])).name.startswith('python'):
        assert Path(os.fsdecode(args[1])).name not in ('qualify-supervisor-broker-handoff-02.py', 'finish-supervisor-broker-handoff-02.py'), 'Existing candidate02 driver remains active'
"""
text = text.replace("out = r / 'local/m02-supervisor-broker-handoff-02'", guard + "out = r / 'local/m02-supervisor-broker-handoff-02'")
ast.parse(text)
path = r / 'local/finish-supervisor-broker-handoff-02.py'
path.write_text(text, newline='\n')
shutil.copyfile(path, r / 'evidence/supervisor-broker-handoff-2026-10-09/scripts' / path.name)
