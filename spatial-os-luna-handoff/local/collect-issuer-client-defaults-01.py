import json,pathlib,subprocess,re,hashlib,tarfile,socket
base=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local');root=base/'m02-issuer-client-defaults-01';gate=base/'m02-phase-gate-02'
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
r=json.loads((root/'review.json').read_text());assert r['status']=='passed_review_pending'
for profile in ('debug','runtime-sanitize'):
 assert [(x['passed'],x['failed'],x['skipped']) for x in r['profiles'][profile]]==[(30,0,0),(3,0,0)]
old=json.loads(subprocess.check_output(['ctest','--test-dir',str(gate/'debug/build'),'--show-only=json-v1']))
new=json.loads((root/'inventory.log').read_text())
def normal(test,source,build):
 h=re.search(r'sha256:([a-f0-9]{64})',(build/'build_id.hpp').read_text())[1]
 def clean(value):
  if isinstance(value,str):
   if value.startswith('--setenv=SPATIAL_SECURITY_EVIDENCE='):return '--setenv=SPATIAL_SECURITY_EVIDENCE=EVIDENCE'
   return value.replace(str(source),'SOURCE').replace(str(build),'BUILD').replace(h,'BUILD_HASH')
  if isinstance(value,list):return [clean(x) for x in value]
  if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k!='backtrace'}
  return value
 return clean(test)
before=[normal(t,gate/'source',gate/'debug/build') for t in old['tests']]
after=[normal(t,root/'source',root/'configuration') for t in new['tests'] if t['name']!='issuer-client-defaults']
assert before==after,'Existing CTest command or property changed'
review={'host':host,'status':'focused_checks_and_registration_verified','source_commit':r['binary_source_commit'],'unchanged_existing_ctest_entries':124,'profiles':r['profiles'],'source_and_binaries_unchanged':r['source_and_binaries_unchanged'],'scanned_logs':len(r['scanned_logs']),'sanitizer_matches':r['sanitizer_matches'],'phase_complete':False,'source_inputs':r['source_inputs']}
(root/'registration-review.json').write_text(json.dumps(review,indent=2)+'\n')
selected={k:root/k for k in r['attachments']}
selected.update({'review.json':root/'review.json','registration-review.json':root/'registration-review.json','qualify-issuer-client-defaults-01.py':base/'qualify-issuer-client-defaults-01.py','source/CMakeLists.txt':root/'source/CMakeLists.txt','source/tests/core/test_issuer_client_defaults.py':root/'source/tests/core/test_issuer_client_defaults.py','adoption-preflight.json':base/'m02-adoption-preflight-02.json'})
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for name,p in selected.items():
 assert not pathlib.Path(name).is_absolute() and '..' not in pathlib.Path(name).parts and not p.is_symlink()
 data=p.read_bytes()
 assert b'PRIVATE '+b'KEY-----' not in data and b'Bearer '+b'fleet-' not in data
 if name in r['attachments']:assert sha(p)==r['attachments'][name]
manifest={n:sha(p) for n,p in selected.items()}
(root/'collection-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
archive=base/('m02-issuer-client-defaults-01-'+host+'.tar.gz')
with tarfile.open(archive,'x:gz') as output:
 for name,p in selected.items():output.add(p,arcname=name,recursive=False)
 output.add(root/'collection-manifest.json',arcname='collection-manifest.json',recursive=False)
print(json.dumps({'host':host,'archive':str(archive),'archive_sha256':sha(archive),'attachments':len(selected),'review':review}))
