#!/usr/bin/env python3
"""Import-only preflight for the exact supervised copy recovery fixture."""
import importlib.util, json, pathlib, sys
build, source = map(pathlib.Path, sys.argv[1:3])
sys.argv = [str(pathlib.Path(__file__).resolve()), str(build/'identityd'), str(build/'spatial-issuer-client'), str(build/'worldd')]
sys.path[:0] = [str(build/'python'), str(source), str(source/'tests/core'), str(source/'tests/native')]
path = source/'tests/core/test_file_action_supervised_copy_recovery.py'
spec = importlib.util.spec_from_file_location('supervised_copy_recovery_fixture', path)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
cls = module.SupervisedFileActionCopyRecoveryTests
methods = sorted(name for name in dir(cls) if name.startswith('test_'))
expected = ['test_sigkill_checked_retirement_and_restart_reconcile_same_copy_journal']
assert expected[0] in methods, (methods, expected)
import launch_pb2 as launch, issuer_pb2 as issuer
assert {'process','native','binding','stage','fence'} <= set(launch.AuthorityLaunchRecord.DESCRIPTOR.fields_by_name)
assert {'generation','service_incarnation','launch_id','authorization_id','supervision_revision','fence_id','supervisor_public_key'} <= set(issuer.AuthorityFenceState.DESCRIPTOR.fields_by_name)
assert {'participant_id','registered_revision','generation','service_incarnation','launch_id','supervision_revision','authorization_id'} <= set(issuer.AuthorityFenceBegin.DESCRIPTOR.fields_by_name)
print(json.dumps(dict(methods=expected,services_started=0,methods_executed=0,protobuf_contract='valid'),sort_keys=True))
