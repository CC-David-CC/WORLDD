import hashlib,json,shlex,subprocess
from pathlib import Path
ROOT='/home/dflanag3/src/spacial-m06-host-session-integrated-check-20261012'
RUN=ROOT+'/local/integrated-build01'
DEST=Path(__file__).resolve().parent
PIN='2954de07c8646da6a0dfc7ffbcf9fdd8d59846fe'
def ssh(command):return subprocess.check_output(['ssh','a5500',command])
code='''from pathlib import Path
import json,hashlib,subprocess
root=Path(%r);run=Path(%r)
files=[p for p in run.iterdir() if p.is_file() and p.name!='build.lock']+list((run/'steps').glob('*'))
files.append(Path('/home/dflanag3/m06-host-session-integrated-build.sh'))
rows=[]
for p in sorted(files):
 if not p.is_file():continue
 name=str(p.relative_to(run)) if p.is_relative_to(run) else 'build-command.sh'
 rows.append(dict(path=str(p),name=name,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),size=p.stat().st_size))
print(json.dumps(dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),status=subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=root,text=True),files=rows),indent=2))
'''%(ROOT,RUN)
receipt=json.loads(ssh('python3 -c '+shlex.quote(code)))
assert receipt['commit']==PIN and not receipt['status']
(DEST/'original-sha256.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8',newline='\n')
for row in receipt['files']:
 target=DEST/'raw'/row['name'];target.parent.mkdir(parents=True,exist_ok=True)
 data=ssh('cat -- '+shlex.quote(row['path']))
 assert hashlib.sha256(data).hexdigest()==row['sha256'],row['path']
 target.write_bytes(data)
for name in ['command.exit','wrapper.exit']:
 assert (DEST/'raw'/name).read_text().strip()=='0',name
assert (DEST/'raw/terminal-status.txt').read_text().strip()=='COMPLETE'
for profile in ['debug','asan']:
 for mode in ['on','off']:
  for operation in ['configure','build','bindings']:
   name=f'{profile}-{mode}-{operation}'
   assert (DEST/'raw/steps'/f'{name}.exit').read_text().strip()=='0',name
 assert (DEST/'raw/steps'/f'{profile}-inventory.exit').read_text().strip()=='0'
required={'actor-transition-real-world','actor-transition-crosshost','host-actor-controller-native','host-session-startup','host-session-production-native'}
selected={}
for profile in ['debug','asan']:
 inventory=json.loads((DEST/'raw/steps'/f'{profile}-inventory.log').read_text())
 tests={t['name']:t for t in inventory['tests'] if t['name'] in required}
 assert set(tests)==required
 assert all(t.get('command') and t['command'][0] for t in tests.values())
 assert any('with_resource_delegate.py' in x for x in tests['host-actor-controller-native']['command'])
 selected[profile]=tests
(DEST/'selected-registrations.json').write_text(json.dumps(selected,indent=2)+'\n',encoding='utf-8',newline='\n')
(DEST/'source-pin.txt').write_text(PIN+'\nTracked checkout clean before collection.\nBuild and static registration only; runtime UNRUN.\n',encoding='utf-8',newline='\n')
print('Verified',len(receipt['files']),'original/transferred files; 12 configure/build/binding exits; 2 inventories; command and wrapper exits zero.')