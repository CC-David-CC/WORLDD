"""Host-independent checks for fixture ownership. No project process is launched."""
import ast
import importlib.util
import io
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2]


def fixture_class():
    path = SOURCE/'tests/core/test_workspace_authority.py'
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BrokerProcess')
    namespace = dict(os=os, Path=Path, socket=socket, struct=struct, select=select,
                     subprocess=subprocess, time=time, threading=threading,
                     BROKER=Path('/fixture/workspaced'), WORLD=Path('/fixture/worldd'),
                     ResourceBroker=None)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['BrokerProcess']


class Child:
    def __init__(self):
        self.pid = os.getpid()
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.returncode = None
        self.killed = False
    def poll(self): return self.returncode
    def wait(self, timeout=None):
        self.returncode = 0
        return 0
    def kill(self): self.killed = True; self.returncode = -9
    def terminate(self): self.returncode = -15


class StorageOwnerFixtureToolingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = types.SimpleNamespace(root=self.root, brokers=[])
        self.cls = fixture_class()

    def descriptors(self):
        return set(os.listdir('/proc/self/fd'))

    def test_storage_inputs_cross_only_explicit_allowlist(self):
        path = SOURCE/'tools/with_resource_delegate.py'
        spec = importlib.util.spec_from_file_location('fixture_delegate', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        inputs = dict(SPATIAL_TEST_STORAGE_VOLUME='/volume', SPATIAL_TEST_STORAGE_LAUNCH_CONFIG='/launch.pb',
                      SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES='100663296', HOST_PRIVATE_KEY='secret')
        command = module.build_service_command(unit='fixture', username='fixture', native=True,
            copy_engine_v2=False, working_directory=self.root, environment=inputs, argv=['true'])
        for name, value in inputs.items():
            self.assertEqual('--setenv='+name+'='+value in command, name != 'HOST_PRIVATE_KEY')

    def test_normal_constructor_ignores_storage_environment(self):
        child = Child()
        with patch.dict(os.environ, {'SPATIAL_TEST_STORAGE_VOLUME':'/wrong-volume',
                                    'SPATIAL_TEST_STORAGE_LAUNCH_CONFIG':'/wrong-config'}, clear=True), \
             patch.object(subprocess, 'Popen', return_value=child) as launch:
            broker = self.cls(self.fixture, self.root/'execution')
            self.assertNotIn('--storage-fd', launch.call_args.args[0])
            self.assertIsNone(broker.storage_control)
            self.assertEqual(broker.storage_cleanup_path.suffix, '.log')
            broker.close()
        self.assertEqual(len(self.fixture.brokers), 1)

    def test_partial_validation_closes_descriptors(self):
        before = self.descriptors()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'ResourceBroker template'):
                self.cls(self.fixture, self.root, storage_options=(self.root, '/launch.pb'))
        self.assertEqual(self.descriptors(), before)
        self.assertEqual(self.fixture.brokers, [])

    def test_resource_constructor_failure_closes_socket_and_gate_inputs(self):
        before = self.descriptors()
        namespace = self.cls.__init__.__globals__
        def refused(*args, **kwargs): raise OSError('resource constructor refused')
        with patch.dict(os.environ, {'SPATIAL_TEST_BROKER_CONFIG':'/template'}, clear=True), \
             patch.dict(namespace, ResourceBroker=refused):
            with self.assertRaisesRegex(OSError, 'resource constructor refused'):
                self.cls(self.fixture, self.root, storage_options=(self.root, '/launch.pb'))
        self.assertEqual(self.descriptors(), before)

    def test_owner_start_failure_never_releases_gate(self):
        retained = {}
        class Resources:
            def __init__(self, *args, startup_gate_fd, **kwargs):
                self.process = Child(); self.pid = self.process.pid
                retained['gate'] = os.dup(startup_gate_fd)
                retained['resources'] = self
                self.closed = False
            def close(self): self.closed = True
        namespace = self.cls.__init__.__globals__
        with patch.dict(os.environ, {'SPATIAL_TEST_BROKER_CONFIG':'/template'}, clear=True), \
             patch.dict(namespace, ResourceBroker=Resources), \
             patch.object(self.cls, 'start_storage_owner', side_effect=EOFError('startup refused')):
            with self.assertRaisesRegex(EOFError, 'startup refused'):
                self.cls(self.fixture, self.root, storage_options=(self.root, '/launch.pb'))
        try: self.assertEqual(os.read(retained['gate'], 1), b'')
        finally: os.close(retained['gate'])
        self.assertTrue(retained['resources'].closed)
        self.assertTrue(retained['resources'].process.killed)
        self.assertEqual(self.fixture.brokers, [])

    def test_owner_launch_failure_closes_pidfd_and_channel(self):
        owner = self.cls.__new__(self.cls)
        owner.pid = os.getpid(); owner.log_path = self.root/'broker.log'
        endpoint, peer = socket.socketpair(); self.addCleanup(peer.close)
        before = self.descriptors()
        with patch.object(subprocess, 'Popen', side_effect=OSError('launch refused')):
            with self.assertRaisesRegex(OSError, 'launch refused'):
                owner.start_storage_owner('/probe', '/config', endpoint)
        self.assertEqual(endpoint.fileno(), -1)
        # start_storage_owner transfers its log to constructor cleanup.
        owner.storage_owner_log.close()
        self.assertEqual(len(self.descriptors()), len(before)-1)

    def test_failed_owner_stop_closes_all_handles_and_records_unit(self):
        owner = self.cls.__new__(self.cls)
        child = Child(); owner.storage_owner = child
        owner.storage_unit = 'spatial-storage-owner-exact.service'
        owner.storage_cleanup_confirmed = None
        owner.storage_cleanup_path = self.root/'cleanup.txt'
        owner.storage_control, peer = socket.socketpair(); self.addCleanup(peer.close)
        owner.storage_owner_log = (self.root/'owner.log').open('wb')
        owner.storage_owner_log.write(b'Storage owner unit: exact-unit-from-diagnostics.service\n')
        def refused(*args): raise EOFError('stop reply missing')
        owner.storage_exchange = refused
        with self.assertRaisesRegex(EOFError, 'stop reply missing') as caught: owner.stop_storage_owner()
        self.assertTrue(any(owner.storage_unit in note for note in caught.exception.__notes__))
        self.assertTrue(child.stdin.closed and child.stdout.closed)
        self.assertTrue(owner.storage_owner_log.closed)
        self.assertEqual(owner.storage_control.fileno(), -1)
        self.assertIsNone(owner.storage_owner)
        self.assertIn(owner.storage_unit, owner.storage_cleanup_path.read_text())
        self.assertIn('exact-unit-from-diagnostics.service', owner.storage_cleanup_path.read_text())
        self.assertIn('checked_cleanup_confirmed=None', owner.storage_cleanup_path.read_text())

    def test_gate_eof_refuses_exec(self):
        tree = ast.parse((SOURCE/'harness/resource_broker.py').read_text())
        assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and
                          any(isinstance(t, ast.Name) and t.id == 'gate' for t in n.targets))
        read_end, write_end = os.pipe(); os.close(write_end)
        namespace = dict(gate_descriptor=read_end)
        exec(compile(ast.Module(body=[assignment], type_ignores=[]), '<gate>', 'exec'), namespace)
        try:
            with self.assertRaises(AssertionError): exec(namespace['gate'], dict(os=os))
        finally: os.close(read_end)


if __name__ == '__main__': unittest.main(verbosity=2)