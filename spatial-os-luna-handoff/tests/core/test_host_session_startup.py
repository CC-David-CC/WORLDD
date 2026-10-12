"""Fail-closed startup checks for the production host-session executable."""
import fcntl
import os
import socket
import subprocess
import sys
from pathlib import Path
import unittest
import host_session_pb2 as host_session


class HostSessionStartupTests(unittest.TestCase):
    def run_config(self, content, sealed):
        config = os.memfd_create('host-session-config', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.write(config, content.encode())
        if sealed:
            fcntl.fcntl(config, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        parent, child = socket.socketpair()
        process = subprocess.run([sys.argv[1], str(config), str(child.fileno()), str(os.getpid())],
            pass_fds=(config, child.fileno()), capture_output=True, timeout=15)
        parent.close(); child.close(); os.close(config)
        self.assertNotEqual(process.returncode, 0)
        self.assertNotIn(b'password', process.stderr.lower())
        return process.stderr

    def test_unsealed_configuration_is_rejected_before_startup(self):
        self.run_config('protocol_version: 1\n', False)

    def test_unknown_configuration_field_is_rejected(self):
        self.run_config('protocol_version: 1\nunknown_authority: true\n', True)

    def test_unsupported_configuration_version_is_rejected(self):
        self.run_config('protocol_version: 77\n', True)

    def test_actor_transport_schema_has_no_host_authority_inputs(self):
        actor_fields = {field.name for field in host_session.HostControlRequest.DESCRIPTOR.fields}
        transition_fields = {field.name for field in host_session.HostControlRequest.DESCRIPTOR.fields_by_name['transition'].message_type.fields}
        forbidden = {'control_owner', 'credential_path', 'private_key_path', 'executable_path',
                     'supervisor_command', 'compositor_command', 'authority_mutation'}
        self.assertFalse(forbidden & actor_fields)
        self.assertFalse(forbidden & transition_fields)

    def test_production_only_python_bindings_and_helper_argument_parser(self):
        if len(sys.argv) < 3:
            self.skipTest('pass the BUILD_TESTING=OFF python directory for packaging qualification')
        bindings = Path(sys.argv[2]).resolve()
        self.assertTrue((bindings / 'host_session_pb2.py').is_file())
        self.assertTrue((bindings / 'host_actor_controller_pb2.py').is_file())
        self.assertTrue((bindings / 'actor_transition_pb2.py').is_file())
        environment = dict(os.environ, PYTHONPATH=str(bindings))
        imported = subprocess.run([sys.executable, '-c',
            'import host_session_pb2, host_actor_controller_pb2, actor_transition_pb2'],
            cwd=bindings, env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        helper = Path(__file__).resolve().parents[2] / 'tools' / 'host_session_client.py'
        parsed = subprocess.run([sys.executable, str(helper), '--help'],
            cwd=bindings, env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        self.assertIn('--fd', parsed.stdout)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(HostSessionStartupTests)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
