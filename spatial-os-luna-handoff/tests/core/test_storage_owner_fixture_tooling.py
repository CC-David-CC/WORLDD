"""Host-independent checks for fixture ownership. No project process is launched."""
import ast
import importlib.util
import io
import os
from pathlib import Path
import select
import socket
import broker_pb2 as broker
import storage_pb2 as storage
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
    lifecycle = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and
                     n.name == 'initialize_broker_lifecycle')
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BrokerProcess')
    namespace = dict(os=os, Path=Path, socket=socket, struct=struct, select=select,
                     subprocess=subprocess, time=time, threading=threading,
                     stat=__import__('stat'), broker=broker,
                     BROKER=Path('/fixture/workspaced'), WORLD=Path('/fixture/worldd'),
                     ResourceBroker=None)
    # Execute the fixture's real lifecycle initializer with its synthetic class.
    exec(compile(ast.Module(body=[lifecycle, node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['BrokerProcess']


def resource_environment_function():
    path = SOURCE/'harness/resource_broker.py'
    tree = ast.parse(path.read_text())
    allowlist = next(node for node in tree.body if isinstance(node, ast.Assign) and
                     any(isinstance(target, ast.Name) and target.id == '_RESOURCE_ENVIRONMENT'
                         for target in node.targets))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and
                    node.name == 'resource_environment')
    namespace = {}
    exec(compile(ast.Module(body=[allowlist, function], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['resource_environment']


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


def provisioned_resource_function():
    path = SOURCE/'tools/with_adapter_resources.py'
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and
                n.name == 'provisioned_storage_resources')
    namespace = dict(Path=Path, RuntimeError=RuntimeError, stat=__import__('stat'),
                     manager_memory_mib=lambda profile: {'Debug':256,'ASan-UBSan':512}[profile])
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['provisioned_storage_resources']


class StorageOwnerFixtureToolingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = types.SimpleNamespace(root=self.root, brokers=[])
        self.cls = fixture_class()

    def storage_runtime(self):
        volume = self.root/'volume'
        volume.mkdir(exist_ok=True)
        runtime_path = self.root/'runtime.pb'
        owner_config = self.root/'owner.pb'
        owner_config.write_bytes(b'private policy')
        owner = storage.StorageOwnerLaunchConfig(protocol_version=1,
            owner_configuration=str(owner_config), volume_root=str(volume))
        owner.budget.memory_bytes = 16*os.sysconf('SC_PAGE_SIZE')
        owner.budget.swap_bytes = 0
        owner.budget.processes = 2
        owner.budget.cpu_microseconds = 1000
        owner.budget.io.add(major=7, minor=14,
            read_bytes_per_second=64*1024*1024, write_bytes_per_second=16*1024*1024)
        runtime = broker.StorageVolumeRuntime(protocol_version=1, volume_root=str(volume),
            require_confinement=True)
        runtime.storage_owner.CopyFrom(owner)
        runtime_path.write_bytes(runtime.SerializeToString())
        original_lstat = Path.lstat
        def protected_lstat(path, *args, **kwargs):
            if path == runtime_path:
                return types.SimpleNamespace(st_uid=0, st_mode=0o100644)
            if path == owner_config:
                return types.SimpleNamespace(st_uid=0, st_nlink=1, st_mode=0o100600)
            if path in owner_config.parents:
                return types.SimpleNamespace(st_uid=0, st_mode=0o40700)
            return original_lstat(path, *args, **kwargs)
        return runtime_path, volume, runtime, protected_lstat

    def descriptors(self):
        return set(os.listdir('/proc/self/fd'))

    def test_runtime_wrapper_extracts_unchanged_typed_owner_configuration(self):
        runtime_path, volume, runtime, lstat = self.storage_runtime()
        owner = self.cls.__new__(self.cls)
        with patch.object(Path, 'lstat', lstat):
            payload = owner.storage_owner_launch_payload(runtime_path, volume)
        self.assertEqual(payload, runtime.storage_owner.SerializeToString())
        self.assertEqual(storage.StorageOwnerLaunchConfig.FromString(payload), runtime.storage_owner)
        self.assertEqual(runtime.storage_owner.volume_root, str(volume))
        self.assertEqual(runtime.storage_owner.budget.io[0].major, 7)
        self.assertEqual(runtime.storage_owner.budget.io[0].minor, 14)
        owner.log_path = self.root/'broker-0.log'
        owner.storage_owner_launch_path = None
        with patch.object(Path, 'lstat', lstat):
            private = owner.private_storage_owner_launch_file(runtime_path, volume)
        self.assertEqual(private.read_bytes(), payload)
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        owner.pid = os.getpid()
        evidence = self.root/'security-evidence'; evidence.mkdir(mode=0o700)
        with patch.dict(os.environ, {'SPATIAL_SECURITY_EVIDENCE':str(evidence)}):
            retained = Path(owner.retain_storage_owner_launch_file())
        self.assertEqual(retained.read_bytes(), payload)
        self.assertEqual(retained.stat().st_mode & 0o777, 0o600)
        self.assertTrue(private.exists(), 'Private config remains available until probe cleanup')
        private.unlink()

    def test_wrong_typed_runtime_fails_before_probe_launch(self):
        runtime_path, volume, runtime, lstat = self.storage_runtime()
        runtime_path.write_bytes(storage.StorageOwnerLaunchConfig(protocol_version=1).SerializeToString())
        owner = self.cls.__new__(self.cls)
        owner.log_path = self.root/'broker-0.log'
        owner.pid = os.getpid()
        owner.storage_owner_launch_path = None
        endpoint, peer = socket.socketpair(); self.addCleanup(peer.close)
        with patch.object(Path, 'lstat', lstat), patch.object(subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'contain StorageVolumeRuntime'):
                owner.start_storage_owner('/probe', runtime_path, volume, endpoint)
        launch.assert_not_called()
        self.assertEqual(endpoint.fileno(), -1)

    def test_runtime_wrapper_rejects_wrong_type_and_invalid_owner_contract(self):
        runtime_path, volume, runtime, lstat = self.storage_runtime()
        owner = self.cls.__new__(self.cls)
        with patch.object(Path, 'lstat', lstat):
            with self.assertRaisesRegex(RuntimeError, 'contain StorageVolumeRuntime'):
                owner.storage_owner_launch_payload(self.root/'missing.pb', volume)
        runtime_path.write_bytes(storage.StorageOwnerLaunchConfig(protocol_version=1).SerializeToString())
        with patch.object(Path, 'lstat', lstat):
            with self.assertRaisesRegex(RuntimeError, 'contain StorageVolumeRuntime'):
                owner.storage_owner_launch_payload(runtime_path, volume)
        runtime_path.write_bytes(runtime.SerializeToString())
        cases = (
            ('runtime-version', lambda runtime: setattr(runtime, 'protocol_version', 2), 'runtime protocol version'),
            ('runtime-root', lambda runtime: setattr(runtime, 'volume_root', str(self.root/'other')), 'runtime volume root'),
            ('confinement', lambda runtime: setattr(runtime, 'require_confinement', False), 'require confinement'),
            ('owner-missing', lambda runtime: runtime.ClearField('storage_owner'), 'no StorageOwner launch config'),
            ('owner-version', lambda runtime: setattr(runtime.storage_owner, 'protocol_version', 2), 'launch protocol version'),
            ('owner-root', lambda runtime: setattr(runtime.storage_owner, 'volume_root', str(self.root/'other')), 'StorageOwner volume root'),
            ('owner-path', lambda runtime: setattr(runtime.storage_owner, 'owner_configuration', str(self.root/'owner policy.pb')), 'canonical absolute path'),
            ('budget-missing', lambda runtime: runtime.storage_owner.ClearField('budget'), 'budget is missing'),
        )
        for label, mutate, message in cases:
            runtime_path, volume, runtime, lstat = self.storage_runtime()
            mutate(runtime)
            runtime_path.write_bytes(runtime.SerializeToString())
            with self.subTest(label=label), patch.object(Path, 'lstat', lstat):
                with self.assertRaisesRegex(RuntimeError, message):
                    owner.storage_owner_launch_payload(runtime_path, volume)

    def test_resource_environment_forwards_copy_deadline_and_keeps_allowlist(self):
        source = {'SPATIAL_TEST_COPY_DEADLINE_MS': '50', 'UNRELATED_SECRET': 'must-not-pass'}
        self.assertEqual(resource_environment_function()(source),
                         {'SPATIAL_TEST_COPY_DEADLINE_MS': '50'})

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

    def test_explicit_storage_uses_exact_runtime_devices_and_rejects_mismatch(self):
        function = provisioned_resource_function()
        volume = self.root/'volumes'/'focus'/'mount'
        volume.mkdir(parents=True)
        record = volume.parent/'runtime.pb'
        record.write_bytes(b'fixture')

        def message(**fields):
            value = types.SimpleNamespace(**fields)
            value.HasField = lambda name: hasattr(value, name) and getattr(value, name) is not None
            return value

        def budget(devices=((7, 14), (259, 0))):
            return message(memory_bytes=256*1024*1024,
                io=[types.SimpleNamespace(major=major, minor=minor) for major, minor in devices])

        def make_runtime():
            resources = message(protocol_version=2, broker_budget=budget(), manager_budget=budget(),
                default_policy=message(total=budget(), actor_default=budget(),
                    profiles=[message(maximum=budget())]))
            owner = message(budget=budget())
            return message(protocol_version=1, volume_root=str(volume), require_confinement=True,
                resources=resources, storage_owner=owner)

        runtime = make_runtime()
        module = types.SimpleNamespace(StorageVolumeRuntime=types.SimpleNamespace(
            FromString=lambda data: runtime))
        original_lstat = Path.lstat
        metadata = types.SimpleNamespace(st_uid=0, st_mode=0o100644)
        def lstat(path, *args, **kwargs):
            result = original_lstat(path, *args, **kwargs)
            return metadata if path == record else result

        with patch.object(Path, 'lstat', lstat):
            actual, device = function(volume, 'Debug', module)
        self.assertIs(actual, runtime.resources)
        self.assertEqual(device, '7:14')

        for uid, mode in ((1000, 0o100644), (0, 0o100664), (0, 0o120777)):
            runtime = make_runtime()
            module.StorageVolumeRuntime.FromString = lambda data, runtime=runtime: runtime
            metadata.st_uid, metadata.st_mode = uid, mode
            with self.subTest(uid=uid, mode=oct(mode)), patch.object(Path, 'lstat', lstat):
                with self.assertRaisesRegex(RuntimeError, 'root-owned protected regular file'):
                    function(volume, 'Debug', module)

        metadata.st_uid, metadata.st_mode = 0, 0o100644
        runtime = make_runtime()
        runtime.HasField = lambda name: name != 'resources'
        module.StorageVolumeRuntime.FromString = lambda data: runtime
        with patch.object(Path, 'lstat', lstat), self.assertRaisesRegex(RuntimeError, 'no provisioned resource budgets'):
            function(volume, 'Debug', module)

        runtime = make_runtime()
        runtime.resources.HasField = lambda name: name != 'manager_budget'
        module.StorageVolumeRuntime.FromString = lambda data: runtime
        with patch.object(Path, 'lstat', lstat), self.assertRaisesRegex(RuntimeError, 'incomplete resource budgets'):
            function(volume, 'Debug', module)

        runtime = make_runtime()
        runtime.resources.manager_budget.io = []
        module.StorageVolumeRuntime.FromString = lambda data: runtime
        with patch.object(Path, 'lstat', lstat), self.assertRaisesRegex(RuntimeError, 'device set'):
            function(volume, 'Debug', module)

        runtime = make_runtime()
        runtime.resources.default_policy.total.io = [types.SimpleNamespace(major=7, minor=99)]
        module.StorageVolumeRuntime.FromString = lambda data: runtime
        with patch.object(Path, 'lstat', lstat), self.assertRaisesRegex(RuntimeError, 'device set'):
            function(volume, 'Debug', module)

        runtime = make_runtime()
        module.StorageVolumeRuntime.FromString = lambda data: runtime
        with patch.object(Path, 'lstat', lstat), self.assertRaisesRegex(RuntimeError, 'does not match the selected resource profile'):
            function(volume, 'ASan-UBSan', module)

    def test_ordinary_adapter_fallback_keeps_host_budget_and_device(self):
        tree = ast.parse((SOURCE/'tools/with_adapter_resources.py').read_text())
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and
                      isinstance(node.test, ast.Name) and node.test.id == 'storage_volume')
        fallback = ast.unparse(branch.orelse)
        self.assertIn('broker.BrokerResourceConfig(protocol_version=2,', fallback)
        self.assertIn('budget(1024, 128, 400000)', fallback)
        self.assertIn('budget(manager_memory_mib(resource_profile), 32, 200000)', fallback)
        self.assertIn("io_device = f'{major}:{minor}'", fallback)

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
                with patch.object(self.cls, 'private_storage_owner_launch_file', return_value=Path('/config')):
                    owner.start_storage_owner('/probe', '/config', self.root, endpoint)
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