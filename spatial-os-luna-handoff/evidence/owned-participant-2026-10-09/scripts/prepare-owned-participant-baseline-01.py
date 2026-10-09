import hashlib,json,tarfile
from pathlib import Path
r=Path(__file__).parent;source=r/'owned-participant-draft-01';source.mkdir()
with tarfile.open(r/'launch-authorization-candidate-03.tar') as a:a.extractall(source,filter='data')
def edit(name,old,new):
    p=source/name;s=p.read_text(encoding='utf-8');assert old in s,name;p.write_text(s.replace(old,new,1),encoding='utf-8',newline='\n')
edit('proto/spatial/supervisor/v1/launch.proto','import "native_scope.proto";','import "native_scope.proto";\nimport "issuer.proto";\nimport "grants.proto";')
edit('proto/spatial/supervisor/v1/launch.proto','  bytes service_incarnation = 4;','  bytes service_incarnation = 4;\n  bool supervised = 5;')
edit('proto/spatial/supervisor/v1/launch.proto','  NativeScopeStopped native_stopped = 8;','  NativeScopeStopped native_stopped = 8;\n  spatial.identity.v1.AuthorityLaunchAuthorization authorization = 9;')
edit('proto/spatial/supervisor/v1/launch.proto','message AuthorityLaunchQuery {}','''message AuthorityParticipantAttach {
  string runtime_config = 1;
  repeated spatial.authority.v1.Domain domains = 2;
}
message AuthorityLaunchAuthorize {
  string issuer_client_config = 1;
  string private_key_path = 2;
  uint64 supervision_revision = 3;
  optional uint64 previous_generation = 4;
}
message AuthorityLaunchQuery {}''')
edit('proto/spatial/supervisor/v1/launch.proto','    AuthorityLaunchQuery stop = 13;','''    AuthorityLaunchQuery stop = 13;
    AuthorityParticipantAttach attach = 14;
    AuthorityLaunchQuery participant_poll = 15;
    AuthorityLaunchAuthorize authorize = 16;
    spatial.identity.v1.ParticipantCommand participant_command = 17;''')
edit('proto/spatial/supervisor/v1/launch.proto','    AuthorityLaunchError error = 11;','    AuthorityLaunchError error = 11;\n    spatial.identity.v1.ParticipantUpdate participant_update = 12;')
edit('CMakeLists.txt','        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1/launch.proto','''        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1/launch.proto''')
edit('CMakeLists.txt','add_dependencies(spatial-authority-launch-probe launch-python-protobuf scope-python-protobuf native-scope-python-protobuf security-python-protobuf python-protobuf)','add_dependencies(spatial-authority-launch-probe launch-python-protobuf scope-python-protobuf native-scope-python-protobuf security-python-protobuf python-protobuf issuer-python-protobuf key-python-protobuf grants-python-protobuf)')
edit('CMakeLists.txt','    add_test(NAME authority-launch-coordinator COMMAND','''    add_test(NAME authority-owned-participant COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-owned-participant-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_authority_owned_participant.py"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-owned-participant PROPERTIES TIMEOUT 120)
    add_test(NAME authority-launch-coordinator COMMAND''')
test='''"""Owned process execution requires approval of the coordinator's actual participant connection."""
import os
from pathlib import Path
import sys
import time
import unittest
from google.protobuf import text_format
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import test_authority_launch as existing
import test_supervisor_registry as registry
import issuer_pb2 as identity
import launch_pb2 as pb

class OwnedParticipantTests(existing.AuthorityLaunchTests):
    def setUp(self):
        super().setUp()
        self.issuer=registry.SupervisorRegistryTests('runTest')
        def cleanup_issuer():
            self.issuer.tearDown()
            self.assertTrue(self.issuer.doCleanups(),'Issuer fixture cleanup failed')
        self.addCleanup(cleanup_issuer);self.issuer.setUp()
        self.assertTrue(self.issuer.bind().HasField('supervision'))
        self.binding.CopyFrom(pb.AuthorityLaunchBinding(issuer_id=self.issuer.info.issuer_id,
            participant_id=self.issuer.registration.participant.id,registered_revision=1,
            service_incarnation=os.urandom(32),supervised=True))

    def runtime_config(self,**changes):
        config=identity.AuthorityParticipantRuntimeConfig(protocol_version=1,
            issuer_client_config=str(self.issuer.config_file()),private_key_path=str(self.issuer.signer),
            participant_id=self.binding.participant_id,registered_revision=1,
            service_incarnation=self.binding.service_incarnation)
        for n,v in changes.items():setattr(config,n,v)
        path=self.root/('runtime-'+str(time.monotonic_ns())+'.textproto')
        path.write_text(text_format.MessageToString(config));path.chmod(0o600);return path

    def attach(self,proc,**changes):
        return self.call(proc,attach=pb.AuthorityParticipantAttach(runtime_config=str(self.runtime_config(**changes)),
            domains=self.issuer.registration.participant.domains))

    def update(self,proc,field):
        deadline=time.monotonic()+5
        while True:
            reply=self.call(proc,participant_poll=pb.AuthorityLaunchQuery())
            self.assertFalse(reply.HasField('error'),reply)
            if reply.HasField('participant_update'):
                self.assertTrue(reply.participant_update.HasField(field),reply);return reply.participant_update
            self.assertLess(time.monotonic(),deadline,'Owned participant update deadline');time.sleep(.005)

    def test_supervised_child_cannot_execute_without_durable_authorization(self):
        proc=self.probe();view=self.prepared(proc);observed=self.root/'unauthorized-execution'
        reply=self.run_child(proc,f'import pathlib;pathlib.Path({str(observed)!r}).touch()')
        self.assertTrue(reply.HasField('error'),reply)
        self.assertFalse(observed.exists());self.assertEqual(self.read_record(),view.record)
        self.assertEqual((self.cgroup/view.record.process.scope_name/'cgroup.procs').read_text(),'')
        self.stopped(proc)

    def test_coordinator_owns_real_authenticated_participant_connection(self):
        proc=self.probe();self.prepared(proc)
        reply=self.attach(proc);self.assertTrue(reply.HasField('view'),reply)
        auth=self.update(proc,'authenticated').authenticated
        self.assertEqual(auth.registration,self.issuer.registration)
        self.assertEqual(auth.service_incarnation,self.binding.service_incarnation)
        self.assertEqual(auth.issuer_id,self.binding.issuer_id)
        self.assertEqual(len(auth.connection_binding),32)
        batch=self.update(proc,'revocations').revocations;self.assertTrue(batch.initial)
        self.stopped(proc)

if __name__=='__main__':
    suite=unittest.TestSuite(OwnedParticipantTests(n) for n in OwnedParticipantTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
'''
(source/'tests/native/test_authority_owned_participant.py').write_text(test,encoding='utf-8',newline='\n')
base=json.loads((r/'launch-authorization-candidate-03.json').read_text(encoding='utf-8'))['files']
files={n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in sorted([*base,'tests/native/test_authority_owned_participant.py'])}
target=r/'owned-participant-baseline-01'
target.with_suffix('.json').write_text(json.dumps(dict(files=files,base='launch-authorization-candidate-03'),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(target.with_suffix('.tar'),'w') as a:
    for n in files:a.add(source/n,arcname=n,recursive=False)
print('Frozen owned-participant baseline:274 files; schema declarations and two missing-feature checks only.')
