"""Authenticated actors use real workspace grants and confined POSIX processes."""
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
import threading
import unittest
from google.protobuf import text_format
from google.protobuf import descriptor_pb2
import broker_pb2 as broker
import core_pb2 as core
import copy_pb2 as workspace_copy
import grants_pb2 as grant
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.git_fixture import GitRepo
from harness.wire import frame, make_request, result_value, read_message
from harness.resource_broker import ResourceBroker
from test_world_identity import WorldIdentityFixture, WorldProcess, WORLD, receive
import test_world_authority as world_authority

BROKER = WORLD.parent/'workspaced'
WRITE = [grant.FILE_CREATE, grant.FILE_EDIT, grant.FILE_RENAME, grant.FILE_MOVE, grant.FILE_COPY,
         grant.FILE_TRASH, grant.FILE_RESTORE, grant.FILE_PERMANENT_DELETE]
PROJECT = [grant.FILE_BROWSE, grant.FILE_READ, *WRITE, grant.PROCESS_EXECUTE]
RIGHTS = [grant.WORKSPACE_ACCESS, *PROJECT, grant.POLICY_ADMINISTER]
POLICY = ['workspace.select', 'files.read', 'files.write', 'process.launch']


class BrokerChannel:
    """Give each worldd a private endpoint and serialize it with direct fixture requests."""
    def __init__(self, broker_process, stream):
        self.broker = broker_process; self.stream = stream
        self.reader = stream.makefile('rb'); self.send_lock = threading.Lock(); self.errors = []
        self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()

    def send(self, reply):
        with self.send_lock: self.stream.sendall(frame(reply))

    def run(self):
        try:
            while True:
                request = read_message(self.reader, broker.Request)
                if request.protocol_version != 1 or request.request_id <= 0:
                    raise AssertionError('Invalid world broker request identity')
                self.broker.world_request_count += 1
                self.send(self.broker.exchange(request))
        except (EOFError, OSError):
            pass
        except BaseException as error:
            self.errors.append(error)

    def close(self):
        if not getattr(self, 'closed', False):
            try: self.stream.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.stream.close(); self.thread.join(5)
            if self.thread.is_alive(): raise AssertionError('Serialized broker channel did not stop')
            if not self.reader.closed: self.reader.close()
            self.closed = True
            if self in self.broker.channels: self.broker.channels.remove(self)
        if self.errors: raise self.errors[0]


class BrokerProcess:
    def __init__(self, fixture, state, config=None):
        self.stream, child = socket.socketpair(); self.sequence = 0
        self.log = (fixture.root/f'broker-{len(fixture.brokers)}.log').open('wb')
        command = [str(BROKER), '--state-dir', str(state)]
        if config: command += ['--authority-config', str(config)]
        self.resources = None
        configuration = os.environ.get('SPATIAL_TEST_BROKER_CONFIG')
        self.owner_log = None
        if configuration:
            self.owner_log = (fixture.root/f'broker-owner-{len(fixture.brokers)}.log').open('wb')
            self.resources = ResourceBroker(BROKER, command, child, self.log, self.owner_log, configuration)
            self.proc = self.resources.process
            self.pid = self.resources.pid
        else:
            self.proc = subprocess.Popen(command, stdin=child, stdout=child, stderr=self.log)
            self.pid = self.proc.pid
        child.close()
        self.io_lock = threading.RLock(); self.channels = []
        self.world_request_count = 0
        fixture.brokers.append(self)

    def dispatch_control(self, reply):
        if not reply.HasField('control_revoked') or reply.request_id != 0:
            raise AssertionError('Invalid unsolicited broker control reply')
        if len(self.channels) != 1:
            raise AssertionError('Selected fixture relay supports one active world for control delivery')
        self.channels[0].send(reply)

    def exchange(self, request):
        with self.io_lock:
            self.stream.sendall(frame(request))
            while True:
                reply = receive(self.stream.recv, self.stream, broker.Reply)
                if reply.protocol_version != 1: raise AssertionError('Invalid broker reply protocol')
                if reply.HasField('control_revoked'):
                    self.dispatch_control(reply)
                    continue
                if reply.request_id != request.request_id:
                    raise AssertionError(f'Broker reply {reply.request_id} did not match request {request.request_id}')
                return reply

    def open_world_channel(self):
        world, relay = socket.socketpair()
        channel = BrokerChannel(self, relay); self.channels.append(channel)
        return world, channel

    def call(self, method, **args):
        with self.io_lock:
            self.sequence += 1; request = make_request(method, args); request.request_id = self.sequence
            reply = self.exchange(broker.Request(protocol_version=1, request_id=self.sequence, management=request))
            if reply.HasField('error') or reply.management.HasField('error'): raise AssertionError(reply)
            return result_value(reply.management)

    def request(self, **operation):
        with self.io_lock:
            self.sequence += 1
            request = broker.Request(protocol_version=1, request_id=self.sequence, **operation)
            return self.exchange(request)

    def close(self):
        if self.stream.fileno()<0:return
        for channel in tuple(self.channels): channel.close()
        if self.resources:
            try:
                self.stream.shutdown(socket.SHUT_WR)
                self.resources.close()
            finally:
                self.stream.close(); self.log.close(); self.owner_log.close()
            return
        if self.proc.poll() is None:
            try:self.stream.shutdown(socket.SHUT_WR)
            except OSError:pass
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=3); raise
        self.stream.close();self.log.close()


class WorkspaceAuthorityTests(WorldIdentityFixture):
    principal = world_authority.WorldAuthorityTests.principal
    terms = world_authority.WorldAuthorityTests.terms
    scoped_world = world_authority.WorldAuthorityTests.scoped_world
    enroll_second = world_authority.WorldAuthorityTests.enroll_second
    authenticate_second = world_authority.WorldAuthorityTests.authenticate_second

    def setUp(self):
        super().setUp(); self.brokers = []
        self.addCleanup(lambda: ([world.close() for world in reversed(self.worlds)],
                                 [item.close() for item in reversed(self.brokers)]))

    def workspace_terms(self, claims=None, rights=RIGHTS, depth=3, components=(), delegable=None):
        return grant.GrantTerms(subject=self.principal(claims),
            scope=grant.Scope(domain=grant.Domain(kind=grant.WORKSPACE, id='one'), components=components),
            rights=rights, delegable_rights=rights if delegable is None and depth else delegable or (), delegation_depth=depth)

    def workspace(self, policy=POLICY, roots=None, issuer_port=None, registry=False, legacy=False, pair=False):
        self.execution = self.root/'execution'; first = BrokerProcess(self, self.execution)
        repository = GitRepo(self.root)
        first.call('git.repo.import', id='repo-one', path=str(repository.path))
        work = first.call('git.worktree.create', repo='repo-one', id='tree-one', branch='branch-one', start='main', operation_id='tree')
        self.project = Path(work['path'])
        first.call('workspace.create', id='one', worktree='tree-one', policy=policy)
        if pair:
            second_repository = GitRepo(self.root/'second-repository')
            first.call('git.repo.import', id='repo-two', path=str(second_repository.path))
            first.call('git.worktree.create', repo='repo-two', id='tree-two', branch='branch-two',
                       start='main', operation_id='tree-two')
            first.call('workspace.create', id='two', worktree='tree-two', policy=policy)
        if legacy: first.call('workspace.member.set', id='one', actor='legacy-member', rights=POLICY)
        first.close()
        directory = self.root/'workspace-authority'; directory.mkdir(mode=0o700)
        seed = broker.WorkspaceAuthoritySeed(protocol_version=1, workspace='one', authority_directory=str(directory),
            roots=roots() if roots else [self.workspace_terms()])
        seed_path = self.root/'workspace-seed.textproto'; seed_path.write_text(text_format.MessageToString(seed)); seed_path.chmod(0o600)
        result = subprocess.run([str(BROKER), '--state-dir', str(self.execution), '--init-authority', str(seed_path)], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        bindings = [broker.WorkspaceAuthorityBinding(workspace='one', authority_directory=str(directory))]
        if pair:
            second_dir = self.root/'workspace-two-authority'; second_dir.mkdir(mode=0o700)
            second_terms = self.workspace_terms()
            second_terms.scope.domain.id = 'two'
            second_seed = broker.WorkspaceAuthoritySeed(protocol_version=1, workspace='two',
                authority_directory=str(second_dir), roots=[second_terms])
            second_seed_path = self.root/'workspace-two-seed.textproto'
            second_seed_path.write_text(text_format.MessageToString(second_seed)); second_seed_path.chmod(0o600)
            second_result = subprocess.run([str(BROKER), '--state-dir', str(self.execution),
                '--init-authority', str(second_seed_path)], capture_output=True, timeout=8)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            bindings.append(broker.WorkspaceAuthorityBinding(workspace='two', authority_directory=str(second_dir)))
        if registry:
            registry_dir = self.root/'workspace-registry-authority'; registry_dir.mkdir(mode=0o700)
            create_root = grant.GrantTerms(subject=self.principal(),
                scope=grant.Scope(domain=grant.Domain(kind=grant.WORKSPACE, id='workspace-registry')),
                rights=[grant.WORKSPACE_CREATE], delegation_depth=0)
            registry_seed = broker.WorkspaceAuthoritySeed(protocol_version=1, workspace='workspace-registry',
                authority_directory=str(registry_dir), roots=[create_root])
            registry_seed_path = self.root/'registry-seed.textproto'
            registry_seed_path.write_text(text_format.MessageToString(registry_seed)); registry_seed_path.chmod(0o600)
            registry_result = subprocess.run([str(BROKER), '--state-dir', str(self.execution),
                '--init-authority', str(registry_seed_path)], capture_output=True, timeout=8)
            self.assertEqual(registry_result.returncode, 0, registry_result.stderr)
            bindings.append(broker.WorkspaceAuthorityBinding(workspace='workspace-registry',
                authority_directory=str(registry_dir)))
        client_path = self.root/'workspace-issuer.textproto'
        client_path.write_bytes(self.config_file(endpoint_port=issuer_port).read_bytes()); client_path.chmod(0o600)
        config = broker.WorkspaceAuthorityConfig(protocol_version=1, issuer_client_config=str(client_path),
            bindings=bindings)
        self.configure_participant(config,[grant.Domain(kind=grant.WORKSPACE,id=binding.workspace) for binding in config.bindings])
        path = self.root/'workspace-policy.textproto'; path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
        self.authority_config = path
        self.broker = BrokerProcess(self, self.execution) if legacy else BrokerProcess(self, self.execution, path)
        world = self.scoped_world(session.REGISTERED_ACTORS, broker=self.broker)
        world.management('workspace.available', id='one', available=True)
        return world

    def own(self, peer):
        reply = peer.call(grants=session.WorldGrantList(workspace='one'))
        self.assertTrue(reply.HasField('grants'), reply); return reply.grants

    def delegate(self, peer, terms, parent=None):
        page = self.own(peer); parent = parent or next(item for item in page.grants if not item.parent_id)
        peer.action_sequence += 1
        result = peer.call(delegate=session.WorldGrantDelegate(workspace='one', seq=peer.action_sequence,
            parent_id=parent.id, terms=terms, expected_revision=page.revision))
        return result

    def grant_second(self, world, rights, components=()):
        result = self.delegate(world.peers[0], self.workspace_terms(self.second, rights=rights, depth=0, components=components))
        self.assertTrue(result.HasField('grant'), result); return result.grant

    def revoke_grant(self, peer, record):
        revision = self.own(peer).revision; peer.action_sequence += 1
        result = peer.call(revoke=session.WorldGrantRevoke(workspace='one', seq=peer.action_sequence, id=record.id, expected_revision=revision))
        self.assertTrue(result.HasField('grant'), result); return result.grant

    def select(self, peer):
        result = peer.act(workspace_select=core.WorkspaceIdRequest(id='one'))
        self.assertTrue(result.HasField('action'), result)

    def launch_process(self, peer, code, operation='launch', profile='development'):
        result = peer.act(process_launch=core.ProcessLaunchRequest(argv=['/usr/bin/python3','-c',code], operation_id=operation, profile=profile))
        self.assertTrue(result.HasField('action'), result); return result.action.process

    def wait_job(self, world, process):
        self.assertEqual(world.management('process.wait', id=process.process_id, timeout_seconds=5), {'exit_code':0})

    def descendants(self):
        todo = [self.broker.pid]; found = set()
        while todo:
            parent = todo.pop()
            try: children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
            except FileNotFoundError: continue
            for child in map(int, children):
                if child not in found: found.add(child); todo.append(child)
        return found

    @staticmethod
    def gone(pids):
        for pid in pids:
            try: state = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
            # A process can exit after open(), while procfs serves read().
            except (FileNotFoundError, ProcessLookupError): continue
            if state not in ('Z','X'): return False
        return True

    def running(self, world, peer):
        self.select(peer)
        process = self.launch_process(peer, "from pathlib import Path\nimport time\nPath('ready').touch()\nwhile not Path('release').exists():time.sleep(.01)\nPath('done').touch()")
        self.until(lambda:(self.project/'ready').exists(), bool)
        return process

    def test_m07_actor_schema_tags_and_copy_status_privacy(self):
        action_fields = core.ActorActionRequest.DESCRIPTOR.oneofs_by_name['action'].fields
        self.assertEqual({field.name: field.number for field in action_fields if 25 <= field.number <= 29}, {
            'presentation_create': 25, 'presentation_move': 26, 'presentation_remove': 27,
            'workspace_copy': 28, 'workspace_copy_status': 29,
        })
        create = core.PresentationCreateRequest.DESCRIPTOR
        self.assertEqual(create.fields_by_name['transform'].message_type.full_name, 'spatial.core.v1.Pose')
        self.assertTrue(create.fields_by_name['room_id'].has_presence)

        file_descriptor = descriptor_pb2.FileDescriptorProto()
        file_descriptor.ParseFromString(workspace_copy.DESCRIPTOR.serialized_pb)
        status = next(message for message in file_descriptor.message_type if message.name == 'WorkspaceCopyStatus')
        self.assertEqual([(entry.start, entry.end) for entry in status.reserved_range], [(5, 6)])
        self.assertEqual({field.number for field in status.field}, {1, 2, 3, 4, 6})
        self.assertFalse(any(any(term in field.name for term in ('snapshot', 'storage', 'path', 'receipt'))
                             for field in status.field))
        public_actor_fields = {field.message_type.full_name for field in action_fields if field.message_type}
        self.assertNotIn('spatial.workspace.copy.v1.SnapshotReceipt', public_actor_fields)
        reply_fields = core.ActorActionReply.DESCRIPTOR.fields_by_name
        self.assertEqual(reply_fields['presentation'].number, 6)
        self.assertEqual(reply_fields['copy_status'].number, 7)
        self.assertEqual(reply_fields['presentation'].message_type.full_name, 'spatial.core.v1.PresentationView')
        self.assertEqual(reply_fields['copy_status'].message_type.full_name, 'spatial.workspace.copy.v1.WorkspaceCopyStatus')
        self.assertEqual(set(broker.ScopedAuthorityCheckReply.DESCRIPTOR.fields_by_name), {'allowed','revision','incarnation','epoch'})
        self.assertEqual(broker.ScopedAuthorityCheckReply.DESCRIPTOR.fields_by_name['incarnation'].number, 3)
        self.assertEqual(broker.ScopedAuthorityCheckReply.DESCRIPTOR.fields_by_name['epoch'].number, 4)
        self.assertEqual(broker.ApplicationAccessReply.DESCRIPTOR.fields_by_name['incarnation'].number, 2)
        self.assertEqual(broker.ApplicationAccessReply.DESCRIPTOR.fields_by_name['epoch'].number, 3)
        self.assertEqual(broker.Request.DESCRIPTOR.fields_by_name['authorization_fence'].number, 114)
        self.assertEqual(broker.Reply.DESCRIPTOR.fields_by_name['authorization_fence'].number, 113)
        self.assertEqual(broker.ScopedAuthorityCheckRequest.DESCRIPTOR.fields_by_name['components'].number, 4)
        self.assertEqual(broker.ScopedAuthorityCheckRequest.DESCRIPTOR.fields_by_name['components'].type,
                         descriptor_pb2.FieldDescriptorProto.TYPE_BYTES)
        self.assertEqual(broker.Request.DESCRIPTOR.fields_by_name['scoped_authority_check'].number, 113)
        self.assertEqual(broker.Reply.DESCRIPTOR.fields_by_name['scoped_authority_check'].number, 112)
        public_view = core.PresentationView.DESCRIPTOR
        self.assertEqual({field.name for field in public_view.fields},
                         {'representation_id', 'room_id', 'transform', 'generation', 'locked'})
        self.assertEqual(public_view.fields_by_name['transform'].message_type.full_name, 'spatial.core.v1.Pose')

    def test_private_scoped_authority_check_uses_exact_scope_and_fresh_revocation(self):
        self.enroll_second()
        world = self.workspace(roots=lambda: [self.workspace_terms(
            rights=[*RIGHTS, grant.WORKSPACE_PRESENTATION_ATTACH])])
        components = (b'presentations\x00', b'\xffrep-1')
        record = self.grant_second(world, [grant.WORKSPACE_PRESENTATION_ATTACH], components)
        query = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(self.second),
            right=grant.WORKSPACE_PRESENTATION_ATTACH, components=list(components))
        reply = self.broker.request(scoped_authority_check=query)
        self.assertTrue(reply.HasField('scoped_authority_check'), reply)
        self.assertTrue(reply.scoped_authority_check.allowed)
        self.assertGreater(reply.scoped_authority_check.revision, 0)
        self.assertEqual(len(reply.scoped_authority_check.incarnation), 16)
        self.assertGreater(reply.scoped_authority_check.epoch, 0)
        query.components[-1] = b'\xffother-representation'
        denied = self.broker.request(scoped_authority_check=query)
        self.assertFalse(denied.scoped_authority_check.allowed)
        for right in (grant.WORLD_PLACEMENT_CREATE, 99, grant.WORKSPACE_CREATE):
            invalid = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(self.second), right=right)
            self.denied(self.broker.request(scoped_authority_check=invalid), core.INVALID_ARGUMENT)
        query.components[:] = components
        before = self.broker.request(scoped_authority_check=query).scoped_authority_check.revision
        self.revoke_grant(world.peers[0], record)
        after = self.broker.request(scoped_authority_check=query)
        self.assertFalse(after.scoped_authority_check.allowed)
        self.assertGreater(after.scoped_authority_check.revision, before)
        self.assertGreater(after.scoped_authority_check.epoch, reply.scoped_authority_check.epoch)

    def test_application_access_fails_closed_without_authority_fence(self):
        self.workspace(legacy=True)
        request = broker.ApplicationAccessRequest(principal=self.principal(),
            applications=[broker.ApplicationRef(workspace=b'one', application=b'missing-app', server_id=b's' * 32)])
        self.denied(self.broker.request(application_access=request), core.UNIMPLEMENTED)

    def test_authorization_fence_rejects_later_workspace_revocation_in_application_batch(self):
        relay = world_authority.RelayingIssuer(self, lambda: self.revoke(kind=issuer.ACTOR_TARGET))
        self.workspace(issuer_port=relay.port, pair=True)
        principal = self.principal()
        self.owner_peer = self.connect()
        relay.trigger_after_status = 2
        relay.armed.set()
        request = broker.ApplicationAccessRequest(principal=principal, applications=[
            broker.ApplicationRef(workspace=b'one', application=b'missing-one', server_id=b's' * 32),
            broker.ApplicationRef(workspace=b'two', application=b'missing-two', server_id=b's' * 32)])
        self.denied(self.broker.request(application_access=request), core.UNAUTHORIZED)
        self.assertEqual(relay.status_seen, 2)
        self.assertTrue(relay.delivered.is_set())
        self.assertFalse(relay.errors)

    def test_authorization_fence_tracks_delegation_revision(self):
        self.enroll_second(); world = self.workspace()
        principal = self.principal()
        request = broker.AuthorizationFenceRequest(principal=principal)
        before = self.broker.request(authorization_fence=request).authorization_fence
        self.grant_second(world, [grant.WORKSPACE_ACCESS])
        after = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual(after.incarnation, before.incarnation)
        self.assertGreater(after.epoch, before.epoch)
        repeated = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual((repeated.incarnation, repeated.epoch), (after.incarnation, after.epoch))

    def test_authorization_fence_is_stable_and_changes_incarnation_on_restart(self):
        world = self.workspace()
        principal = self.principal()
        request = broker.AuthorizationFenceRequest(principal=principal)
        first_reply = self.broker.request(authorization_fence=request)
        self.assertTrue(first_reply.HasField('authorization_fence'), first_reply)
        first = first_reply.authorization_fence
        self.assertEqual(len(first.incarnation), 16); self.assertGreater(first.epoch, 0)
        second = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual((second.incarnation, second.epoch), (first.incarnation, first.epoch))
        baseline_world_requests = self.broker.world_request_count
        direct_results = []; direct_errors = []; start = threading.Barrier(2)
        def query_fence_repeatedly():
            try:
                start.wait()
                for _ in range(6):
                    direct_results.append(self.broker.request(authorization_fence=request).authorization_fence)
            except BaseException as error:
                direct_errors.append(error)
        query_thread = threading.Thread(target=query_fence_repeatedly, daemon=True)
        query_thread.start(); start.wait()
        for _ in range(3):
            selected = world.peers[0].act(workspace_select=core.WorkspaceIdRequest(id='one'))
            self.assertTrue(selected.HasField('action'), selected)
            cleared = world.peers[0].act(workspace_clear=core.ActorStopRequest())
            self.assertTrue(cleared.HasField('action'), cleared)
        query_thread.join(5)
        self.assertFalse(query_thread.is_alive(), 'direct broker requests exceeded the existing 5-second bound')
        self.assertFalse(direct_errors, direct_errors)
        self.assertEqual(len(direct_results), 6)
        self.assertGreater(self.broker.world_request_count, baseline_world_requests)
        self.assertIsNone(world.proc.poll(), 'worldd must remain live through serialized broker traffic')
        scoped = self.broker.request(scoped_authority_check=broker.ScopedAuthorityCheckRequest(
            workspace='one', principal=principal, right=grant.WORKSPACE_ACCESS)).scoped_authority_check
        app_batch = self.broker.request(application_access=broker.ApplicationAccessRequest(principal=principal,
            applications=[broker.ApplicationRef(workspace=b'one', application=b'missing-app', server_id=b's' * 32)]))
        self.assertTrue(app_batch.HasField('application_access'), app_batch)
        self.assertEqual(len(app_batch.application_access.incarnation), 16)
        fence = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual((scoped.incarnation, scoped.epoch), (fence.incarnation, fence.epoch))
        self.assertEqual((app_batch.application_access.incarnation, app_batch.application_access.epoch),
                         (fence.incarnation, fence.epoch))
        initial_epoch = fence.epoch
        second_repo = GitRepo(self.root/'second-repository')
        self.broker.call('git.repo.import', id='repo-two', path=str(second_repo.path))
        self.broker.call('git.worktree.create', repo='repo-two', id='tree-two', branch='branch-two',
                         start='main', operation_id='tree-two')
        self.broker.call('workspace.create', id='two', worktree='tree-two', policy=['workspace.select'])
        created = self.broker.request(authorization_fence=request).authorization_fence
        self.assertGreater(created.epoch, initial_epoch)
        self.broker.call('workspace.member.set', id='two', actor='test-member', rights=['workspace.select'])
        changed = self.broker.request(authorization_fence=request).authorization_fence
        self.assertGreater(changed.epoch, created.epoch)
        self.broker.call('workspace.member.set', id='two', actor='test-member', rights=['workspace.select'])
        unchanged = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual(unchanged.epoch, changed.epoch)
        self.assertLess(scoped.epoch, changed.epoch)
        self.assertLess(app_batch.application_access.epoch, changed.epoch)
        world.close()
        self.assertEqual(world.proc.returncode, 0, 'worldd must close before its broker restarts')
        self.broker.close()
        self.broker = BrokerProcess(self, self.execution, self.authority_config)
        restarted = self.broker.request(authorization_fence=request).authorization_fence
        self.assertNotEqual(restarted.incarnation, first.incarnation)
        self.assertGreater(restarted.epoch, 0)

    def test_registry_authority_binding_needs_no_execution_workspace(self):
        self.workspace(registry=True)
        query = broker.ScopedAuthorityCheckRequest(workspace='workspace-registry', principal=self.principal(),
            right=grant.WORKSPACE_CREATE)
        reply = self.broker.request(scoped_authority_check=query)
        self.assertTrue(reply.HasField('scoped_authority_check'), reply)
        self.assertTrue(reply.scoped_authority_check.allowed)
        with self.assertRaisesRegex(AssertionError, 'INVALID_ARGUMENT'):
            self.broker.call('workspace.create', id='workspace-registry', worktree='tree-one', policy=POLICY)
        ordinary = broker.ScopedAuthorityCheckRequest(workspace='workspace-registry', principal=self.principal(),
            right=grant.FILE_READ)
        self.denied(self.broker.request(scoped_authority_check=ordinary), core.INVALID_ARGUMENT)
        self.denied(self.broker.request(access=broker.AccessRequest(workspace='workspace-registry', actor='x')),
                    core.INVALID_ARGUMENT)

    def test_scoped_authority_obeys_workspace_policy_ceiling(self):
        self.workspace(policy=['workspace.select'])
        query = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(), right=grant.FILE_READ)
        reply = self.broker.request(scoped_authority_check=query)
        self.assertTrue(reply.HasField('scoped_authority_check'), reply)
        self.assertFalse(reply.scoped_authority_check.allowed)

    def test_scoped_authority_does_not_fall_back_to_legacy_membership(self):
        self.workspace(legacy=True)
        request = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(), right=grant.WORKSPACE_ACCESS)
        self.denied(self.broker.request(scoped_authority_check=request), core.UNIMPLEMENTED)

    def test_scoped_authority_fails_closed_after_participant_transport_loss(self):
        relay = world_authority.RelayingIssuer(self, lambda: None)
        self.workspace(issuer_port=relay.port)
        relay.close()
        query = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(),
            right=grant.WORKSPACE_ACCESS)
        self.denied(self.broker.request(scoped_authority_check=query), core.UNAVAILABLE)

    def test_scoped_authority_denies_after_issuer_actor_revocation(self):
        world = self.workspace()
        query = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(),
            right=grant.WORKSPACE_ACCESS)
        before = self.broker.request(scoped_authority_check=query)
        self.assertTrue(before.scoped_authority_check.allowed)
        self.owner_peer = self.connect()
        self.revoke(kind=issuer.ACTOR_TARGET)
        after = self.broker.request(scoped_authority_check=query)
        self.assertTrue(after.HasField('scoped_authority_check'), after)
        self.assertFalse(after.scoped_authority_check.allowed)
        self.assertGreater(after.scoped_authority_check.epoch, before.scoped_authority_check.epoch)

    def test_reserved_m07_actor_actions_fail_closed(self):
        world = self.workspace(); peer = world.peers[0]
        actions = (
            {'presentation_create': core.PresentationCreateRequest(protocol_version=1, operation_id=b'a' * 16)},
            {'presentation_move': core.PresentationMoveRequest(protocol_version=1, operation_id=b'b' * 16)},
            {'presentation_remove': core.PresentationRemoveRequest(protocol_version=1, operation_id=b'c' * 16)},
            {'workspace_copy': workspace_copy.WorkspaceCopyRequest(protocol_version=1, operation_id=b'd' * 16)},
            {'workspace_copy_status': workspace_copy.WorkspaceCopyStatusRequest(protocol_version=1, operation_id=b'd' * 16)},
        )
        for action in actions:
            with self.subTest(action=next(iter(action))):
                reply = peer.act(**action)
                self.assertEqual(reply.WhichOneof('outcome'), 'error', reply)
                self.assertEqual(reply.error.code, core.UNIMPLEMENTED, reply)
        state = peer.call(inspect=session.WorldSessionInspect()).state
        self.assertFalse(state.HasField('workspace'))

    def test_authenticated_workspace_selection_and_confined_process(self):
        world = self.workspace(); peer = world.peers[0]; self.select(peer)
        process = self.launch_process(peer, "from pathlib import Path\nimport os\nassert Path.cwd()==Path('/project')\nassert 'NoNewPrivs:\\t1' in Path('/proc/self/status').read_text()\nassert 'DISPLAY' not in os.environ\nPath('actor-file').write_text('real')")
        self.wait_job(world, process); self.assertEqual((self.project/'actor-file').read_text(), 'real')
        self.assertEqual(peer.call(inspect=session.WorldSessionInspect()).state.workspace, 'one')

    def test_world_admission_and_availability_do_not_grant_workspace_access(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.denied(peer.act(workspace_select=core.WorkspaceIdRequest(id='one')), core.FORBIDDEN)
        self.assertFalse(self.own(peer).grants)
        self.grant_second(world, [grant.WORKSPACE_ACCESS]); self.select(peer)
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/usr/bin/true'], operation_id='denied', profile='development')), core.FORBIDDEN)

    def test_grants_do_not_make_workspace_available_implicitly(self):
        world = self.workspace(); world.management('workspace.available', id='one', available=False)
        self.denied(world.peers[0].act(workspace_select=core.WorkspaceIdRequest(id='one')), core.UNAVAILABLE)

    def test_readonly_profile_keeps_project_and_git_readonly(self):
        world = self.workspace(); peer = world.peers[0]; self.select(peer)
        code = "from pathlib import Path\nassert Path('README.txt').read_text()=='base\\n'\nadmin=Path('.git').read_text().strip().split('gitdir: ',1)[1]\nfor name in ['README.txt',admin+'/HEAD']:\n try:Path(name).write_text('escape')\n except OSError:pass\n else:raise SystemExit(20)\nPath('/state/home/own-state').write_text('ok')"
        self.wait_job(world, self.launch_process(peer, code, profile='read-only'))
        self.assertEqual((self.project/'README.txt').read_text(), 'base\n')

    def test_partial_file_write_grants_do_not_mount_entire_project_writable(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        self.grant_second(world, [grant.WORKSPACE_ACCESS,grant.FILE_READ,grant.FILE_BROWSE,grant.FILE_EDIT,grant.PROCESS_EXECUTE])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); self.select(peer)
        code = "from pathlib import Path\ntry:Path('new').write_text('escape')\nexcept OSError:pass\nelse:raise SystemExit(21)"
        self.wait_job(world, self.launch_process(peer, code)); self.assertFalse((self.project/'new').exists())

    def test_workspace_policy_limits_a_broader_actor_grant(self):
        world = self.workspace(policy=['workspace.select','files.read']); peer = world.peers[0]; self.select(peer)
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/usr/bin/true'], operation_id='denied', profile='development')), core.FORBIDDEN)

    def test_grant_revocation_kills_confined_process_without_recipient_request(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        child = self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); process = self.running(world, peer)
        pids = self.descendants(); self.assertTrue(pids)
        self.revoke_grant(world.peers[0], child)
        self.until(lambda:self.gone(pids), bool)
        with self.assertRaisesRegex(AssertionError, 'UNAVAILABLE'):
            world.management('process.wait', id=process.process_id, timeout_seconds=0)
        self.assertFalse((self.project/'done').exists())

    def test_access_revocation_keeps_valid_process_execution(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        access = self.grant_second(world, [grant.WORKSPACE_ACCESS]); self.grant_second(world, PROJECT)
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); process = self.running(world, peer)
        self.revoke_grant(world.peers[0], access)
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))
        (self.project/'release').touch(); self.wait_job(world, process); self.assertTrue((self.project/'done').exists())

    def test_actor_revocation_stops_execution_while_broker_is_idle(self):
        world = self.workspace(); process = self.running(world, world.peers[0]); pids = self.descendants()
        self.owner_peer = self.connect(); self.revoke(kind=issuer.ACTOR_TARGET)
        self.until(lambda:self.gone(pids), bool)
        with self.assertRaisesRegex(AssertionError, 'UNAVAILABLE'):
            world.management('process.wait', id=process.process_id, timeout_seconds=0)

    def test_credential_revocation_preserves_valid_workspace_process(self):
        world = self.workspace(); process = self.running(world, world.peers[0])
        principal = self.principal()
        before = self.broker.request(authorization_fence=broker.AuthorizationFenceRequest(principal=principal)).authorization_fence
        self.owner_peer = self.connect(); self.revoke(kind=issuer.CREDENTIAL_TARGET)
        after = self.broker.request(authorization_fence=broker.AuthorizationFenceRequest(principal=principal)).authorization_fence
        self.assertEqual(after.incarnation, before.incarnation)
        self.assertGreater(after.epoch, before.epoch)
        time.sleep(.1); (self.project/'release').touch(); self.wait_job(world, process)
        self.assertTrue((self.project/'done').exists())

    def test_revoked_ancestor_without_world_session_stops_process(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); self.running(world, peer)
        pids = self.descendants(); world.peers[0].close()
        self.owner_peer = self.connect(); self.revoke(kind=issuer.ACTOR_TARGET)
        self.until(lambda:self.gone(pids), bool); self.assertFalse((self.project/'done').exists())

    def test_independent_execution_grant_survives_other_grant_revocation(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        child = self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); process = self.running(world, peer)
        self.revoke_grant(world.peers[0], child)
        (self.project/'release').touch(); self.wait_job(world, process)
        self.assertTrue((self.project/'done').exists())

    def test_file_scope_does_not_authorize_a_whole_project_mount(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        self.grant_second(world, [grant.WORKSPACE_ACCESS,grant.PROCESS_EXECUTE])
        self.grant_second(world, PROJECT, components=[b'README.txt'])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); self.select(peer)
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/usr/bin/true'],operation_id='scoped',profile='development')), core.FORBIDDEN)

    def test_cross_domain_unknown_and_foreign_grants_are_rejected(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[0]
        for fault in ('world','workspace','unknown','foreign'):
            with self.subTest(fault=fault):
                terms = self.workspace_terms(self.second, rights=[grant.POLICY_ADMINISTER], depth=0)
                if fault == 'world': terms.scope.domain.kind = grant.WORLD; terms.scope.domain.id = self.world_info['world_id']
                elif fault == 'workspace': terms.scope.domain.id = 'another'
                elif fault == 'unknown': terms.subject.actor_id = b'u'*32
                else: terms.subject.issuer_id = b'f'*32
                self.denied(self.delegate(peer, terms), core.FORBIDDEN)
        self.assertTrue(self.authenticate_second(world.peers[1]).HasField('opened'))
        self.assertFalse(self.own(world.peers[1]).grants)

    def test_bound_workspace_rejects_static_authority_edits(self):
        world = self.workspace(); peer = world.peers[0]
        actor = peer.call(inspect=session.WorldSessionInspect()).state.id
        with self.assertRaisesRegex(AssertionError, 'FORBIDDEN'):
            world.management('workspace.member.set',id='one',actor=actor,rights=POLICY)
        world.close()
        self.denied(self.broker.request(access=broker.AccessRequest(workspace='one',actor=actor)), core.FORBIDDEN)
        wrong = broker.AccessRequest(workspace='one',actor='chosen-name',principal=self.principal())
        self.denied(self.broker.request(access=wrong), core.INVALID_ARGUMENT)

    def test_persistent_grants_and_selection_survive_broker_and_world_restart(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        child = self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); self.select(peer)
        process = self.launch_process(peer,"from pathlib import Path\nPath('durable').write_text('saved')")
        self.wait_job(world,process); world.close(); self.broker.close()
        self.broker = BrokerProcess(self,self.execution,self.root/'workspace-policy.textproto')
        restarted = WorldProcess(self,world.config_path,world.state_path,runtime_config=world.runtime_path,broker=self.broker)
        self.worlds.append(restarted); restarted.management('system.hello'); peer = restarted.peers[0]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.assertEqual(peer.call(inspect=session.WorldSessionInspect()).state.workspace,'one')
        self.assertIn(child.id,[record.id for record in self.own(peer).grants])
        self.assertEqual((self.project/'durable').read_text(),'saved'); self.wait_job(restarted,process)

    def test_actor_revocation_interrupts_a_blocked_process_wait(self):
        world = self.workspace(); process = self.running(world,world.peers[0]); pids = self.descendants()
        request = make_request('process.wait',dict(id=process.process_id,timeout_seconds=5))
        world.sequence += 1; request.request_id = world.sequence
        world.proc.stdin.write(frame(request)); world.proc.stdin.flush(); time.sleep(.05)
        self.owner_peer = self.connect(); started = time.monotonic(); self.revoke(kind=issuer.ACTOR_TARGET)
        reply = receive(lambda n:os.read(world.proc.stdout.fileno(),n),world.proc.stdout,core.Reply)
        self.denied(reply,core.UNAVAILABLE); self.assertLess(time.monotonic()-started,2)
        self.until(lambda:self.gone(pids),bool)

    def test_partial_private_frame_does_not_block_revocation(self):
        world = self.workspace(); self.running(world,world.peers[0]); pids = self.descendants(); world.close()
        self.broker.stream.sendall(b'\x00\x00')
        self.owner_peer = self.connect(); self.revoke(kind=issuer.ACTOR_TARGET)
        self.until(lambda:self.gone(pids),bool); self.assertFalse((self.project/'done').exists())

    def test_retry_cannot_acknowledge_a_process_revoked_during_status_refresh(self):
        self.retry_revoked_process_after_idle(0)

    def test_retry_revocation_uses_live_owner_after_setup_exceeds_idle_timeout(self):
        self.retry_revoked_process_after_idle(self.config.idle_timeout_ms / 1000 + .2)

    def retry_revoked_process_after_idle(self, idle_seconds):
        self.enroll_second(); self.owner_peer = self.connect()
        relay = world_authority.RelayingIssuer(self,lambda:self.revoke(kind=issuer.ACTOR_TARGET))
        read = [grant.WORKSPACE_ACCESS,grant.FILE_READ,grant.FILE_BROWSE,grant.PROCESS_EXECUTE]
        world = self.workspace(roots=lambda:[self.workspace_terms(),self.workspace_terms(self.second,rights=read,depth=0)],issuer_port=relay.port)
        peer = world.peers[1]; self.grant_second(world,WRITE)
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); process = self.running(world,peer)
        with sqlite3.connect(self.execution/'execution.db') as database:
            payload, = database.execute('SELECT payload FROM processes WHERE id=?',(process.process_id,)).fetchone()
        request = broker.LaunchRequest.FromString(payload); pids = self.descendants()
        if idle_seconds: time.sleep(idle_seconds)
        world.close()
        # Setup and sanitizer teardown can exceed the issuer's owner idle timeout.
        # Authenticate the revocation on a fresh connection before arming the relay.
        self.owner_peer = self.connect()
        relay.armed.set(); result = self.broker.request(launch=request)
        self.denied(result,core.UNAVAILABLE); self.assertTrue(relay.delivered.is_set())
        self.until(lambda:self.gone(pids),bool); self.assertFalse((self.project/'done').exists())


if __name__ == '__main__': unittest.main(verbosity=2)
