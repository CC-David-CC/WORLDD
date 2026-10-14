"""Recover the supervised world with its replacement actor connecting from another host."""
import os
from pathlib import Path
import select
import shlex
import socket
import subprocess
import unittest
from google.protobuf import text_format
import test_session_replacement as existing
import launch_pb2 as launch
import world_session_pb2 as session


class WitnessStream:
    def __init__(self, fixture):
        self.log = (fixture.root / 'replacement-remote-actor.log').open('wb')
        command = ['env', 'PYTHONPATH=' + os.environ['SPATIAL_REMOTE_PYTHONPATH'], 'python3',
            os.environ['SPATIAL_REMOTE_WITNESS'], os.environ['SPATIAL_WORLD_ADDRESS'],
            str(fixture.replacement_port), fixture.remote_credentials, '--server-name', 'issuer.test']
        self.proc = subprocess.Popen(['ssh', fixture.remote_host, shlex.join(command)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)

    def sendall(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def recv(self, size):
        if not select.select([self.proc.stdout], [], [], 5)[0]:
            raise TimeoutError('Remote actor witness response timed out')
        return os.read(self.proc.stdout.fileno(), size)

    def close(self):
        if not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            result = self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill(); self.proc.wait(timeout=3)
            raise
        finally:
            self.proc.stdout.close(); self.log.close()
        if result:
            raise AssertionError('Remote actor witness failed: ' + str(result))


class CrossHostRecoveryTests(existing.SessionReplacementTests):
    def world_config(self):
        config = super().world_config()
        self.remote_host = os.environ['SPATIAL_REMOTE_HOST']
        if self.remote_host not in ('sb16', 'sb16-2'):
            raise ValueError('Unauthorized witness host')
        self.replacement_listener = socket.socket()
        self.addCleanup(self.replacement_listener.close)
        self.replacement_listener.bind((os.environ['SPATIAL_WORLD_ADDRESS'], 0))
        self.replacement_listener.listen(8)
        self.replacement_port = self.replacement_listener.getsockname()[1]
        self.coordinator_fds += (self.replacement_listener.fileno(),)
        self.remote_credentials = subprocess.check_output(['ssh', self.remote_host,
            'umask 077; mktemp -d /tmp/spatial-m05-recovery-XXXXXXXX'], text=True, timeout=10).strip()
        if not self.remote_credentials.startswith('/tmp/spatial-m05-recovery-') or '/' in self.remote_credentials[5:]:
            raise ValueError('Invalid remote credential directory')
        self.addCleanup(self.remove_credentials)
        for source, name in ((self.issuer.server_cert, 'server.crt'),
                             (self.issuer.service_cert, 'client.crt'), (self.issuer.service_key, 'client.key')):
            subprocess.run(['scp', '-q', str(source), f'{self.remote_host}:{self.remote_credentials}/{name}'],
                           check=True, timeout=10)
        self.actor_connection_count = 0
        return config

    def remove_credentials(self):
        paths = [self.remote_credentials + '/' + name for name in ('server.crt', 'client.crt', 'client.key')]
        command = shlex.join(['rm', '-f', '--', *paths]) + ' && ' + shlex.join(['rmdir', '--', self.remote_credentials])
        subprocess.run(['ssh', self.remote_host, command], check=True, timeout=10)

    def replacement(self, index=0):
        request = super().replacement(index)
        config = launch.AuthoritySupervisorConfig()
        path = Path(request.configuration_path)
        text_format.Parse(path.read_text(), config)
        tls = session.WorldActorTlsConfig(protocol_version=1, certificate_path=str(self.issuer.server_cert),
            private_key_path=str(self.issuer.server_key), client_ca_path=str(self.issuer.service_cert),
            io_timeout_ms=5000, idle_timeout_ms=5000, connection_limit=16)
        config.launch.arguments.extend(['--actor-tls-config', str(self.config_file('recovery-tls', tls))])
        path.write_text(text_format.MessageToString(config))
        request.actor_listener_fd = self.replacement_listener.fileno()
        return request

    def connect_actor(self):
        local = super().connect_actor()
        self.actor_connection_count += 1
        if self.actor_connection_count == 1:
            return local
        local.close()
        self.replacement_listener.close()
        stream = WitnessStream(self)
        self.addCleanup(stream.close)
        def retain():
            target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
            target.mkdir(parents=True, exist_ok=True)
            (target / (self._testMethodName + '-remote-actor.log')).write_bytes(Path(stream.log.name).read_bytes())
        self.addCleanup(retain)
        return stream

    def test_unknown_listener_descriptor_cannot_start_replacement(self):
        self.held_application()
        request = self.replacement()
        request.actor_listener_fd = 0
        self.retire_old_world()
        before = self.activation()
        rejected = self.request(expect_error=True, start_world=request)
        self.assertIn('Unknown replacement actor listener descriptor', rejected.error.message)
        self.assertEqual(self.activation(), before)
        self.released()
        self.finish_session()


if __name__ == '__main__':
    unittest.main(defaultTest=['CrossHostRecoveryTests.test_crash_replacement_keeps_committed_edit_and_discards_preview',
                              'CrossHostRecoveryTests.test_unknown_listener_descriptor_cannot_start_replacement'],
                  verbosity=2)
