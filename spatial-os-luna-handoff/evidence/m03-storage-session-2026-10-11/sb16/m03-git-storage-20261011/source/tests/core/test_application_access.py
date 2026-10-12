"""Real broker decisions bind issuer grants, native identity, and live process rights."""
import fcntl
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import unittest
from google.protobuf import text_format
import broker_pb2 as pb
import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
from harness.git_fixture import GitRepo
import test_workspace_authority as workspace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'native'))
import test_security_identity as native
import security_pb2 as security


APP_RIGHTS = [grant.APPLICATION_OBSERVE, grant.APPLICATION_CONTROL]
ALL = [*workspace.RIGHTS, *APP_RIGHTS]
READ = [grant.WORKSPACE_ACCESS, grant.FILE_BROWSE, grant.FILE_READ, grant.PROCESS_EXECUTE]
HOLD = "from pathlib import Path\nimport time\nPath('/state/data/ready').touch()\nwhile not Path('/state/data/release').exists():time.sleep(.01)"


class Broker(workspace.BrokerProcess):
    def __init__(self, fixture, config, native_config=None):
        self.stream, child = socket.socketpair(); self.sequence = 0
        self.path = fixture.root/f'broker-{len(fixture.brokers)}.log'
        self.log = self.path.open('wb'); self.fixture = fixture
        command = [str(workspace.BROKER), '--state-dir', str(fixture.execution), '--authority-config', str(config)]
        fd = None
        if native_config is not None:
            fd = os.memfd_create('application-access-native', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            os.write(fd, native_config.SerializeToString())
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            command += ['--native-security-fd', str(fd)]
        try:
            self.proc = subprocess.Popen(command, stdin=child, stdout=child, stderr=self.log,
                                         pass_fds=() if fd is None else (fd,))
        finally:
            child.close()
            if fd is not None: os.close(fd)
        fixture.brokers.append(self)
        self.call('system.hello')

    def close(self):
        if self.stream.fileno() < 0: return
        if self.proc.poll() is None:
            self.stream.shutdown(socket.SHUT_WR)
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=3); raise
        self.stream.close(); self.log.close()
        self.fixture.assertEqual(self.proc.returncode, 0, self.path.read_text())


class ApplicationAccessTests(workspace.WorldIdentityFixture):
    principal = workspace.WorkspaceAuthorityTests.principal
    workspace_terms = workspace.WorkspaceAuthorityTests.workspace_terms
    enroll_second = workspace.WorkspaceAuthorityTests.enroll_second

    def setUp(self):
        super().setUp()
        self.brokers = []; self.sequence = 0
        self.addCleanup(self.close_brokers)

    def close_brokers(self):
        for item in reversed(self.brokers): item.close()
        for path in self.root.glob('broker-*.log'):
            text = path.read_text(errors='replace')
            for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'): self.assertNotIn(marker, text)
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE']); target.mkdir(parents=True, exist_ok=True)
                (target/(self._testMethodName+'-'+path.name)).write_text(text)

    def setup_application(self, rights=ALL, native_enabled=True, policy=workspace.POLICY):
        self.execution = self.root/'execution'
        first = workspace.BrokerProcess(self, self.execution)
        repository = GitRepo(self.root)
        first.call('git.repo.import', id='repo-one', path=str(repository.path))
        first.call('git.worktree.create', repo='repo-one', id='tree-one', branch='app-one', start='main', operation_id='tree')
        first.call('workspace.create', id='one', worktree='tree-one', policy=policy)
        first.close()
        directory = self.root/'authority'; directory.mkdir(mode=0o700)
        seed = pb.WorkspaceAuthoritySeed(protocol_version=1, workspace='one', authority_directory=str(directory),
                                         roots=[self.workspace_terms(rights=rights)])
        path = self.root/'seed.textproto'; path.write_text(text_format.MessageToString(seed)); path.chmod(0o600)
        result = subprocess.run([str(workspace.BROKER), '--state-dir', str(self.execution), '--init-authority', str(path)],
                                capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = pb.WorkspaceAuthorityConfig(protocol_version=1, issuer_client_config=str(self.config_file()),
            bindings=[pb.WorkspaceAuthorityBinding(workspace='one', authority_directory=str(directory))])
        self.configure_participant(config,[grant.Domain(kind=grant.WORKSPACE,id=binding.workspace) for binding in config.bindings])
        self.config = self.root/'broker.textproto'; self.config.write_text(text_format.MessageToString(config)); self.config.chmod(0o600)
        self.native_config = None; self.server_id = b'x'*32
        if native_enabled:
            self.native = native.SecurityIdentityTests(methodName='runTest')
            self.native._testMethodName = self._testMethodName
            self.native.setUp(); self.addCleanup(self.native.doCleanups)
            root = self.native.start()
            self.server_id = root.inspect().server_id
            self.native_config = pb.NativeLaunchConfig(protocol_version=1, display_number=self.native.display,
                broker_cookie=root.issue(security.BROKER, b'', b'').cookie, server_id=self.server_id,
                server_pid=self.native.server.pid, server_uid=os.getuid())
        # Broker cleanup must precede native server cleanup.
        self.broker = Broker(self, self.config, self.native_config)
        self.addCleanup(self.broker.close)
        return self.launch_application()

    def launch_application(self, profile='development', claims=None):
        self.sequence += 1; principal = self.principal(claims)
        actor = 'identity:'+principal.issuer_id.hex()+':'+principal.actor_id.hex()
        reply = self.broker.request(launch=pb.LaunchRequest(workspace='one', actor=actor, principal=principal,
            command=core.ProcessLaunchRequest(operation_id='app-'+str(self.sequence), profile=profile,
                argv=['/usr/bin/python3', '-c', HOLD])))
        self.assertTrue(reply.HasField('launched'), reply)
        result = pb.ApplicationRef(workspace=b'one', application=reply.launched.process_id.encode(), server_id=self.server_id)
        self.until(lambda:(self.data(result)/'ready').exists(), bool)
        return result

    def data(self, app): return self.execution/'applications'/app.application.decode()/'data'

    def access(self, *apps, claims=None):
        reply = self.broker.request(application_access=pb.ApplicationAccessRequest(principal=self.principal(claims), applications=apps))
        self.assertTrue(reply.HasField('application_access'), reply)
        decisions = reply.application_access.applications
        self.assertEqual(len(decisions), len(apps))
        for expected, actual in zip(apps, decisions):
            self.assertEqual(actual.application, expected)
            self.assertTrue(actual.HasField('observe')); self.assertTrue(actual.HasField('control'))
        return [(item.observe, item.control) for item in decisions]

    def delegate(self, rights, app=None, components=None, parent=None, depth=0):
        principal = self.principal()
        page = self.broker.request(grants=pb.GrantListRequest(workspace='one', principal=principal)).grants
        parent = parent or next(item for item in page.grants if not item.parent_id)
        scope = components if components is not None else (() if app is None else (b'applications', app.application))
        terms = self.workspace_terms(self.second, rights=rights, depth=depth, components=scope)
        reply = self.broker.request(delegate=pb.GrantDelegateRequest(workspace='one', principal=principal,
            parent_id=parent.id, terms=terms, expected_revision=page.revision))
        self.assertTrue(reply.HasField('grant'), reply)
        return reply.grant

    def revoke_record(self, record):
        principal = self.principal()
        page = self.broker.request(grants=pb.GrantListRequest(workspace='one', principal=principal)).grants
        result = self.broker.request(revoke=pb.GrantRevokeRequest(workspace='one', principal=principal,
            id=record.id, expected_revision=page.revision))
        self.assertTrue(result.HasField('grant'), result)

    def finish_application(self, app):
        (self.data(app)/'release').touch()
        result = self.broker.call('process.wait', id=app.application.decode(), timeout_seconds=5)
        self.assertEqual(result, {'exit_code': 0})

    def test_explicit_grants_authorize_live_application(self):
        app = self.setup_application(); self.assertEqual(self.access(app), [(True, True)])

    def test_launch_authority_does_not_imply_observation_or_control(self):
        app = self.setup_application(rights=workspace.RIGHTS); self.assertEqual(self.access(app), [(False, False)])

    def test_observation_does_not_require_filesystem_or_execution_grants(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([grant.WORKSPACE_ACCESS]); self.delegate([grant.APPLICATION_OBSERVE], app)
        self.assertEqual(self.access(app, claims=self.second), [(True, False)])

    def test_control_grant_does_not_imply_observation(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([grant.WORKSPACE_ACCESS, *workspace.PROJECT]); self.delegate([grant.APPLICATION_CONTROL], app)
        self.assertEqual(self.access(app, claims=self.second), [(False, False)])

    def test_application_grants_do_not_imply_workspace_access(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([*workspace.PROJECT, *APP_RIGHTS])
        self.assertEqual(self.access(app, claims=self.second), [(False, False)])

    def test_readonly_actor_cannot_control_writable_application(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([*READ, *APP_RIGHTS])
        self.assertEqual(self.access(app, claims=self.second), [(True, False)])

    def test_readonly_actor_can_control_readonly_application(self):
        self.enroll_second(); self.setup_application(); app = self.launch_application(profile='read-only')
        self.delegate([*READ, *APP_RIGHTS])
        self.assertEqual(self.access(app, claims=self.second), [(True, True)])

    def test_workspace_policy_limits_recorded_application_authority(self):
        self.enroll_second()
        app = self.setup_application(policy=['workspace.select', 'files.read', 'process.launch'])
        self.delegate([*READ, *APP_RIGHTS])
        self.assertEqual(self.access(app, claims=self.second), [(True, True)])

    def test_control_requires_application_execution_authority(self):
        self.enroll_second(); self.setup_application(); app = self.launch_application(profile='read-only')
        self.delegate([grant.WORKSPACE_ACCESS, grant.FILE_READ, grant.FILE_BROWSE, *APP_RIGHTS])
        self.assertEqual(self.access(app, claims=self.second), [(True, False)])

    def test_partial_write_grants_do_not_control_writable_application(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([*READ, grant.FILE_EDIT, *APP_RIGHTS])
        self.assertEqual(self.access(app, claims=self.second), [(True, False)])

    def test_application_scope_does_not_authorize_another_application(self):
        self.enroll_second(); first = self.setup_application(); second = self.launch_application()
        self.delegate([grant.WORKSPACE_ACCESS, *workspace.PROJECT]); self.delegate(APP_RIGHTS, first)
        self.assertEqual(self.access(first, second, claims=self.second), [(True, True), (False, False)])

    def test_scope_components_are_exact_not_string_prefixes(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([grant.WORKSPACE_ACCESS, *workspace.PROJECT])
        self.delegate(APP_RIGHTS, components=(b'applications', app.application+b'-suffix'))
        self.assertEqual(self.access(app, claims=self.second), [(False, False)])

    def test_deeper_scope_cannot_authorize_parent_application(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([grant.WORKSPACE_ACCESS, *workspace.PROJECT])
        self.delegate(APP_RIGHTS, components=(b'applications', app.application, b'child'))
        self.assertEqual(self.access(app, claims=self.second), [(False, False)])

    def test_wrong_native_server_unknown_application_and_workspace_are_denied(self):
        app = self.setup_application()
        wrong_server = pb.ApplicationRef(); wrong_server.CopyFrom(app); wrong_server.server_id = b'z'*32
        unknown = pb.ApplicationRef(); unknown.CopyFrom(app); unknown.application = b'unknown'
        foreign = pb.ApplicationRef(); foreign.CopyFrom(app); foreign.workspace = b'two'
        self.assertEqual(self.access(wrong_server, app, unknown, foreign), [(False, False), (True, True), (False, False), (False, False)])

    def test_nonnative_process_does_not_gain_native_application_authority(self):
        app = self.setup_application(native_enabled=False)
        self.assertEqual(self.access(app), [(False, False)])

    def test_exited_application_loses_access(self):
        app = self.setup_application(); self.assertEqual(self.access(app), [(True, True)])
        self.finish_application(app); self.assertEqual(self.access(app), [(False, False)])

    def test_broker_restart_does_not_restore_old_process_authority(self):
        app = self.setup_application(); self.assertEqual(self.access(app), [(True, True)])
        self.broker.close(); self.broker = Broker(self, self.config, self.native_config)
        self.addCleanup(self.broker.close)
        self.assertEqual(self.access(app), [(False, False)])

    def test_control_revocation_preserves_independent_process_execution(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([grant.WORKSPACE_ACCESS, *workspace.PROJECT, grant.APPLICATION_OBSERVE])
        control = self.delegate([grant.APPLICATION_CONTROL], app)
        self.assertEqual(self.access(app, claims=self.second), [(True, True)])
        self.revoke_record(control)
        self.assertEqual(self.access(app, claims=self.second), [(True, False)])
        self.finish_application(app)

    def test_observation_revocation_also_removes_control(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([grant.WORKSPACE_ACCESS, *workspace.PROJECT, grant.APPLICATION_CONTROL])
        observe = self.delegate([grant.APPLICATION_OBSERVE], app)
        self.assertEqual(self.access(app, claims=self.second), [(True, True)])
        self.revoke_record(observe)
        self.assertEqual(self.access(app, claims=self.second), [(False, False)])
        self.finish_application(app)

    def test_independent_application_grant_survives_other_revocation(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([grant.WORKSPACE_ACCESS, *workspace.PROJECT])
        first = self.delegate(APP_RIGHTS, app); self.delegate(APP_RIGHTS, app)
        self.revoke_record(first)
        self.assertEqual(self.access(app, claims=self.second), [(True, True)])

    def test_parent_revocation_invalidates_dependent_application_grant(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([grant.WORKSPACE_ACCESS, *workspace.PROJECT])
        parent = self.delegate(APP_RIGHTS, app, depth=1)
        principal = self.principal(self.second)
        page = self.broker.request(grants=pb.GrantListRequest(workspace='one', principal=principal)).grants
        terms = self.workspace_terms(self.second, rights=APP_RIGHTS, depth=0, components=(b'applications', app.application))
        child = self.broker.request(delegate=pb.GrantDelegateRequest(workspace='one', principal=principal,
            parent_id=parent.id, terms=terms, expected_revision=page.revision))
        self.assertTrue(child.HasField('grant'), child)
        self.assertEqual(self.access(app, claims=self.second), [(True, True)])
        self.revoke_record(parent)
        self.assertEqual(self.access(app, claims=self.second), [(False, False)])
        self.finish_application(app)

    def test_application_scoped_execution_grant_does_not_authorize_project_execution(self):
        self.enroll_second(); app = self.setup_application()
        self.delegate([grant.WORKSPACE_ACCESS, grant.FILE_READ, grant.FILE_BROWSE, *workspace.WRITE, *APP_RIGHTS])
        self.delegate([grant.PROCESS_EXECUTE], app)
        self.assertEqual(self.access(app, claims=self.second), [(True, False)])

    def test_corrupt_recorded_execution_rights_cannot_reduce_control_requirement(self):
        app = self.setup_application()
        with sqlite3.connect(self.execution/'execution.db') as db:
            db.execute('UPDATE processes SET rights=0 WHERE id=?', (app.application.decode(),))
        reply = self.broker.request(application_access=pb.ApplicationAccessRequest(principal=self.principal(), applications=[app]))
        self.assertTrue(reply.HasField('error')); self.assertEqual(reply.error.code, core.STORAGE_ERROR)

    def test_foreign_issuer_principal_cannot_use_local_grants(self):
        app = self.setup_application(); principal = self.principal(); principal.issuer_id = b'z'*32
        reply = self.broker.request(application_access=pb.ApplicationAccessRequest(principal=principal, applications=[app]))
        self.assertTrue(reply.HasField('error')); self.assertEqual(reply.error.code, core.FORBIDDEN)

    def test_revoked_actor_cannot_query_application_authority(self):
        app = self.setup_application(); self.assertEqual(self.access(app), [(True, True)])
        self.owner_peer = self.connect(); self.revoke(kind=issuer.ACTOR_TARGET)
        reply = self.broker.request(application_access=pb.ApplicationAccessRequest(principal=self.principal(), applications=[app]))
        self.assertTrue(reply.HasField('error')); self.assertEqual(reply.error.code, core.UNAUTHORIZED)

    def test_request_requires_authenticated_principal(self):
        app = self.setup_application()
        reply = self.broker.request(application_access=pb.ApplicationAccessRequest(applications=[app]))
        self.assertTrue(reply.HasField('error')); self.assertEqual(reply.error.code, core.FORBIDDEN)

    def test_batch_rejects_duplicate_empty_and_excessive_targets(self):
        app = self.setup_application()
        for targets in ([], [app, app], [pb.ApplicationRef(workspace=b'one', application=str(i).encode(), server_id=self.server_id) for i in range(65)]):
            with self.subTest(size=len(targets)):
                reply = self.broker.request(application_access=pb.ApplicationAccessRequest(principal=self.principal(), applications=targets))
                self.assertTrue(reply.HasField('error')); self.assertEqual(reply.error.code, core.INVALID_ARGUMENT)
        self.assertEqual(self.access(app), [(True, True)])

    def test_malformed_target_does_not_leave_partial_decisions(self):
        app = self.setup_application()
        for field, value in (('workspace', b''), ('application', b'a\0b'), ('server_id', b'x'), ('application', b'x'*257)):
            bad = pb.ApplicationRef(); bad.CopyFrom(app); setattr(bad, field, value)
            reply = self.broker.request(application_access=pb.ApplicationAccessRequest(principal=self.principal(), applications=[app, bad]))
            self.assertTrue(reply.HasField('error')); self.assertEqual(reply.error.code, core.INVALID_ARGUMENT)
        self.assertEqual(self.access(app), [(True, True)])


if __name__ == '__main__': unittest.main(verbosity=2)
