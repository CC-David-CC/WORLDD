import hashlib,json,os,pathlib,shutil,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
out=root/'local/m01-bootstrap-adapter-baseline-01';out.mkdir()
inputs=out/'inputs';inputs.mkdir();python=out/'python';python.mkdir()
with tarfile.open(root/'local/bootstrap-adapter-baseline-01.tar') as archive:
    expected={'grants.proto','world_session.proto','broker.proto','test_world_bootstrap.py'}
    members=archive.getmembers();assert len(members)==len(expected) and {m.name for m in members}==expected
    for member in members:
        assert member.isfile();(inputs/member.name).write_bytes(archive.extractfile(member).read())
for name in ('test_world_authority.py','test_world_identity.py','test_issuer_client.py','test_identity_tls.py',
             'test_identity_enrollment.py','test_identity_issuer.py'):
    shutil.copyfile(root/'tests/core'/name,inputs/name)
schemas=[inputs/'grants.proto',inputs/'world_session.proto',inputs/'broker.proto',
    root/'proto/spatial/core/v1/core.proto',root/'proto/spatial/security/v1/security.proto',
    root/'proto/spatial/identity/v1/issuer.proto',root/'proto/spatial/identity/v1/key.proto']
generator=['protoc','--python_out='+str(python),'-I'+str(inputs)]
for directory in ('core','security','identity','authority','workspace'): generator.append('-I'+str(root/('proto/spatial/'+directory+'/v1')))
generator += [str(path) for path in schemas]
with (out/'generate.log').open('wb') as log:
    generated=subprocess.run(generator,stdout=log,stderr=subprocess.STDOUT)
report={'scope':'proposed adapter API against existing real issuer and worldd; production inputs unchanged',
        'generator':generator,'generator_exit_code':generated.returncode,'test_exit_code':None,
        'inputs':{file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in inputs.iterdir() if file.is_file()}}
(out/'run.json').write_text(json.dumps(report,indent=2)+'\n')
assert generated.returncode==0
command=['python3',str(inputs/'test_world_bootstrap.py'),str(root/'build/identityd'),str(root/'build/spatial-issuer-client'),str(root/'build/worldd')]
env=dict(os.environ,PYTHONPATH=str(python)+':'+str(inputs)+':'+str(root))
with (out/'baseline.log').open('wb') as log:
    result=subprocess.run(command,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT)
report.update(command=command,test_exit_code=result.returncode,
    binaries={name:hashlib.sha256((root/'build'/name).read_bytes()).hexdigest() for name in ('identityd','spatial-issuer-client','worldd')})
(out/'run.json').write_text(json.dumps(report,indent=2)+'\n')
print((out/'baseline.log').read_text()[-5500:],flush=True)
print('Baseline exit',result.returncode,'; original logs retained',flush=True)
