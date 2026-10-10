import hashlib,json,socket,subprocess,tarfile
from pathlib import Path,PurePosixPath
assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
archive=base/'crosshost-recovery-source-01.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='3f14f331ff6a52e704b4477b4f06b511d61d927708e9928132f3de2a51e5d698'
root=base/'m02-crosshost-recovery-01';root.mkdir(exist_ok=False)
with tarfile.open(archive) as tar:
    members=tar.getmembers();assert len(members)==len({m.name for m in members})
    for member in members:
        name=PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert root.joinpath(*name.parts).resolve().is_relative_to(root.resolve())
    for member in members:
        path=root.joinpath(*PurePosixPath(member.name).parts);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(tar.extractfile(member).read())
manifest=json.loads((root/'manifest.json').read_bytes());source=root/'source'
assert len(manifest['files'])==347 and len(manifest['locked_files'])==41
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==h for n,h in manifest['files'].items())
bindings=root/'python';bindings.mkdir()
command=['protoc','--python_out='+str(bindings)]
command += ['--proto_path='+str(source/n) for n in ('tests/proto','proto/spatial/workspace/v1',
    'proto/spatial/supervisor/v1','proto/spatial/security/v1','proto/spatial/identity/v1',
    'proto/spatial/authority/v1','proto/spatial/core/v1')]
command += [str(source/'tests/proto/crosshost_revocation.proto')]
result=subprocess.run(command,capture_output=True)
(root/'bindings.log').write_bytes(result.stdout+result.stderr)
assert result.returncode==0,result.stderr
review=dict(command=command,exit_code=result.returncode,
    schema_sha256=manifest['files']['tests/proto/crosshost_revocation.proto'],
    binding_sha256=hashlib.sha256((bindings/'crosshost_revocation_pb2.py').read_bytes()).hexdigest())
(root/'bindings-review.json').write_text(json.dumps(review,indent=2)+'\n')
print('Verified 347 recovery source files and generated the test-only protobuf binding')
