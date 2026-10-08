import pathlib,shutil,json,re,socket,tarfile
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
target=p/'local/application-observation-evidence-export'
target.mkdir(exist_ok=False)
for source in sorted((p/'local').glob('m04-application-observation-*')):
 if not source.is_dir():continue
 dest=target/source.name;dest.mkdir()
 for f in source.iterdir():
  if f.is_file() and f.suffix in {'.json','.log','.txt'}:shutil.copy2(f,dest/f.name)
 server=source/'server'
 if server.exists():
  for f in server.rglob('*.log'):
   relative=f.relative_to(server);out=dest/'server'/relative;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,out)
summary={}
before=json.loads((target/'m04-application-observation-audit-01/run.json').read_text())['tested_inputs']
after=json.loads((target/'m04-application-observation-audit-02/run.json').read_text())['tested_inputs']
assert {name for name in before if before[name]!=after[name]}=={
 'spatial-os-luna-handoff/CMakeLists.txt','spatial-os-luna-handoff/tests/core/test_application_observation.py'}
baseline=target/'m04-application-observation-baseline-02'
assert json.loads((baseline/'run.json').read_text())['exit_code']==1
assert 'Ran 8 tests' in (baseline/'test.log').read_text() and 'FAILED (failures=8)' in (baseline/'test.log').read_text()
focused=target/'m04-application-observation-focused-04'
assert all(item['exit_code']==0 for item in json.loads((focused/'run.json').read_text())['results'])
assert 'Ran 16 tests' in (focused/'focused.log').read_text() and '\nOK\n' in (focused/'focused.log').read_text()
for label in ['debug','runtime-sanitize']:
 d=target/('m04-application-observation-'+label+'-01')
 run=json.loads((d/'run.json').read_text())
 if label=='debug':
  assert [r['name'] for r in run['results']]==['configure','build','harness','core','P10']
  assert all(r['exit_code']==0 for r in run['results'])
 else:
  assert [r['name'] for r in run['results']]==['configure','build','harness','core']
  assert [r['exit_code'] for r in run['results']]==[0,0,0,8]
 text=(d/'core.log').read_text()
 counts=list(map(int,re.findall(r'Ran (\d+) tests? in',text)))
 assert len(counts)==41 and sum(counts)==787,(len(counts),sum(counts))
 if label=='debug':assert '100% tests passed, 0 tests failed out of 41' in text
 else:
  assert '98% tests passed, 1 tests failed out of 41' in text
  assert '15 - application-observation (Failed)' in text
  assert 'FAILED (failures=16)' in text
 assert 'skipped=' not in text
 harness=(d/'harness.log').read_text();assert 'Ran 51 tests' in harness and 'OK' in harness
 corrected=target/('m04-application-observation-corrected-'+label+'-01')
 correction=json.loads((corrected/'run.json').read_text())
 assert [r['name'] for r in correction['results']]==['configure','build','harness','affected','P10']
 assert all(r['exit_code']==0 for r in correction['results'])
 affected=(corrected/'affected.log').read_text()
 assert 'Ran 16 tests' in affected and '100% tests passed, 0 tests failed out of 1' in affected
 assert 'skipped=' not in affected
 assert 'Ran 51 tests' in (corrected/'harness.log').read_text()
 binaries=json.loads((corrected/'binaries.json').read_text())
 assert not binaries['spatial-confined-xprobe']['asan']
 for name in ['worldd','workspaced','spatialwm','spatial-xprobe']:assert binaries[name]['asan']==(label=='runtime-sanitize')
 gate=json.loads((corrected/'P10.json').read_text())
 assert gate['status']=='passed' and gate['selected']==151 and gate['executed']==151
 assert len(gate['tests'])==151 and all(t['status']=='passed' for t in gate['tests'])
 summary[label]={'core_suites':len(counts),'core_checks':sum(counts),'harness_checks':51,'acceptance_checks':151,
  'initial_failed_checks':0 if label=='debug' else 16,'corrected_suite_checks':16,'remaining_failures':0,'skipped':0,
  'coverage_basis':'Complete initial core suite plus affected-suite rerun after confined-witness fixture correction; final harness and P10 rerun'}
(target/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(p/'local/application-observation-evidence.tar','w') as tar:
 for f in sorted(target.rglob('*')):
  if f.is_file():tar.add(f,arcname=str(f.relative_to(target)))
print(socket.gethostname(),[(k,v.get('core_checks',v.get('checks'))) for k,v in summary.items()],flush=True)
