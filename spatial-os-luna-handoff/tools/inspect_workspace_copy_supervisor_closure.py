"""Inventory the additional real-supervisor closure before packaging the lost-ACK fixture."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
from copy_fixture_profiles import instrumentation_preflight

BINDINGS=('launch_pb2.py','native_scope_pb2.py','process_scope_pb2.py','security_pb2.py',
          'issuer_pb2.py','key_pb2.py','grants_pb2.py','broker_pb2.py','storage_pb2.py','core_pb2.py')
SOURCES=('tests/native/copy_fault_supervised_world.py','tests/native/test_security_identity.py',
         'tests/native/display_ready.py','tests/native/native_display_geometry.py',
         'tests/core/test_workspace_copy_faults.py','tools/copy_fixture_profiles.py',
         'tools/run_workspace_copy_faults.py','harness/wire.py')

def record(path,executable=False):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or (executable and not info.st_mode&0o111):
        raise ValueError('Expected regular closure file with required executable mode: '+str(path))
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),bytes=info.st_size,
                mode=stat.S_IMODE(info.st_mode))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--build',required=True,type=Path)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--profile',required=True,choices=('Debug','ASan-UBSan'))
    args=parser.parse_args();build=args.build.resolve(strict=True);source=args.source.resolve(strict=True)
    profile=instrumentation_preflight(build,args.profile,additional_binaries=('spatial-authority-supervisor',))
    binding_names=set(BINDINGS);pending=list(BINDINGS)
    while pending:
        name=pending.pop()
        for node in ast.walk(ast.parse((build/'python'/name).read_bytes())):
            if isinstance(node,ast.Import):
                for alias in node.names:
                    if alias.name.endswith('_pb2') and not alias.name.startswith('google.'):
                        dependency=alias.name+'.py'
                        if dependency not in binding_names:binding_names.add(dependency);pending.append(dependency)
    files={str(path.relative_to(build)):record(path,path.name=='spatial-authority-supervisor')
           for path in [build/'spatial-authority-supervisor',build/'runtime.textproto',
                        *[build/'python'/name for name in sorted(binding_names)]]}
    dependencies={}
    result=subprocess.run(['ldd',str(build/'spatial-authority-supervisor')],capture_output=True,text=True,
                          timeout=10,check=True)
    if 'not found' in result.stdout:raise ValueError('Supervisor runtime dependency is missing')
    for name in re.findall(r'(?:=>\s+)?(/[^\s]+)',result.stdout):
        path=Path(name).resolve(strict=True)
        dependencies[str(path)]=record(path)
    print(json.dumps(dict(qualification='UNQUALIFIED',kind='build-and-package-input-inventory',
        profile=profile,files=files,dependencies=dependencies,
        sources={name:record(source/name) for name in SOURCES},
        generated_bindings_note='Include the complete transitive generated binding closure, including existing engine bindings.',
        runtime_config_note='runtime.textproto stays adjacent to worldd; supervisor/participant configs are fixture-generated private files.',
        native_closure_note='Deployment must separately verify the selected host/profile qualified XACE wrapper, server receipt and graphics dependencies.'),
        sort_keys=True,indent=2))

if __name__=='__main__':main()
