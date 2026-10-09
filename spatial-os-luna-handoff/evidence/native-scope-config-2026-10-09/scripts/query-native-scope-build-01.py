import json, pathlib, shlex, socket, subprocess

r = pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
build = r / 'local/m02-participant-relay-01/debug/build'
run = subprocess.run(['ninja', '-C', str(build), '-t', 'commands', 'spatial-native-scope-probe'], capture_output=True, text=True, check=True)
commands = []
for line in run.stdout.splitlines():
    if 'native_scope_probe.cpp.o' in line and (' -c ' in line or ' -o spatial-native-scope-probe ' in line):
        commands.append(line)
assert len(commands) == 2, commands
print(json.dumps(dict(host=host, commands=commands)), flush=True)
