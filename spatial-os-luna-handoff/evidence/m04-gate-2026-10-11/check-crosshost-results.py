"""Audit all completed local cross-host artifacts without exporting credentials."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import re
import socket
import sys
from reviewed_local_gate import require_reviewed_gate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--profile', choices=('Debug', 'ASan-UBSan'))
args = parser.parse_args()
profiles = (args.profile,) if args.profile else ('Debug', 'ASan-UBSan')

root = Path('/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff')
fixture = Path('/home/dflanag3/src/spacial_desktop-m04-fixture-20261011/spatial-os-luna-handoff')
gate = require_reviewed_gate(root)
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
sys.path.insert(0, str(root / 'local/m04-gate-m04-full-gate-01/Debug/python'))
import issuer_pb2 as identity

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

cross = fixture / 'local/m04-crosshost-01'
record = dict(host=host, candidate=gate['candidate'], profiles=list(profiles), phase_complete=False, nodes=[], files={})
for profile in profiles:
    for scenario in ('delay', 'crash'):
        for direction in ('forward', 'reverse'):
            label = '-'.join((profile, scenario, direction)) + '01'
            case = cross / 'runs' / label
            target = 'sb16-2' if direction == 'forward' else 'sb16'
            for mode in (('controller', 'worker') if host == 'sb16' else ('worker',)):
                receipt_path = case / (mode + '-review.json')
                receipt = json.loads(receipt_path.read_text())
                result_path = case / mode / 'result.json'
                result = json.loads(result_path.read_text())
                assert receipt['status'] == result['status'] == 'passed'
                assert receipt['exit_code'] == 0 and receipt['result'] == result
                assert receipt['candidate'] == gate['candidate']
                assert receipt['gate_sha256'] == digest(root / 'local/m04-full-gate-01/run.json')
                assert (receipt['host'], receipt['profile'], receipt['scenario'], receipt['direction'], receipt['mode']) == (host, profile, scenario, direction, mode)
                if mode == 'controller':
                    assert result['issuer_host'] == 'sb16' and result['delayed_host'] == target
                    assert result['active_participants'] == result['exact_active_generations'] == 4
                    assert result['committed_revision'] == 1 and result['pending_obligation_observed']
                    assert set(result['native_cancellation_hosts']) == {'sb16', 'sb16-2'}
                    assert result['applications_survived'] == 2
                    assert result['complete_by_acknowledgement'] == (scenario == 'delay')
                    assert result['retired_worlds'] == (1 if scenario == 'crash' else 0)
                    for stage in ('pending', 'complete') + (('crashed', 'recovered') if scenario == 'crash' else ()):
                        path = case / mode / (stage + '-barrier.pb')
                        barrier = identity.AuthorityBarrierStatus.FromString(path.read_bytes())
                        assert barrier.participant_count == len(barrier.obligations) == 4
                        if stage == 'complete':
                            assert barrier.complete and barrier.pending_count == 0
                            assert all(item.complete for item in barrier.obligations)
                            retired = [item for item in barrier.obligations if item.HasField('retirement')]
                            assert len(retired) == (1 if scenario == 'crash' else 0)
                            assert all(item.applied_revision == (0 if item.HasField('retirement') else 1) for item in barrier.obligations)
                        else:
                            assert not barrier.complete and barrier.pending_count > 0
                            assert any(not item.complete for item in barrier.obligations)
                        record['files'][str(path.relative_to(cross))] = digest(path)
                else:
                    assert result['host'] == host and result['scenario'] == scenario and result['ready']
                    if scenario == 'crash' and host == target:
                        assert result['crashed'] and result['recovered'] and result['retired']
                    else:
                        assert result['cancelled']
                for path in (receipt_path, result_path):
                    record['files'][str(path.relative_to(cross))] = digest(path)
                record['nodes'].append(dict(case=label, mode=mode, result=result))

pattern = re.compile(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|runtime error:')
def walk_error(error):
    raise error
for directory, dirs, files in os.walk(cross / 'runs', onerror=walk_error):
    if Path(directory) == cross / 'runs':
        dirs[:] = [name for name in dirs if any(name.startswith(profile + '-') for profile in profiles)]
    # Private keys and mounted project data are outside this service-log audit.
    dirs[:] = [name for name in dirs if name not in ('private-bundle', 'volume')]
    for name in files:
        path = Path(directory) / name
        if path.suffix in ('.log', '.stderr'):
            assert not pattern.search(path.read_text(errors='replace')), str(path)
            record['files'][str(path.relative_to(cross))] = digest(path)
record['status'] = 'ready_for_primary_review'
record['excluded_directories'] = ['private-bundle', 'volume']
output = 'artifact-check-' + args.profile + '.json' if args.profile else 'artifact-check.json'
(cross / output).write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(dict(host=host, status=record['status'], nodes=len(record['nodes']), hashed_files=len(record['files']))))
