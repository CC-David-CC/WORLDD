import hashlib,json,re,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];e=r/'evidence/launch-authorization-2026-10-09'
manifest=json.loads((e/'manifests/launch-authorization-candidate-03.json').read_text(encoding='utf-8'))['files']
for n,h in manifest.items():assert hashlib.sha256((r/'local/launch-authorization-review-03'/n).read_bytes()).hexdigest()==h,n
for host in ['sb16','sb16-2']:
    with tarfile.open(r/('local/launch-authorization-debug-'+host+'-03.tar.gz')) as a:a.extractall(e,filter='data')
    folder=e/'candidate-03'/host;summary=json.loads((folder/'summary-debug.json').read_text(encoding='utf-8'))
    assert summary['source_files']==273 and summary['locked_files']==41 and summary['production_unchanged_from_candidate01']
    result=summary['debug'];assert tuple(result[k] for k in ['checks','new_checks','existing_checks','suites','harness_checks','failed','skipped'])==(285,23,262,16,51,0,0)
    log=(folder/'debug/focused.log').read_text(encoding='utf-8');counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
    assert len(counts)==16 and sum(counts)==285 and '100% tests passed, 0 tests failed out of 16' in log
    assert 'test_client_configuration_remains_private_and_unchanged_for_delayed_readers' in log
    assert summary['schema']['existing_descriptors_unchanged']
    log=(e/'fixture-config-overwrite'/host/'diagnostic.log').read_text(encoding='utf-8')
    assert 'Another client configuration overwrote the first launch input' in log and 'FAILED (failures=1)' in log
    incarnation=json.loads((r/'evidence/participant-incarnation-2026-10-09/full-candidate-01'/host/'summary-final.json').read_text(encoding='utf-8'))
    for profile in ['debug','runtime-sanitize']:
        result=incarnation[profile]
        assert tuple(result[k] for k in ['core_checks','core_suites','harness_checks','P10_cases','failed','skipped'])==(1287,84,51,151,0,0)
p=e/'candidate-03-review.json';review=json.loads(p.read_text(encoding='utf-8'));review['qualification']='Both hosts passed285 focused Debug checks and51harness. Runtime sanitizer qualification is running. No source integrated.';p.write_text(json.dumps(review,indent=2)+'\n',encoding='utf-8',newline='\n')
print('Verified candidate03 Debug results, descriptor preservation, failure evidence, and incarnation full results on both hosts.')
