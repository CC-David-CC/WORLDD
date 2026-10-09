import hashlib, json, os, pathlib, shlex, socket, subprocess

r = pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
original = r / 'local/m02-participant-relay-01'
# This diagnostic must not contaminate the running timed full qualification.
# A retained failed terminal profile also ends the continuation driver.
for profile in ('debug', 'runtime-sanitize'):
    completed = json.loads((original / profile / 'run-full.json').read_text())
    if any(item['exit_code'] != 0 for item in completed['results']):
        break
    assert len(completed['results']) == 6 and completed['results'][-1]['name'] == 'P10'
source = original / 'source'
manifest = json.loads((r / 'local/participant-relay-candidate-01.json').read_text())['files']
for name, digest in manifest.items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
out = r / 'local/native-scope-config-baseline-01'
out.mkdir()
server = json.loads((r / 'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest() == server['sha256']
results = []
linked_inputs = {}
for profile in ('debug', 'runtime-sanitize'):
    folder = out / profile
    folder.mkdir()
    build = original / profile / 'build'
    query = subprocess.run(['ninja', '-C', str(build), '-t', 'commands', 'spatial-native-scope-probe'],
                           capture_output=True, text=True, check=True)
    lines = [line for line in query.stdout.splitlines() if 'native_scope_probe.cpp.o' in line and
             (' -c ' in line or ' -o spatial-native-scope-probe ' in line)]
    assert len(lines) == 2
    compile_original, link_original = map(shlex.split, lines)
    compile_command = []
    index = 0
    while index < len(compile_original):
        item = compile_original[index]
        if item in ('-MT', '-MF'):
            index += 2
        elif item == '-MD':
            index += 1
        elif item == '-o':
            compile_command += [item, str(folder / 'witness.o')]; index += 2
        elif item == '-c':
            compile_command += [item, str(r / 'local/native-scope-config-witness.cpp')]; index += 2
        else:
            compile_command.append(item); index += 1
    assert link_original[:2] == [':', '&&'] and link_original[-2:] == ['&&', ':']
    link_command = link_original[2:-2]
    object_name = 'CMakeFiles/spatial-native-scope-probe.dir/src/testing/native_scope_probe.cpp.o'
    assert link_command.count(object_name) == 1
    link_command[link_command.index(object_name)] = str(folder / 'witness.o')
    link_command[link_command.index('-o') + 1] = str(folder / 'witness')
    inputs = {}
    for item in link_command[1:]:
        path = build / item
        if (item.endswith('.o') or item.endswith('.a') or item.endswith('.so')) and path.is_file():
            inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    linked_inputs[profile] = inputs
    env = dict(os.environ, PYTHONPATH=str(build / 'python') + ':' + str(source),
        SPATIAL_TEST_XSERVER=server['path'], SPATIAL_SECURITY_EVIDENCE=str(folder / 'server'),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands = [('compile', compile_command), ('link', link_command),
        ('test', ['python3', str(r / 'local/test_native_scope_config_ownership.py'),
         str(folder / 'witness'), str(source), str(build / 'spatial-security-auth')])]
    for name, command in commands:
        with (folder / (name + '.log')).open('wb') as log:
            run = subprocess.run(command, cwd=build, env=env, stdout=log, stderr=subprocess.STDOUT)
        results.append(dict(profile=profile, name=name, command=command, exit_code=run.returncode))
        (out / 'run.json').write_text(json.dumps(dict(host=host, results=results, linked_inputs=linked_inputs,
            source_files=len(manifest), production_changed=False, server=server), indent=2) + '\n')
        print(host, profile, name, run.returncode, flush=True)
        if name != 'test':
            assert run.returncode == 0, (folder / (name + '.log')).read_text()
        else:
            log = (folder / 'test.log').read_text()
            assert run.returncode == 1 and 'Ran 3 tests in ' in log and 'FAILED (failures=2)' in log, log
            assert 'FAIL: NativeScope consumed or replaced the caller-owned configuration descriptor' in log, log
            assert 'FAIL: NativeScope consumed the caller-owned rejected configuration descriptor' in log, log
            assert 'PASS: NativeLaunch rejection retains its consuming interface' in log, log
            print(host, profile, 'descriptor ownership defect reproduced against unchanged native production object', flush=True)
    for name, digest in inputs.items():
        assert hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() == digest
print(host, 'baseline defect reproduced in both profiles; no acceptance pass', flush=True)
