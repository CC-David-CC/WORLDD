from pathlib import Path
import json
roots=[Path('/home/dflanag3/spatial-test-artifacts/m06-transition-combined-20261012-01'),Path('/home/dflanag3/spatial-test-artifacts/m06-transition-combined-host-20261012-02')]
markers=[b'AddressSanitizer',b'LeakSanitizer',b'runtime error:']
files=[];hits=[]
for root in roots:
 paths=list((root/'evidence').rglob('*.log'))+list(root.glob('*runtime.log'))
 for p in paths:
  data=p.read_bytes();files.append(str(p))
  for marker in markers:
   if marker in data:hits.append(dict(file=str(p),marker=marker.decode()))
print(json.dumps(dict(log_count=len(files),hits=hits,roots=[str(p) for p in roots]),indent=2))
raise SystemExit(bool(hits))