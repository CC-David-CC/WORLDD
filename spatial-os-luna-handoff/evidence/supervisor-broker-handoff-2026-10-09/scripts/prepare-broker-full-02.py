"""Prepare cumulative qualification using the exact focused source and binaries."""
import ast
from pathlib import Path
import shutil
r = Path(__file__).resolve().parents[1]
text = (r / 'local/finish-authority-supervisor-full-02.py').read_text()
text = text.replace('m02-authority-supervisor-02', 'm02-supervisor-broker-handoff-02')
text = text.replace('authority-supervisor-candidate-02', 'supervisor-broker-handoff-candidate-02')
text = text.replace("['configure','build','harness','ownership','supervisor','focused']", "['configure','build','harness','handoff','regressions','focused']")
old_pattern = '^(identity-(launch-authorization|supervisor-registry|keys|issuer|enrollment|bootstrap|revocation|participants|participant-(client|incarnation|channel|runtime|barrier|barrier-client|session|session-client))|issuer-client|authority-(native-config|supervisor|participant-relay|owned-participant|launch-coordinator|prepared-launch|process-scope|native-scope))$'
new_pattern = '^(authority-(broker-handoff|broker-handoff-regressions|supervisor|native-config|participant-relay|owned-participant|launch-coordinator|prepared-launch|process-scope|native-scope)|native-application-launch)$'
assert old_pattern in text
text = text.replace(old_pattern, new_pattern).replace('full authority supervisor qualification complete', 'full broker handoff qualification complete')
collector = (r / 'local/collect-authority-supervisor-full-02.py').read_text()
collector = collector.replace('m02-authority-supervisor-02', 'm02-supervisor-broker-handoff-02')
collector = collector.replace('authority-supervisor-candidate-02', 'supervisor-broker-handoff-candidate-02')
collector = collector.replace("['configure','build','harness','ownership','supervisor','focused']", "['configure','build','harness','handoff','regressions','focused']")
collector = collector.replace("[('ownership',1,3),('supervisor',1,14),('focused',23,366)]", "[('handoff',1,1),('regressions',1,9),('focused',9,102)]")
collector = collector.replace("[('core',66,1000)]", "[('core',82,1281)]")
collector = collector.replace('len(names)==91 and total==1383', 'len(names)==93 and total==1393')
collector = collector.replace('authority-supervisor-full-', 'supervisor-broker-full-')
collector = collector.replace('verified281 source', 'verified283 source')
for name, content in [('finish-supervisor-broker-full-02.py', text), ('collect-supervisor-broker-full-02.py', collector)]:
    ast.parse(content)
    path = r / 'local' / name
    path.write_text(content, newline='\n')
    shutil.copyfile(path, r / 'evidence/supervisor-broker-handoff-2026-10-09/scripts' / name)
print('Prepared full gate: 1393 checks across 93 suites plus 51 harness and 151 P10 cases per host/profile.')
