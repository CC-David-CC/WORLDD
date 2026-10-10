"""Qualify bounded timeout diagnostics with focused production and native checks."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import tarfile
import time

assert socket.gethostname().split('.')[0]=='sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m02-issuer-timeout-01'
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
archive=base/'issuer-timeout-source-01.tar'
assert sha(archive)==sys.argv[1]
root.mkdir(exist_ok=False)
with tarfile.open(archive) as tar:
    members=tar.getmembers()
    assert len(members)==len({member.name for member in members})
    for member in members:
        name=PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        path=root.joinpath(*name.parts)
        assert path.resolve().is_relative_to(root.resolve())
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(tar.extractfile(member).read())
manifest=json.loads((root/'manifest.json').read_bytes())
source=root/'source'
server=base/'xlibre-patched/96cd5ea7cdca292239c98d2c3c7cbd41cc37964d13b4bdc5f2d4159aa751af5b/source/build/hw/vfb/Xvfb'
server_hash='5fd994bd4890340c7e07a20f3b9905f68c875ef4b1c7812fdb713765c75ad107'
def audit():
    assert all(sha(source/name)==digest for name,digest in manifest['files'].items())
    assert all(manifest['files'][name]==digest for name,digest in manifest['locked_files'].items())
    assert sha(server)==server_hash
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:name=(path/'comm').read_text().strip()
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
        assert name not in ('ctest','ninja','cc1','cc1plus','clang','clang++'),(path,name)

for profile in ('debug','runtime-sanitize'):
    out=root/'checks'/profile;out.mkdir(parents=True,exist_ok=False)
    build=root/profile/'build'
    env={name:value for name,value in os.environ.items() if name!='LSAN_OPTIONS'}
    env.update(CCACHE_BASEDIR=str(root),SPATIAL_TEST_XSERVER=str(server),
        SPATIAL_SECURITY_EVIDENCE=str(out/'native'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
        UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    record=dict(profile=profile,host='sb16',status='running',started=time.time(),commands=[],
        source=manifest['files'],server=dict(path=str(server),sha256=server_hash),suites={})
    def save():(out/'review.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
    targets=['spatial-authority-launch-probe','spatial-security-auth','spatial-authority-supervisor',
        'spatial-participant-runtime-probe','identityd','spatial-issuer-client','spatial-issuer-client-probe']
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug',
        '-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+('ON' if profile=='runtime-sanitize' else 'OFF'),
        '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache','-DCMAKE_C_COMPILER_LAUNCHER=ccache']),
        ('build',['cmake','--build',str(build),'--parallel','2','--target']+targets)]
    commands += [(name,['ctest','--test-dir',str(build),'-V','--output-on-failure','-R','^'+name+'$'])
        for name in manifest['selected_suites']]
    save()
    try:
        for label,command in commands:
            audit();item=dict(label=label,argv=command,started=time.time());record['commands'].append(item);save()
            with (out/(label+'.log')).open('xb') as log:
                result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
            item.update(exit_code=result.returncode,ended=time.time(),sha256=sha(out/(label+'.log')))
            if label=='build':
                record['binaries']={path.name:sha(path) for path in build.iterdir() if path.is_file() and os.access(path,os.X_OK)}
            save();print(profile,label,result.returncode,flush=True)
            if result.returncode:
                print((out/(label+'.log')).read_text()[-8000:],flush=True)
                raise RuntimeError(label+' failed')
            if label in manifest['selected_suites']:
                log=(out/(label+'.log')).read_text()
                counts=re.findall(r'Ran (\d+) tests',log)
                assert len(counts)==1 and re.search(r'\d+: OK\s*\n',log) and 'skipped=' not in log,log
                record['suites'][label]=int(counts[0]);save()
        audit()
        assert all(sha(build/name)==digest for name,digest in record['binaries'].items())
        record.update(status='passed',cases=sum(record['suites'].values()))
    except BaseException:
        record['status']='failed';raise
    finally:
        record['ended']=time.time();save()
print('Both focused profiles passed.',flush=True)
