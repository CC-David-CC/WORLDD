#!/usr/bin/env python3
"""Recompute local report closure. Self-consistency is NOT trust or LOCAL_READY."""
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kernel.canonical import loads
from kernel.crypto import verify
from kernel.objects import identity,verify_bytes

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('directory',type=Path)
a=p.parse_args()
run=loads((a.directory/'run.json').read_bytes())
actor=verify(run,kind='local-conformance-run')
for cid in run['refs'].values():
    obj=verify_bytes(cid,(a.directory/(cid.split(':')[-1]+'.json')).read_bytes())
    if obj['type']=='test-results':
        assert obj['body']['count']==len(obj['body']['results'])
        assert all(x['status']=='PASS' for x in obj['body']['results'])
assert run['body']['readiness']=='NOT_ISSUED'
print('Verified fixture evidence closure:',identity(run))
print('Self-asserted fixture signer:',actor)
print('LOCAL_READY was not issued. No networking authorization exists.')
