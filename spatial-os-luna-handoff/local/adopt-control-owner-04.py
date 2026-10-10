"""Adopt exactly qualified source without rebuilding, restarting, or migrating operator state."""
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');repo=r.parent
host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2') and len(sys.argv)==2
expected=sys.argv[1];assert len(expected)==40 and all(c in '0123456789abcdef' for c in expected)
def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
launch=json.loads((r/'local/m02-control-owner-04/launch.json').read_text())
try:active=(Path('/proc')/str(launch['pid'])/'cmdline').read_bytes()
except FileNotFoundError:active=b''
assert b'qualify-control-owner-04.py' not in active,'Full qualification is still active'
assert git('status','--porcelain')=='' and git('branch','--show-current')=='main'
assert git('remote','get-url','origin')=='dflanag3@box-git:/srv/data/git/spacial_desktop.git'
manifest=json.loads((r/'local/control-owner-candidate-04.json').read_text())['files']
assert len(manifest)==297
source=r/'local/m02-control-owner-04/source'
assert all(sha(source/n)==v for n,v in manifest.items())
summary_path=r/'local/m02-control-owner-04/collected-final.json'
summary=json.loads(summary_path.read_text())
assert summary['host']==host and summary['source_manifest']==manifest and summary['mode']=='final'
run=json.loads((r/'local/m02-control-owner-04/qualification/summary.json').read_text())
assert run['status']=='passed' and not run['compiler_overlap'] and run['source_manifest']==manifest
for profile in ('debug','runtime-sanitize'):
    value=summary['profiles'][profile]
    assert tuple(value[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','native_world_checks','failed','skipped'))==(1474,100,51,151,3,0,0)
    build=r/'local/m02-control-owner-04'/profile/'build'
    assert all(sha(build/n)==v for n,v in value['binaries'].items())
before=git('rev-parse','HEAD')
subprocess.run(['git','-C',str(repo),'fetch','origin','main'],check=True)
assert git('rev-parse','origin/main')==expected
record=json.loads(git('show',expected+':spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/integration-applied-04.json'))
assert record['integrated'] and record['source_files']==manifest
assert sha(summary_path)==record['summary_hashes'][host]
# Read and check every target Git object before changing the checkout.
names=sorted(manifest)
payload=''.join(expected+':spatial-os-luna-handoff/'+n+'\n' for n in names).encode()
stream=io.BytesIO(subprocess.check_output(['git','-C',str(repo),'cat-file','--batch'],input=payload))
for name in names:
    header=stream.readline().split();assert len(header)==3 and header[1]==b'blob',name
    data=stream.read(int(header[2]));assert stream.read(1)==b'\n'
    assert hashlib.sha256(data).hexdigest()==manifest[name],name
subprocess.run(['git','-C',str(repo),'merge','--ff-only',expected],check=True,stdout=subprocess.DEVNULL)
assert git('rev-parse','HEAD')==expected and git('status','--porcelain')==''
assert all(sha(r/n)==v for n,v in manifest.items())
receipt=dict(host=host,before=before,commit=expected,source_hashes_matched=297,
    qualified_source_exact=True,worktree_clean=True,runtime_restarted=False,operator_state_migrated=False)
(r/('local/control-owner-adoption-'+host+'-04.json')).write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
