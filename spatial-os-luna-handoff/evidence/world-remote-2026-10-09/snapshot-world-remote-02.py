import hashlib,json,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
manifest=json.loads((root/'local/world-remote-source-02.json').read_text());source=root/'local/m01-world-remote-source-02'
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
jobs=[]
for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:args=path.read_bytes().split(b'\0')
    except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    if any(arg.endswith(b'/qualify-world-remote-02.py') for arg in args):jobs.append(int(path.parent.name))
profiles={}
for label in ('debug','runtime-sanitize'):
    out=root/('local/m01-world-remote-'+label+'-02')
    if (out/'run.json').exists():
        profiles[label]=[(r['name'],r['exit_code']) for r in json.loads((out/'run.json').read_text())['results']]
        if (out/'core.log').exists():profiles[label+'_tail']=(out/'core.log').read_text()[-650:]
record={'host':host,'live_qualification_pids':jobs,'profiles':profiles,'source_files_verified':len(manifest['files']),
    'git_commit':subprocess.check_output(['git','-C',str(root.parent),'rev-parse','HEAD'],text=True).strip()}
(root/'local/world-remote-checkpoint-02.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record),flush=True)
