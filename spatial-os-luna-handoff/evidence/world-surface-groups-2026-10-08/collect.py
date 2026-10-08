import pathlib,shutil,json,re,socket,tarfile
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
target=p/'local/surface-groups-evidence-export'
target.mkdir(exist_ok=False)
for source in sorted((p/'local').glob('m04-surface-groups-*')):
 if not source.is_dir():continue
 dest=target/source.name;dest.mkdir()
 for f in source.iterdir():
  if f.is_file() and f.suffix in {'.json','.log','.txt'}:shutil.copy2(f,dest/f.name)
 server=source/'server'
 if server.exists():
  for f in server.rglob('*.log'):
   relative=f.relative_to(server);out=dest/'server'/relative;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,out)
summary={}
for label in ['debug','runtime-sanitize']:
 d=target/('m04-surface-groups-'+label+'-01')
 run=json.loads((d/'run.json').read_text())
 assert [r['name'] for r in run['results']]==['configure','build','harness','core','P10']
 assert all(r['exit_code']==0 for r in run['results'])
 text=(d/'core.log').read_text()
 counts=list(map(int,re.findall(r'Ran (\d+) tests? in',text)))
 assert len(counts)==39 and sum(counts)==743,(len(counts),sum(counts))
 assert '100% tests passed, 0 tests failed out of 39' in text
 assert not re.search(r'OK \(skipped=|skipped=|\*\*\*Failed',text)
 harness=(d/'harness.log').read_text();assert 'Ran 51 tests' in harness and 'OK' in harness
 gate=json.loads((d/'P10.json').read_text())
 assert gate['status']=='passed' and gate['selected']==151 and gate['executed']==151
 assert len(gate['tests'])==151 and all(t['status']=='passed' for t in gate['tests'])
 summary[label]={'core_suites':len(counts),'core_checks':sum(counts),'harness_checks':51,'acceptance_checks':151,'failed':0,'skipped':0}
(target/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(p/'local/surface-groups-evidence.tar','w') as tar:
 for f in sorted(target.rglob('*')):
  if f.is_file():tar.add(f,arcname=str(f.relative_to(target)))
print(socket.gethostname(),[(k,v['core_checks']) for k,v in summary.items()],flush=True)
