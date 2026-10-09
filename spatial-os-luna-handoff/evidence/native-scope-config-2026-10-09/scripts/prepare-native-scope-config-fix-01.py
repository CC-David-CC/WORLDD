import difflib, hashlib, json, pathlib

r = pathlib.Path(__file__).resolve().parents[1]
source = r / 'local/authority-supervisor-draft-01'
evidence = r / 'evidence/native-scope-config-2026-10-09'
manifest = json.loads((r / 'local/authority-supervisor-candidate-01.json').read_text())['files']
edits = {
    'src/workspace/native_launch.cpp': [
        ('    Fd input{fd}; struct stat info{};', '    struct stat info{};'),
        ('workspace::v1::NativeLaunchConfig configuration(int fd) {\n',
         'workspace::v1::NativeLaunchConfig configuration(int fd) {\n    Fd input{fd}; // NativeLaunch consumes its inherited configuration; the reader only borrows it.\n'),
    ],
    'src/supervisor/native_scope.hpp': [
        ('    explicit NativeScope(int sealed_config_fd);',
         '    // Borrows the sealed descriptor. The caller retains ownership on success and failure.\n    explicit NativeScope(int sealed_config_fd);'),
    ],
    'src/workspace/native_launch.hpp': [
        ('    explicit NativeLaunch(int sealed_config_fd);',
         '    // Consumes sealed_config_fd while reading its configuration.\n    explicit NativeLaunch(int sealed_config_fd);'),
    ],
    'src/testing/native_scope_probe.cpp': [
        ('        scope = std::make_unique<spatial::supervisor::NativeScope>(std::stoi(argv[1]));',
         '        const int config_fd = std::stoi(argv[1]);\n        scope = std::make_unique<spatial::supervisor::NativeScope>(config_fd);\n        close(config_fd);'),
    ],
}
patch = ''
hashes = {}
for name, replacements in edits.items():
    original = (source / name).read_bytes()
    assert hashlib.sha256(original).hexdigest() == manifest[name], name
    before = original.decode('utf-8'); after = before
    for old, new in replacements:
        assert after.count(old) == 1, name
        after = after.replace(old, new)
    patch += ''.join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile='a/' + name, tofile='b/' + name))
    hashes[name] = dict(before=manifest[name], proposed_after=hashlib.sha256(after.encode('utf-8')).hexdigest())
(evidence / 'proposed-fix-01.patch').write_text(patch, encoding='utf-8', newline='\n')
(evidence / 'proposed-fix-01.json').write_text(json.dumps(dict(status='Proposed, not applied or tested.',
    base='authority-supervisor-candidate-01', files=hashes, production_changed=False,
    prerequisite='Preserve exact ownership-loss baseline before applying this correction.'), indent=2) + '\n', encoding='utf-8', newline='\n')
print('Prepared four-file ownership correction. No candidate or production source changed.')
