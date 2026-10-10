"""Prepare exact frozen294 integration; require all four raw profiles before applying."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re

parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
r=Path(__file__).resolve().parents[1]
e=r/'evidence/supervisor-crash-fencing-2026-10-09'
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def digest(value):return hashlib.sha256(value).hexdigest()
def normalized(path):return path.read_bytes().replace(b'\r\n',b'\n')
base=read(r/'evidence/supervisor-broker-handoff-2026-10-09/integration-applied-02.json')['files']
manifest=read(r/'local/supervisor-crash-fencing-candidate-01.json')['files']
draft=r/'local/supervisor-crash-fencing-draft-01'
assert len(base)==283 and len(manifest)==294 and set(base)<=set(manifest)
assert all(digest(normalized(r/n))==v for n,v in base.items())
contents={n:normalized(draft/n) for n in manifest}
assert {n:digest(v) for n,v in contents.items()}==manifest
changed=sorted(n for n in manifest if base.get(n)!=manifest[n]);assert len(changed)==37
for name in set(manifest)-set(base):assert not (r/name).exists(),name
locks=read(r/'acceptance.lock.json')['files']
assert len(locks)==41 and all(manifest[n]==v for n,v in locks.items())
static=read(e/'combined-static-review-04.json')
assert static['reviewer']=='primary agent' and static['source_files']==manifest
assert static['reviewed_files']==changed and static['unresolved_static_defects']==[]
patch=[]
for n in changed:
    before=normalized(r/n).decode().splitlines(True) if n in base else []
    patch.extend(difflib.unified_diff(before,contents[n].decode().splitlines(True),
        fromfile='a/'+n if n in base else '/dev/null',tofile='b/'+n))
(e/'integration-candidate-04.patch').write_bytes(''.join(patch).encode())
prepared=dict(files=manifest,base_manifest=base,changed=changed,source_files=294,
    base_files=283,locked_files=41,integrated=False,expected_core_checks=1453,
    expected_core_suites=99,expected_harness_checks=51,expected_P10_cases=151,
    source_delta='none: integrate the exact frozen294 source tested in serial02')
(e/'integration-prepared-04.json').write_bytes((json.dumps(prepared,indent=2)+'\n').encode())
if not args.apply:
    print('Prepared exact 37-file candidate integration. Production remains unchanged.')
    raise SystemExit(0)

proofs={}
for host in ('sb16','sb16-2'):
    folder=e/'serial-02'/host
    summary=read(folder/'summary-final.json')
    assert summary['host']==host and summary['mode']=='final' and summary['source_files']==294 and summary['locked_files']==41
    assert summary['source_manifest']==manifest and read(folder/'source-manifest.json')['files']==manifest
    run=read(folder/'run-final.json')
    assert run['host']==host and run['qualification']=='passed' and not run['compiler_overlap']
    assert run['source_manifest']==manifest
    monitor=[json.loads(line) for line in (folder/'compiler-monitor-final.jsonl').read_text().splitlines()]
    assert monitor and not any(x['compilers'] for x in monitor)
    proofs[host]=digest((folder/'summary-final.json').read_bytes())
    for profile in ('debug','runtime-sanitize'):
        value=summary['profiles'][profile]
        assert tuple(value[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped'))==(1453,99,51,151,0,0)
        results=run['profiles'][profile]['results']
        assert [x['name'] for x in results]==['harness','fencing','focused','core','P10']
        assert all(x['exit_code']==0 for x in results)
        assert value['commands']==results and value['binaries']==run['profiles'][profile]['binaries']
        assert monitor[0]['time']<=results[0]['start'] and monitor[-1]['time']>=results[-1]['end']-2
        suites=[];total=0
        for part in ('fencing','focused','core'):
            text=(folder/profile/(part+'.log')).read_text()
            counts=list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)))
            names=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',text,re.M)
            assert counts and len(counts)==len(names)
            assert f'100% tests passed, 0 tests failed out of {len(counts)}' in text
            assert '... skipped' not in text and 'skipped=' not in text
            assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',text)
            assert value['parts'][part]==dict(zip(names,counts))
            suites.extend(names);total+=sum(counts)
        inventory=read(folder/profile/'ctest-inventory.json')
        assert total==1453 and len(suites)==len(set(suites))==99
        assert set(suites)=={x['name'] for x in inventory['tests']}
        harness=(folder/profile/'harness.log').read_text()
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        assert '... skipped' not in harness and 'skipped=' not in harness
        p10=read(folder/profile/'P10.json')
        assert p10['status']=='passed' and p10['executed']==len(p10['tests'])==151
        assert all(x['status']=='passed' for x in p10['tests'])

final=read(e/'integration-primary-review-04.json')
assert final['reviewer']=='primary agent' and final['integration_ready'] is True
assert final['source_files']==manifest and final['reviewed_files']==changed
assert final['summary_hashes']==proofs and final['unresolved_static_defects']==[]
assert final['diagnosed_failures']=='evidence/world-bootstrap-runtime-failure-2026-10-09/issuer-sync-review-09.json'
for name in changed:
    (r/name).parent.mkdir(parents=True,exist_ok=True);(r/name).write_bytes(contents[name])
assert all(digest(normalized(r/n))==v for n,v in manifest.items())
prepared['integrated']=True;prepared['summary_hashes']=proofs
(e/'integration-applied-04.json').write_bytes((json.dumps(prepared,indent=2)+'\n').encode())
print('Integrated exact frozen294 after four full profiles and primary review.')
