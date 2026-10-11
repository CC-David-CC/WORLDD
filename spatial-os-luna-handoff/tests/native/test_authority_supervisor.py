"""The production supervisor launches and pumps a real participant child."""
import unittest
import fcntl
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import time
from google.protobuf import text_format
from harness.wire import frame
import issuer_pb2 as identity
import grants_pb2 as grants
import launch_pb2 as pb
import native_scope_pb2 as native_pb
import process_scope_pb2 as process_pb
import security_pb2 as security
import test_authority_participant_relay as relay
import test_authority_owned_participant as owned

class AuthoritySupervisorTests(owned.OwnedParticipantTests):
    revoke = relay.ParticipantRelayTests.revoke
    barrier = relay.ParticipantRelayTests.barrier

    def healthy_client_config(self):
        path = self.issuer.config_file()
        config = text_format.Parse(path.read_text(), identity.IssuerTlsClientConfig())
        config.ClearField('io_timeout_ms')
        path.write_text(text_format.MessageToString(config))
        return path

    def config(self):
        self.child_config = self.root / 'supervised-child.textproto'
        runtime_path = self.runtime_config()
        runtime = text_format.Parse(runtime_path.read_text(), identity.AuthorityParticipantRuntimeConfig())
        runtime.issuer_client_config = str(self.healthy_client_config())
        runtime_path.write_text(text_format.MessageToString(runtime))
        domains = []
        for domain in self.issuer.registration.participant.domains:
            domains += ['world' if domain.kind == grants.WORLD else 'workspace', domain.id]
        return pb.AuthoritySupervisorConfig(protocol_version=1,
            journal_directory=str(self.journal), delegated_cgroup=str(self.cgroup), binding=self.binding,
            participant=pb.AuthorityParticipantAttach(runtime_config=str(runtime_path),
                domains=self.issuer.registration.participant.domains),
            authorization=pb.AuthorityLaunchAuthorize(issuer_client_config=str(self.healthy_client_config()),
                private_key_path=str(self.issuer.supervisor_key), supervision_revision=1, previous_generation=0),
            launch=process_pb.AuthorityProcessLaunch(executable=str(owned.existing.PROBE.with_name('spatial-participant-runtime-probe')),
                arguments=[str(self.child_config), *domains]),
            child_participant_config=str(self.child_config), participant_target_fd=37)

    def supervisor_process(self, config=None, start_server=True, invalid_mapping=False, handle_fd=None, supervisor_binary=None):
        if start_server:
            self.start()
        if config is None:
            config = self.config()
        native = native_pb.NativeScopeConfig(protocol_version=1, display_number=self.display,
            supervisor_cookie=self.cookie, server_id=self.supervisor.inspect().server_id,
            server_pid=self.server.pid, server_uid=os.getuid(), launch_id=os.urandom(32), role=security.BROKER)
        fd = os.memfd_create('supervisor-runtime-test', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.write(fd, native.SerializeToString())
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        parent, child = socket.socketpair()
        alias = os.dup(fd) if invalid_mapping == 'alias' else os.dup(handle_fd) if invalid_mapping == 'handle-alias' else None
        if invalid_mapping:
            config.inherited_source_fds.append(fd if alias is None else alias)
            config.launch.inherited_fds.append(38)
        path = self.root / ('supervisor-' + str(time.monotonic_ns()))
        path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
        logpath = path.with_suffix('.log'); log = logpath.open('wb')
        try:
            proc = subprocess.Popen([str(supervisor_binary or owned.existing.PROBE.with_name('spatial-authority-supervisor')),
                str(path), str(fd), str(child.fileno())] + ([] if handle_fd is None else [str(handle_fd)]),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=log, pass_fds=(fd, child.fileno()) + (() if alias is None else (alias,)) +
                (() if handle_fd is None else (handle_fd,)))
        finally:
            os.close(fd); child.close()
            if alias is not None:
                os.close(alias)
        def cleanup():
            parent.close()
            if proc.poll() is None:
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=3)
            proc.stdin.close(); proc.stdout.close(); log.close()
            text = logpath.read_text(errors='replace')
            target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                (Path(target) / (self._testMethodName + '-' + logpath.name)).write_bytes(logpath.read_bytes())
            for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
                self.assertNotIn(marker, text)
        self.addCleanup(cleanup)
        self.child_input = proc.stdin
        return proc, parent

    def message(self, stream, message_type):
        deadline = time.monotonic() + 15
        def exact(size):
            data = b''
            while len(data) < size:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, 'Supervisor reply deadline')
                self.assertTrue(select.select([stream], [], [], remaining)[0], 'Supervisor reply deadline')
                chunk = os.read(stream.fileno(), size-len(data))
                self.assertTrue(chunk, 'Supervisor or child closed reply channel')
                data += chunk
            return data
        size = struct.unpack('!I', exact(4))[0]
        self.assertLessEqual(size, 1048576)
        reply = message_type.FromString(exact(size))
        self.assertEqual(reply.protocol_version, 1)
        return reply

    def control(self, channel, **operation):
        channel.sendall(frame(pb.AuthorityLaunchRequest(protocol_version=1, request_id=77, **operation)))
        reply = self.message(channel, pb.AuthorityLaunchReply)
        self.assertEqual(reply.request_id, 77)
        return reply

    def command(self, request_id=1, **operation):
        self.child_input.write(frame(identity.ParticipantCommand(request_id=request_id, **operation)))
        self.child_input.flush()

    def child_update(self, proc, field):
        reply = self.message(proc.stdout, identity.ParticipantUpdate)
        self.assertTrue(reply.HasField(field), reply)
        return reply

    def running(self, config=None):
        proc, channel = self.supervisor_process(config)
        initial = self.message(channel, pb.AuthorityLaunchReply)
        self.assertTrue(initial.HasField('view'), initial)
        self.assertEqual(initial.request_id, 0)
        self.assertEqual(initial.view.record.stage, pb.START_ATTEMPTED)
        self.assertEqual(initial.view.record, self.read_record())
        self.auth = self.child_update(proc, 'authenticated').authenticated
        batch = self.child_update(proc, 'revocations').revocations
        self.assertTrue(batch.initial)
        pids = (self.cgroup / initial.view.record.process.scope_name / 'cgroup.procs').read_text().split()
        self.assertEqual(len(pids), 1); self.authority_pid = int(pids[0])
        status = Path(f'/proc/{self.authority_pid}/status').read_text()
        self.assertEqual(int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:'))), proc.pid)
        return proc, channel

    def activate_child(self, proc):
        self.command(1, activate=identity.AuthorityActivate(synchronized_revision=0))
        result = self.child_update(proc, 'activated')
        self.assertEqual(result.request_id, 1)
        return result.activated

    def checked_cleanup(self):
        record = self.read_record()
        self.assertEqual(record.stage, pb.OWNED_SCOPES_STOPPED)
        self.assertEqual(record.process_stopped.identity, record.process)
        self.assertEqual(record.native_stopped.identity, record.native)
        self.assertFalse((self.cgroup / record.process.scope_name).exists())
        return record

    def test_production_supervisor_is_available_for_owned_authority_execution(self):
        self.assertTrue(owned.existing.PROBE.with_name('spatial-authority-supervisor').is_file(),
            'Missing production authority supervisor; a manually ticked test probe does not provide its event loop')

    def test_real_child_activates_releases_and_exits_without_manual_pumping(self):
        proc, channel = self.running()
        state = self.activate_child(proc)
        self.assertEqual(state.connection_binding, self.auth.connection_binding)
        self.assertEqual(self.read_record().authorization.request.service, self.auth)
        child = text_format.Parse(self.child_config.read_text(), identity.AuthorityParticipantRuntimeConfig())
        self.assertEqual(child.supervisor_channel_fd, 37)
        self.assertEqual(child.service_incarnation, self.binding.service_incarnation)
        self.assertEqual(child.private_key_path, ''); self.assertEqual(child.issuer_client_config, '')
        self.assertEqual(self.child_config.stat().st_mode & 0o777, 0o600)
        self.command(2, release=identity.AuthorityRelease(generation=state.generation, applied_revision=0))
        self.assertEqual(self.child_update(proc, 'released').request_id, 2)
        final = self.message(channel, pb.AuthorityLaunchReply)
        self.assertEqual(final.view.record.stage, pb.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.assertEqual(self.checked_cleanup(), final.view.record)

    def test_revocation_requires_child_acknowledgement_with_automatic_delivery(self):
        proc, channel = self.running(); state = self.activate_child(proc)
        self.revoke(); self.assertEqual(self.child_update(proc, 'revocations').revocations.through_revision, 1)
        self.assertEqual(self.barrier().pending_count, 1)
        inspected = self.control(channel, inspect=pb.AuthorityLaunchQuery())
        self.assertEqual(inspected.view.record.stage, pb.START_ATTEMPTED)
        self.assertEqual(self.barrier().pending_count, 1)
        self.command(2, applied=identity.AuthorityApplied(generation=state.generation, from_revision=0, through_revision=1))
        self.assertEqual(self.child_update(proc, 'applied').applied.applied_revision, 1)
        self.assertEqual(self.barrier().pending_count, 0)
        self.command(3, release=identity.AuthorityRelease(generation=state.generation, applied_revision=1))
        self.child_update(proc, 'released'); self.message(channel, pb.AuthorityLaunchReply)
        self.assertEqual(proc.wait(timeout=5), 0); self.checked_cleanup()

    def test_management_cannot_inject_participant_commands_or_repeat_start(self):
        proc, channel = self.running()
        for operation in (
            dict(participant_command=identity.ParticipantCommand(activate=identity.AuthorityActivate(synchronized_revision=0))),
            dict(start=process_pb.AuthorityProcessLaunch(executable='/usr/bin/true')),
            dict(pump_channel=pb.AuthorityLaunchQuery()),
        ):
            self.assertTrue(self.control(channel, **operation).HasField('error'))
        self.assertEqual(self.activation_rows(), [])
        self.assertEqual(self.control(channel, inspect=pb.AuthorityLaunchQuery()).view.record.stage, pb.START_ATTEMPTED)
        self.assertEqual(self.control(channel, stop=pb.AuthorityLaunchQuery()).view.record.stage, pb.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0); self.checked_cleanup()

    def test_explicit_stop_preserves_unresolved_issuer_generation(self):
        proc, channel = self.running(); self.activate_child(proc)
        self.revoke(); self.child_update(proc, 'revocations')
        before = self.activation_rows(); self.assertEqual(self.barrier().pending_count, 1)
        self.assertEqual(self.control(channel, stop=pb.AuthorityLaunchQuery()).view.record.stage, pb.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0); self.checked_cleanup()
        self.assertEqual(self.activation_rows(), before); self.assertEqual(self.barrier().pending_count, 1)

    def test_management_loss_stops_owned_child_and_records_cleanup(self):
        proc, channel = self.running(); self.activate_child(proc)
        before = self.activation_rows(); channel.close()
        self.assertEqual(proc.wait(timeout=8), 2); self.checked_cleanup()
        self.assertEqual(self.activation_rows(), before)

    def test_child_death_is_failure_with_checked_cleanup(self):
        proc, channel = self.running(); self.activate_child(proc)
        before = self.activation_rows(); os.kill(self.authority_pid, signal.SIGKILL)
        self.assertTrue(self.message(channel, pb.AuthorityLaunchReply).HasField('error'))
        self.assertEqual(proc.wait(timeout=5), 2); self.checked_cleanup()
        self.assertEqual(self.activation_rows(), before)

    def test_nonzero_exit_after_release_is_not_successful_completion(self):
        # Independent byte relay. The real issuer still validates every command.
        config = self.config()
        code = ('import os,select,socket\ns=socket.socket(fileno=37)\n'
                'while True:\n'
                ' ready,_,_=select.select([0,s],[],[],3)\n'
                ' for source in ready:\n'
                '  data=os.read(0,65536) if source==0 else s.recv(65536)\n'
                '  if not data:raise SystemExit(44)\n'
                '  if source==0:s.sendall(data)\n'
                '  else:os.write(1,data)\n')
        config.launch.CopyFrom(process_pb.AuthorityProcessLaunch(executable='/usr/bin/python3', arguments=['-c', code]))
        proc, channel = self.running(config); state = self.activate_child(proc)
        self.command(2, release=identity.AuthorityRelease(generation=state.generation, applied_revision=0))
        self.child_update(proc, 'released')
        error = self.message(channel, pb.AuthorityLaunchReply)
        self.assertTrue(error.HasField('error')); self.assertIn('failed after release', error.error.message)
        self.assertEqual(proc.wait(timeout=5), 2); self.checked_cleanup()

    def test_excessive_management_frame_stops_owned_execution(self):
        proc, channel = self.running()
        channel.sendall(struct.pack('!I', 1048577))
        self.assertTrue(self.message(channel, pb.AuthorityLaunchReply).HasField('error'))
        self.assertEqual(proc.wait(timeout=5), 2); self.checked_cleanup()

    def test_termination_signal_stops_owned_child_and_records_cleanup(self):
        proc, channel = self.running(); proc.send_signal(signal.SIGTERM)
        self.assertTrue(self.message(channel, pb.AuthorityLaunchReply).HasField('error'))
        self.assertEqual(proc.wait(timeout=5), 2); self.checked_cleanup()

    def test_existing_child_configuration_is_preserved_and_prevents_execution(self):
        config = self.config(); self.child_config.write_text('preserve this file\n')
        proc, channel = self.supervisor_process(config)
        reply = self.message(channel, pb.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error')); self.assertIn('already exists', reply.error.message)
        self.assertEqual(proc.wait(timeout=5), 2); self.checked_cleanup()
        self.assertEqual(self.child_config.read_text(), 'preserve this file\n')
        self.assertEqual(self.activation_rows(), [])

    def test_privileged_descriptor_mapping_is_rejected_before_ownership(self):
        proc, _ = self.supervisor_process(invalid_mapping=True)
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse(self.child_config.exists()); self.assertEqual(self.activation_rows(), [])

    def test_privileged_descriptor_alias_is_rejected_before_ownership(self):
        proc, _ = self.supervisor_process(invalid_mapping='alias')
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse(self.child_config.exists()); self.assertEqual(self.activation_rows(), [])

    def test_reopened_journal_cannot_replay_execution(self):
        proc, channel = self.running()
        self.control(channel, stop=pb.AuthorityLaunchQuery()); self.assertEqual(proc.wait(timeout=5), 0)
        before = self.checked_cleanup(); child_before = self.child_config.read_bytes()
        other, channel2 = self.supervisor_process(start_server=False)
        error = self.message(channel2, pb.AuthorityLaunchReply)
        self.assertTrue(error.HasField('error')); self.assertIn('Launch already exists', error.error.message)
        self.assertEqual(other.wait(timeout=5), 2); self.assertEqual(self.read_record(), before)
        self.assertEqual(self.child_config.read_bytes(), child_before); self.assertEqual(self.activation_rows(), [])

if __name__=='__main__':
    suite=unittest.TestSuite(AuthoritySupervisorTests(n) for n in AuthoritySupervisorTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
