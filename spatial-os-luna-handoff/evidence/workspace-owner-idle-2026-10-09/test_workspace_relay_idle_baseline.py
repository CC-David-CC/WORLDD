"""Authenticated actors use real workspace grants and confined POSIX processes."""
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
import unittest
from google.protobuf import text_format
import broker_pb2 as broker
import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.git_fixture import GitRepo
from harness.wire import frame, make_request, result_value
from test_world_identity import WorldIdentityFixture, WorldProcess, WORLD, receive
import test_world_authority as world_authority

BROKER = WORLD.parent/'workspaced'
WRITE = [grant.FILE_CREATE, grant.FILE_EDIT, grant.FILE_RENAME, grant.FILE_MOVE, grant.FILE_COPY,
         grant.FILE_TRASH, grant.FILE_RESTORE, grant.FILE_PERMANENT_DELETE]
PROJECT = [grant.FILE_BROWSE, grant.FILE_READ, *WRITE, grant.PROCESS_EXECUTE]
RIGHTS = [grant.WORKSPACE_ACCESS, *PROJECT, grant.POLICY_ADMINISTER]
POLICY = ['workspace.select', 'files.read', 'files.write', 'process.launch']


class BrokerProcess:
    def __init__(self, fixture, state, config=None):
        self.stream, child = socket.socketpair(); self.sequence = 0
        self.log = (fixture.root/f'broker-{len(fixture.brokers)}.log').open('wb')
        command = [str(BROKER), '--state-dir', str(state)]
        if config: command += ['--authority-config', str(config)]
        self.proc = subprocess.Popen(command, stdin=child, stdout=child, stderr=self.log)
        child.close(); fixture.brokers.append(self)

    def call(self, method, **args):
        self.sequence += 1; request = make_request(method, args); request.request_id = self.sequence
        self.stream.sendall(frame(broker.Request(protocol_version=1, request_id=self.sequence, management=request)))
        reply = receive(self.stream.recv, self.stream, broker.Reply)
        if reply.request_id != self.sequence or reply.HasField('error') or reply.management.HasField('error'):
            raise AssertionError(reply)
        return result_value(reply.management)

    def request(self, **operation):
        self.sequence += 1
        self.stream.sendall(frame(broker.Request(protocol_version=1, request_id=self.sequence, **operation)))
        reply = receive(self.stream.recv, self.stream, broker.Reply)
        if reply.protocol_version != 1 or reply.request_id != self.sequence: raise AssertionError(reply)
        return reply

    def close(self):
        self.stream.close()
        if self.proc.poll() is None:
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=3); raise
        self.log.close()


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

    def workspace(self, policy=POLICY, roots=None, issuer_port=None):
        self.execution = self.root/'execution'; first = BrokerProcess(self, self.execution)
        repository = GitRepo(self.root)
        first.call('git.repo.import', id='repo-one', path=str(repository.path))
        work = first.call('git.worktree.create', repo='repo-one', id='tree-one', branch='branch-one', start='main', operation_id='tree')
        self.project = Path(work['path'])
        first.call('workspace.create', id='one', worktree='tree-one', policy=policy); first.close()
        directory = self.root/'workspace-authority'; directory.mkdir(mode=0o700)
        seed = broker.WorkspaceAuthoritySeed(protocol_version=1, workspace='one', authority_directory=str(directory),
            roots=roots() if roots else [self.workspace_terms()])
        seed_path = self.root/'workspace-seed.textproto'; seed_path.write_text(text_format.MessageToString(seed)); seed_path.chmod(0o600)
        result = subprocess.run([str(BROKER), '--state-dir', str(self.execution), '--init-authority', str(seed_path)], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        client_path = self.root/'workspace-issuer.textproto'
        client_path.write_bytes(self.config_file(endpoint_port=issuer_port).read_bytes()); client_path.chmod(0o600)
        config = broker.WorkspaceAuthorityConfig(protocol_version=1, issuer_client_config=str(client_path),
            bindings=[broker.WorkspaceAuthorityBinding(workspace='one', authority_directory=str(directory))])
        path = self.root/'workspace-policy.textproto'; path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
        self.broker = BrokerProcess(self, self.execution, path)
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
        todo = [self.broker.proc.pid]; found = set()
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
        self.owner_peer = self.connect(); self.revoke(kind=issuer.CREDENTIAL_TARGET)
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
        relay.armed.set(); result = self.broker.request(launch=request)
        self.denied(result,core.UNAVAILABLE); self.assertTrue(relay.delivered.is_set())
        self.until(lambda:self.gone(pids),bool); self.assertFalse((self.project/'done').exists())


if __name__ == '__main__': unittest.main(verbosity=2)
