"""Real coordinator registration binds one supervised world to its actual launch owner."""
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import unittest
from google.protobuf import text_format
import test_supervisor_broker_handoff_regressions as base
import broker_pb2 as broker
import core_pb2 as core
import issuer_pb2 as identity
import launch_pb2 as launch
import native_scope_pb2 as native
import process_scope_pb2 as process
import security_pb2 as security
from harness.wire import frame


class SessionWorldTests(base.BrokerHandoffRegressionTests):
    def world_config(self):
        signer, public = self.issuer.key('world-authority')
        registration = self.issuer.register(identity=b'w' * 32, public=public, domain='world-one', revision=1)
        approved = self.issuer.bind(participant=registration.participant.id, registration=registration.registered_revision)
        self.assertTrue(approved.HasField('supervision'), approved)
        config = self.config()
        self.world_journal = self.root / 'world-journal'; self.world_journal.mkdir(mode=0o700)
        config.journal_directory = str(self.world_journal)
        config.binding.CopyFrom(launch.AuthorityLaunchBinding(issuer_id=self.issuer.info.issuer_id,
            participant_id=registration.participant.id, registered_revision=registration.registered_revision,
            service_incarnation=os.urandom(32), supervised=True))
        runtime = identity.AuthorityParticipantRuntimeConfig(protocol_version=1,
            issuer_client_config=str(self.issuer.config_file()), private_key_path=str(signer),
            participant_id=config.binding.participant_id, registered_revision=config.binding.registered_revision,
            service_incarnation=config.binding.service_incarnation)
        path = self.root / 'world-participant-runtime.textproto'; path.write_text(text_format.MessageToString(runtime)); path.chmod(0o600)
        config.participant.CopyFrom(launch.AuthorityParticipantAttach(runtime_config=str(path), domains=registration.participant.domains))
        config.child_participant_config = str(self.root / 'world-child.textproto')
        config.compositor_native_target_fd = 38
        config.launch.CopyFrom(process.AuthorityProcessLaunch(executable=str(base.baseline.supervisor.owned.existing.PROBE.with_name('worldd')),
            arguments=['--state-dir', str(self.root / 'world'), '--native-security-fd', '38']))
        return config

    def start_coordinator(self, mutation=None, native_role=security.COMPOSITOR):
        self.start()
        broker_config = self.broker_config()
        world_config = self.world_config()
        if mutation: mutation(world_config)
        descriptors = []
        arguments = []
        for name, config, role in [('broker', broker_config, security.BROKER), ('world', world_config, native_role)]:
            path = self.root / ('session-' + name + '.textproto')
            path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
            value = native.NativeScopeConfig(protocol_version=1, display_number=self.display, supervisor_cookie=self.cookie,
                server_id=self.supervisor.inspect().server_id, server_pid=self.server.pid, server_uid=os.getuid(),
                launch_id=os.urandom(32), role=role)
            descriptor = os.memfd_create('session-native-fixture', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            os.write(descriptor, value.SerializeToString())
            fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            descriptors.append(descriptor); arguments += [str(path), str(descriptor)]
        self.logpath = self.root / 'session-world.log'; log = self.logpath.open('wb')
        try:
            proc = subprocess.Popen([str(base.baseline.supervisor.owned.existing.PROBE.with_name('spatial-session-world-probe')), *arguments],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, pass_fds=tuple(descriptors))
        finally:
            for descriptor in descriptors: os.close(descriptor)
        def cleanup():
            if proc.poll() is None:
                proc.stdin.close()
                try: proc.wait(timeout=20)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
            if not proc.stdin.closed: proc.stdin.close()
            proc.stdout.close(); log.close()
            data = self.logpath.read_bytes()
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE']); target.mkdir(parents=True, exist_ok=True)
                (target / (self._testMethodName + '-coordinator.log')).write_bytes(data)
            for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'): self.assertNotIn(marker, data)
        self.addCleanup(cleanup)
        initial = self.message(proc.stdout, launch.AuthorityLaunchReply)
        self.assertEqual(initial.view.record, self.read_record())
        return proc

    def ready(self):
        proc = self.start_coordinator()
        record = self.message(proc.stdout, launch.AuthorityLaunchReply).view.record
        owner = self.message(proc.stdout, launch.AuthorityLaunchReply).control_owner
        self.assertEqual(owner.issuer_id, record.binding.issuer_id)
        self.assertEqual(owner.native, record.native)
        expected = dict(participant_id=record.binding.participant_id,
            registered_revision=record.binding.registered_revision,
            generation=record.authorization.request.previous_generation + 1,
            service_incarnation=record.binding.service_incarnation,
            launch_id=record.process.launch_id,
            supervision_revision=record.authorization.request.supervision_revision,
            authorization_id=record.authorization.authorization_id)
        for name, value in expected.items(): self.assertEqual(getattr(owner.launch, name), value, name)
        self.assertNotEqual(record.process.launch_id, self.read_record().process.launch_id)
        ids = self.message(proc.stdout, core.Reply).identities
        self.assertGreater(ids.worldd_pid, 1); self.assertGreater(ids.spatialwm_pid, 1)
        self.assertNotEqual(ids.worldd_pid, ids.spatialwm_pid)
        self.assertEqual(ids.workspaced_pid, int((self.cgroup / self.read_record().process.scope_name / 'cgroup.procs').read_text().strip()))
        members = set(map(int, (self.cgroup / record.process.scope_name / 'cgroup.procs').read_text().split()))
        self.assertEqual(members, {ids.worldd_pid, ids.spatialwm_pid})
        self.world_ids = ids
        with sqlite3.connect("file:" + str(self.world_journal / "launch.db") + "?mode=ro", uri=True) as db:
            saved = launch.AuthorityLaunchRecord.FromString(db.execute("SELECT record FROM launch WHERE singleton=1").fetchone()[0])
        self.assertEqual(saved, record)
        return proc, owner, record

    def assert_private_channels_stay_in_coordinator(self, proc):
        pids = [proc.pid, self.world_ids.worldd_pid, self.world_ids.spatialwm_pid]
        pins = [os.pidfd_open(pid) for pid in pids]
        try:
            self.assertEqual(select.select(pins, [], [], 0)[0], [])
            script = ("import json, os, sys; "
                      "print(json.dumps({p: [os.readlink('/proc/' + p + '/fd/' + f) "
                      "for f in os.listdir('/proc/' + p + '/fd') "
                      "if os.readlink('/proc/' + p + '/fd/' + f).startswith('socket:')] for p in sys.argv[1:]}))")
            result = subprocess.run(['sudo', '-n', 'python3', '-c', script, *map(str, pids)],
                capture_output=True, text=True, check=True, timeout=5)
            self.assertEqual(select.select(pins, [], [], 0)[0], [])
            sockets = json.loads(result.stdout)
            private = set(sockets[str(proc.pid)])
            self.assertGreaterEqual(len(private), 5)
            for pid in pids[1:]: self.assertFalse(private.intersection(sockets[str(pid)]), sockets)
        finally:
            for pin in pins: os.close(pin)

    def finish(self, proc, owner, query=None):
        request = broker.ControlSupervisorRequest(protocol_version=1, request_id=71, inspect=query or owner)
        proc.stdin.write(frame(request)); proc.stdin.flush()
        response = self.message(proc.stdout, broker.ControlSupervisorReply)
        self.assertEqual(response.request_id, 71)
        hello = self.message(proc.stdout, core.Reply); self.assertEqual(hello.hello.implementation, 'production')
        stopped = self.message(proc.stdout, launch.AuthorityLaunchReply).view.record
        self.assertEqual(stopped.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(stopped.native, owner.native)
        self.assertEqual(stopped.process_stopped.identity, stopped.process)
        self.assertEqual(stopped.native_stopped.identity, stopped.native)
        self.assertFalse((self.cgroup / stopped.process.scope_name).exists())
        survived = self.message(proc.stdout, core.Reply); self.assertEqual(survived.hello.implementation, 'production')
        broker_stopped = self.message(proc.stdout, launch.AuthorityLaunchReply).view.record
        self.assertEqual(broker_stopped, self.checked_cleanup())
        self.assertEqual(proc.wait(timeout=10), 0, self.logpath.read_text())
        return response

    def test_actual_world_owner_registers_and_broker_survives_world_shutdown(self):
        proc, owner, record = self.ready()
        self.assert_private_channels_stay_in_coordinator(proc)
        response = self.finish(proc, owner)
        self.assertTrue(response.HasField('state'), response)
        self.assertEqual(response.state.owner, owner)
        self.assertFalse(response.state.channel_fenced); self.assertEqual(list(response.state.reservations), [])

    def test_caller_cannot_substitute_another_owner_generation(self):
        proc, owner, record = self.ready()
        wrong = broker.ControlOwner(); wrong.CopyFrom(owner); wrong.launch.generation += 1
        response = self.finish(proc, owner, wrong)
        self.assertTrue(response.HasField('error'), response)
        self.assertIn('not a retained session world', response.error.message)

    def test_world_crash_delivers_checked_cleanup_after_supervisor_exit(self):
        proc, owner, initial = self.ready()
        parent = int(next(line.split()[1] for line in Path('/proc/' + str(self.world_ids.worldd_pid) + '/status').read_text().splitlines() if line.startswith('PPid:')))
        self.assertEqual(int(next(line.split()[1] for line in Path('/proc/' + str(parent) + '/status').read_text().splitlines() if line.startswith('PPid:'))), proc.pid)
        pins = [os.pidfd_open(pid) for pid in (self.world_ids.worldd_pid, self.world_ids.spatialwm_pid, parent)]
        try:
            signal.pidfd_send_signal(pins[0], signal.SIGKILL)
            for pin in pins: self.assertEqual(select.select([pin], [], [], 5)[0], [pin])
            with sqlite3.connect('file:' + str(self.world_journal / 'launch.db') + '?mode=ro', uri=True) as db:
                saved = launch.AuthorityLaunchRecord.FromString(db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0])
            self.assertEqual(saved.stage, launch.OWNED_SCOPES_STOPPED)
            request = broker.ControlSupervisorRequest(protocol_version=1, request_id=91, fence=owner)
            proc.stdin.write(frame(request)); proc.stdin.flush()
            state = self.message(proc.stdout, broker.ControlSupervisorReply)
            self.assertTrue(state.state.channel_fenced, state)
            cleanup = self.message(proc.stdout, launch.AuthorityLaunchReply)
            self.assertEqual(cleanup.view.record, saved)
            self.assertEqual(cleanup.view.record.process_stopped.identity, initial.process)
            self.assertEqual(cleanup.view.record.native_stopped.identity, initial.native)
            self.assertEqual(self.message(proc.stdout, core.Reply).hello.implementation, 'production')
            self.assertEqual(self.message(proc.stdout, launch.AuthorityLaunchReply).view.record, self.checked_cleanup())
            self.assertEqual(proc.wait(timeout=10), 0, self.logpath.read_text())
            self.assertIn('Session launch failed after checked cleanup:', self.logpath.read_text())
        finally:
            for pin in pins: os.close(pin)

    def test_supervisor_crash_without_receipt_cannot_claim_cleanup(self):
        proc, owner, initial = self.ready()
        parent = int(next(line.split()[1] for line in Path('/proc/' + str(self.world_ids.worldd_pid) + '/status').read_text().splitlines() if line.startswith('PPid:')))
        pin = os.pidfd_open(parent)
        try:
            signal.pidfd_send_signal(pin, signal.SIGKILL)
            self.assertEqual(select.select([pin], [], [], 5)[0], [pin])
            request = broker.ControlSupervisorRequest(protocol_version=1, request_id=91, fence=owner)
            proc.stdin.write(frame(request)); proc.stdin.flush()
            state = self.message(proc.stdout, broker.ControlSupervisorReply)
            # The coordinator fences this exact endpoint before attempting cleanup delivery.
            self.assertEqual(state.state.owner, owner)
            cleanup = self.message(proc.stdout, launch.AuthorityLaunchReply)
            self.assertTrue(cleanup.HasField('error'), cleanup)
            self.assertFalse(cleanup.error.HasField('cleanup_record'))
            self.assertEqual(self.message(proc.stdout, core.Reply).hello.implementation, 'production')
            self.assertEqual(proc.wait(timeout=15), 0, self.logpath.read_text())
            with sqlite3.connect('file:' + str(self.world_journal / 'launch.db') + '?mode=ro', uri=True) as db:
                saved = launch.AuthorityLaunchRecord.FromString(db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0])
            self.assertNotEqual(saved.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertFalse(saved.HasField('process_stopped')); self.assertFalse(saved.HasField('native_stopped'))
        finally: os.close(pin)

    def rejected_world(self, mutation=None, native_role=security.COMPOSITOR):
        proc = self.start_coordinator(mutation, native_role)
        reply = self.message(proc.stdout, core.Reply); self.assertEqual(reply.hello.implementation, 'production')
        stopped = self.message(proc.stdout, launch.AuthorityLaunchReply)
        self.assertEqual(stopped.view.record, self.checked_cleanup())
        self.assertEqual(proc.wait(timeout=10), 2)
        return self.logpath.read_text()

    def test_world_cannot_supply_its_own_broker_endpoint(self):
        text = self.rejected_world(lambda config: config.launch.arguments.extend(['--workspace-fd', '92']))
        self.assertIn('Session owns world broker endpoint arguments', text)
        self.assertFalse((self.world_journal / 'launch.db').exists())

    def test_broker_native_role_cannot_register_as_world(self):
        text = self.rejected_world(native_role=security.BROKER)
        self.assertIn('Compositor handoff requires a compositor native scope', text)
        self.assertFalse((self.world_journal / 'launch.db').exists())


if __name__ == '__main__':
    suite = unittest.TestSuite(SessionWorldTests(name) for name in SessionWorldTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
