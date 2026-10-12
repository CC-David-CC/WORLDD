"""Read-only recovery and transient-unit check after terminal resource qualifiers."""
import json
from pathlib import Path
import re
import socket
import subprocess

host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
units=set();records={}
for candidate in ('01','02','03','04'):
    root=base/('m03-authenticated-resources-'+candidate)
    review=json.loads((root/'review.json').read_bytes())
    assert 'ended' in review and review['status'] in ('failed','passed_review_pending')
    records[candidate]=review['status']
    for command in review['commands']:
        units.update(arg.split('=',1)[1] for arg in command['argv'] if arg.startswith('--unit=spatial-'))
    for name in review['attachments']:
        if not name.endswith('.log'):continue
        text=(root/name).read_text(errors='replace')
        units.update(re.findall(r'--unit=(spatial-[a-z0-9-]+(?:\.service)?)',text))
if host=='sb16':
    for number in ('01','02','03'):
        directory=base/('m03-authenticated-startup-diagnostic-'+number)
        value=json.loads((directory/'review.json').read_bytes());assert 'ended' in value
        for name in value['attachments']:
            if name.endswith('.log'):units.update(re.findall(r'--unit=(spatial-m03-resources-[a-z0-9-]+\.service)',(directory/name).read_text(errors='replace')))
result=dict(host=host,terminal_qualifiers=records,units=[],commands=[])
def run(command):
    process=subprocess.run(command,capture_output=True,text=True,timeout=15)
    result['commands'].append(dict(argv=command,exit_code=process.returncode,stdout=process.stdout,stderr=process.stderr))
    assert process.returncode==0, result['commands'][-1]
    return process.stdout
for unit in sorted(units):
    command=['systemctl']
    if not unit.startswith('spatial-m03-resources-'):command+=['--user']
    command+=['show',unit,'-p','ActiveState','-p','LoadState']
    state=run(command)
    assert 'ActiveState=inactive' in state and 'LoadState=not-found' in state,(unit,state)
    result['units'].append(unit)
result['root_subtree_control']=Path('/sys/fs/cgroup/cgroup.subtree_control').read_text().strip()
result['user_controllers']=Path('/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/cgroup.controllers').read_text().strip()
assert set(result['root_subtree_control'].split())=={'cpu','memory','pids'}
assert set(result['user_controllers'].split())=={'cpu','memory','pids'}
normal=base.parents[1]
result['normal_checkout_head']=run(['git','-C',str(normal),'rev-parse','HEAD']).strip()
result['normal_checkout_status']=run(['git','-C',str(normal),'status','--porcelain'])
assert result['normal_checkout_head']=='b5902eaaad0a9d0ce3cb272d37f0d0936a6575cd'
assert result['normal_checkout_status']==''
run(['sudo','-n','true'])
result['ssh_recovery']=True
output=base/'m03-authenticated-resources-final-cleanup.json'
assert not output.exists()
output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(dict(host=host,units=len(units),state='clean',path=str(output))))
