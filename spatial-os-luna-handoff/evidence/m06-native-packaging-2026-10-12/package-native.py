import hashlib,json,os,tarfile
from pathlib import Path
root=Path("/home/dflanag3/src/spacial-m06-coordination-20261012")
project=root/"spatial-os-luna-handoff"; out=root/"local/m06-host-controller"
files=[]
for folder in ("tests","harness","config","native"):
 for p in (project/folder).rglob("*"):
  if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc",".pyo"):
   files.append((p,"project/"+str(p.relative_to(project))))
required=("spatial-host-actor-controller-probe","spatial-host-actor-native-runner","spatial-session-world-probe","worldd","spatialwm","identityd","spatial-authority-supervisor","spatial-authority-launch-probe","spatial-security-auth","spatial-issuer-client","workspaced","spatial-app-init")
for profile in ("debug","asan"):
 build=out/("run02-build-"+profile)
 for name in required:
  p=build/name
  assert p.is_file() and os.access(p,os.X_OK),f"Missing {p}"
  files.append((p,profile+"/bin/"+name))
 for module in ("compositor_native_pb2.py", "host_actor_controller_pb2.py", "world_session_pb2.py", "core_pb2.py", "broker_pb2.py", "issuer_pb2.py", "launch_pb2.py", "native_scope_pb2.py", "process_scope_pb2.py", "security_pb2.py"):
  assert (build/"python"/module).is_file(),f"Missing generated binding {module}"
 assert (build/"runtime.textproto").is_file(),"Missing runtime configuration"
 files.append((build/"runtime.textproto",profile+"/bin/runtime.textproto"))
 for p in (build/"python").rglob("*.py"): files.append((p,profile+"/python/"+str(p.relative_to(build/"python"))))
manifest="".join(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+name+chr(10) for p,name in files)
(out/"bundle.sha256").write_text(manifest)
with tarfile.open(out/"attempt02-bundle.tar.gz","w:gz") as archive:
 for p,name in files: archive.add(p,arcname=name,recursive=False)
 archive.add(out/"bundle.sha256",arcname="bundle.sha256")
print(json.dumps({"files":len(files),"sha256":hashlib.sha256((out/"attempt02-bundle.tar.gz").read_bytes()).hexdigest()}))
