"""Preserve startup timing diagnostics without turning successful repeats into a fix."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16-2'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
manifest = json.loads((base/'m02-phase-gate-01/manifest.json').read_bytes())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
attachments = {}
results = {}
for tag in ('01','02','03'):
    root = base/('m02-startup-timing-'+tag)
    record = json.loads((root/'review.json').read_bytes())
    assert 'ended' in record and record['source'] == manifest['files']
    assert all(sha(base/'m02-phase-gate-01/source'/n) == d for n,d in record['source'].items())
    assert all(sha(base/'m02-phase-gate-01/debug/build'/n) == d for n,d in record['binaries'].items())
    for name,digest in record['attachments'].items():
        p=root/name
        assert p.resolve().is_relative_to(root) and p.is_file() and not p.is_symlink()
        assert p.suffix in ('.json','.jsonl','.log','.py','.bt') or p.name == 'trace-ready'
        assert sha(p)==digest
        attachments[p.relative_to(base).as_posix()]=digest
    attachments[(root/'review.json').relative_to(base).as_posix()]=sha(root/'review.json')
    timing=(root/'timing.log').read_text()
    assert 'ROOT_EXIT' in timing and 'TRACE_DEADLINE' not in timing and record['trace_exit_code']==0
    if tag == '01':
        assert len(record['results'])==2 and record['results'][-1]['exit_code']==1
        assert "KeyError: 'SPATIAL_TEST_XSERVER'" in (root/'authority-native-config.log').read_text()
        results[tag]=dict(status='diagnostic_setup_failed_target_unrun',
            correction='The raw failure_reproduced label was incorrect. The target suite never ran.',
            error='The wrapper omitted the pinned X server environment variable.')
        continue
    assert record['status']=='not_reproduced_cause_unconfirmed' and len(record['results'])==4
    counts=[]
    for command in record['results']:
        text=(root/(command['suite']+'.log')).read_text()
        count=re.findall(r'^Ran (\d+) tests? in ',text,re.M)
        assert command['exit_code']==0 and len(count)==1 and text.rstrip().endswith('OK') and 'skipped' not in text
        counts.append(int(count[0]))
    assert counts==[21,3,11,4]
    maximum={name:int(re.search(r'@maximum_us\[identityd, tracepoint:syscalls:sys_exit_'+name+r'\]: (\d+)',timing)[1])
        for name in ('fsync','fdatasync')}
    slow=[line for line in timing.splitlines() if line.startswith(('SLOW ','WAIT ')) and 'comm=identityd ' in line]
    results[tag]=dict(status=record['status'],case_executions_passed=39,issuer_syscall_maximum_us=maximum,
        issuer_events_at_least_100ms=slow,preceded_by_isolated_rebuild=tag=='03')
    if tag=='03':
        assert record['monitor_errors']==[] and all(c['exit_code']==0 for c in record['build_commands'])
        end=record['build_commands'][-1]['ended_ns']
        results[tag]['seconds_from_build_end_to_first_suite']=(record['results'][0]['started_ns']-end)/1e9
        results[tag]['seconds_from_build_end_to_target_suite']=(record['results'][-1]['started_ns']-end)/1e9

attachments[Path(__file__).name]=sha(Path(__file__))
review=dict(status='diagnostics_inconclusive',host='sb16-2',source_commit=manifest['commit'],
    source_files=351,locked_files_unchanged=41,production_changed=False,deadlines_changed=False,
    original_failure_resolved=False,phase_complete=False,results=results,attachments=attachments,
    limitations=[
        'Successful repetitions do not explain the original 985 ms issuer-loop delay.',
        'The rebuilt attempt preserves the four-suite order but omits the gate inventory and harness commands before it.',
        'Its post-build target starts earlier. Background writeback timing is not identical to the failed gate.',
        'Tracing can affect timing. The trace observes selected syscalls and scheduler waits, not every syscall.'
    ],next_action='Use the original complete qualification context with broader retained timing; keep the historical failures open.')
out=base/'m02-startup-timing-review-01.json'
with out.open('xb') as stream:stream.write((json.dumps(review,indent=2)+'\n').encode())
archive=base/'m02-startup-timing-evidence-01.tar.gz'
with tarfile.open(archive,'x:gz') as tar:
    for name in sorted(attachments):
        p=base/name;data=p.read_bytes()
        assert b'-----BEGIN '+b'PRIVATE KEY-----' not in data
        assert not re.search(rb'Bearer '+rb'fleet-[A-Za-z0-9_-]{12,}',data)
        tar.add(p,arcname=name,recursive=False)
    tar.add(out,arcname='primary-review.json',recursive=False)
print(json.dumps(dict(archive=str(archive),sha256=sha(archive),attachments=len(attachments),results=results)))
