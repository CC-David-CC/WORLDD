import ast,hashlib,json
from pathlib import Path
root=Path('/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff/local/m06-scoped-input-05')
results=[]
for profile in ('Debug','ASan-UBSan'):
    receipt_path=root/profile/'server-build/run.json'
    receipt=json.loads(receipt_path.read_text())
    assert receipt['status']=='passed'
    assert receipt['sanitizers']==(profile=='ASan-UBSan')
    checked={}
    for name,item in receipt['binaries'].items():
        actual=hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()
        assert actual==item['sha256'],name
        checked[name]=actual
    for source,target in receipt['base_lock']['overlays'].items():
        actual=hashlib.sha256((Path(receipt['effective_source'])/target).read_bytes()).hexdigest()
        assert actual==receipt['extension_sources'][source],source
    wrapper=root/profile/'server-launch/Xvfb'
    assignments=[node for node in ast.parse(wrapper.read_text()).body if isinstance(node,ast.Assign)]
    server=next(ast.literal_eval(node.value) for node in assignments if any(isinstance(t,ast.Name) and t.id=='server' for t in node.targets))
    assert server==receipt['binaries']['Xvfb']['path']
    results.append(dict(profile=profile,binaries=checked,overlay_count=len(receipt['base_lock']['overlays']),wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest()))
print(json.dumps(results,indent=2))
