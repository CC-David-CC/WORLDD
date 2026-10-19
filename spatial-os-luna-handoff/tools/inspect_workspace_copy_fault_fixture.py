"""Import the real copy fixture and discover selected methods without running services."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import unittest
import google.protobuf


def fixture_fault_methods(module):
    """Read the explicit five-case contract from the fixture's load_tests hook."""
    tree=ast.parse(Path(module.__file__).read_bytes())
    hooks=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='load_tests']
    assert len(hooks)==1,'Expected one fixture load_tests hook'
    assignments=[]
    for node in ast.walk(hooks[0]):
        if not isinstance(node,ast.Assign):continue
        if any(isinstance(target,ast.Name) and target.id=='names' for target in node.targets):
            assignments.append(node.value)
    assert len(assignments)==1,'Expected one explicit fault-method tuple'
    names=ast.literal_eval(assignments[0])
    assert isinstance(names,tuple) and len(names)==5
    assert all(isinstance(name,str) and name.startswith('test_') for name in names)
    assert len(set(names))==5,'Fault-method contract contains duplicates'
    assert all(callable(getattr(module.WorkspaceCopyFaultTests,name,None)) for name in names),names
    return names


def suite_cases(suite):
    for case in suite:
        if isinstance(case,unittest.TestSuite):yield from suite_cases(case)
        else:yield case


def validate_discovered_tests(suite,module,fault_names,auxiliary_names):
    """Keep the fault wrapper's five cases distinct from registered unit coverage."""
    assert len(fault_names)==5 and len(set(fault_names))==5
    assert all(callable(getattr(module.WorkspaceCopyFaultTests,name,None)) for name in fault_names)
    cases=list(suite_cases(suite))
    assert all(isinstance(case,unittest.TestCase) for case in cases)
    faults=[case for case in cases if type(case) is module.WorkspaceCopyFaultTests]
    auxiliary=[case for case in cases if type(case) is module.StorageBrokerConstructorFailureTests]
    assert len(faults)==5 and [case._testMethodName for case in faults]==list(fault_names)
    actual_auxiliary=[case._testMethodName for case in auxiliary]
    assert len(actual_auxiliary)==len(set(actual_auxiliary))
    assert actual_auxiliary==list(auxiliary_names)
    assert len(cases)==len(faults)+len(auxiliary),'Unexpected test case type in fixture discovery'
    return [case._testMethodName for case in faults],actual_auxiliary


def main():
    assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
    assert len(sys.argv)==3
    build,source=map(lambda value:Path(value).resolve(),sys.argv[1:])
    assert build.is_dir() and source.is_dir()
    bindings=build/'python'
    sys.path[:0]=[str(bindings),str(source),str(source/'tests/core')]
    fixture=source/'tests/core/test_workspace_copy_faults.py'
    sys.argv=[str(fixture),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]
    spec=importlib.util.spec_from_file_location('_spatial_copy_fault_fixture_discovery',fixture)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    suite=module.load_tests(unittest.TestLoader(),unittest.TestSuite(),None)
    fault_names=fixture_fault_methods(module)
    auxiliary_names=unittest.TestLoader().getTestCaseNames(module.StorageBrokerConstructorFailureTests)
    selected,auxiliary=validate_discovered_tests(suite,module,fault_names,auxiliary_names)
    reached={}
    for name,value in tuple(sys.modules.items()):
        if not name.endswith('_pb2'):continue
        path=Path(value.__file__).resolve()
        if name.startswith('google.protobuf.'):continue
        assert path.is_relative_to(bindings),(name,str(path))
        reached[name]=dict(path=str(path.relative_to(build)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert 'world_session_pb2' in reached and 'storage_pb2' in reached and 'copy_pb2' in reached
    print(json.dumps(dict(methods=selected,auxiliary_methods=auxiliary,protobuf_modules=reached,
        protobuf_version=google.protobuf.__version__,services_started=False,methods_executed=0,
        fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest()),sort_keys=True))


if __name__=='__main__':main()
