"""Pure fixture checks for gate tooling. No runtime, builds, SSH, or services."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

tool, receipt_input, source_input = map(Path, sys.argv[1:4])
sys.argv[1:4] = []
spec = importlib.util.spec_from_file_location('deployed_gate', tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class GateStaticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Fixture helper files are ordinary temporary files, never installed helpers.
        self.helper_check = patch.object(gate, 'verify_helper_ownership')
        self.helper_check.start()

    def tearDown(self):
        self.helper_check.stop()
        self.tmp.cleanup()

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def runtime_fixture(self):
        source = self.root / 'source'
        receipt = json.loads(receipt_input.read_text())
        for name in [*receipt['extension_sources'], 'server/base.lock.json']:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_input / name, target)
        server = self.put('runtime/Xvfb', 'fixture binary, never executed')
        receipt['binaries']['Xvfb'] = dict(path=str(server), sha256=gate.sha(server))
        library = self.put('runtime/libEGL.so', 'fixture EGL, never loaded')
        vendor = self.put('runtime/vendor.json', json.dumps({'ICD': {'library_path': str(library)}}))
        env = {'__EGL_VENDOR_LIBRARY_FILENAMES': str(vendor)}
        wrapper = self.put('runtime/wrapper', 'import os, sys\nenv = dict(os.environ)\nenv.update('
                           + repr(env) + ')\nserver = ' + repr(str(server))
                           + '\nos.execve(server, [server, *sys.argv[1:]], env)\n')
        helpers = [self.put('runtime/' + name, 'fixture helper')
                   for name in ('spatial-bwrap', 'spatial-storage-owner')]
        receipt_path = self.put('runtime/receipt.json', json.dumps(receipt))
        entry = lambda path: dict(path=str(path), sha256=gate.sha(path))
        row = dict(server_receipt=entry(receipt_path), xace_wrapper=entry(wrapper),
                   egl_files=list(map(entry, [vendor, library])), installed_helpers=list(map(entry, helpers)))
        runtime = dict(candidate='new-project-commit-with-identical-server-source',
                       required_egl_names=[vendor.name, library.name],
                       required_helper_names=[path.name for path in helpers],
                       hosts={'sb16': {'Debug': row}})
        return source, runtime, row

    def test_retains_runtime_config_and_python(self):
        build = self.root / 'build'
        self.put('build/runtime.textproto', 'configuration')
        self.put('build/python/nested/foo_pb2.py', '# generated')
        self.put('build/CTestTestfile.cmake', '# registrations')
        kept = {Path(name).as_posix() for name in gate.retained_files(build)}
        self.assertTrue({'runtime.textproto', 'python/nested/foo_pb2.py', 'CTestTestfile.cmake'} <= kept)

    def test_missing_runtime_config_rejected(self):
        self.put('build/python/a_pb2.py', '# generated')
        with self.assertRaisesRegex(RuntimeError, 'runtime.textproto'):
            gate.retained_files(self.root / 'build')

    def test_actual_receipt_schema_accepts_unchanged_server(self):
        source, runtime, row = self.runtime_fixture()
        self.assertEqual(gate.verify_runtime(runtime, 'sb16', 'Debug', source), row)

    def test_server_extension_tamper_rejected(self):
        source, runtime, _ = self.runtime_fixture()
        receipt = json.loads(receipt_input.read_text())
        next_path = source / next(iter(receipt['extension_sources']))
        next_path.write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'extension differs'):
            gate.verify_runtime(runtime, 'sb16', 'Debug', source)

    def test_wrong_wrapper_server_rejected_even_with_fresh_wrapper_hash(self):
        source, runtime, row = self.runtime_fixture()
        wrapper = Path(row['xace_wrapper']['path'])
        wrapper.write_text(wrapper.read_text().replace('Xvfb', 'old-Xvfb'))
        row['xace_wrapper']['sha256'] = gate.sha(wrapper)
        with self.assertRaisesRegex(RuntimeError, 'wrapper does not execute'):
            gate.verify_runtime(runtime, 'sb16', 'Debug', source)

    def test_full_inventory_relocation_is_single_pass(self):
        inventory = [dict(name='native', command=['/old/source/build/probe',
                     'EVIDENCE=/old/source/local/evidence/case'], properties=[dict(name='TIMEOUT', value=240)])]
        mapping = [('/old/source', '/new/source'), ('/old/source/build', '/new/build'),
                   ('/old/source/local/evidence', '/new/attempt')]
        actual = gate.map_inventory(inventory, mapping)
        self.assertEqual(actual[0]['command'], ['/new/build/probe', 'EVIDENCE=/new/attempt/case'])
        self.assertEqual(actual[0]['properties'], inventory[0]['properties'])
        gate.require_inventory_equal(actual, inventory, mapping)
        changed = copy.deepcopy(actual)
        changed[0]['properties'][0]['value'] = 999
        with self.assertRaisesRegex(RuntimeError, 'commands/properties'):
            gate.require_inventory_equal(changed, inventory, mapping)
        changed = copy.deepcopy(actual)
        changed[0]['command'].append('--skip-checks')
        with self.assertRaisesRegex(RuntimeError, 'commands/properties'):
            gate.require_inventory_equal(changed, inventory, mapping)

    def test_output_paths_exempt_inputs_required(self):
        source = self.root / 'source'
        build = self.root / 'build'
        probe = self.put('build/probe', 'fixture')
        output = source / 'local/future-output'
        inventory = [dict(command=[str(probe), 'EVIDENCE=' + str(output / 'case')])]
        gate.verify_inventory_paths(inventory, source, build, [output])
        probe.unlink()
        with self.assertRaisesRegex(RuntimeError, 'closure missing'):
            gate.verify_inventory_paths(inventory, source, build, [output])

    def test_inner_skip_rejected(self):
        for output in ('OK (skipped=1)\n', '17: OK (skipped=1)\n',
                       '17: test_packaging ... skipped \'missing OFF\'\n'):
            with self.assertRaisesRegex(RuntimeError, 'skipped'):
                gate.reject_inner_skips(output)
        gate.reject_inner_skips('17: Ran 5 tests in 1.0s\n17: OK\n')

    def test_off_startup_environment_excludes_testing_bindings(self):
        environment = dict(PYTHONPATH='/testing/python:/source', ASAN_OPTIONS='detect_leaks=1')
        production, source = Path('/production-off/Debug'), Path('/source')
        selected = gate.stage_environment(environment, 'production-off-startup', production, source)
        self.assertEqual(selected['PYTHONPATH'], str(production / 'python') + ':' + str(source))
        self.assertNotIn('/testing/python', selected['PYTHONPATH'])
        self.assertEqual(selected['ASAN_OPTIONS'], environment['ASAN_OPTIONS'])
        self.assertEqual(environment['PYTHONPATH'], '/testing/python:/source')
        self.assertEqual(gate.stage_environment(environment, 'core', production, source), environment)

    def test_off_runtime_staging_retains_pinned_closure(self):
        source = self.root / 'production-off'
        destination = self.root / 'runtime-production-off'
        self.put('production-off/runtime.textproto', 'fixture config')
        self.put('production-off/python/host_session_pb2.py', '# generated fixture')
        self.put('production-off/spatial-host-session', 'fixture executable')
        hashes = {str(path.relative_to(source)): gate.sha(path) for path in source.rglob('*') if path.is_file()}
        gate.copy_runtime_closure(source, destination, hashes)
        self.assertEqual({str(path.relative_to(destination)): gate.sha(path)
                          for path in destination.rglob('*') if path.is_file()}, hashes)
        (source / 'runtime.textproto').write_text('tampered')
        with self.assertRaisesRegex(RuntimeError, 'changed before packaging'):
            gate.copy_runtime_closure(source, destination, hashes)

    def test_startup_packaging_cli_and_metadata_augmentation(self):
        build = self.root / 'build'
        script = self.root / 'source/tests/core/test_host_session_startup.py'
        executable = build / 'spatial-host-session'
        bindings = self.root / 'production-off/Debug/python'
        inventory = [dict(name='host-session-startup', command=['python3', str(script), str(executable)],
                          properties=[dict(name='TIMEOUT', value=60)])]
        metadata = self.put('build/CTestTestfile.cmake',
            'add_test([=[host-session-startup]=] "python3" "' + str(script) + '" "' + str(executable) + '")\n')
        augmented, edits = gate.add_startup_bindings(build, inventory, bindings)
        self.assertEqual(len(edits), 1)
        self.assertEqual(len(inventory[0]['command']), 3)
        self.assertEqual(augmented[0]['command'], gate.startup_packaging_command(self.root / 'source', executable, bindings))
        self.assertEqual(augmented[0]['properties'], inventory[0]['properties'])
        self.assertIn('[=[' + str(bindings) + ']=]', metadata.read_text())
        gate.require_inventory_equal(augmented, augmented, [])
        with self.assertRaisesRegex(RuntimeError, 'commands/properties'):
            gate.require_inventory_equal(inventory, augmented, [])
        with self.assertRaisesRegex(RuntimeError, 'CLI changed'):
            gate.add_startup_bindings(build, augmented, bindings)

    def test_production_off_closure_requires_transitive_binding(self):
        self.put('build/python/host_session_pb2.py', 'import actor_session_client_pb2 as client\n')
        self.put('source/tools/host_session_client.py', 'import host_session_pb2\n')
        with self.assertRaisesRegex(RuntimeError, 'closure missing'):
            gate.generated_closure(self.root / 'build')
        self.put('build/python/actor_session_client_pb2.py', '# generated')
        gate.generated_closure(self.root / 'build')
        gate.verify_production_client(self.root / 'source', self.root / 'build')


unittest.main(verbosity=2)
