import ast
import json
from pathlib import Path
import shutil

r = Path(__file__).resolve().parents[1]
e = r / 'evidence/supervisor-broker-handoff-2026-10-09'
shutil.copyfile(Path(__file__), e / 'scripts' / Path(__file__).name)
shutil.copyfile(r / 'local/prepare-supervisor-broker-handoff-02.py', e / 'scripts/prepare-supervisor-broker-handoff-02.py')
for path in (e / 'scripts').glob('*.py'):
    ast.parse(path.read_text())
diagnosis = '''Broker handoff observer failure, candidate01, sb16

The Debug build, 51 harness checks, and real broker startup test passed.
The eight regression methods ran. Six passed. Two stopped with PermissionError.
Both errors occurred while checking /proc/PID/fd/38 before application actions.
The existing 102 focused checks and runtime sanitizer profile remained unrun.

NativeLaunch sets PR_SET_DUMPABLE to zero before loading the broker credential.
An unprivileged observer cannot inspect that process's descriptor table.
This is a test-observer defect. The application behavior remains unverified.
Do not interpret inaccessible descriptors as closed descriptors.

Candidate02 changes one test file. Production files remain identical to candidate01.
A privileged read-only observer lists descriptor numbers for the exact child.
The test checks the retained pidfd before and after observation.
It requires standard descriptors to exist and descriptor 38 to be absent.
Permission errors and observer failures still fail the test.
A new control checks an open descriptor and its actual closure on a non-dumpable process.
No acceptance lock or existing production assertion was weakened.

Candidate01 failure logs and manifests remain in candidate-01/sb16.
Candidate02 is frozen separately. Its runtime qualification has started on sb16.
The queued candidate01 on sb16-2 remains unchanged.
Complete broker participant activation and recovery remain required.
'''
(e / 'observer-failure-01.txt').write_text(diagnosis, newline='\n')
path = r / 'CURRENT_STATE.md'
text = path.read_text(encoding='utf-8')
heading = '# Implementation status\n'
assert text.startswith(heading)
addition = '''
## 2026-10-09: sb16 full supervisor passes; broker observer correction starts

Supervisor candidate02 passed full Debug and runtime ASan/UBSan on sb16.
Each profile passed 1383 core checks across 91 suites, 51 harness checks, and 151 P10 cases.
No case failed or skipped. Both-host full Debug already passed.
The sb16-2 full sanitizer worker remains active. No supervisor production files are integrated yet.

The unchanged-source broker baseline confirmed the missing handoff on sb16 in both profiles.
Broker handoff candidate01 then passed Debug build, 51 harness checks, and real broker startup.
Six regression methods passed. Two stopped on an observer PermissionError before application actions.
NativeLaunch disables unprivileged process inspection with PR_SET_DUMPABLE=0.
The existing 102 focused checks and runtime sanitizer profile remained unrun for candidate01.
Read evidence/supervisor-broker-handoff-2026-10-09/observer-failure-01.txt and preserved raw logs.

Broker candidate02 retains identical production code and all 41 locked files.
One test file adds a privileged read-only descriptor observer and an open/closed descriptor control.
The observer requires a live pidfd across inspection and treats inspection errors as failures.
Candidate02 qualification is active on sb16. Its nine regression methods remain unverified until execution finishes.
The sb16-2 baseline and original candidate01 remain queued behind its exact supervisor worker.
M02 and Spatial-M1 remain incomplete. Fencing, recovery, and full broker participation remain required.

'''
path.write_text(heading + addition + text[len(heading):], encoding='utf-8', newline='\n')
path = r / 'tasks/m1/identity.authority_supervisor.json'
task = json.loads(path.read_text())
task['cumulative'] = 'Full Debug passed on both hosts. Full runtime sanitizer qualification passed on sb16 and remains active on sb16-2. Each completed profile passed 1383 core checks, 51 harness checks, and 151 P10 cases; zero failures or skips. No source integrated.'
path.write_text(json.dumps(task, indent=2) + '\n', newline='\n')
path = r / 'tasks/m1/identity.supervisor_broker_handoff.json'
task = json.loads(path.read_text())
task['baseline'] = 'sb16 confirmed the missing broker_native_target_fd parser field in both unchanged-source profiles. sb16-2 baseline remains queued behind its exact supervisor worker.'
task['correction_tests'] = 'Candidate01 sb16 Debug: real broker startup and six regression methods passed; two regression methods errored on unprivileged descriptor inspection. Focused102 and runtime sanitizer unrun. Candidate02 adds a reviewed privileged observer and a ninth regression method proving open/closed descriptor observation. Candidate02 qualification is active on sb16.'
task['production_changed'] = False
task['isolated_candidate'] = 2
task['correction_test_evidence'] = 'evidence/supervisor-broker-handoff-2026-10-09/observer-review-02.json'
task['remaining'][0] = 'Complete sb16-2 unchanged-source baseline and preserve original candidate01 results.'
task['remaining'][1] = 'Qualify the isolated sealed broker configuration handoff and corrected descriptor observer.'
path.write_text(json.dumps(task, indent=2) + '\n', newline='\n')
path = r / 'docs/SUPERVISOR_BROKER_NATIVE_HANDOFF.txt'
text = path.read_text().replace('Status: baseline prepared. Runtime reproduction and implementation are pending.',
    'Status: sb16 baseline confirmed. Isolated implementation and observer correction are in qualification.', 1)
text = text.replace('All eight tests remain unrun. Static syntax checks do not establish runtime correctness.\nThe existing unchanged-source baseline remains unchanged and queued.',
    'Candidate01 ran all eight on sb16 Debug: six passed and two stopped on observer PermissionError.\nThe unchanged-source baseline confirmed the missing handoff on sb16 in both profiles.\nCandidate02 preserves production code and corrects the observer, with one additional control test.\nRead evidence/supervisor-broker-handoff-2026-10-09/observer-failure-01.txt.\nBoth-host qualification remains required.')
path.write_text(text, newline='\n')
path = r / 'local/driver-state.json'
state = json.loads(path.read_text())
state.update(commit='fea5319', current_step='supervisor_final_qualification_and_broker_observer_correction',
    previous_goal_turn='verified wait: sb16-2 exact full supervisor worker was observed running.',
    last_turn='progress: preserved sb16 full supervisor success and broker candidate01 failure; froze candidate02 observer correction; started sb16 qualification. Remote qualifier PID1006960 confirmed live after local session handle disappeared.',
    next_test='Observe sb16 PID1006960 and sb16-2 supervisor/full queue. Collect each terminal result without restarting. Integrate supervisor only after both-host full success; qualify broker correction separately.')
state['qualification_sessions']['broker_candidate02_sb16_remote_pid'] = 1006960
state['qualification_sessions']['broker_candidate01_sb16_2_remote_pid'] = 785544
path.write_text(json.dumps(state, indent=2) + '\n', newline='\n')
print('Recorded preserved failure, explicit observer correction, and current qualification states.')
