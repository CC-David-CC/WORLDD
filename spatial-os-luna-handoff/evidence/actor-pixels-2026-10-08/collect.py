import pathlib,shutil,json,re,socket,tarfile,hashlib
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
summary={}
for label in ['debug','runtime-sanitize']:
 d=p/('local/m10-actor-pixels-'+label+'-01')
 run=json.loads((d/'run.json').read_text())
 assert [r['name'] for r in run['results']]==['configure','build','harness','core','P10']
 assert all(r['exit_code']==0 for r in run['results'])
 text=(d/'core.log').read_text();counts=list(map(int,re.findall(r'Ran (\d+) tests? in',text)))
 assert len(counts)==47 and sum(counts)==914,(len(counts),sum(counts))
 assert '100% tests passed, 0 tests failed out of 47' in text and 'skipped=' not in text
 assert 'Ran 51 tests' in (d/'harness.log').read_text()
 gate=json.loads((d/'P10.json').read_text())
 assert gate['status']=='passed' and gate['selected']==151 and gate['executed']==151
 assert len(gate['tests'])==151 and all(t['status']=='passed' for t in gate['tests'])
 summary[label]=dict(core_suites=47,core_checks=914,harness_checks=51,acceptance_checks=151,failed=0,skipped=0)
focused=p/'local/m10-actor-pixels-focused-02'
assert all(r['exit_code']==0 for r in json.loads((focused/'run.json').read_text())['results'])
counts=list(map(int,re.findall(r'Ran (\d+) tests? in',(focused/'core.log').read_text())))
assert sorted(counts)==[9,9,13,14],counts
summary['focused']=dict(checks=45,failed=0,skipped=0)
summary['native-server-sanitize']=dict(status='unrun',reason='These tests require GLX. Existing instrumented GLX cleanup remains unqualified.')
inputs=json.loads((p/'local/actor-pixels-tested-inputs.json').read_text())
assert all(hashlib.sha256((p.parent/s).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==h for s,h in inputs.items())
tree=json.loads((p/'local/actor-pixels-full-tested-tree.json').read_text())
assert all(hashlib.sha256((p.parent/s).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==h for s,h in tree['files'].items())
target=p/'local/actor-pixels-evidence-export';target.mkdir(exist_ok=False)
for source in sorted((p/'local').glob('m10-actor-pixels-*')):
 if not source.is_dir() or 'schema' in source.name or 'prerequisite' in source.name:continue
 dest=target/source.name;dest.mkdir()
 for f in source.iterdir():
  if f.is_file() and f.suffix in {'.json','.log','.txt'}:shutil.copy2(f,dest/f.name)
 server=source/'server'
 if server.exists():
  for f in server.rglob('*.log'):
   out=dest/'server'/f.relative_to(server);out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,out)
(target/'tested-inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
(target/'tested-tree.json').write_text(json.dumps(tree,indent=2)+'\n')
(target/'source-integrity.json').write_text(json.dumps(dict(host=socket.gethostname(),checked=len(tree['files']),mismatches=[]),indent=2)+'\n')
(target/'binaries.json').write_text(json.dumps({b:{n:hashlib.sha256((p/b/n).read_bytes()).hexdigest() for n in ['worldd','spatialwm','workspaced','spatial-render-observation-probe']} for b in ['build','build-sanitize']},indent=2)+'\n')
(target/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(p/'local/actor-pixels-evidence.tar','w') as tar:
 for f in sorted(target.rglob('*')):
  if f.is_file():tar.add(f,arcname=str(f.relative_to(target)))
print(socket.gethostname(),json.dumps(summary),flush=True)
