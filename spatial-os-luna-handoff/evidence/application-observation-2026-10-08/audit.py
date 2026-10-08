import pathlib,hashlib,json,subprocess
p=pathlib.Path('/home/dflanag3/src/spacial_desktop')
base='180ee5537feaf80320b94958061aee8f08075bdf'
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=p,text=True).strip()==base
names=['CMakeLists.txt','src/platform/broker.hpp','src/platform/broker.cpp','src/world/surfaces.hpp','src/world/surfaces.cpp','src/world/actors.hpp','src/world/actors.cpp','src/world/identity_sessions.cpp','tests/core/test_application_observation.py']
hashes={'spatial-os-luna-handoff/'+name:hashlib.sha256((p/'spatial-os-luna-handoff'/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in names}
lock='spatial-os-luna-handoff/acceptance.lock.json'
original=subprocess.check_output(['git','show',base+':'+lock],cwd=p)
assert (p/lock).read_bytes()==original
unchanged=subprocess.check_output(['git','diff','--name-only','--','spatial-os-luna-handoff/proto','spatial-os-luna-handoff/tests/acceptance','spatial-os-luna-handoff/native/xprobe.c','spatial-os-luna-handoff/server'],cwd=p,text=True)
assert not unchanged,unchanged
out=p/'spatial-os-luna-handoff/local/m04-application-observation-audit-02'
out.mkdir(exist_ok=False)
data={'base_commit':base,'tested_inputs':hashes,'acceptance_lock_sha256':hashlib.sha256(original).hexdigest(),'locked_schemas_tests_server_unchanged':True}
(out/'run.json').write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps(data))
