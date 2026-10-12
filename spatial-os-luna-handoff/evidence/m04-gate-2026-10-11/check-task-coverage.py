"""Map M04 task witnesses to executed full-gate suites and named test cases."""
import json
from pathlib import Path
import re
import socket
import xml.etree.ElementTree as ET
from reviewed_local_gate import require_reviewed_gate

root = Path('/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff')
gate = require_reviewed_gate(root)
output = root / 'local/m04-full-gate-01'
tasks = [json.loads(path.read_text()) for path in sorted((root / 'tasks/m1').glob('*.json'))]
tasks = [task for task in tasks if task.get('phase') == 'M04']
assert len(tasks) == 20
report = dict(host=socket.gethostname().split('.')[0], candidate=gate['candidate'],
              phase_complete=False, profiles={}, errors=[])
for profile in ('Debug', 'ASan-UBSan'):
    directory = output / profile
    inventory = json.loads((directory / 'inventory.log').read_text())['tests']
    cases = {case.attrib['name']: case for case in ET.parse(directory / 'core.xml').iter('testcase')}
    log = (directory / 'core.log').read_text()
    rows = []
    for task in tasks:
        row = dict(id=task['id'], suites=[], missing_ids=[])
        witness = task.get('counterexample_suite')
        if witness:
            suites = [suite['name'] for suite in inventory if str(root / witness) in suite['command']]
            if not suites:
                report['errors'].append(profile + ': missing suite for ' + task['id'])
            for name in suites:
                case = cases[name]
                assert case.attrib['status'] == 'run'
                assert all(case.find(tag) is None for tag in ('failure', 'error', 'skipped')), name
            row['suites'] = suites
        for test_id in task.get('test_ids', []):
            # Require the actual class and method in the unittest execution log.
            if not re.search(r'\((?:[\w]+\.)*' + re.escape(test_id) + r'\)', log):
                row['missing_ids'].append(test_id)
        if row['missing_ids']:
            report['errors'].append(profile + ': missing test IDs for ' + task['id'])
        row['named_cases'] = len(task.get('test_ids', []))
        row['required_evidence'] = task.get('required_evidence', [])
        row['contract'] = task.get('contract')
        row['limitation'] = 'Execution mapping only; primary requirement and historical evidence review still required.'
        rows.append(row)
    report['profiles'][profile] = rows
report['status'] = 'execution_mapping_complete' if not report['errors'] else 'incomplete'
(output / 'task-coverage.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(dict(host=report['host'], status=report['status'], tasks=len(tasks), errors=report['errors'])))
raise SystemExit(1 if report['errors'] else 0)
