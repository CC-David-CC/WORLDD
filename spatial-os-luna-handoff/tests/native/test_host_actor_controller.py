"""Real retained session worlds and host actor controller. Run only on qualification hosts."""
import fcntl
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import unittest
from unittest.mock import patch
from google.protobuf import text_format
import test_session_world as fixture
import test_session_application as application
import broker_pb2 as broker
import core_pb2 as core
import grants_pb2 as grants
import host_actor_controller_pb2 as host
import launch_pb2 as launch
import native_scope_pb2 as native
import security_pb2 as security
import world_session_pb2 as session
from harness.wire import frame


class HostActorControllerNativeTests(application.SessionApplicationTests):
    def issuer_fixture(self):
        issuer = super().issuer_fixture()
        ready = issuer.ready
        def ready_with_world_capacity():
            # Three worlds and their supervised services exceed the small TLS unit fixture.
            issuer.config.connection_limit = 32
            return ready()
        issuer.ready = ready_with_world_capacity
        return issuer

    def witness(self, path):
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.startswith('{')]
        self.assertEqual(rows[0]['event'], 'ready')
        confinement = json.loads((path.parent / 'confinement.json').read_text())
        self.assertEqual(confinement['cwd'], '/project')
        self.assertEqual(confinement['fds'], [])
        self.assertTrue(confinement['no_new_privs'])
        self.assertNotEqual(confinement['mnt'], os.readlink('/proc/self/ns/mnt'))
        return rows

    def between(self, rows, earlier, later):
        starts = [i for i, row in enumerate(rows) if row.get('event') == 'ack' and row.get('seq') == earlier]
        ends = [i for i, row in enumerate(rows) if row.get('event') == 'ack' and row.get('seq') == later]
        self.assertEqual(len(starts), 1, rows)
        self.assertEqual(len(ends), 1, rows)
        self.assertLess(starts[0], ends[0])
        return rows[starts[0] + 1:ends[0]]

    def assert_input(self, rows, event, pressed):
        self.assertTrue(any(row.get('event') == event and row.get('pressed') is pressed for row in rows), rows)

    def identity_config(self, config, suffix, admitted):
        if not hasattr(self, 'actor'):
            self.actor = self.issuer.decide(self.issuer.peer, self.issuer.enroll(self.issuer.peer)).enrollment
        claims = self.issuer.claims(self.actor, self.issuer.info)
        suffix = getattr(self, 'world_instance_suffix', '')
        directory = self.root / ('host-world-authority' + suffix)
        directory.mkdir(mode=0o700)
        seed = session.WorldAuthoritySeed(protocol_version=1, authority_directory=str(directory))
        # A provisioned ledger needs a root. Observation alone does not grant admission.
        rights = ([grants.WORLD_ENTER, grants.WORLD_NAVIGATE,
                   grants.WORLD_OBSERVE, grants.WORLD_MANIPULATE]
                  if admitted else [grants.WORLD_OBSERVE])
        seed.roots.add().CopyFrom(grants.GrantTerms(subject=self.principal,
                scope=grants.Scope(domain=grants.Domain(kind=grants.WORLD, id=self.world_domain)),
                rights=rights))
        seed_path = self.config_file('host-world-seed' + suffix, seed)
        serialized_seed = text_format.Parse(Path(seed_path).read_text(),
                                            session.WorldAuthoritySeed())
        self.assertEqual(len(serialized_seed.roots), 1)
        seeded_rights = set(serialized_seed.roots[0].rights)
        self.assertEqual(grants.WORLD_ENTER in seeded_rights, admitted)
        if not admitted:
            self.assertEqual(seeded_rights, {grants.WORLD_OBSERVE})
        result = subprocess.run([config.launch.executable, '--state-dir',
            str(self.root / ('world' + suffix)), '--init-authority', str(seed_path)],
            capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = session.WorldIdentityConfig(protocol_version=1,
            issuer_client_config=str(self.issuer.config_file()),
            participant_runtime_config=config.child_participant_config,
            admission=session.EXPLICIT_ACTORS, authority_directory=str(directory))
        path = self.root / ('host-actor-identity' + suffix + '.textproto')
        path.write_text(text_format.MessageToString(value)); path.chmod(0o600)
        config.launch.arguments.extend(['--identity-config', str(path)])
        runtime = core.RuntimeConfig()
        text_format.Parse(Path(config.launch.executable).with_name('runtime.textproto').read_text(), runtime)
        runtime.input_timeout = 10
        config.launch.arguments.extend(['--config', str(self.config_file('host-runtime' + suffix, runtime))])
        credential = self.root / 'host-actor-credential.textproto'
        credential.write_text(text_format.MessageToString(self.actor.credential)); credential.chmod(0o600)
        os.environ['SPATIAL_HOST_ACTOR_CREDENTIAL'] = str(credential)
        os.environ['SPATIAL_HOST_ACTOR_KEY'] = str(self.issuer.actor_key)
        os.environ['SPATIAL_HOST_ACTOR_SERVER_PID'] = str(self.server.pid)

    def seeded_world_config(self):
        suffix = getattr(self, 'world_instance_suffix', '')
        state = self.root / ('world' + suffix)
        state.mkdir(mode=0o700, exist_ok=True)
        binary = fixture.base.baseline.supervisor.owned.existing.PROBE.with_name('worldd')
        request = core.Request(protocol_version=1, request_id=1, world_info=core.WorldInfoRequest())
        seed = subprocess.run([str(binary), '--state-dir', str(state)],
            input=frame(request), capture_output=True, timeout=10)
        self.assertEqual(seed.returncode, 0, seed.stderr)
        size = struct.unpack('!I', seed.stdout[:4])[0]
        info = core.Reply.FromString(seed.stdout[4:4+size])
        self.assertTrue(info.HasField('world_info'), info)
        previous_domain = getattr(self, 'world_exact_domain', None)
        self.world_exact_domain = info.world_info.world_id
        try:
            self.world_domain = info.world_info.world_id
            config = fixture.SessionWorldTests.world_config(self)
            self.assertEqual([domain.id for domain in config.participant.domains], [info.world_info.world_id])
            return config
        finally:
            if previous_domain is None:
                del self.world_exact_domain
            else:
                self.world_exact_domain = previous_domain

    def world_config(self):
        suffix = getattr(self, 'world_instance_suffix', '')
        config = self.seeded_world_config()
        admitted = getattr(self, 'world_admitted', True)
        self.identity_config(config, suffix, admitted)
        if suffix == '-two':
            saved_journal = self.world_journal
            previous_identity = getattr(self, 'world_participant_identity', None)
            self.world_participant_identity = b'd' * 32
            self.world_instance_suffix = '-denied'
            try:
                denied = self.seeded_world_config()
                self.identity_config(denied, '-denied', False)
            finally:
                if previous_identity is None:
                    del self.world_participant_identity
                else:
                    self.world_participant_identity = previous_identity
                self.world_instance_suffix = suffix
                self.world_journal = saved_journal
            path = self.root / 'host-actor-denied.textproto'
            path.write_text(text_format.MessageToString(denied)); path.chmod(0o600)
            request = core.Request(protocol_version=1, request_id=1, world_info=core.WorldInfoRequest())
            seed = subprocess.run([denied.launch.executable, '--state-dir', str(self.root / 'world-denied')],
                input=frame(request), capture_output=True, timeout=10)
            self.assertEqual(seed.returncode, 0, seed.stderr)
            size = struct.unpack('!I', seed.stdout[:4])[0]
            info = core.Reply.FromString(seed.stdout[4:4+size])
            self.assertTrue(info.HasField('world_info'), info)
            os.environ['SPATIAL_HOST_ACTOR_DENIED_CONFIG'] = str(path)
            os.environ['SPATIAL_HOST_ACTOR_DENIED_WORLD'] = info.world_info.world_id
            scope = native.NativeScopeConfig(protocol_version=1, display_number=self.display,
                supervisor_cookie=self.cookie, server_id=self.supervisor.inspect().server_id,
                server_pid=self.server.pid, server_uid=os.getuid(), launch_id=os.urandom(32), role=security.COMPOSITOR)
            fd = os.memfd_create('host-actor-denied-native', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            os.write(fd, scope.SerializeToString())
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            self.addCleanup(os.close, fd)
            self.coordinator_fds = (fd,)
            os.environ['SPATIAL_HOST_ACTOR_EXECUTION'] = str(self.execution)
        return config

    def test_native_a_b_a_denial_and_retained_process_identities(self):
        self.shared_presentation = True
        self.two_presented_worlds = True
        self.coordinator_probe = os.environ.get('SPATIAL_HOST_ACTOR_NATIVE_RUNNER', 'spatial-host-actor-native-runner')
        # Keep paths and credentials local to fixture startup. The child inherits them.
        with patch.dict(os.environ):
            proc, owner, first = self.ready()
        second = self.message(proc.stdout, launch.AuthorityLaunchReply).view.record
        other = self.message(proc.stdout, launch.AuthorityLaunchReply).control_owner
        identities = self.message(proc.stdout, core.Reply).identities
        self.assertNotEqual(identities.worldd_pid, self.world_ids.worldd_pid)
        self.assertEqual(identities.spatialwm_pid, self.world_ids.spatialwm_pid)
        denial = self.message(proc.stdout, host.HostActorTransitionResult)
        self.assertTrue(denial.actor_control_available, denial)
        self.assertEqual(denial.host_selection, host.HOST_SELECTION_NOT_ATTEMPTED)
        self.assertEqual(denial.actor_transition.server_error.code, core.FORBIDDEN)
        ab = self.message(proc.stdout, host.HostActorTransitionResult)
        ba = self.message(proc.stdout, host.HostActorTransitionResult)
        for result in (ab, ba):
            self.assertTrue(result.actor_transition.destination_activation_confirmed, result)
            self.assertEqual(result.host_selection, host.HOST_SELECTION_CONFIRMED)
        self.assertEqual(ab.actor_transition.destination_world_id, ba.actor_transition.source_world_id)
        self.assertEqual(ab.actor_transition.source_world_id, ba.actor_transition.destination_world_id)
        self.assertEqual(ba.operation_generation, 3)
        proc.stdin.write(frame(broker.ControlSupervisorRequest(protocol_version=1, request_id=91, inspect=owner)))
        proc.stdin.flush()
        self.assertEqual(self.message(proc.stdout, broker.ControlSupervisorReply).state.owner, owner)
        self.assertEqual(self.message(proc.stdout, core.Reply).hello.implementation, 'production')
        self.assertEqual(self.message(proc.stdout, launch.AuthorityLaunchReply).view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(self.message(proc.stdout, core.Reply).identities, identities)
        stopped = self.message(proc.stdout, launch.AuthorityLaunchReply).view.record
        self.assertEqual(stopped.native, other.native)
        self.assertEqual(stopped.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(self.message(proc.stdout, core.Reply).hello.implementation, 'production')
        self.assertEqual(self.message(proc.stdout, launch.AuthorityLaunchReply).view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(self.message(proc.stdout, launch.AuthorityLaunchReply).view.record, self.checked_cleanup())
        self.assertEqual(proc.wait(timeout=20), 0, self.logpath.read_text())
        self.assertIn('PASS native-host-controller-held-input-A-B-A', self.logpath.read_text())
        paths = list((self.execution / 'applications').glob('*/data/probe.jsonl'))
        self.assertEqual(len(paths), 2, paths)
        by_name = {}
        for path in paths:
            rows = self.witness(path)
            by_name[max((row.get('seq', 0) for row in rows if row.get('event') == 'ack'), default=0)] = rows
        self.assertEqual(set(by_name), {5, 7})
        source, destination = by_name[7], by_name[5]
        for event in ('key', 'button'):
            self.assert_input(source[:next(i for i, row in enumerate(source) if row.get('seq') == 1 and row.get('event') == 'ack')], event, True)
            self.assert_input(self.between(source, 2, 3), event, False)
        self.assert_input(self.between(source, 1, 2), 'key', True)
        self.assertFalse([row for row in self.between(source, 4, 5) if row.get('event') in ('key', 'button')])
        self.assertFalse([row for row in self.between(destination, 2, 3) if row.get('event') in ('key', 'button')])
        for event in ('key', 'button'):
            self.assert_input(self.between(source, 6, 7), event, True)
            self.assert_input(self.between(source, 6, 7), event, False)
        self.assert_input(self.between(destination, 3, 4), 'key', True)
        self.assert_input(self.between(destination, 3, 4), 'key', False)
        self.assertEqual(sum(row.get('event') == 'key' and row.get('pressed') is True for row in source),
            sum(row.get('event') == 'key' and row.get('pressed') is False for row in source))
        self.assertEqual(sum(row.get('event') == 'button' and row.get('pressed') is True for row in source),
            sum(row.get('event') == 'button' and row.get('pressed') is False for row in source))


if __name__ == '__main__':
    suite = unittest.TestSuite([HostActorControllerNativeTests(
        'test_native_a_b_a_denial_and_retained_process_identities')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
