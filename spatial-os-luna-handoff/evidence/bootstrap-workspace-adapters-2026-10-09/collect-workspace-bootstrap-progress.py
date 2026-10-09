import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');files=[]
folders=['m01-adapters-debug-02','m01-adapters-audit-03']
if host=='sb16':folders+=['m01-broker-bootstrap-baseline-01','m01-workspace-bootstrap-draft-01',
    'm01-workspace-bootstrap-races-01','m01-workspace-bootstrap-corrected-02','m01-workspace-bootstrap-expanded-03',
    'm01-bootstrap-survival-baseline-01','m01-bootstrap-survival-fixed-02']
for name in folders:
    folder=root/'local'/name
    for path in folder.iterdir():
        if path.is_file() and path.suffix in ('.json','.log','.py','.descriptor'):
            files.append((path,host+'/'+name+'/'+path.name))
if host=='sb16':
    for folder in ('m01-workspace-bootstrap-draft-01','m01-bootstrap-survival-fixed-02'):
        source=root/'local'/folder/'source'
        for name in ('src/workspace/authority.cpp','src/world/identity_sessions.cpp','src/platform/broker.cpp',
            'tests/core/test_workspace_bootstrap.py','tests/core/test_workspace_bootstrap_races.py','source-manifest.json'):
            path=source/name
            if path.is_file():files.append((path,host+'/'+folder+'/source/'+name))
    fixed=root/'local/m01-bootstrap-survival-fixed-02'
    report=json.loads((fixed/'run.json').read_text());assert all(r['exit_code']==0 for r in report['results'])
    assert re.search(r'Ran 8 tests in ',(fixed/'races.log').read_text())
    assert re.search(r'Ran 11 tests in ',(fixed/'workspace.log').read_text())
    summary={'fixed_candidate_checks':{'workspace':11,'revocation':8},'failed_final_focused':0,'skipped_final_focused':0,
        'new_broker_client_checks':22,'original_world_checks':21,
        'limits':['Ninth workspace race pending in full matrix','Both-host complete Debug and runtime sanitizer matrices running',
            'No adapter production integration','No operator state migrated'],
        'preserved_failures':['22 checked-client baseline errors','Two incorrect initial workspace test expectations',
            'Two real process-survival failures before correction','Both hosts: three harness failures because snapshot omitted four locked documents']}
    record=root/'local/workspace-bootstrap-progress-summary.json';record.write_text(json.dumps(summary,indent=2)+'\n')
    files.append((record,host+'/workspace-progress-summary.json'))
for name in ('adapters-tested-tree-02.json','adapters-tested-tree-03.json'):
    files.append((root/'local'/name,host+'/'+name))
with tarfile.open(root/('local/workspace-bootstrap-progress-'+host+'.tar.gz'),'w:gz') as archive:
    for path,name in files:archive.add(path,arcname=name)
print(host,'preserved',len(files),'progress evidence files; full qualification still pending',flush=True)
