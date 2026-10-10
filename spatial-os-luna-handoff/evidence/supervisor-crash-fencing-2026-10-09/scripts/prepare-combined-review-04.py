"""Record the primary review of the exact combined candidate, before qualification approval."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

r=Path(__file__).resolve().parents[1];e=r/'evidence/supervisor-crash-fencing-2026-10-09'
base=json.loads((r/'evidence/supervisor-broker-handoff-2026-10-09/integration-applied-02.json').read_text())['files']
manifest=json.loads((r/'local/supervisor-crash-fencing-candidate-01.json').read_text())['files']
draft=r/'local/supervisor-crash-fencing-draft-01'
changed=sorted(n for n in manifest if base.get(n)!=manifest[n]);assert len(changed)==37
for name,digest in manifest.items():assert hashlib.sha256((draft/name).read_bytes()).hexdigest()==digest,name
migration_tests=[n for n in changed if n.startswith('tests/') and n in base]
assert len(migration_tests)==7
for name in migration_tests:
    before=(r/name).read_text(encoding='utf-8')
    after=(draft/name).read_text(encoding='utf-8').replace('DROP TABLE authority_fence; ','')
    after=re.sub(r'(fetchone\(\)\[0\],\s*)9(?=\))',lambda match:match[1]+'8',after)
    assert before==after,name
groups=[
    dict(area='Build and protobuf schemas',files=[n for n in changed if n=='CMakeLists.txt' or n.startswith('proto/')],
         review='Additive protobuf fields preserve existing numbers. Both bindings are generated. The native connector is linked only into spatialwm. The frozen source includes the reviewed compositor comment correction.'),
    dict(area='Issuer fencing',files=[n for n in changed if n.startswith('src/identity/')],
         review='The supervisor proves the exact consumed launch and active generation. Nonces bind to TLS connection, issuer incarnation, purpose, and lifetime. A transaction preserves fences and existing obligations. Fenced generations cannot apply or release. Existing activation and replacement checks continue to reject reuse. Version9 requires explicit migration.'),
    dict(area='Native compositor handoff',files=[n for n in changed if n.startswith(('src/platform/','src/render/','src/x11/')) or n=='src/world/main.cpp'],
         review='Only the issued compositor credential reaches the sealed handoff. Descriptor seals, bounds, fields, display, socket PID/UID, server ID, and native role are checked. Authentication uses the exact verified socket. The Xlib placeholder cannot disclose the real credential when the adapter is absent. Parent and child consume the descriptor and wipe parsed cookie bytes. Legacy fixture startup remains explicit.'),
    dict(area='Supervisor fencing and crash coordination',files=[n for n in changed if n.startswith('src/supervisor/') or n=='src/testing/authority_launch_probe.cpp'],
         review='Live retained launch ownership selects the target. The issuer proves activation; previous_generation alone is not evidence. Fencing stops forwarding before proof. Checked fence persistence precedes reply. Fatal cleanup preserves the original error. Graceful release and explicit stop do not acquire implicit fences. Reopened journals cannot reconstruct ownership.'),
    dict(area='Migration fixtures',files=migration_tests,
         review='Mechanically verified that changes only drop the new table when constructing old schemas and expect schema9 after explicit migration. Existing identity, key, denial, rollback, and idempotence assertions remain unchanged.'),
    dict(area='New independent checks',files=[n for n in changed if n.startswith('tests/') and n not in base],
         review='Real issuer and supervisor processes exercise wrong keys, mismatched targets, replay, migration, lost commits, pending obligations, and crash cleanup. Independent native clients verify real pixels, held-input release, process death, descriptor consumption, wrong server rejection, and unchanged unrelated application identity. TLS fault peers cover transport substitution only.')]
assert sorted(n for group in groups for n in group['files'])==changed
review=dict(reviewer='primary agent',source_files=manifest,base_files=283,reviewed_files=changed,groups=groups,
    unresolved_static_defects=[],production_changed=False,integration_ready=False,
    qualification='Full unchanged serial02 qualification is running on both hosts. This static review is not final approval.',
    locked_files_unchanged=41,migration_fixture_only_changes=migration_tests,
    limitations=[
        'Issuer fencing preserves pending obligations. It does not implement verified retirement.',
        'Dependent application-control cleanup and cross-host cancellation remain required.',
        'Supervisor restart cannot reconstruct lost process/native ownership.',
        'No operator database migration, session restart, or native hardware qualification is performed by source integration.',
        'Full runtime sanitizer qualification must retain leak detection and all original deadlines.'],
    failure_diagnosis='evidence/world-bootstrap-runtime-failure-2026-10-09/issuer-sync-review-09.json')
(e/'combined-static-review-04.json').write_bytes((json.dumps(review,indent=2)+'\n').encode())
command=['python',str(r/'local/integrate-crash-fencing-04.py')]
subprocess.run(command,check=True)
before={n:hashlib.sha256((r/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for n in base}
refused=subprocess.run(command+['--apply'],capture_output=True,text=True)
assert refused.returncode!=0 and 'summary-final.json' in refused.stderr
after={n:hashlib.sha256((r/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for n in base}
assert before==after==base
assert all(not (r/n).exists() for n in set(manifest)-set(base))
(e/'integration-refusal-04.json').write_bytes((json.dumps(dict(command=command+['--apply'],
    exit_code=refused.returncode,stderr=refused.stderr,reason='Missing full qualification evidence',
    production_files_unchanged=283,new_production_files_created=0),indent=2)+'\n').encode())
for name in ('collect-crash-fencing-serial-02.py','integrate-crash-fencing-04.py','prepare-combined-review-04.py'):
    (e/'scripts').mkdir(exist_ok=True);(e/'scripts'/name).write_bytes((r/'local'/name).read_bytes())
print('Reviewed all37changed files. Guard refused missing full evidence and preserved production283.')
