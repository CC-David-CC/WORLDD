import hashlib,json,pathlib,socket
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');assert socket.gethostname().split('.')[0]=='sb16'
source=r/'local/m02-participant-world-draft-02/source';build=source.parent/'debug/build'
manifest=json.loads((r/'local/participant-world-candidate-02.json').read_text())['files']
for name,digest in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
text=(r/'local/workspace-participant-baseline-02.log').read_text();assert 'FAILED (failures=3)' in text and 'Ran 3 tests' in text and '(0, 0, True) != (1, 1, False)' in text
report=dict(host='sb16',passed=0,failed=3,errors=0,source_files=len(manifest),source_manifest='participant-world-candidate-02.json',
    command='PYTHONPATH=local/m02-participant-world-draft-02/debug/build/python:local/m02-participant-world-draft-02/source:local/m02-participant-world-draft-02/source/tests/core python3 local/test_workspace_participant_baseline_02.py local/m02-participant-world-draft-02/debug/build/identityd local/m02-participant-world-draft-02/debug/build/spatial-issuer-client local/m02-participant-world-draft-02/debug/build/worldd WorkspaceParticipantTests.test_authority_broker_requires_participant_configuration WorkspaceParticipantTests.test_broker_activates_before_protected_work_and_cleanly_releases WorkspaceParticipantTests.test_completion_waits_for_real_detached_process_exit',
    binaries={name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in ['workspaced','identityd','spatial-issuer-client','spatial-app-init']},
    files={name:hashlib.sha256((r/'local'/name).read_bytes()).hexdigest() for name in ['workspace-participant-baseline-02.log','test_workspace_participant_baseline_02.py']})
(r/'local/workspace-participant-baseline-02.json').write_text(json.dumps(report,indent=2)+'\n');print('Verified baseline:',len(manifest),'source hashes, three genuine failures')
