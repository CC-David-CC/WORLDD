import json,pathlib,socket,subprocess,time
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
out=root/'local/m01-world-remote-finish-02';out.mkdir()
record={'host':host,'status':'waiting for exact qualification runner','results':[]}
def save():(out/'run.json').write_text(json.dumps(record,indent=2)+'\n')
def runners():
    result=[]
    for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        try:args=path.read_bytes().split(b'\0')
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
        if any(arg.endswith(b'/qualify-world-remote-02.py') for arg in args):result.append(int(path.parent.name))
    return result
try:
    record['initial_runner_pids']=runners();save()
    print(host,'waiting for remote candidate02 runner',record['initial_runner_pids'],flush=True)
    while runners():time.sleep(1)
    command=['python3',str(root/'local/collect-world-remote-02.py'),'final']
    with (out/'collector.log').open('wb') as log:
        result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,cwd=root)
    record['results'].append({'command':command,'exit_code':result.returncode})
    if result.returncode:
        record['status']='failed';save();print((out/'collector.log').read_text()[-6500:],flush=True);raise SystemExit(result.returncode)
    record['status']='passed';save();print(host,'both complete remote profiles verified; integration remains pending',flush=True)
except BaseException as error:
    record.update(status='failed',error=str(error));save();raise
