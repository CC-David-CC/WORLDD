"""Real actor, authority, broker, compositor, and confined application recovery."""
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import array
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch
from google.protobuf import text_format
import test_session_world as existing
import broker_pb2 as broker
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as identity
import launch_pb2 as launch
import session_probe_pb2 as probe
import world_session_pb2 as session
from harness.git_fixture import GitRepo
from harness.oracles import pose
from harness.wire import frame, make_request, result_value

WORKSPACE = 'workspace-one'
RIGHTS = [grants.WORKSPACE_ACCESS, grants.FILE_BROWSE, grants.FILE_READ,
    grants.FILE_CREATE, grants.FILE_EDIT, grants.FILE_RENAME, grants.FILE_MOVE,
    grants.FILE_COPY, grants.FILE_TRASH, grants.FILE_RESTORE, grants.FILE_PERMANENT_DELETE,
    grants.PROCESS_EXECUTE, grants.APPLICATION_OBSERVE, grants.APPLICATION_CONTROL]


def register_workspace(fixture):
    participant = identity.AuthorityParticipant(id=b'p' * 32, public_key=fixture.public,
        domains=[grants.Domain(kind=grants.WORKSPACE, id=WORKSPACE)])
    op = identity.IssuerOperation(participant_register=identity.AuthorityParticipantRegister(
        participant=participant, expected_registry_revision=0))
    reply = fixture.execute(fixture.peer, fixture.challenge(fixture.peer, op))
    fixture.assertTrue(reply.HasField('participant'), reply)
    return reply.participant


class SessionApplicationTests(existing.SessionActorTests):
    coordinator_probe = 'spatial-session-application-probe'

    def setUp(self):
        registry = existing.base.baseline.supervisor.owned.registry.SupervisorRegistryTests
        with patch.object(registry, 'register', register_workspace): super().setUp()
        self.actor = self.issuer.decide(self.issuer.peer, self.issuer.enroll(self.issuer.peer)).enrollment
        self.claims = self.issuer.claims(self.actor, self.issuer.info)
        self.principal = grants.ActorRef(issuer_id=self.claims.issuer_id, actor_id=self.claims.actor_id)
        self.coordinator_sequence = self.action_sequence = self.probe_sequence = 0
        self.execution = self.root / 'execution'
        self.actor_receiver, self.actor_delivery = socket.socketpair(type=socket.SOCK_SEQPACKET)
        self.addCleanup(self.actor_receiver.close); self.addCleanup(self.actor_delivery.close)
        self.coordinator_fds = (self.actor_delivery.fileno(),)

    def connect_actor(self):
        self.actor_delivery.close()
        self.actor_receiver.settimeout(3)
        packet, control, flags, _ = self.actor_receiver.recvmsg(65536, socket.CMSG_SPACE(4), socket.MSG_CMSG_CLOEXEC)
        self.assertFalse(flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC))
        binding = launch.AuthorityLaunchReply.FromString(packet)
        self.assertEqual(binding.protocol_version, 1); self.assertEqual(binding.control_owner, self.owner)
        self.assertEqual(len(control), 1); level, kind, data = control[0]
        self.assertEqual((level, kind), (socket.SOL_SOCKET, socket.SCM_RIGHTS))
        descriptors = array.array('i'); descriptors.frombytes(data); self.assertEqual(len(descriptors), 1)
        stream = socket.socket(fileno=descriptors[0]); stream.settimeout(3); self.addCleanup(stream.close)
        self.actor_receiver.close()
        return stream

    def config_file(self, name, value):
        path = self.root / (name + '.textproto')
        path.write_text(text_format.MessageToString(value)); path.chmod(0o600)
        return path

    def broker_config(self, target=38):
        config = super().broker_config(target)
        binary = config.launch.executable
        bootstrap = subprocess.Popen([binary, '--state-dir', str(self.execution)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if bootstrap.poll() is None: bootstrap.kill()
            bootstrap.communicate(timeout=5)
        self.addCleanup(cleanup)
        channel = existing.base.BrokerPipe(self, bootstrap)
        repo = GitRepo(self.root)
        channel.call('git.repo.import', id='repo', path=str(repo.path))
        channel.call('git.worktree.create', repo='repo', id='tree', branch='application', start='main', operation_id='tree')
        channel.call('workspace.create', id=WORKSPACE, worktree='tree', policy=existing.base.native_launch.POLICY)
        bootstrap.stdin.close(); bootstrap.stdin = None
        self.assertEqual(bootstrap.wait(timeout=5), 0)
        directory = self.root / 'workspace-authority'; directory.mkdir(mode=0o700)
        seed = broker.WorkspaceAuthoritySeed(protocol_version=1, workspace=WORKSPACE, authority_directory=str(directory),
            roots=[grants.GrantTerms(subject=self.principal,
                scope=grants.Scope(domain=grants.Domain(kind=grants.WORKSPACE, id=WORKSPACE)), rights=RIGHTS)])
        result = subprocess.run([binary, '--state-dir', str(self.execution), '--init-authority',
            str(self.config_file('workspace-seed', seed))], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        authority = broker.WorkspaceAuthorityConfig(protocol_version=1,
            issuer_client_config=str(self.issuer.config_file()), participant_runtime_config=config.child_participant_config,
            bindings=[broker.WorkspaceAuthorityBinding(workspace=WORKSPACE, authority_directory=str(directory))])
        config.launch.arguments.extend(['--authority-config', str(self.config_file('workspace-authority', authority))])
        with sqlite3.connect('file:' + str(self.execution / 'execution.db') + '?mode=ro', uri=True) as db:
            self.project = Path(db.execute('SELECT path FROM worktrees WHERE id=?', ('tree',)).fetchone()[0])
        shutil.copy2(Path(binary).with_name('spatial-confined-xprobe'), self.project / 'probe')
        return config

    def world_config(self):
        config = super().world_config()
        directory = self.root / 'world-authority'; directory.mkdir(mode=0o700)
        seed = session.WorldAuthoritySeed(protocol_version=1, authority_directory=str(directory),
            roots=[grants.GrantTerms(subject=self.principal,
                scope=grants.Scope(domain=grants.Domain(kind=grants.WORLD, id=self.world_domain)),
                rights=[grants.WORLD_ENTER, grants.WORLD_NAVIGATE, grants.WORLD_OBSERVE, grants.WORLD_MANIPULATE])])
        result = subprocess.run([config.launch.executable, '--state-dir', str(self.root / 'world'),
            '--init-authority', str(self.config_file('world-seed', seed))], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = session.WorldIdentityConfig(protocol_version=1, issuer_client_config=str(self.issuer.config_file()),
            participant_runtime_config=config.child_participant_config, admission=session.EXPLICIT_ACTORS,
            authority_directory=str(directory))
        self.config_file('actor-identity', policy)
        runtime = core.RuntimeConfig()
        text_format.Parse(Path(config.launch.executable).with_name('runtime.textproto').read_text(), runtime)
        runtime.input_timeout = 10
        config.launch.arguments.extend(['--config', str(self.config_file('actor-runtime', runtime))])
        return config

    def request(self, expect_error=False, **operation):
        self.coordinator_sequence += 1
        request = probe.SessionProbeRequest(protocol_version=1, request_id=self.coordinator_sequence, **operation)
        self.coordinator.stdin.write(frame(request)); self.coordinator.stdin.flush()
        reply = self.message(self.coordinator.stdout, probe.SessionProbeReply)
        self.assertEqual(reply.request_id, request.request_id); self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.HasField('error'), expect_error, reply)
        return reply

    def management(self, method, **arguments):
        reply = self.request(world=make_request(method, arguments)).result
        self.assertFalse(reply.HasField('error'), reply)
        return result_value(reply)

    def act(self, **action):
        self.action_sequence += 1
        reply = self.actor_call(self.stream, action=core.ActorActionRequest(seq=self.action_sequence, **action))
        self.assertTrue(reply.HasField('action'), reply)
        return reply.action

    def until(self, function, predicate):
        deadline = time.monotonic() + 6
        while True:
            value = function()
            if predicate(value): return value
            self.assertLess(time.monotonic(), deadline, repr(value))
            time.sleep(.01)

    def events(self):
        path = self.app_data / 'probe.jsonl'
        if not path.exists(): return []
        return [json.loads(line) for line in path.read_text().split('\n')[:-1] if line.startswith('{')]

    def native_command(self, name, *args):
        self.probe_sequence += 1
        with (self.app_data / 'commands').open('a') as stream:
            stream.write(' '.join(map(str, [name, self.probe_sequence, *args])) + '\n')
        return self.until(self.events, lambda rows: any(r.get('event') == 'ack' and r.get('seq') == self.probe_sequence for r in rows))

    def held_application(self):
        self.coordinator, self.owner, self.world_record = self.ready()
        self.assert_private_channels_stay_in_coordinator(self.coordinator)
        self.stream = self.connect_actor()
        challenge = self.actor_call(self.stream, begin=session.WorldAuthenticationBegin(credential=self.actor.credential)).challenge
        proof = self.issuer.proof(challenge, self.issuer.actor_key, self.issuer.actor_public)
        opened = self.actor_call(self.stream, complete=identity.AuthenticationComplete(nonce=challenge.nonce, proof=proof))
        self.assertTrue(opened.HasField('opened'), opened)
        self.management('workspace.available', id=WORKSPACE, available=True)
        self.act(workspace_select=core.WorkspaceIdRequest(id=WORKSPACE))
        marker = self.root / 'ungranted-host-file'; marker.write_text('host-only fixture data')
        code = ("import json,os,subprocess,time\nfrom pathlib import Path\n"
            "descriptors=[]\nfor fd in range(3,256):\n"
            " try:os.fstat(fd);descriptors.append(fd)\n except OSError:pass\n"
            "status=Path('/proc/self/status').read_text()\n"
            "Path('/state/data/confinement.json').write_text(json.dumps(dict(cwd=os.getcwd(),fds=descriptors,"
            "mnt=os.readlink('/proc/self/ns/mnt'),no_new_privs='NoNewPrivs:\\t1' in status,"
            f"host_file_visible=Path({str(marker)!r}).exists())))\n"
            "out=open('/state/data/probe.jsonl','w')\n"
            "p=subprocess.Popen(['/project/probe','--id','coordinator-application'],stdin=subprocess.PIPE,stdout=out,stderr=out)\n"
            "sent=0\nwhile not Path('/state/data/release').exists():\n"
            " if p.poll() is not None:raise SystemExit(p.returncode or 1)\n"
            " commands=Path('/state/data/commands')\n if commands.exists():\n"
            "  data=commands.read_bytes();end=data.rfind(b'\\n')+1\n"
            "  if end>sent:p.stdin.write(data[sent:end]);p.stdin.flush();sent=end\n"
            " time.sleep(.01)\np.terminate();p.wait();out.close()\n")
        result = self.act(process_launch=core.ProcessLaunchRequest(operation_id='native-application', profile='development',
            argv=['/usr/bin/python3', '-c', code]))
        self.app = broker.ApplicationRef(workspace=WORKSPACE.encode(), application=result.process.process_id.encode(),
            server_id=self.owner.native.server_id)
        self.app_data = self.execution / 'applications' / result.process.process_id / 'data'
        def retain():
            target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
            if not target: return
            Path(target).mkdir(parents=True, exist_ok=True)
            for name in ('probe.jsonl', 'confinement.json'):
                path = self.app_data / name
                if path.exists(): (Path(target) / (self._testMethodName + '-' + name + '.log')).write_bytes(path.read_bytes())
        self.addCleanup(retain)
        surface = self.until(lambda: self.management('surface.find', probe_id='coordinator-application'),
            lambda reply: reply['surface'] is not None)['surface']
        confinement = json.loads((self.app_data / 'confinement.json').read_text())
        self.assertEqual(confinement['cwd'], '/project'); self.assertEqual(confinement['fds'], [])
        self.assertTrue(confinement['no_new_privs']); self.assertFalse(confinement['host_file_visible'])
        self.assertNotEqual(confinement['mnt'], os.readlink('/proc/self/ns/mnt'))
        self.management('surface.place', id=surface['id'], room=self.management('world.info')['entry_room'], pose=pose((0, 0, -2)), size=[1, 1])
        self.act(aim=core.ActorAimRequest(direction=core.Vec3(z=-1)))
        self.act(pointer=core.ActorPointerRequest(button=1, pressed=True))
        self.act(key=core.ActorKeyRequest(keysym='Shift_L', pressed=True))
        self.until(self.events, lambda rows: all(any(r.get('event') == name and r.get('pressed') for r in rows) for name in ('key', 'button')))
        state = self.request(inspect=self.owner).owner
        self.assertFalse(state.channel_fenced); self.assertEqual(len(state.reservations), 1)
        self.lease = state.reservations[0]
        self.assertEqual(self.lease.application, self.app); self.assertEqual(self.lease.principal, self.principal)
        # Both real issuer participants activated under their retained supervisors.
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT active FROM authority_activation').fetchall()
        self.assertEqual(rows, [(1,), (1,)])

    def released(self):
        self.until(self.events, lambda rows: all(any(r.get('event') == name and not r.get('pressed') for r in rows) for name in ('key', 'button')))
        self.native_command('resize', 640, 480)
        reply = self.request(broker=make_request('system.hello', {})).result
        self.assertEqual(reply.hello.implementation, 'production')

    def finish_session(self):
        stopped = self.request(stop_session=launch.AuthorityLaunchQuery()).cleanup
        self.assertEqual(stopped, self.checked_cleanup())
        self.assertEqual(self.coordinator.wait(timeout=10), 0, self.logpath.read_text())

    def test_actor_stop_releases_native_input_and_its_reservation(self):
        self.held_application()
        self.act(stop=core.ActorStopRequest())
        self.released()
        state = self.request(inspect=self.owner).owner
        self.assertFalse(state.channel_fenced); self.assertEqual(list(state.reservations), [])
        cleanup = self.request(stop_world=self.owner).cleanup
        self.assertEqual(cleanup.stage, launch.OWNED_SCOPES_STOPPED)
        self.finish_session()

    def test_world_crash_preserves_application_and_pending_owned_reservation(self):
        self.held_application()
        self.crash_world()
        self.finish_session()

    def crash_world(self):
        supervisor = int(next(row.split()[1] for row in Path('/proc/' + str(self.world_ids.worldd_pid) + '/status').read_text().splitlines() if row.startswith('PPid:')))
        pins = [os.pidfd_open(pid) for pid in (self.world_ids.worldd_pid, self.world_ids.spatialwm_pid, supervisor)]
        try:
            signal.pidfd_send_signal(pins[0], signal.SIGKILL)
            for pin in pins: self.assertEqual(select.select([pin], [], [], 6)[0], [pin])
            self.released()
            state = self.request(inspect=self.owner).owner
            self.assertTrue(state.channel_fenced); self.assertEqual(list(state.reservations), [self.lease])
            cleanup = self.request(stop_world=self.owner).cleanup
            self.assertEqual(cleanup.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(cleanup.process_stopped.identity, self.world_record.process)
            self.assertEqual(cleanup.native_stopped.identity, self.world_record.native)
            with sqlite3.connect('file:' + str(self.world_journal / 'launch.db') + '?mode=ro', uri=True) as db:
                saved = launch.AuthorityLaunchRecord.FromString(db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0])
            self.assertEqual(cleanup, saved)
            self.released()
            self.assertEqual(list(self.request(inspect=self.owner).owner.reservations), [self.lease])
            return cleanup
        finally:
            for pin in pins: os.close(pin)

    def test_recovery_requires_checked_cleanup_and_preserves_live_application(self):
        self.held_application()
        rejected = self.request(expect_error=True, recover_world=self.owner)
        self.assertIn('requires checked world cleanup', rejected.error.message)
        self.assertEqual(list(self.request(inspect=self.owner).owner.reservations), [self.lease])
        cleanup = self.crash_world()
        changed = broker.ControlOwner(); changed.CopyFrom(self.owner); changed.launch.generation += 1
        self.request(expect_error=True, recover_world=changed)
        recovered = self.request(recover_world=self.owner).owner
        self.assertTrue(recovered.channel_fenced); self.assertEqual(list(recovered.reservations), [])
        self.assertEqual(recovered.recovery.cleanup.owner, self.owner)
        self.assertEqual(recovered.recovery.cleanup.process_stopped, cleanup.process_stopped)
        self.assertEqual(recovered.recovery.cleanup.native_stopped, cleanup.native_stopped)
        self.assertEqual(list(recovered.recovery.released), [self.lease])
        self.assertEqual(self.request(recover_world=self.owner).owner, recovered)
        self.assertEqual(self.request(inspect=self.owner).owner, recovered)
        self.released()
        self.finish_session()


if __name__ == '__main__':
    suite = unittest.TestSuite(SessionApplicationTests(name) for name in SessionApplicationTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
