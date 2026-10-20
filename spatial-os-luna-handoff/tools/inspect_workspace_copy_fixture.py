"""Import the real copy fixture and discover selected methods without running services."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import unittest
import google.protobuf

assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
assert len(sys.argv)==3
build,source=map(lambda value:Path(value).resolve(),sys.argv[1:])
assert build.is_dir() and source.is_dir()
bindings=build/'python'
sys.path[:0]=[str(bindings),str(source),str(source/'tests/core')]
fixture=source/'tests/core/test_workspace_copy_engine.py'
sys.argv=[str(fixture),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]
spec=importlib.util.spec_from_file_location('_spatial_copy_fixture_discovery',fixture)
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
suite=module.load_tests(unittest.TestLoader(),unittest.TestSuite(),None)
def methods(tests):
    for test in tests:
        if isinstance(test,unittest.TestSuite):yield from methods(test)
        else:yield test._testMethodName
selected=list(methods(suite))
assert len(selected)==14 and len(set(selected))==14
assert all(callable(getattr(module.WorkspaceCopyEngineTests,name)) for name in selected)
reached={}
for name,value in tuple(sys.modules.items()):
    if not name.endswith('_pb2'):continue
    path=Path(value.__file__).resolve()
    if name.startswith('google.protobuf.'):continue
    assert path.is_relative_to(bindings),(name,str(path))
    reached[name]=dict(path=str(path.relative_to(build)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
assert 'world_session_pb2' in reached and 'storage_pb2' in reached and 'copy_pb2' in reached
print(json.dumps(dict(methods=selected,protobuf_modules=reached,protobuf_version=google.protobuf.__version__,
    services_started=False,methods_executed=0,fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest()),sort_keys=True))
