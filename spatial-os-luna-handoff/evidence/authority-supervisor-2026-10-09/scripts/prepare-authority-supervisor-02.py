"""Apply the correction after verified baseline evidence; both hosts still require qualification."""
import argparse, ast, difflib, hashlib, json, pathlib, shutil, subprocess, tarfile, tempfile

r = pathlib.Path(__file__).resolve().parents[1]
local = r / 'local'
evidence = r / 'evidence/authority-supervisor-2026-10-09'
ownership = r / 'evidence/native-scope-config-2026-10-09'
parser = argparse.ArgumentParser()
parser.add_argument('--baseline-host', choices=('sb16', 'sb16-2'), action='append')
baseline_hosts = parser.parse_args().baseline_host or ['sb16', 'sb16-2']
for host in baseline_hosts:
    baseline = json.loads((ownership / 'baseline-01' / host / 'summary.json').read_text())
    assert baseline['source_files'] == 277 and baseline['production_changed'] is False
    assert baseline['expected_failures_per_profile'] == 2 and baseline['passed_controls_per_profile'] == 1
    for profile in ('debug', 'runtime-sanitize'):
        log = (ownership / 'baseline-01' / host / profile / 'test.log').read_text()
        assert 'FAILED (failures=2)' in log and 'Ran 3 tests in ' in log
        assert 'FAIL: NativeScope consumed or replaced the caller-owned configuration descriptor' in log
        assert 'FAIL: NativeScope consumed the caller-owned rejected configuration descriptor' in log
        assert 'PASS: NativeLaunch rejection retains its consuming interface' in log
source = local / 'authority-supervisor-draft-02'
source.mkdir()
with tarfile.open(local / 'authority-supervisor-candidate-01.tar') as archive:
    archive.extractall(source, filter='data')
subprocess.run(['git', 'apply', '--unsafe-paths', str(ownership / 'proposed-fix-01.patch')], cwd=source, check=True)
for name, digest in json.loads((ownership / 'proposed-fix-01.json').read_text())['files'].items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest['proposed_after'], name
shutil.copyfile(local / 'native-scope-config-witness.cpp', source / 'src/testing/native_scope_config_witness.cpp')
shutil.copyfile(local / 'test_native_scope_config_ownership.py', source / 'tests/native/test_native_scope_config_ownership.py')
path = source / 'CMakeLists.txt'
text = path.read_text()
marker = '    add_test(NAME authority-native-scope COMMAND "${Python3_EXECUTABLE}"'
assert text.count(marker) == 1
addition = '''    add_executable(spatial-native-config-witness src/testing/native_scope_config_witness.cpp)
    target_link_libraries(spatial-native-config-witness PRIVATE spatial-authority-launch)
    add_test(NAME authority-native-config COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_native_scope_config_ownership.py"
        $<TARGET_FILE:spatial-native-config-witness> "${CMAKE_CURRENT_SOURCE_DIR}" $<TARGET_FILE:spatial-security-auth>)
    set_tests_properties(authority-native-config PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")

'''
path.write_text(text.replace(marker, addition + marker), encoding='utf-8', newline='\n')
base = json.loads((local / 'participant-relay-candidate-01.json').read_text())['files']
prior = json.loads((local / 'authority-supervisor-candidate-01.json').read_text())['files']
names = sorted([*prior, 'src/testing/native_scope_config_witness.cpp', 'tests/native/test_native_scope_config_ownership.py'])
files = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names}
assert len(files) == 281
for name in base:
    if name.startswith('tests/'):
        assert files[name] == base[name], name
for name, digest in json.loads((source / 'acceptance.lock.json').read_text())['files'].items():
    assert files[name] == digest
assert files['tests/native/test_authority_supervisor.py'] == prior['tests/native/test_authority_supervisor.py']
for name in ('tests/native/test_native_scope_config_ownership.py', 'tests/native/test_authority_supervisor.py'):
    ast.parse((source / name).read_text())
manifest = dict(base='participant-relay-candidate-01', candidate=2, files=files, new_checks=17,
    ownership_correction='NativeScope borrows input; NativeLaunch keeps consuming input.', qualified=False, integrated=False)
target = local / 'authority-supervisor-candidate-02'
assert not target.with_suffix('.json').exists()
target.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8', newline='\n')
with tarfile.open(target.with_suffix('.tar'), 'w') as archive:
    for name in files:
        archive.add(source / name, arcname=name, recursive=False)
with tempfile.TemporaryDirectory(dir=local, prefix='supervisor02-reconstruction-') as temporary:
    before = pathlib.Path(temporary)
    with tarfile.open(local / 'participant-relay-candidate-01.tar') as archive:
        archive.extractall(before, filter='data')
    patch = ''
    changed = []
    for name, digest in files.items():
        if digest == base.get(name):
            continue
        changed.append(name)
        old = (before / name).read_text().splitlines(keepends=True) if name in base else []
        patch += ''.join(difflib.unified_diff(old, (source / name).read_text().splitlines(keepends=True),
            fromfile='a/' + name if old else '/dev/null', tofile='b/' + name))
    patch_path = evidence / 'candidate-02.patch'
    patch_path.write_text(patch, encoding='utf-8', newline='\n')
    subprocess.run(['git', 'apply', '--unsafe-paths', str(patch_path)], cwd=before, check=True)
    for name, digest in files.items():
        assert hashlib.sha256((before / name).read_bytes()).hexdigest() == digest, name
shutil.copyfile(target.with_suffix('.json'), evidence / 'candidate-source-02.json')
qualifier = (local / 'qualify-authority-supervisor-01.py').read_text()
start = qualifier.index('# Avoid overlapping builds')
end = qualifier.index("out=r/'local/m02-authority-supervisor-01';")
qualifier = qualifier[:start] + '''baseline=json.loads((r/'local/native-scope-config-baseline-01/summary.json').read_text())
assert baseline['expected_failures_per_profile']==2 and baseline['passed_controls_per_profile']==1
''' + qualifier[end:]
qualifier = qualifier.replace('m02-authority-supervisor-01', 'm02-authority-supervisor-02').replace('authority-supervisor-candidate-01', 'authority-supervisor-candidate-02')
qualifier = qualifier.replace("        ('supervisor',['ctest'", "        ('ownership',['ctest','--test-dir',str(build),'-V','-R','^authority-native-config$']),\n        ('supervisor',['ctest'")
ast.parse(qualifier)
(local / 'qualify-authority-supervisor-02.py').write_text(qualifier, encoding='utf-8', newline='\n')
collector = (local / 'collect-authority-supervisor-01.py').read_text()
collector = collector.replace('m02-authority-supervisor-01', 'm02-authority-supervisor-02').replace('authority-supervisor-candidate-01', 'authority-supervisor-candidate-02')
collector = collector.replace('candidate=1', 'candidate=2').replace("'candidate-01/'", "'candidate-02/'").replace("+ '-01.tar.gz'", "+ '-02.tar.gz'")
collector = collector.replace("'harness', 'supervisor', 'focused'", "'harness', 'ownership', 'supervisor', 'focused'")
collector = collector.replace("(('supervisor', 14, 1),", "(('ownership', 3, 1), ('supervisor', 14, 1),")
collector = collector.replace('checks=380, suites=24, new_checks=14', 'checks=383, suites=25, new_checks=17')
collector = collector.replace("'harness.log', 'supervisor.log'", "'harness.log', 'ownership.log', 'supervisor.log'")
collector = collector.replace(".glob('AuthoritySupervisorTests-*.log')", ".glob('*.log')")
ast.parse(collector)
(local / 'collect-authority-supervisor-02.py').write_text(collector, encoding='utf-8', newline='\n')
for name in ('qualify-authority-supervisor-02.py', 'collect-authority-supervisor-02.py', 'prepare-authority-supervisor-02.py'):
    shutil.copyfile(local / name, evidence / 'scripts' / name)
(evidence / 'source-review-02.json').write_text(json.dumps(dict(status='Prepared after verified ownership baseline; Linux qualification pending.',
    baseline_hosts=baseline_hosts, required_qualification_hosts=['sb16', 'sb16-2'],
    candidate=2, source_files=281, changed_files=changed, new_checks=17, existing_tests_unchanged=True,
    supervisor_candidate01_tests_unchanged=True, locked_files_unchanged=41,
    patch_reconstruction='All281 hashes match applying the patch to frozen relay source.', integrated=False, qualified=False), indent=2) + '\n', encoding='utf-8', newline='\n')
print('Prepared corrected281-file supervisor candidate02 with17 new tests; no integration or runtime pass claimed.')
