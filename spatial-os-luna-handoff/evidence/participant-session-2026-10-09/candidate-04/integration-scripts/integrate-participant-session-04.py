import argparse,ast,hashlib,json,pathlib,re,tarfile
root=pathlib.Path(__file__).resolve().parents[1];evidence=root/'evidence/participant-session-2026-10-09/candidate-04'
parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
base=json.loads((root/'evidence/participant-registry-2026-10-09/candidate-02/integration-applied.json').read_text())['files']
old=json.loads((root/'local/participant-session-candidate-03.json').read_text());new=json.loads((root/'local/participant-session-candidate-04.json').read_text())
fixture='tests/core/test_workspace_authority.py';assert [n for n in old['files'] if old['files'][n]!=new['files'][n]]==[fixture]
proposed={}
with tarfile.open(root/'local/participant-session-candidate-03.tar') as archive:
    for name,digest in old['files'].items():
        data=archive.extractfile(name).read();assert hashlib.sha256(data).hexdigest()==digest,name;proposed[name]=data
proposed[fixture]=(root/'evidence/workspace-owner-idle-2026-10-09/test_workspace_authority.proposed.py').read_bytes()
for name,data in proposed.items():assert hashlib.sha256(data).hexdigest()==new['files'][name],name
for name,digest in base.items():assert hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest,name
for host in ('sb16','sb16-2'):
    folder=evidence/host;summary=json.loads((folder/'summary-final.json').read_text())
    assert json.loads((folder/'source-manifest.json').read_text())['files']==new['files']
    assert json.loads((folder/'original-source-manifest.json').read_text())['files']==old['files']
    audit=json.loads((folder/'original-schema-audit.json').read_text());assert audit['status']=='passed' and audit['locked_files']==41 and audit['unchanged_existing_test_files']==89
    receipt=json.loads((folder/'fixture-run.json').read_text());assert receipt['host']==host and [r['exit_code'] for r in receipt['results']]==[1,0,0]
    for profile in ('debug','runtime-sanitize'):
        value=summary[profile];assert tuple(value[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed_final','skipped_final'))==(1153,66,51,151,0,0)
        f=folder/profile;original_failure=host=='sb16-2' and profile=='runtime-sanitize';run=json.loads((f/'original-run.json').read_text())
        assert [r['exit_code'] for r in run['results']]==([0,0,0,0,8] if original_failure else [0,0,0,0,0,0])
        for part,suites,checks in [('sessions',2,32),('core',64,1120)]:
            log=(f/('original-'+part+'.log')).read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
            assert len(counts)==suites and sum(counts)==checks and 'skipped=' not in log and '... skipped ' not in log
            if original_failure and part=='core':
                assert re.findall(r'^\d+/\d+ Test\s+#\d+: (\S+) .*?Failed',log,re.M)==['workspace-authority']
                assert '98% tests passed, 1 tests failed out of 64' in log
                assert len(re.findall(r'^\d+: FAIL:',log,re.M))==1 and not re.search(r'^\d+: ERROR:',log,re.M)
                assert 'AssertionError: False is not true' in log
            else:assert f'100% tests passed, 0 tests failed out of {suites}' in log
        log=(f/'workspace-authority-corrected.log').read_text();assert re.search(r'Ran 20 tests in ',log) and log.rstrip().endswith('OK') and 'skipped' not in log
        fixed=next(r for r in receipt['results'] if r['name']==profile+'-fixed');assert fixed['test_sha256']==new['files'][fixture]
        for binary,digest in fixed['binaries'].items():assert value['binaries'][binary]==digest
        p10=json.loads((f/'P10.json').read_text());assert p10['status']=='passed' and p10['selected']==p10['executed']==len(p10['tests'])==151 and all(t['status']=='passed' for t in p10['tests'])
        if original_failure:assert json.loads((f/'P10-completion-run.json').read_text())['exit_code']==0
        log=(f/'original-harness.log').read_text();assert re.search(r'Ran 51 tests in ',log) and log.rstrip().endswith('OK') and 'skipped' not in log
review=json.loads((root/'evidence/workspace-owner-idle-2026-10-09/assertion-review.json').read_text());assert review['preserved_assertions'] and review['added_delayed_case']
# Independently compare all existing methods and the assertions moved into the fixture helper.
def methods(data):return {n.name:n for c in ast.parse(data).body if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,ast.FunctionDef)}
with tarfile.open(root/'local/participant-session-candidate-03.tar') as archive:before=methods(archive.extractfile(fixture).read())
after=methods(proposed[fixture]);changed_methods=[n for n in before if ast.dump(before[n],include_attributes=False)!=ast.dump(after[n],include_attributes=False)]
assert changed_methods==['test_retry_cannot_acknowledge_a_process_revoked_during_status_refresh']
def assertions(node):return [ast.dump(n,include_attributes=False) for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('assertTrue','assertFalse','denied','until')]
assert assertions(before[changed_methods[0]])==assertions(after['retry_revoked_process_after_idle'])
locked=json.loads((root/'acceptance.lock.json').read_text())['files']
for name,digest in locked.items():assert hashlib.sha256(proposed[name]).hexdigest()==digest,name
changed=[n for n,data in proposed.items() if base.get(n)!=hashlib.sha256(data).hexdigest()];assert len(changed)==12
record=dict(candidate=4,status='prepared',source_files=len(proposed),files=new['files'],changed_files=changed,fixture_change=fixture,qualification='both hosts Debug and runtime sanitizers; explicit fixture replacement;original failure preserved',core_checks=1153,harness_checks=51,P10_cases=151,locked_files=len(locked))
if args.apply:
    for name in changed:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(proposed[name])
    for name,digest in new['files'].items():assert hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest,name
    record['status']='applied;commit and host adoption pending'
    (root/'local/participant-session-integration-applied-04.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
else:(root/'local/participant-session-integration-prepared-04.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
print(record['status'],len(changed),'changed files',len(proposed),'hashes',len(locked),'locked files')
