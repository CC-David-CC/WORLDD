import ast, hashlib, json, os, socket
from pathlib import Path
root = Path("/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff/local/m04-full-gate-01")
report = {"host":socket.gethostname(),"kind":"read-only native prerequisites","profiles":[]}
for profile in ("Debug","ASan-UBSan"):
    wrapper=root/profile/"server-launch/Xvfb"
    data=wrapper.read_bytes()
    tree=ast.parse(data)
    server=None
    environment={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="server" for t in node.targets):
            server=Path(ast.literal_eval(node.value))
        if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=="update":
            environment.update(ast.literal_eval(node.value.args[0]))
    assert server and server.is_file() and os.access(server,os.X_OK), "missing executable server"
    dependencies=[]
    for key,value in environment.items():
        if key=="LD_LIBRARY_PATH":
            dependencies.extend(value.split(":"))
        elif key=="__EGL_VENDOR_LIBRARY_FILENAMES":
            dependencies.extend(value.split(":"))
    assert all(Path(p).exists() for p in dependencies), "missing wrapper dependency"
    report["profiles"].append({"profile":profile,"wrapper":str(wrapper),"wrapper_sha256":hashlib.sha256(data).hexdigest(),"server":str(server),"server_sha256":hashlib.sha256(server.read_bytes()).hexdigest(),"dependency_paths_present":True})
print(json.dumps(report,indent=2))