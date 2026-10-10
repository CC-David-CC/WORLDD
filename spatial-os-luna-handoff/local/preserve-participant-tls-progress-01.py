"""Preserve metadata-only TLS tracing and review actual request/reply timings."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

root=Path(__file__).resolve().parents[1]
out=root/'evidence/control-recovery-2026-10-09/participant-tls-progress-01'
(out/'sb16').mkdir(parents=True,exist_ok=False)
sha=lambda data:hashlib.sha256(data).hexdigest()
records={}
for tag in ('01','02'):
    name='m02-participant-tls-progress-'+tag
    subprocess.run(['scp','-q','-r','sb16:/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/'+name,
        str(out/'sb16'/name)],check=True)
    where=out/'sb16'/name
    record=json.loads((where/'review.json').read_bytes());records[tag]=record
    for path,digest in record['attachments'].items():
        file=where/path
        assert file.resolve().is_relative_to(where.resolve())
        assert sha(file.read_bytes())==digest,path
assert records['01']['status']=='diagnostic_error'
assert 'More than one END probe defined' in (out/'sb16/m02-participant-tls-progress-01/timing.log').read_text()
assert records['02']['status']=='not_reproduced_cause_unconfirmed'
assert records['02']['exit_code']==records['02']['trace_exit_code']==0
assert len(records['02']['results'])==20
assert all(row['passed'] and row['tests']==1 and not row['skipped'] for row in records['02']['results'])
trace=(out/'sb16/m02-participant-tls-progress-02/timing.log').read_text()
assert 'ROOT_EXIT' in trace and 'TRACE_DEADLINE' not in trace
assert not re.search(r'Lost \d+ events',trace)
awaiting={};roundtrips=[];issuer_api_max=0;tls_rows=0
for line in trace.splitlines():
    if not line.startswith('TLS_END '):continue
    tls_rows+=1
    values=dict(item.split('=',1) for item in line.split()[1:])
    if values['comm']=='identityd':issuer_api_max=max(issuer_api_max,int(values['us']))
    if values['comm']!='spatial-partici' or values['result']!='1':continue
    key=(values['pid'],values['tid'],values['ssl'])
    if values['call'].endswith(':SSL_write_ex'):
        assert key not in awaiting,'A second request write preceded its reply'
        awaiting[key]=int(values['ns'])
    elif key in awaiting:
        assert int(values['count'])==4,'First response read did not complete its header'
        roundtrips.append((int(values['ns'])-awaiting.pop(key))//1000)
assert roundtrips and tls_rows and not awaiting
attachments={}
for path in out.rglob('*'):
    if not path.is_file():continue
    data=path.read_bytes()
    assert b'-----BEGIN '+b'PRIVATE KEY-----' not in data
    assert not re.search(rb'Bearer '+rb'fleet-[A-Za-z0-9_-]{12,}',data)
    attachments[path.relative_to(out).as_posix()]=sha(data)
primary=dict(status='not_reproduced_cause_unconfirmed',passed_cases=20,
    original_failures_resolved=False,source_changed=False,deadlines_changed=False,
    failed_tracer_setup='Attempt01 had two END blocks. No target case ran before setup failed.',
    tls_return_records=tls_rows,complete_participant_replies=len(roundtrips),
    participant_max_write_to_header_us=max(roundtrips),issuer_max_tls_io_call_us=issuer_api_max,
    limitations=['Uprobes can perturb timing. These successful attempts do not explain the untraced timeout.',
        'Only TLS sizes, return codes, process-local pointers, and timing were recorded. No payload buffers were read.'],
    attachments=attachments)
(out/'primary-review.json').write_bytes((json.dumps(primary,indent=2)+'\n').encode())
print(json.dumps({key:value for key,value in primary.items() if key!='attachments'}))
