"""Pure fixture checks for gate tooling. No runtime, builds, SSH, or services."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
from types import SimpleNamespace
import unittest
import zlib
from unittest.mock import patch

tool, receipt_input, source_input = map(Path, sys.argv[1:4])
sys.argv[1:4] = []
spec = importlib.util.spec_from_file_location('deployed_gate', tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
m07_spec = importlib.util.spec_from_file_location('m07_deployed_gate', tool.with_name('m07_deployed_gate.py'))
m07 = importlib.util.module_from_spec(m07_spec)
m07_spec.loader.exec_module(m07)


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
        expected_host = receipt.get('host')
        if expected_host not in ('sb16', 'sb16-2'):
            raise RuntimeError('runtime fixture receipt must name an actual qualification host')
        for name in [*receipt['extension_sources'], 'server/base.lock.json']:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            # Fixture sources may be frozen read-only. Copy bytes into fresh writable temp files.
            shutil.copyfile(source_input / name, target)
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
                       hosts={expected_host: {'Debug': row}})
        return source, runtime, row, expected_host

    def test_runtime_fixture_copies_are_writable_and_leave_frozen_sources_unchanged(self):
        receipt = json.loads(receipt_input.read_text())
        names = [*receipt['extension_sources'], 'server/base.lock.json']
        before = {name: ((source_input / name).read_bytes(), (source_input / name).stat().st_mode)
                  for name in names}
        first_source, _, _, _ = self.runtime_fixture()
        for name in names:
            fixture_copy = first_source / name
            self.assertEqual(fixture_copy.read_bytes(), before[name][0])
            self.assertTrue(fixture_copy.stat().st_mode & 0o200, name)
            self.assertEqual((source_input / name).read_bytes(), before[name][0])
            self.assertEqual((source_input / name).stat().st_mode, before[name][1])
        patch_name = next(name for name in receipt['extension_sources']
                          if name.endswith('0001-spatial-input.patch'))
        fixture_patch = first_source / patch_name
        fixture_patch.write_bytes(before[patch_name][0] + b'fixture mutation')
        self.assertEqual((source_input / patch_name).read_bytes(), before[patch_name][0])
        second_source, _, _, _ = self.runtime_fixture()
        self.assertEqual((second_source / patch_name).read_bytes(), before[patch_name][0])
        self.assertTrue((second_source / patch_name).stat().st_mode & 0o200)
        for name in names:
            self.assertEqual((source_input / name).read_bytes(), before[name][0])
            self.assertEqual((source_input / name).stat().st_mode, before[name][1])

    def make_tar(self, path, members):
        with tarfile.open(path, 'w:gz') as archive:
            for name, payload in members:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(payload))
        return path

    def test_sequential_archive_hash_and_extraction_positive(self):
        bundle = self.make_tar(self.root / 'good.tar.gz', [
            ('source/a.txt', b'alpha\n'), ('build/fixture.bin', b'\x00\x01test')])
        expected = gate.stream_archive_hashes(bundle)
        self.assertEqual(set(expected), {'source/a.txt', 'build/fixture.bin'})
        out = self.root / 'out'
        out.mkdir()
        extracted = gate.extract_verified_bundle(bundle, out, expected)
        self.assertEqual(extracted, expected)
        self.assertEqual((out / 'source/a.txt').read_bytes(), b'alpha\n')
        self.assertEqual((out / 'build/fixture.bin').read_bytes(), b'\x00\x01test')

    def test_sequential_archive_rejects_tamper_duplicate_truncation_and_manifest_drift(self):
        original = self.make_tar(self.root / 'original.tar.gz', [('source/a.txt', b'original')])
        original_manifest = gate.stream_archive_hashes(original)
        tampered = self.make_tar(self.root / 'tampered.tar.gz', [('source/a.txt', b'tampered')])
        with self.assertRaisesRegex(RuntimeError, 'manifest mismatch'):
            gate.extract_verified_bundle(tampered, self._empty_dir('tampered-out'), original_manifest)

        duplicate = self.make_tar(self.root / 'duplicate.tar.gz', [
            ('source/a.txt', b'one'), ('source/a.txt', b'two')])
        with self.assertRaisesRegex(RuntimeError, 'duplicate archive file'):
            gate.stream_archive_hashes(duplicate)
        with self.assertRaisesRegex(RuntimeError, 'duplicate archive file'):
            gate.extract_verified_bundle(duplicate, self._empty_dir('duplicate-out'), {})

        truncated = self.root / 'truncated.tar.gz'
        truncated.write_bytes(original.read_bytes()[:-8])
        with self.assertRaises((EOFError, OSError, tarfile.TarError, RuntimeError, zlib.error)):
            gate.stream_archive_hashes(truncated)
        truncated_out = self._empty_dir('truncated-extract-out')
        with self.assertRaises((EOFError, OSError, tarfile.TarError, RuntimeError, zlib.error)):
            gate.extract_verified_bundle(truncated, truncated_out, original_manifest)
        self.assertEqual(list(truncated_out.iterdir()), [])

        for expected in ({}, dict(original_manifest, **{'unexpected.txt': '0' * 64})):
            with self.assertRaisesRegex(RuntimeError, 'manifest mismatch'):
                gate.extract_verified_bundle(original, self._empty_dir('manifest-out-' + str(len(expected))), expected)

        unsafe_cases = [
            ('/absolute.txt', tarfile.REGTYPE),
            ('../escape.txt', tarfile.REGTYPE),
            ('source/link', tarfile.SYMTYPE),
            ('source/fifo', tarfile.FIFOTYPE),
        ]
        for index, (name, kind) in enumerate(unsafe_cases):
            unsafe = self.root / ('unsafe-' + str(index) + '.tar.gz')
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = 'target' if kind == tarfile.SYMTYPE else ''
            info.size = 0
            with tarfile.open(unsafe, 'w:gz') as archive:
                archive.addfile(info, io.BytesIO(b''))
            out = self._empty_dir('unsafe-out-' + str(index))
            with self.assertRaisesRegex(RuntimeError, 'unsafe bundle member'):
                gate.extract_verified_bundle(unsafe, out, {})
            self.assertEqual(list(out.iterdir()), [])

        alias = self.make_tar(self.root / 'normalized-alias.tar.gz', [
            ('source/a.txt', b'one'), ('source/./a.txt', b'two')])
        out = self._empty_dir('normalized-alias-out')
        with self.assertRaises((RuntimeError, FileExistsError)):
            gate.extract_verified_bundle(alias, out, {})
        self.assertEqual(list(out.iterdir()), [])

    def test_m07_inventory_distinguishes_missing_and_disabled_required_suites(self):
        required = set(gate.REQUIRED) | set(m07.M07_REQUIRED)
        inventory = [
            {"name": name, "properties": []}
            for name in sorted(required - {"workspace-copy-engine", "workspace-copy-journal"})
        ]
        inventory.append({"name": "workspace-copy-engine", "properties": [
            {"name": "DISABLED", "value": "TRUE"}]})
        result = m07.audit_inventory(inventory)
        self.assertEqual(result["missing"], ["workspace-copy-journal"])
        self.assertEqual(result["disabled"], ["workspace-copy-engine"])
        self.assertEqual(result["registrations"]["workspace-copy-journal"], "missing")
        self.assertEqual(result["registrations"]["workspace-copy-engine"], "disabled")
        self.assertTrue(set(gate.REQUIRED).issubset(set(result["required"])))

    def test_m07_adds_requirements_without_replacing_m06_baseline(self):
        required = gate.required_test_names(m07.M07_REQUIRED)
        self.assertTrue(set(gate.REQUIRED).issubset(required))
        self.assertTrue(set(m07.M07_REQUIRED).issubset(required))

    def test_m07_requires_both_crosshost_registrations_and_keeps_m06_default(self):
        self.assertEqual(gate.DEDICATED_CROSSHOST, {"geometry-native-crosshost"})
        self.assertEqual(gate.ctest_exclusion_expression(), "^geometry-native-crosshost$")
        self.assertEqual(gate.ctest_exclusion_expression(m07.M07_DEDICATED),
                         "^(geometry-native-crosshost|workspace-geometry-native-crosshost)$")
        with self.assertRaisesRegex(RuntimeError, "invalid dedicated"):
            gate.ctest_exclusion_expression({"workspace-geometry-native-crosshost"})

    def test_m07_build_path_map_requires_four_distinct_external_profile_paths(self):
        mapping = {"build": {}, "production-off": {}}
        for group in mapping:
            for profile in gate.PROFILES:
                mapping[group][profile] = str(self.root / group / profile)
        config = self.root / "build-paths.json"
        config.write_text(json.dumps(mapping))
        resolved = gate._validated_build_paths(config)
        self.assertEqual(set(resolved), {"build", "production-off"})
        self.assertEqual(set(resolved["build"]), set(gate.PROFILES))
        mapping["production-off"].pop("ASan-UBSan")
        config.write_text(json.dumps(mapping))
        with self.assertRaisesRegex(RuntimeError, "exactly"):
            gate._validated_build_paths(config)

    def test_m07_cleanup_receipt_parser_accepts_fixture_shape_and_rejects_missing_receipt(self):
        method = self.root / "method"
        observer = method / "authority-native-observer"
        observer.mkdir(parents=True)
        unit = "spatial-m03-resources-m06-geometry-test.service"
        adapter = {"scope_removed": True, "unexpected_population": False, "exit_code": 0}
        remote_unit = {"unit": unit, "unit_after": "LoadState=not-found\nActiveState=inactive",
                       "query_exit": 4, "cgroup_exists": False}
        local_unit = {"unit": unit, "unit_after": "LoadState=not-found\nActiveState=inactive",
                      "query_exit": 4, "cgroup_exists": False}
        (method / "peer-resource-terminal.txt").write_text(
            json.dumps(adapter) + "\n" + json.dumps(remote_unit) + "\n")
        (method / "exact-unit-terminal.json").write_text(json.dumps(
            {"unit": unit, "state": local_unit["unit_after"], "query_exit": 4, "cgroup_absent": True}))
        (observer / "cleanup-receipts.json").write_text(json.dumps([
            {"returncode": 0, "stdout": json.dumps(adapter) + "\n" + json.dumps(local_unit) + "\n",
             "stderr": ""}
        ]))
        result = m07._verify_cleanup_receipts(method, method)
        self.assertTrue(result["adapter_scope_removed"])
        (method / "peer-resource-terminal.txt").unlink()
        with self.assertRaisesRegex(RuntimeError, "cleanup evidence is missing"):
            m07._verify_cleanup_receipts(method, method)

    def test_m07_local_log_scan_rejects_sanitizer_in_observer_stderr(self):
        evidence = self.root / "logs"
        observer = evidence / "authority-native-observer"
        observer.mkdir(parents=True)
        (evidence / "peer-native.stderr").write_text("clean\\n")
        (observer / "observer-raw.stdout").write_text("clean\\n")
        (evidence / "geometry-host-session.log").write_text("")
        (observer / "observer.stderr").write_text("clean\\n")
        records = m07._scan_local_logs((evidence,))
        self.assertTrue(any(row["bytes"] == 0 and row["path"].endswith("geometry-host-session.log")
                            for row in records))
        (observer / "observer.stderr").write_text("AddressSanitizer: heap-use-after-free\\n")
        with self.assertRaisesRegex(RuntimeError, "sanitizer diagnostic"):
            m07._scan_local_logs((evidence,))

    def test_m07_workspace_log_scan_uses_actual_observer_records(self):
        evidence = self.root / "workspace-producer-logs"
        observer = evidence / "authority-native-observer"
        observer.mkdir(parents=True)
        (evidence / "peer-native.stderr").write_text("clean\n")
        for name in ("observer.stderr", "observer-raw.stdout", "geometry-host-session.log"):
            (observer / name).write_text("clean\n")
        records = m07._scan_local_logs((evidence,))
        self.assertEqual({Path(row["path"]).name for row in records},
                         {"peer-native.stderr", "observer.stderr", "observer-raw.stdout",
                          "geometry-host-session.log"})
        self.assertFalse((evidence / "boundary.stdout").exists())

    def test_m07_workspace_log_scan_rejects_missing_observer_output(self):
        evidence = self.root / "workspace-missing-observer-output"
        observer = evidence / "authority-native-observer"
        observer.mkdir(parents=True)
        for name in ("peer-native.stderr", "boundary.stdout", "boundary.stderr"):
            (evidence / name).write_text("clean\n")
        for name in ("observer.stderr", "geometry-host-session.log"):
            (observer / name).write_text("clean\n")
        with self.assertRaisesRegex(RuntimeError, "required native/service logs"):
            m07._scan_local_logs((evidence,))

    def test_m07_configure_map_requires_all_geometry_placeholders(self):
        values = {key: "/external/" + key.lower() for key in m07.GEOMETRY_ENV}
        config = self.root / "configure-env.json"
        config.write_text(json.dumps(values))
        self.assertEqual(m07._validate_configure_map(config), values)
        values.pop(m07.GEOMETRY_ENV[-1])
        config.write_text(json.dumps(values))
        with self.assertRaisesRegex(RuntimeError, "all eight"):
            m07._validate_configure_map(config)

    def _empty_dir(self, name):
        path = self.root / name
        path.mkdir()
        return path

    def test_retains_runtime_config_and_python(self):
        build = self.root / 'build'
        self.put('build/runtime.textproto', 'configuration')
        self.put('build/python/nested/foo_pb2.py', '# generated')
        cache = build / 'python/__pycache__/foo.cpython-312.pyc'; cache.parent.mkdir(parents=True); cache.write_bytes(b'generated cache')
        legacy = build / 'python/__pycache__/foo.pyo'; legacy.write_bytes(b'legacy cache')
        self.put('build/CTestTestfile.cmake', '# registrations')
        kept = {Path(name).as_posix() for name in gate.retained_files(build)}
        self.assertTrue({'runtime.textproto', 'python/nested/foo_pb2.py', 'CTestTestfile.cmake'} <= kept)
        self.assertFalse(any(name.endswith(('.pyc', '.pyo')) for name in kept))

    def test_missing_runtime_config_rejected(self):
        self.put('build/python/a_pb2.py', '# generated')
        with self.assertRaisesRegex(RuntimeError, 'runtime.textproto'):
            gate.retained_files(self.root / 'build')

    def test_actual_receipt_schema_accepts_unchanged_server(self):
        source, runtime, row, expected_host = self.runtime_fixture()
        self.assertEqual(gate.verify_runtime(runtime, expected_host, 'Debug', source), row)

    def test_server_extension_tamper_rejected(self):
        source, runtime, _, expected_host = self.runtime_fixture()
        receipt = json.loads(receipt_input.read_text())
        next_path = source / next(iter(receipt['extension_sources']))
        next_path.write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'extension differs'):
            gate.verify_runtime(runtime, expected_host, 'Debug', source)

    def test_wrong_wrapper_server_rejected_even_with_fresh_wrapper_hash(self):
        source, runtime, row, expected_host = self.runtime_fixture()
        wrapper = Path(row['xace_wrapper']['path'])
        wrapper.write_text(wrapper.read_text().replace('Xvfb', 'old-Xvfb'))
        row['xace_wrapper']['sha256'] = gate.sha(wrapper)
        with self.assertRaisesRegex(RuntimeError, 'wrapper does not execute'):
            gate.verify_runtime(runtime, expected_host, 'Debug', source)

    def test_runtime_receipt_rejects_the_other_qualification_host(self):
        source, runtime, row, expected_host = self.runtime_fixture()
        wrong_host = 'sb16-2' if expected_host == 'sb16' else 'sb16'
        wrong_runtime = dict(runtime, hosts={wrong_host: {'Debug': row}})
        with self.assertRaisesRegex(RuntimeError, 'server receipt host mismatch'):
            gate.verify_runtime(wrong_runtime, wrong_host, 'Debug', source)

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

    def test_storage_evidence_directories_are_fresh_owned_and_confined(self):
        source = self.root / 'source'
        source.mkdir()
        evidence_root = self.root / 'artifacts' / 'm06-gate-storage'
        evidence_root.parent.mkdir(mode=0o700)
        with patch.object(gate, 'STORAGE_EVIDENCE_ROOT', evidence_root):
            attempt = gate.create_storage_evidence_attempt(source, 'sb16', 'attempt-01')
            debug = gate.create_storage_evidence_profile(attempt['root'], source, 'Debug')
            asan = gate.create_storage_evidence_profile(attempt['root'], source, 'ASan-UBSan')
            self.assertNotIn(source.resolve(), Path(debug['path']).resolve().parents)
            self.assertNotIn(source.resolve(), Path(asan['path']).resolve().parents)
            self.assertEqual(debug['mode'], 0o700)
            self.assertEqual(asan['mode'], 0o700)
            self.assertEqual(len(gate.verify_storage_evidence_path(source, Path(debug['path']))), 3)
            self.assertEqual(set(debug['registrations']), set(gate.STORAGE_LOCAL_EVIDENCE_TESTS))
            self.assertEqual(len({row['path'] for row in debug['registrations'].values()}), 2)
            for row in debug['registrations'].values():
                path = Path(row['path'])
                self.assertEqual(path.name, 'spatial_security_evidence')
                self.assertEqual(row['mode'], 0o700)
                self.assertEqual(len(gate.verify_storage_evidence_path(source, path)), 5)
            with self.assertRaises(FileExistsError):
                gate.create_storage_evidence_attempt(source, 'sb16', 'attempt-01')
            with self.assertRaisesRegex(RuntimeError, 'approved test host'):
                gate.create_storage_evidence_attempt(source, 'a5500', 'attempt-02')
            with self.assertRaisesRegex(RuntimeError, 'identifier is invalid'):
                gate.create_storage_evidence_attempt(source, 'sb16', '../escape')

            outside = self.root / 'outside'
            outside.mkdir()
            (evidence_root / 'sb16').rename(evidence_root / 'sb16.saved')
            (evidence_root / 'sb16').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'real directory'):
                gate.create_storage_evidence_attempt(source, 'sb16', 'attempt-03')
        self.assertFalse((source / 'local').exists())

    def test_storage_inventory_augmentation_changes_only_named_tests(self):
        def row(name):
            return dict(name=name, command=['/build/' + name],
                        properties=[dict(name='ENVIRONMENT', value=[
                            'SPATIAL_SECURITY_EVIDENCE=/attempt/native', 'ASAN_OPTIONS=detect_leaks=1'])])
        inventory = [row('session-storage-owner'), row('session-storage-contention'), row('session-application')]
        before = copy.deepcopy(inventory)
        changed = gate.relocate_storage_evidence_inventory(
            inventory, Path('/attempt/native'),
            {name: Path('/source/local/m06-gate-storage/attempt/Debug') / name
             for name in gate.STORAGE_LOCAL_EVIDENCE_TESTS})
        self.assertEqual(inventory, before)
        owner_target = '/source/local/m06-gate-storage/attempt/Debug/session-storage-owner'
        contention_target = '/source/local/m06-gate-storage/attempt/Debug/session-storage-contention'
        self.assertEqual(changed[0]['properties'][0]['value'][0],
                         'SPATIAL_SECURITY_EVIDENCE=' + owner_target)
        self.assertEqual(changed[1]['properties'][0]['value'][0],
                         'SPATIAL_SECURITY_EVIDENCE=' + contention_target)
        self.assertNotEqual(changed[1]['properties'][0]['value'][0], changed[0]['properties'][0]['value'][0])
        self.assertEqual(changed[2], inventory[2])
        with self.assertRaisesRegex(RuntimeError, 'source differs'):
            gate.relocate_storage_evidence_inventory(
                inventory, Path('/wrong/native'),
                {name: Path('/source/local/elsewhere') / name for name in gate.STORAGE_LOCAL_EVIDENCE_TESTS})
        with self.assertRaisesRegex(RuntimeError, 'missing or duplicated'):
            gate.relocate_storage_evidence_inventory(
                inventory[1:], Path('/attempt/native'),
                {name: Path('/source/local/elsewhere') / name for name in gate.STORAGE_LOCAL_EVIDENCE_TESTS})

    def test_gate_storage_augmentation_uses_prepared_security_path_shape(self):
        prepared_root = Path('/prepared/ctest-evidence/Debug')
        ctest_root = Path('/deployed/Debug/ctest')
        security_value = 'SPATIAL_SECURITY_EVIDENCE=' + str(
            prepared_root / gate.EVIDENCE_KEYS[0].lower())

        def row(name):
            return dict(name=name, command=['/prepared/build/' + name],
                        properties=[dict(name='ENVIRONMENT', value=[
                            security_value, 'ASAN_OPTIONS=detect_leaks=1'])])

        inventory = [row('session-storage-owner'), row('session-storage-contention'),
                     row('session-application')]
        mapping = [(str(prepared_root), str(ctest_root))]
        prepared = gate.map_inventory(inventory, mapping)
        source = gate.storage_evidence_source(prepared, ctest_root)
        self.assertEqual(source, ctest_root / 'spatial_security_evidence')
        targets = {name: Path('/deployed/source/local/attempt/Debug') / name
                   for name in gate.STORAGE_LOCAL_EVIDENCE_TESTS}
        expected = gate.relocate_storage_evidence_inventory(prepared, source, targets)
        self.assertEqual(expected[0]['properties'][0]['value'][0],
                         'SPATIAL_SECURITY_EVIDENCE=' + str(targets['session-storage-owner']))
        self.assertEqual(expected[1]['properties'][0]['value'][0],
                         'SPATIAL_SECURITY_EVIDENCE=' + str(targets['session-storage-contention']))
        self.assertEqual(expected[2], prepared[2])

        build = self.root / 'prepared-build'
        suffix = gate.EVIDENCE_KEYS[0].lower()
        rows = []
        for name in ('session-storage-owner', 'session-storage-contention', 'session-application'):
            rows.append('set_tests_properties([=[' + name + ']=] PROPERTIES ENVIRONMENT "'
                        + 'SPATIAL_SECURITY_EVIDENCE=' + str(prepared_root / suffix) + '")' + chr(10))
        self.put('prepared-build/CTestTestfile.cmake', ''.join(rows))
        gate.replace_metadata(build, mapping)
        gate.relocate_storage_evidence_metadata(build, source, targets)
        text = (build / 'CTestTestfile.cmake').read_text()
        self.assertIn('SPATIAL_SECURITY_EVIDENCE=' + str(targets['session-storage-owner']), text)
        self.assertIn('SPATIAL_SECURITY_EVIDENCE=' + str(targets['session-storage-contention']), text)
        peer_line = next(line for line in text.splitlines() if 'session-application' in line)
        self.assertIn('SPATIAL_SECURITY_EVIDENCE=' + str(ctest_root / suffix), peer_line)
        with self.assertRaisesRegex(RuntimeError, 'path differs from declared CTest root'):
            bad = copy.deepcopy(prepared)
            bad[1]['properties'][0]['value'][0] = 'SPATIAL_SECURITY_EVIDENCE=' + str(ctest_root / 'wrong')
            gate.storage_evidence_source(bad, ctest_root)
        with self.assertRaisesRegex(RuntimeError, 'missing or duplicated'):
            gate.storage_evidence_source(prepared[:1], ctest_root)

    def _write_storage_case(self, root):
        root.mkdir(parents=True)
        volume, image = root / 'volume', root / 'volume.img'
        volume.mkdir()
        image.touch()
        commands = [
            dict(argv=['umount', str(volume)], exit_code=0, stdout='', stderr=''),
            dict(argv=['losetup', '-d', '/dev/loop17'], exit_code=0, stdout='', stderr=''),
            dict(argv=['losetup', '-j', str(image)], exit_code=0, stdout='', stderr=''),
        ]
        receipt = dict(status='prepared', mounted=False, loop='/dev/loop17',
            policy_directory='/run/spatial-m03-native-storage-' + 'a' * 32, commands=commands,
            configuration=str(root / 'broker.textproto'), volume=str(volume.resolve()),
            devices=[[8, 1], [7, 17]], loop_attached=False, cleanup_confirmed=True)
        (root / 'volume-setup.json').write_text(json.dumps(receipt))
        (root / 'prepare.log').write_text('storage setup completed\\n')
        return root

    def _bounded_artifact_fixture(self, name='artifact-target'):
        target = self.root / name
        root = target / 'bounded-storage-git'
        root.mkdir(parents=True)
        volume = root / 'volume'
        volume.mkdir(mode=0o700)
        image = root / 'volume.img'
        with image.open('wb') as stream:
            stream.truncate(2 * 2**30)
        image.chmod(0o600)
        ordinary = target / 'ordinary.log'
        ordinary.write_text('ordinary evidence\n')
        results = {name: {'passed': True} for name in (
            'large_import_retirement', 'application_and_actor_quota', 'read_only',
            'sibling_survival', 'restart')}
        review = {
            'status': 'passed', 'mounted': False, 'results': results,
            'unit': 'spatial-m03-storage-integration-' + 'a' * 32 + '.service',
            'scope': '/run/systemd/transient/spatial-m03-storage-integration-' + 'a' * 32 + '.scope',
            'owner_unit_cleanup': {
                'spatial-m03-storage-owner-a.service': 'ActiveState=inactive',
                'spatial-m03-storage-owner-b.service': 'ActiveState=inactive'},
            'service_stop': {'exit_code': 0}, 'service_after': 'ActiveState=inactive',
            'unmount': {'exit_code': 0}, 'detach': {'exit_code': 0}}
        (root / 'review.json').write_text(json.dumps(review))
        return target, root, volume, image, ordinary, review

    def _retirement_marker_fixture(self, target, *, identifier='a' * 32, link_target='never-created'):
        parent = target / 'acceptance' / 'state' / 'execution' / 'retired' / identifier
        (parent / 'data' / 'project').mkdir(parents=True)
        (parent / 'git').mkdir()
        (parent / 'complete').write_text('complete\n')
        (parent / 'staging').symlink_to(parent / link_target)
        return parent / 'staging'

    def _allow_root_owned_fixture_probes(self, *, loop_output='', find_output='', mounted=False):
        patcher = patch.object(gate, 'STORAGE_FIXTURE_UID', os.getuid())
        patcher.start()
        self.addCleanup(patcher.stop)
        def run(argv, **kwargs):
            if len(argv) == 5 and argv[:4] == ['sudo', '-n', 'losetup', '-j']:
                return subprocess.CompletedProcess(argv, 0, loop_output, '')
            if (len(argv) == 10 and argv[:3] == ['sudo', '-n', 'find']
                    and argv[4:] == ['-mindepth', '1', '-maxdepth', '1', '-print', '-quit']):
                return subprocess.CompletedProcess(argv, 0, find_output, '')
            raise AssertionError('unexpected cleanup probe: ' + repr(argv))
        patcher = patch.object(gate.subprocess, 'run', side_effect=run)
        patcher.start()
        self.addCleanup(patcher.stop)
        mount_patcher = patch.object(gate.os.path, 'ismount', side_effect=lambda path: mounted)
        mount_patcher.start()
        self.addCleanup(mount_patcher.stop)

    def _protobuf_proof_fixture(self, provenance_path):
        provenance = {
            "candidate": "c" * 40, "source_root": "/source",
            "source_sha256": {"src/a.py": "a" * 64},
            "profiles": {}, "production_off": {},
        }
        py_outputs = {"a_pb2.py": "1" * 64, "b_pb2.py": "2" * 64}
        round_outputs = {"python/" + name: value for name, value in py_outputs.items()}
        round_outputs.update({"src/a.pb.cc": "3" * 64, "src/a.pb.h": "4" * 64})
        off_outputs = {"a_pb2.py": "1" * 64}
        provenance["profiles"]["Debug"] = {"generated_python_sha256": {"python/" + k: v for k, v in py_outputs.items()}}
        provenance["profiles"]["ASan-UBSan"] = {"generated_python_sha256": {"python/" + k: v for k, v in py_outputs.items()}}
        provenance["production_off"]["Debug"] = {"runtime_files": {"python/" + k: v for k, v in off_outputs.items()}}
        gate.write_json(provenance_path, provenance)
        digest = "b" * 64
        canonical = lambda value: __import__("hashlib").sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        contract = {"schema": 1, "candidate": provenance["candidate"],
                    "provenance_path": str(provenance_path.resolve()),
                    "provenance_sha256": gate.sha(provenance_path), "source_root": "/source",
                    "build_dir": "/build/Debug", "production_off_python_path": "/production-off/Debug/python",
                    "artifact_parent": str(provenance_path.parent.resolve()), "source_file_count": 1,
                    "recipe_count": 67, "candidate_python_output_count": len(py_outputs),
                    "production_off_python_output_count": len(off_outputs), "cpp_output_count": 2,
                    "round_output_count": len(round_outputs),
                    "toolchain": {name: {"version":"test", "sha256":"6"*64, "closure_sha256":"7"*64}
                                  for name in ("protoc", "cmake", "ninja")}}
        contract_path = provenance_path.parent / "protobuf-reproducibility-contract.json"
        contract_bytes = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        contract_path.write_bytes(contract_bytes)
        comparison = {
            "candidate_python_path": "/build/Debug/python", "candidate_python_output_count": len(py_outputs),
            "candidate_python_manifest_sha256": canonical(py_outputs), "candidate_python_equal": True,
            "production_off_python_path": "/production-off/Debug/python", "production_off_python_output_count": len(off_outputs),
            "production_off_python_manifest_sha256": canonical(off_outputs), "production_off_subset_equal": True,
            "cpp_build_path": "/build/Debug", "cpp_output_count": 2,
            "cpp_manifest_sha256": canonical({k:v for k,v in round_outputs.items() if k.endswith((".pb.cc", ".pb.h"))}),
            "cpp_equal": True}
        return provenance, {
            "schema": 1, "status": "PROTOBUF_REPRODUCIBILITY_PASSED",
            "qualification": "UNQUALIFIED_M08", "qualification_effect": "NONE",
            "candidate": provenance["candidate"], "provenance_sha256": gate.sha(provenance_path),
            "source_manifest_sha256": canonical(provenance["source_sha256"]),
            "recipes_sha256": digest, "inputs_sha256": digest,
            "verifier_sha256": "5" * 64, "contract_schema": 1,
            "contract": contract, "contract_sha256": __import__("hashlib").sha256(contract_bytes).hexdigest(),
            "contract_manifest_sha256": canonical(contract),
            "generator": {"protoc_path": "/usr/bin/protoc", "protoc_version": "test",
                "protoc_sha256": digest, "closure_sha256": digest, "cmake_sha256": digest, "ninja_sha256": digest},
            "round_a": {"output_count": len(round_outputs), "manifest_sha256": canonical(round_outputs)},
            "round_b": {"output_count": len(round_outputs), "manifest_sha256": canonical(round_outputs)},
            "rounds_equal": True, "round_a_outputs": round_outputs, "round_b_outputs": copy.deepcopy(round_outputs),
            "candidate_output_comparison": comparison}

    def test_protobuf_reproducibility_proof_is_bound_and_unqualified(self):
        provenance_path = self.root / 'provenance.json'
        provenance, proof = self._protobuf_proof_fixture(provenance_path)
        proof_path = self.root / gate.PROTOBUF_PROOF_NAME
        gate.write_json(proof_path, proof)
        pin = gate.validate_protobuf_reproducibility(proof, provenance, provenance_path, proof_path)
        self.assertEqual(pin['sha256'], gate.sha(proof_path))
        self.assertIsNone(gate.require_deployed_protobuf_proof(self.root, provenance))
        provenance['protobuf_reproducibility_policy'] = gate.PROTOBUF_REPRODUCIBILITY_POLICY
        proof_path.unlink()
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            gate.require_deployed_protobuf_proof(self.root, provenance)
        gate.write_json(self.root / 'provenance.json', provenance)
        proof['provenance_sha256'] = gate.sha(self.root / 'provenance.json')
        contract_path = self.root / 'protobuf-reproducibility-contract.json'
        contract = proof['contract']; contract['provenance_sha256'] = proof['provenance_sha256']
        contract_bytes = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        contract_path.write_bytes(contract_bytes); proof['contract_sha256'] = __import__("hashlib").sha256(contract_bytes).hexdigest()
        proof['contract_manifest_sha256'] = __import__("hashlib").sha256(json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        gate.write_json(proof_path, proof)
        receipt = {'protobuf_reproducibility': {'path': str(proof_path.resolve()), 'sha256': gate.sha(proof_path)}}
        self.assertEqual(gate.require_deployed_protobuf_proof(self.root, provenance, receipt), receipt['protobuf_reproducibility'])
        for key, value in [('candidate', 'd' * 40), ('rounds_equal', False)]:
            changed = copy.deepcopy(proof); changed[key] = value
            with self.assertRaises(RuntimeError): gate.validate_protobuf_reproducibility(changed, provenance, provenance_path, proof_path)
        changed = copy.deepcopy(proof); changed['candidate_output_comparison']['candidate_python_equal'] = False
        with self.assertRaisesRegex(RuntimeError, 'differ'):
            gate.validate_protobuf_reproducibility(changed, provenance, provenance_path, proof_path)
        changed = copy.deepcopy(proof); changed['round_a_outputs']['python/a_pb2.py'] = 'f' * 64
        with self.assertRaisesRegex(RuntimeError, 'rounds differ'):
            gate.validate_protobuf_reproducibility(changed, provenance, provenance_path, proof_path)
        changed = copy.deepcopy(proof); changed['round_a']['manifest_sha256'] = 'f' * 64
        with self.assertRaisesRegex(RuntimeError, 'round A summary'):
            gate.validate_protobuf_reproducibility(changed, provenance, provenance_path, proof_path)
        relocated = self.root / 'relocated'; relocated.mkdir()
        relocated_provenance = relocated / 'provenance.json'; relocated_provenance.write_bytes(provenance_path.read_bytes())
        relocated_contract = relocated / 'protobuf-reproducibility-contract.json'
        relocated_contract.write_bytes((self.root / 'protobuf-reproducibility-contract.json').read_bytes())
        relocated_proof = relocated / gate.PROTOBUF_PROOF_NAME; relocated_proof.write_bytes(proof_path.read_bytes())
        self.assertEqual(gate.validate_protobuf_reproducibility(proof, provenance, relocated_provenance, relocated_proof)['sha256'], gate.sha(relocated_proof))
        pretty = json.dumps(proof['contract'], indent=2).encode()
        relocated_contract.write_bytes(pretty)
        changed = copy.deepcopy(proof); changed['contract_sha256'] = __import__('hashlib').sha256(pretty).hexdigest()
        gate.validate_protobuf_reproducibility(changed, provenance, relocated_provenance, relocated_proof)
        changed_provenance = copy.deepcopy(provenance); changed_provenance['source_sha256']['src/a.py'] = 'e' * 64
        with self.assertRaisesRegex(RuntimeError, 'source manifest'):
            gate.validate_protobuf_reproducibility(proof, changed_provenance, provenance_path, proof_path)

    def test_protobuf_prepare_hook_builds_contract_and_pins_emitted_proof(self):
        provenance_path = self.root / 'provenance.json'
        provenance, proof = self._protobuf_proof_fixture(provenance_path)
        source = self.root / 'source'; source.mkdir()
        debug = self.root / 'Debug'; (debug / 'python').mkdir(parents=True)
        off = self.root / 'production-off' / 'python'; off.mkdir(parents=True)
        provenance['source_root'] = str(source.resolve())
        gate.write_json(provenance_path, provenance)
        proof['provenance_sha256'] = gate.sha(provenance_path)
        tool = self.root / 'verifier.py'
        tool.write_text('''from pathlib import Path
DEFAULT_TOOLCHAIN={name:{"version":"test","sha256":"6"*64,"closure_sha256":"7"*64} for name in ("protoc","cmake","ninja")}
def recipes_for(build, source, expected_count=None): return ([{} for _ in range(2)], "recipe-pin")
def generated_map(path, allow_bytecode_cache=False):
    return {"a_pb2.py":"1"*64} if Path(path)==Path("OFF_PATH") else {"a_pb2.py":"1"*64,"b_pb2.py":"2"*64}
def cpp_map(root): return {"src/a.pb.cc":"3"*64,"src/a.pb.h":"4"*64}
'''.replace('OFF_PATH', str(off)))
        dest = self.root / 'attempt'; dest.mkdir()
        proof['candidate_output_comparison']['candidate_python_path'] = str(debug / 'python')
        proof['candidate_output_comparison']['production_off_python_path'] = str(off)
        proof['candidate_output_comparison']['cpp_build_path'] = str(debug)
        def fake_run(command, **kwargs):
            args = command
            contract_path = Path(args[args.index('--contract') + 1])
            contract_bytes = contract_path.read_bytes(); contract = json.loads(contract_bytes)
            artifact = Path(args[args.index('--artifact-root') + 1]); artifact.mkdir()
            local_proof = copy.deepcopy(proof)
            local_proof['contract'] = contract
            local_proof['contract_sha256'] = __import__('hashlib').sha256(contract_bytes).hexdigest()
            local_proof['contract_manifest_sha256'] = __import__('hashlib').sha256(
                json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            local_proof['provenance_sha256'] = gate.sha(provenance_path)
            p = artifact / gate.PROTOBUF_PROOF_NAME; p.write_text(json.dumps(local_proof))
            return type('Result', (), {'returncode': 0, 'stderr': '',
                'stdout': json.dumps({'status': local_proof['status'], 'proof_path': str(p),
                                      'proof_sha256': gate.sha(p)})})()
        with patch.object(gate, 'ROOT', source), \
             patch.object(gate, 'PROTOBUF_PROOF_TOOL', tool), \
             patch.object(gate.subprocess, 'run', side_effect=fake_run):
            pin, emitted = gate.run_protobuf_reproducibility(
                dest, provenance_path, debug, debug / 'python', off)
        contract = json.loads((dest / 'protobuf-reproducibility-contract.json').read_text())
        self.assertEqual(contract['candidate_python_output_count'], 2)
        self.assertEqual(contract['production_off_python_output_count'], 1)
        self.assertEqual(contract['cpp_output_count'], 2)
        self.assertEqual(contract['recipe_count'], 2)
        self.assertEqual(emitted.name, gate.PROTOBUF_PROOF_NAME)
        self.assertEqual(pin['sha256'], gate.sha(emitted))

    def test_m07_remote_profile_predicate_ignores_only_bytecode_cache(self):
        deployment = self.root / 'deployment'
        build = deployment / 'build' / 'Debug'
        build.mkdir(parents=True)
        payloads = {
            'python/__pycache__/mod.cpython-312.pyc': b'old cache bytes',
            'python/mod_pb2.py': b'generated source',
            'python/handwritten.py': b'handwritten source',
            'bin/runner': b'ELF bytes',
            'CTestTestfile.cmake': b'metadata',
        }
        hashes = {}
        for relative, raw in payloads.items():
            path = build / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            hashes[relative] = gate.sha(path)
        executable_hash = gate.sha(build / 'bin/runner')
        (build / 'python/__pycache__/mod.cpython-312.pyc').write_bytes(b'new regenerated cache bytes')
        xserver = self.root / 'xace-wrapper'; xserver.write_bytes(b'xserver')
        provenance = {
            'profiles': {'Debug': {'executables': {'bin/runner': executable_hash,
                    'python/__pycache__/mod.cpython-312.pyc': hashes['python/__pycache__/mod.cpython-312.pyc']},
                'runtime_files': hashes, 'ctest_metadata': {'CTestTestfile.cmake': gate.sha(build/'CTestTestfile.cmake')}}},
            'runtime_manifest': {'hosts': {'sb16': {'Debug': {'xace_wrapper': {'path': str(xserver), 'sha256': gate.sha(xserver)}}}}},
        }
        provenance_raw = json.dumps(provenance).encode(); provenance_hash = 'a' * 64; receipt_hash = 'b' * 64
        (deployment / 'provenance.json').write_bytes(provenance_raw)
        roles = {'SPATIAL_GEOMETRY_REMOTE_SOURCE': str(deployment/'source'),
            'SPATIAL_GEOMETRY_REMOTE_BIN': str(build), 'SPATIAL_GEOMETRY_REMOTE_PYTHON': str(build/'python'),
            'SPATIAL_GEOMETRY_REMOTE_XSERVER': str(xserver)}
        def remote(host, argv):
            if argv[0] == 'sha256sum':
                path = argv[1]
                if path.endswith('provenance.json'): return (provenance_hash+'  '+path+'\n').encode()
                if path.endswith('deployment-receipt.json'): return (receipt_hash+'  '+path+'\n').encode()
                return (gate.sha(xserver)+'  '+path+'\n').encode()
            if argv[0] == 'cat': return provenance_raw
            if argv[0] == 'python3':
                result = subprocess.run([sys.executable, '-B', '-c', argv[2], argv[3], argv[4]],
                    capture_output=True, check=False)
                return result.stdout
            raise AssertionError(argv)
        roles = dict(roles, SPATIAL_GEOMETRY_REMOTE_SOURCE=str(deployment/'source'))
        with patch.object(m07.m06, '_validate_remote_deployment'), patch.object(m07.m06, '_run_on_host', side_effect=remote):
            result = m07._remote_profile_pin('sb16', deployment, 'candidate', 'Debug', provenance_hash, receipt_hash, roles)
        self.assertEqual(result['profile'], 'Debug')
        for relative in ('python/mod_pb2.py', 'python/handwritten.py', 'bin/runner'):
            altered = payloads[relative] + b' changed'
            (build/relative).write_bytes(altered)
            hashes[relative] = '0' * 64
            provenance_raw = json.dumps(provenance).encode()
            with self.assertRaisesRegex(RuntimeError, 'differ from provenance'):
                with patch.object(m07.m06, '_validate_remote_deployment'), patch.object(m07.m06, '_run_on_host', side_effect=remote):
                    m07._remote_profile_pin('sb16', deployment, 'candidate', 'Debug', provenance_hash, receipt_hash, roles)
            (build/relative).write_bytes(payloads[relative]); hashes[relative] = gate.sha(build/relative)
            provenance['profiles']['Debug']['runtime_files'] = hashes
            provenance['profiles']['Debug']['executables']['bin/runner'] = gate.sha(build/'bin/runner')
            provenance_raw = json.dumps(provenance).encode()

    def test_runtime_bytecode_policy_excludes_only_mutable_cache_files(self):
        self.assertTrue(gate.validate_runtime_bytecode_policy({}) == 'legacy')
        self.assertEqual(gate.validate_runtime_bytecode_policy({
            'runtime_bytecode_integrity_policy': gate.RUNTIME_BYTECODE_INTEGRITY_POLICY}), 'v1')
        with self.assertRaisesRegex(RuntimeError, 'unsupported runtime bytecode integrity policy'):
            gate.validate_runtime_bytecode_policy({'runtime_bytecode_integrity_policy': {'version': 2}})
        self.assertTrue(gate.is_runtime_bytecode('python/__pycache__/x.cpython-312.pyc'))
        self.assertTrue(gate.is_runtime_bytecode('python/__pycache__/x.pyo'))
        self.assertFalse(gate.is_runtime_bytecode('python/actor_session_pb2.py'))
        self.assertFalse(gate.is_runtime_bytecode('python/handwritten.py'))

    def test_artifact_manifest_excludes_only_reviewed_clean_storage_volume(self):
        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture()
        self._allow_root_owned_fixture_probes()
        generated = target / 'python' / 'fixture_pb2.py'
        generated.parent.mkdir()
        generated.write_text('# reproducibly generated module\n')
        cache = generated.parent / '__pycache__' / 'fixture_pb2.cpython-312.pyc'
        cache.parent.mkdir()
        cache.write_bytes(b'first cache bytes')
        handwritten = target / 'python' / 'handwritten.py'
        handwritten.write_text('VALUE = 1\n')
        external_img = target / 'other' / 'volume.img'
        external_img.parent.mkdir()
        external_img.write_bytes(b'ordinary image evidence')
        original_sha = gate.sha
        hashed = []
        def record_sha(path):
            hashed.append(Path(path).resolve())
            return original_sha(path)
        with patch.object(gate, 'sha', side_effect=record_sha):
            manifest = gate.artifact_evidence_manifest(target)
        self.assertEqual({row['path'] for row in manifest['exclusions']}, {
            'bounded-storage-git/volume.img', 'bounded-storage-git/volume'})
        self.assertIn('ordinary.log', manifest['sha256'])
        self.assertIn('python/fixture_pb2.py', manifest['sha256'])
        self.assertIn('python/handwritten.py', manifest['sha256'])
        self.assertNotIn('python/__pycache__/fixture_pb2.cpython-312.pyc', manifest['sha256'])
        self.assertIn('other/volume.img', manifest['sha256'])
        self.assertIn('bounded-storage-git/review.json', manifest['sha256'])
        self.assertNotIn(image.resolve(), hashed)
        self.assertFalse(any(path == volume.resolve() or volume.resolve() in path.parents for path in hashed))
        self.assertEqual(manifest['sha256']['bounded-storage-git/review.json'], original_sha(root / 'review.json'))

    def test_artifact_manifest_fails_closed_on_bad_cleanup_or_unexpected_unreadable_file(self):
        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture()
        self._allow_root_owned_fixture_probes()
        review['results']['restart']['passed'] = False
        (root / 'review.json').write_text(json.dumps(review))
        with self.assertRaisesRegex(RuntimeError, 'storage-owner scenario receipt contains a failed result'):
            gate.artifact_evidence_manifest(target)
        review['results']['restart']['passed'] = True
        (root / 'review.json').write_text(json.dumps(review))
        original_sha = gate.sha
        def denied(path):
            if Path(path) == ordinary:
                raise PermissionError('fixture unreadable ordinary evidence')
            return original_sha(path)
        with patch.object(gate, 'sha', side_effect=denied):
            with self.assertRaisesRegex(RuntimeError, 'cannot hash gate artifact: ordinary.log'):
                gate.artifact_evidence_manifest(target)

    def test_artifact_manifest_rejects_links_and_keeps_exclusions_narrow(self):
        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture()
        self._allow_root_owned_fixture_probes()
        link = target / 'evidence-link'
        link.symlink_to(ordinary)
        with self.assertRaisesRegex(RuntimeError, 'file symlink'):
            gate.artifact_evidence_manifest(target)
        link.unlink()
        outside_img = target / 'other-volume.img'
        outside_img.write_bytes(b'not the declared bounded-storage fixture')
        result = gate.artifact_evidence_manifest(target)
        self.assertIn('other-volume.img', result['sha256'])
        bad_review = json.loads((root / 'review.json').read_text())
        bad_review['unmount']['exit_code'] = 1
        (root / 'review.json').write_text(json.dumps(bad_review))
        with self.assertRaisesRegex(RuntimeError, 'review or cleanup receipt'):
            gate.artifact_evidence_manifest(target)

    def test_artifact_manifest_records_only_completed_retirement_marker_without_following(self):
        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture()
        marker = self._retirement_marker_fixture(target)
        self._allow_root_owned_fixture_probes()
        manifest = gate.artifact_evidence_manifest(target)
        expected = dict(path=str(marker.relative_to(target)),
                        kind='completed-execution-retirement-marker',
                        target_bytes_hex=os.fsencode(str(marker.parent / 'never-created')).hex(),
                        target_sha256=gate.hashlib.sha256(os.fsencode(str(marker.parent / 'never-created'))).hexdigest())
        self.assertEqual(manifest['artifact_links'], [expected])
        self.assertFalse((marker.parent / 'never-created').exists())
        self.assertEqual(gate.validate_artifact_links(target, [expected]), [expected])
        for invalid in ([], [dict(expected, target_sha256='0' * 64)], [expected, dict(expected, path='extra')]):
            with self.assertRaisesRegex(RuntimeError, 'link manifest'):
                gate.validate_artifact_links(target, invalid)

    def test_retirement_marker_rejects_malformed_identity_target_and_companions(self):
        cases = ('bad-id', 'bad-target', 'missing-project', 'missing-complete', 'existing-target',
                 'project-link', 'data-link', 'git-link', 'complete-link')
        for case in cases:
            target, root, volume, image, ordinary, review = self._bounded_artifact_fixture(case)
            marker = self._retirement_marker_fixture(
                target, identifier='z' * 32 if case == 'bad-id' else 'b' * 32,
                link_target='elsewhere' if case == 'bad-target' else 'never-created')
            if case == 'missing-project':
                (marker.parent / 'data' / 'project').rmdir()
            elif case == 'missing-complete':
                (marker.parent / 'complete').unlink()
            elif case == 'existing-target':
                (marker.parent / 'never-created').write_text('unexpected')
            elif case == 'project-link':
                project = marker.parent / 'data' / 'project'
                project.rmdir()
                project.symlink_to(marker.parent / 'git', target_is_directory=True)
            elif case == 'data-link':
                data = marker.parent / 'data'
                data.rename(marker.parent / 'data-real')
                data.symlink_to(marker.parent / 'data-real', target_is_directory=True)
            elif case == 'git-link':
                git = marker.parent / 'git'
                git.rename(marker.parent / 'git-real')
                git.symlink_to(marker.parent / 'git-real', target_is_directory=True)
            elif case == 'complete-link':
                complete = marker.parent / 'complete'
                complete.unlink()
                complete.symlink_to(marker.parent / 'never-created')
            self._allow_root_owned_fixture_probes()
            with self.subTest(case=case), self.assertRaises(RuntimeError):
                gate.artifact_evidence_manifest(target)

    def test_artifact_manifest_rejects_arbitrary_directory_links_special_nodes_and_link_churn(self):
        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture()
        arbitrary = target / 'unrelated-directory'
        arbitrary.mkdir()
        link = target / 'unrelated-link'
        link.symlink_to(arbitrary, target_is_directory=True)
        self._allow_root_owned_fixture_probes()
        with self.assertRaisesRegex(RuntimeError, 'unapproved directory symlink'):
            gate.artifact_evidence_manifest(target)
        link.unlink()
        fifo = target / 'special-node'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(RuntimeError, 'not a regular file'):
            gate.artifact_evidence_manifest(target)
        fifo.unlink()
        marker = self._retirement_marker_fixture(target)
        original_readlink = gate.os.readlink
        def replace_during_read(path):
            marker.unlink()
            marker.symlink_to(marker.parent / 'never-created')
            return original_readlink(path)
        with patch.object(gate.os, 'readlink', side_effect=replace_during_read):
            with self.assertRaisesRegex(RuntimeError, 'identity is invalid'):
                gate._retirement_marker_entry(target, str(marker.relative_to(target)))

    def test_artifact_link_validator_rejects_linked_root_and_unreadable_walk(self):
        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture()
        self._allow_root_owned_fixture_probes()
        linked_root = self.root / 'linked-artifact-root'
        linked_root.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'root is not a real directory'):
            gate.validate_artifact_links(linked_root, [])
        def unreadable_walk(*args, **kwargs):
            kwargs['onerror'](PermissionError('fixture subtree denied'))
            return iter(())
        with patch.object(gate.os, 'walk', side_effect=unreadable_walk):
            with self.assertRaisesRegex(RuntimeError, 'cannot inspect artifact links'):
                gate.validate_artifact_links(target, [])

    def test_bounded_storage_exclusions_reject_active_mount_and_loop_resources(self):
        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture('active-loop')
        self._allow_root_owned_fixture_probes(loop_output=str(image) + ': /dev/loop4\n')
        with self.assertRaisesRegex(RuntimeError, 'image is attached'):
            gate.artifact_evidence_manifest(target)

        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture('mounted-volume')
        self._allow_root_owned_fixture_probes(mounted=True)
        with self.assertRaisesRegex(RuntimeError, 'unmounted real directory'):
            gate.artifact_evidence_manifest(target)

        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture('nonempty-volume')
        self._allow_root_owned_fixture_probes(find_output=str(volume / 'leftover\n'))
        with self.assertRaisesRegex(RuntimeError, 'mountpoint is not empty'):
            gate.artifact_evidence_manifest(target)

    def test_bounded_storage_exclusions_reject_declared_symlinks_and_bad_images(self):
        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture('volume-link')
        outside = self.root / 'volume-outside'
        outside.mkdir()
        volume.rmdir()
        volume.symlink_to(outside, target_is_directory=True)
        self._allow_root_owned_fixture_probes()
        with self.assertRaisesRegex(RuntimeError, 'private unmounted real directory'):
            gate.artifact_evidence_manifest(target)

        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture('image-link')
        outside_image = self.root / 'outside-image'
        outside_image.write_bytes(b'fixture')
        image.unlink()
        image.symlink_to(outside_image)
        self._allow_root_owned_fixture_probes()
        with self.assertRaisesRegex(RuntimeError, 'private regular fixture'):
            gate.artifact_evidence_manifest(target)

        for name, mutation in (
            ('wrong-size', lambda image: image.write_bytes(b'too small')),
            ('broad-mode', lambda image: image.chmod(0o644)),
            ('hardlink', lambda image: os.link(image, image.parent / 'image-alias')),
        ):
            target, root, volume, image, ordinary, review = self._bounded_artifact_fixture(name)
            self._allow_root_owned_fixture_probes()
            mutation(image)
            with self.subTest(image=name), self.assertRaisesRegex(RuntimeError, 'private regular fixture'):
                gate.artifact_evidence_manifest(target)

        target, root, volume, image, ordinary, review = self._bounded_artifact_fixture('wrong-owner')
        self._allow_root_owned_fixture_probes()
        original_lstat = Path.lstat
        def wrong_image_owner(path):
            info = original_lstat(path)
            if path == image:
                return SimpleNamespace(st_mode=info.st_mode, st_uid=os.getuid() + 1,
                                       st_size=info.st_size, st_nlink=info.st_nlink)
            return info
        with patch.object(Path, 'lstat', autospec=True, side_effect=wrong_image_owner):
            with self.assertRaisesRegex(RuntimeError, 'private regular fixture'):
                gate.artifact_evidence_manifest(target)

    def test_postrun_audit_preserves_original_failure_and_marks_unrun_profiles(self):
        report = {
            'status': 'failed', 'error': "PermissionError('volume.img')",
            'execution_error': {'type': 'PermissionError', 'traceback': 'original traceback'},
            'source_sha256_rechecked': True,
            'profile_artifacts_rechecked': {'Debug': True},
            'production_off_rechecked': {'Debug': True}, 'runtime_rechecked': {'Debug': True},
            'metadata_rechecked': {'Debug': True}, 'storage_evidence_rechecked': {'Debug': True},
            'profiles': {'Debug': {'status': 'passed_needs_crosshost_and_review'}}}
        failures = gate.finalize_postrun_audit(report)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['error'], "PermissionError('volume.img')")
        self.assertEqual(report['execution_error']['traceback'], 'original traceback')
        unrun = [row for row in failures if row.get('profile') == 'ASan-UBSan']
        self.assertEqual(len(unrun), 5)
        self.assertTrue(all(row['reason'] == 'profile-not-run' for row in unrun))

    def test_storage_sanitizer_scan_includes_receipts_and_fails_on_log_diagnostic(self):
        registrations = {name: self._write_storage_case(self.root / 'storage' / name)
                        for name in gate.STORAGE_LOCAL_EVIDENCE_TESTS}
        clean = gate.scan_storage_evidence(registrations)
        self.assertEqual(set(clean['files']), set(gate.STORAGE_LOCAL_EVIDENCE_TESTS))
        self.assertEqual(set(clean['cleanups']), set(gate.STORAGE_LOCAL_EVIDENCE_TESTS))
        for name in gate.STORAGE_LOCAL_EVIDENCE_TESTS:
            self.assertEqual({row['kind'] for row in clean['exclusions'][name]},
                             {'cleaned-volume-mountpoint', 'sparse-volume-image'})
            self.assertTrue(clean['cleanups'][name][0]['cleanup_commands_verified'])
        (registrations['session-storage-contention'] / 'contention.log').write_text(
            'AddressSanitizer: fixture diagnostic\\n')
        with self.assertRaisesRegex(RuntimeError, 'sanitizer diagnostic in storage evidence log'):
            gate.scan_storage_evidence(registrations)

    def test_storage_inventory_rejects_symlink_or_mounted_volume_exclusions(self):
        root = self._write_storage_case(self.root / 'storage-symlink')
        (root / 'volume').rmdir()
        outside = self.root / 'outside-volume'
        outside.mkdir()
        (root / 'volume').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'directory symlink'):
            gate.storage_evidence_inventory(root)

        root = self._write_storage_case(self.root / 'storage-mounted')
        with patch.object(gate.os.path, 'ismount', side_effect=lambda path: Path(path) == root / 'volume'):
            with self.assertRaisesRegex(RuntimeError, 'unmounted real directory'):
                gate.storage_evidence_inventory(root)

    def test_storage_inventory_requires_actual_cleanup_command_receipts(self):
        root = self._write_storage_case(self.root / 'storage-bad-cleanup')
        path = root / 'volume-setup.json'
        receipt = json.loads(path.read_text())
        receipt['commands'][-1]['stdout'] = '/dev/loop17: stale association'
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(RuntimeError, 'incomplete or inconsistent'):
            gate.storage_evidence_inventory(root)

    def test_storage_metadata_rewrite_is_limited_to_exact_two_rows(self):
        build = self.root / 'build'
        old = '/attempt/native'
        new = '/source/local/m06-gate-storage/attempt/Debug'
        owner = new + '/session-storage-owner'
        contention = new + '/session-storage-contention'
        self.put('build/CTestTestfile.cmake',
            'set_tests_properties([=[session-storage-owner]=] PROPERTIES TIMEOUT "180" ENVIRONMENT "SPATIAL_SECURITY_EVIDENCE=' + old + '")\n'
            'set_tests_properties([=[session-storage-contention]=] PROPERTIES TIMEOUT "180" ENVIRONMENT "SPATIAL_SECURITY_EVIDENCE=' + old + '")\n'
            'set_tests_properties([=[session-application]=] PROPERTIES ENVIRONMENT "SPATIAL_SECURITY_EVIDENCE=' + old + '")\n')
        targets = {'session-storage-owner': owner, 'session-storage-contention': contention}
        edits = gate.relocate_storage_evidence_metadata(build, Path(old), targets)
        text = (build / 'CTestTestfile.cmake').read_text()
        self.assertEqual([item['test'] for item in edits], list(gate.STORAGE_LOCAL_EVIDENCE_TESTS))
        self.assertIn('SPATIAL_SECURITY_EVIDENCE=' + owner, text)
        self.assertIn('SPATIAL_SECURITY_EVIDENCE=' + contention, text)
        shared = 'SPATIAL_SECURITY_EVIDENCE=' + old
        self.assertEqual(text.count('SPATIAL_SECURITY_EVIDENCE=' + owner), 1)
        self.assertEqual(text.count('SPATIAL_SECURITY_EVIDENCE=' + contention), 1)
        self.assertEqual(text.count(shared + chr(34)), 1)
        receipt_path = self.root / 'storage-metadata-relocation.json'
        gate.write_json(receipt_path, dict(edits=edits, targets=targets))
        saved = json.loads(receipt_path.read_text())
        self.assertEqual(saved['edits'][0]['evidence_path'], owner)
        self.assertEqual(saved['edits'][1]['evidence_path'], contention)
        with self.assertRaisesRegex(RuntimeError, 'unsupported storage evidence CTest metadata row'):
            gate.relocate_storage_evidence_metadata(build, Path(old), targets)

    def test_declared_python_cache_directories_are_empty_runtime_outputs(self):
        source = self.root / 'source'
        build = self.root / 'build'
        source.mkdir()
        build.mkdir()
        cache_name = 'pycache-actor-cabinet-gateway'
        cache = build / cache_name
        inventory = [dict(properties=[dict(name='ENVIRONMENT',
            value=['PYTHONPYCACHEPREFIX=' + str(cache)])])]
        directories = gate.ensure_inventory_runtime_directories(inventory, source, build)
        self.assertEqual(directories, [cache_name])
        self.assertTrue(cache.is_dir())
        self.assertEqual(gate.verify_inventory_paths(inventory, source, build), set())
        ignored = [dict(command=['--note', str(build / cache_name)])]
        self.assertEqual(gate.ensure_inventory_runtime_directories(ignored, source, build), [])

        cache.joinpath('stale.pyc').write_bytes(b'old cache data')
        destination = self.root / 'staged-build'
        gate.copy_runtime_closure(build, destination, {}, directories)
        staged_cache = destination / cache_name
        self.assertTrue(staged_cache.is_dir())
        self.assertEqual(list(staged_cache.iterdir()), [])
        self.assertEqual(cache.joinpath('stale.pyc').read_bytes(), b'old cache data')
        gate.ensure_runtime_directories(build, directories)
        self.assertEqual(cache.joinpath('stale.pyc').read_bytes(), b'old cache data')

    def test_declared_python_cache_archive_roundtrip_preserves_empty_directory(self):
        bundle = self.root / 'cache-dir.tar.gz'
        cache_name = 'pycache-actor-cabinet-gateway-instrumentation'
        rel = 'build/Debug/' + cache_name
        with tarfile.open(bundle, 'w:gz') as archive:
            info = tarfile.TarInfo(rel)
            info.type = tarfile.DIRTYPE
            info.mode = 0o700
            archive.addfile(info)
        out = self._empty_dir('cache-dir-out')
        files = gate.stream_archive_hashes(bundle)
        self.assertEqual(files, {})
        gate.extract_verified_bundle(bundle, out, files, [rel])
        target = out / rel
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        with self.assertRaisesRegex(RuntimeError, 'directory manifest mismatch'):
            gate.extract_verified_bundle(bundle, self._empty_dir('cache-dir-missing-out'), files, [])

    def test_declared_python_cache_directory_rejects_unsafe_roots_and_links(self):
        source = self.root / 'source'
        build = self.root / 'build'
        source.mkdir()
        build.mkdir()
        name = 'pycache-actor-cabinet-gateway'
        def inventory(path):
            return [dict(properties=[dict(name='ENVIRONMENT', value=['PYTHONPYCACHEPREFIX=' + str(path)])])]
        with self.assertRaisesRegex(RuntimeError, 'build root'):
            gate.ensure_inventory_runtime_directories(inventory(source / name), source, build)
        with self.assertRaisesRegex(RuntimeError, 'direct child'):
            gate.ensure_inventory_runtime_directories(inventory(build / 'nested' / name), source, build)
        with self.assertRaisesRegex(RuntimeError, 'direct child'):
            gate.ensure_inventory_runtime_directories(inventory(self.root / name), source, build)
        with self.assertRaisesRegex(RuntimeError, 'basename is not allowlisted'):
            gate.ensure_inventory_runtime_directories(
                inventory(build / 'pycache-unreviewed'), source, build)
        with self.assertRaisesRegex(RuntimeError, 'safe path'):
            gate.ensure_inventory_runtime_directories(inventory(build / '..' / 'source' / name), source, build)
        outside = self.root / 'outside'
        outside.mkdir()
        (build / name).symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            gate.ensure_inventory_runtime_directories(inventory(build / name), source, build)

        real_build = self.root / 'real-build'
        real_build.mkdir()
        build_link = self.root / 'build-link'
        build_link.symlink_to(real_build, target_is_directory=True)
        linked_inventory = inventory(build_link / name)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            gate.ensure_inventory_runtime_directories(linked_inventory, source, build_link)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            gate.ensure_runtime_directories(build_link, [name])
        self.assertFalse((real_build / name).exists())

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
        cache = source / 'python/__pycache__/host_session_pb2.cpython-312.pyc'; cache.parent.mkdir(parents=True); cache.write_bytes(b'cache')
        executable = self.put('production-off/spatial-host-session', 'fixture executable')
        executable.chmod(0o755)
        hashes = {name: gate.sha(source / name) for name in gate.retained_files(source)}
        gate.copy_runtime_closure(source, destination, hashes)
        packaged = {str(path.relative_to(destination)): gate.sha(path)
                    for path in destination.rglob('*') if path.is_file()}
        self.assertEqual(packaged, hashes)
        self.assertFalse(any(name.endswith(('.pyc', '.pyo')) for name in packaged))
        self.assertIn('python/host_session_pb2.py', packaged)
        self.assertIn('spatial-host-session', packaged)
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



    def crossstage_artifact_fixture(self):
        deployment = self.root / 'crossstage-deployment'
        (deployment / 'source').mkdir(parents=True)
        provenance = dict(candidate='a' * 40, runtime_manifest={}, profiles={}, production_off={})
        run_path = deployment / 'evidence' / 'attempt' / 'run.json'
        run_path.parent.mkdir(parents=True)
        runtime_profile = {'hosts': {'sb16': {'Debug': {}}}}
        report = {}
        for profile in ('Debug', 'ASan-UBSan'):
            build = deployment / 'build' / profile
            production = deployment / 'production-off' / profile
            for base in (build, production):
                (base / 'python').mkdir(parents=True)
                (base / 'python' / 'fixture_pb2.py').write_text('# generated\n')
            generated = {str(path.relative_to(build)): gate.sha(path)
                         for path in (build / 'python').rglob('*') if path.is_file()}
            off_generated = gate.generated_closure(production)
            executable = build / 'fixture-bin'
            executable.write_text('fixture executable')
            on_runtime = build / 'runtime.textproto'
            on_runtime.write_text('runtime configuration')
            off_runtime = production / 'runtime.textproto'
            off_runtime.write_text('production runtime configuration')
            source_client = deployment / 'source' / 'tools' / 'host_session_client.py'
            source_client.parent.mkdir(parents=True, exist_ok=True)
            source_client.write_text('import fixture_pb2\n')
            metadata_rel = 'CTestTestfile.cmake'
            metadata = build / metadata_rel
            metadata.write_text('# relocated metadata\n')
            metadata_digest = gate.sha(metadata)
            metadata_receipt = run_path.parent / profile / 'metadata-relocation.json'
            metadata_receipt.parent.mkdir(parents=True)
            metadata_receipt.write_text(json.dumps({'after': {metadata_rel: metadata_digest}}))
            provenance['profiles'][profile] = dict(executables={'fixture-bin': gate.sha(executable)},
                runtime_files={'runtime.textproto': gate.sha(on_runtime)},
                generated_python_sha256=generated, generated_binding_imports=gate.generated_closure(build),
                ctest_metadata={metadata_rel: '0' * 64})
            provenance['production_off'][profile] = dict(runtime_files={'runtime.textproto': gate.sha(off_runtime)},
                generated_binding_imports=off_generated)
            report.setdefault('profiles', {})[profile] = dict(status='passed_needs_crosshost_and_review',
                artifact_sha256={'metadata-relocation.json': gate.sha(metadata_receipt)})
        run_path.write_text(json.dumps(dict(host='sb16', candidate='a' * 40,
            status='local_passed_crosshost_pending', phase_complete=False, profiles=report['profiles'])))
        run_hash = gate.sha(run_path)
        provenance['runtime_manifest'] = runtime_profile
        return deployment, provenance, run_path, run_hash

    def test_crossstage_artifact_receipts_accept_and_reject_drift(self):
        deployment, provenance, run_path, run_hash = self.crossstage_artifact_fixture()
        with patch.object(gate, 'verify_runtime'), patch.object(gate, 'runtime_files', return_value=({}, [])), \
             patch.object(gate, 'verify_production_client'):
            for profile in ('Debug', 'ASan-UBSan'):
                result = gate._validate_crossstage_profile_artifacts(deployment, provenance, 'sb16',
                                                                      profile, run_path, run_hash)
                self.assertEqual(result['status'], 'passed')
            executable = deployment / 'build' / 'Debug' / 'fixture-bin'
            executable.write_text('changed executable')
            with self.assertRaisesRegex(RuntimeError, 'artifact changed'):
                gate._validate_crossstage_profile_artifacts(deployment, provenance, 'sb16',
                                                              'Debug', run_path, run_hash)
            executable.write_text('fixture executable')
            off_runtime = deployment / 'production-off' / 'Debug' / 'runtime.textproto'
            off_runtime.write_text('changed production-off artifact')
            with self.assertRaisesRegex(RuntimeError, 'production-off/runtime.textproto'):
                gate._validate_crossstage_profile_artifacts(deployment, provenance, 'sb16',
                                                              'Debug', run_path, run_hash)

    def test_crossstage_generated_binding_and_metadata_drift_rejected(self):
        deployment, provenance, run_path, run_hash = self.crossstage_artifact_fixture()
        with patch.object(gate, 'verify_runtime'), patch.object(gate, 'runtime_files', return_value=({}, [])), \
             patch.object(gate, 'verify_production_client'):
            binding = deployment / 'build' / 'Debug' / 'python' / 'fixture_pb2.py'
            binding.write_text('# changed generated binding\n')
            with self.assertRaisesRegex(RuntimeError, 'generated binding artifact changed'):
                gate._validate_crossstage_profile_artifacts(deployment, provenance, 'sb16',
                                                              'Debug', run_path, run_hash)
            binding.write_text('# generated\n')
            metadata = deployment / 'build' / 'Debug' / 'CTestTestfile.cmake'
            metadata.write_text('# changed metadata\n')
            with self.assertRaisesRegex(RuntimeError, 'relocated CTest metadata changed'):
                gate._validate_crossstage_profile_artifacts(deployment, provenance, 'sb16',
                                                              'Debug', run_path, run_hash)

    def test_crossstage_ignores_legacy_python_cache_but_pins_generated_source(self):
        deployment, provenance, run_path, run_hash = self.crossstage_artifact_fixture()
        for profile in ('Debug', 'ASan-UBSan'):
            build = deployment / 'build' / profile
            cache = build / 'python' / '__pycache__' / 'fixture_pb2.cpython-312.pyc'
            cache.parent.mkdir()
            cache.write_bytes(b'cache one')
            relative = 'python/__pycache__/fixture_pb2.cpython-312.pyc'
            provenance['profiles'][profile]['generated_python_sha256'][relative] = gate.sha(cache)
            provenance['profiles'][profile]['executables'][relative] = gate.sha(cache)
            provenance['profiles'][profile]['runtime_files'][relative] = gate.sha(cache)
            production = deployment / 'production-off' / profile
            prod_cache = production / 'python' / '__pycache__' / 'fixture_pb2.cpython-312.pyc'
            prod_cache.parent.mkdir()
            prod_cache.write_bytes(b'production cache one')
            provenance['production_off'][profile]['runtime_files'][relative] = gate.sha(prod_cache)
        with patch.object(gate, 'verify_runtime'), patch.object(gate, 'runtime_files', return_value=({}, [])), \
             patch.object(gate, 'verify_production_client'):
            for profile in ('Debug', 'ASan-UBSan'):
                (deployment / 'build' / profile / 'python/__pycache__/fixture_pb2.cpython-312.pyc').write_bytes(b'cache regenerated')
                (deployment / 'production-off' / profile / 'python/__pycache__/fixture_pb2.cpython-312.pyc').write_bytes(b'production cache regenerated')
                gate._validate_crossstage_profile_artifacts(deployment, provenance, 'sb16', profile, run_path, run_hash)
            binding = deployment / 'build' / 'Debug' / 'python' / 'fixture_pb2.py'
            binding.write_text('# generated bytes changed\n')
            with self.assertRaisesRegex(RuntimeError, 'generated binding artifact changed'):
                gate._validate_crossstage_profile_artifacts(deployment, provenance, 'sb16', 'Debug', run_path, run_hash)

    def test_runtime_helper_and_egl_drift_are_rejected_by_crossstage_authority_check(self):
        source, runtime, row, expected_host = self.runtime_fixture()
        with patch.object(gate, 'verify_helper_ownership'):
            helper = Path(row['installed_helpers'][0]['path'])
            helper.write_text('changed helper')
            with self.assertRaisesRegex(RuntimeError, 'runtime file missing or changed'):
                gate.verify_runtime(runtime, expected_host, 'Debug', source)
        source, runtime, row, expected_host = self.runtime_fixture()
        with patch.object(gate, 'verify_helper_ownership'):
            egl = Path(row['egl_files'][0]['path'])
            egl.write_text('changed EGL vendor receipt')
            with self.assertRaisesRegex(RuntimeError, 'runtime file missing or changed'):
                gate.verify_runtime(runtime, expected_host, 'Debug', source)

    def test_crossstage_checker_pins_source_before_import_behaviorally(self):
        host = gate.socket.gethostname().split('.')[0]
        self.assertIn(host, gate.TEST_HOSTS)
        deployment = self.root / 'pinned-checker-deployment'
        source = deployment / 'source'
        (source / 'tools').mkdir(parents=True)
        (deployment / 'evidence' / 'attempt').mkdir(parents=True)
        candidate = 'e' * 40
        marker = self.root / 'checker-imported'
        checker = source / 'tools/m06_deployed_gate.py'
        checker.write_text(
            'import os\nfrom pathlib import Path\n'
            'Path(os.environ["M06_IMPORT_MARKER"]).write_text("imported")\n'
            'def _validate_crossstage_profile_artifacts(deployment, provenance, host, profile, run_path, run_hash):\n'
            '    return dict(status="passed", host=host, profile=profile)\n')
        extra = source / 'pinned-extra.py'
        extra.write_text('pinned source bytes\n')
        provenance = dict(candidate=candidate, source_sha256={
            'tools/m06_deployed_gate.py': gate.sha(checker), 'pinned-extra.py': gate.sha(extra)})
        provenance_path = deployment / 'provenance.json'
        provenance_path.write_text(json.dumps(provenance))
        receipt_path = deployment / 'deployment-receipt.json'
        receipt_path.write_text(json.dumps(dict(candidate=candidate, host=host, status='verified')))
        run_path = deployment / 'evidence' / 'attempt' / 'run.json'
        run_path.write_text('{}')
        script = gate._crossstage_host_check_script()
        args = [host, str(deployment), candidate, gate.sha(provenance_path), gate.sha(receipt_path),
                str(run_path), gate.sha(run_path)]
        environment = dict(os.environ, M06_IMPORT_MARKER=str(marker))

        def invoke():
            completed = subprocess.run([sys.executable, '-c', script, *args], env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout.splitlines()[-1])

        result = invoke()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(set(result['profiles']), {'Debug', 'ASan-UBSan'})
        self.assertEqual(marker.read_text(), 'imported')

        marker.unlink()
        receipt_path.write_text(json.dumps(dict(candidate=candidate, host='wrong-host', status='verified')))
        args[4] = gate.sha(receipt_path)
        result = invoke()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('wrong-host', result['errors'][0])
        self.assertFalse(marker.exists())

        receipt_path.write_text(json.dumps(dict(candidate=candidate, host=host, status='verified')))
        args[4] = gate.sha(receipt_path)
        extra.write_text('tampered source bytes\n')
        result = invoke()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('source pin mismatch before checker import', result['errors'][0])
        self.assertFalse(marker.exists())

        extra.write_text('pinned source bytes\n')
        checker.write_text(checker.read_text() + '# changed checker pin\n')
        result = invoke()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(marker.exists())

        provenance['source_sha256'].pop('tools/m06_deployed_gate.py')
        provenance_path.write_text(json.dumps(provenance))
        args[3] = gate.sha(provenance_path)
        result = invoke()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('checker source is absent', result['errors'][0])
        self.assertFalse(marker.exists())

    def test_crossstage_receipts_require_exact_hosts_profiles_and_echoed_identity(self):
        def successful(host):
            return dict(status='passed', host=host, actual_host=host, profiles={
                profile: dict(status='passed', host=host, profile=profile) for profile in gate.PROFILES})

        complete = [dict(host=host, exit_code=0, result=successful(host)) for host in sorted(gate.TEST_HOSTS)]
        report = dict(status='running', phase_complete=False)
        with patch.object(gate, 'write_json') as writer:
            self.assertTrue(gate._record_crossstage_artifact_checks(report, 'before', complete, self.root / 'r.json'))
            for invalid in (complete[:1], [complete[0], dict(complete[0])]):
                report = dict(status='running', phase_complete=False)
                self.assertFalse(gate._record_crossstage_artifact_checks(report, 'before', invalid, self.root / 'r.json'))
            missing_profile = copy.deepcopy(complete)
            del missing_profile[0]['result']['profiles']['Debug']
            report = dict(status='running', phase_complete=False)
            self.assertFalse(gate._record_crossstage_artifact_checks(report, 'before', missing_profile, self.root / 'r.json'))
            wrong_identity = copy.deepcopy(complete)
            wrong_identity[0]['result']['actual_host'] = 'other-host'
            report = dict(status='running', phase_complete=False)
            self.assertFalse(gate._record_crossstage_artifact_checks(report, 'before', wrong_identity, self.root / 'r.json'))
            report = dict(status='running', phase_complete=False)
            self.assertTrue(gate._record_crossstage_artifact_checks(report, 'before', complete, self.root / 'r.json'))
            report['execution_error'] = "RuntimeError('native job failed')"
            after = copy.deepcopy(complete)
            after[0]['result']['status'] = 'failed'
            self.assertFalse(gate._record_crossstage_artifact_checks(report, 'after', after, self.root / 'r.json'))
        self.assertEqual(report['status'], 'failed_or_incomplete')
        self.assertFalse(report['phase_complete'])
        self.assertIn('after', report['artifact_rechecks'])
        self.assertEqual(report['execution_error'], "RuntimeError('native job failed')")
        self.assertEqual(writer.call_count, 7)

    def crosshost_plan(self):
        deployment = self.root / 'deployment'
        output = self.root / 'final-evidence'
        directions = ['sb16-to-sb16-2', 'sb16-2-to-sb16']
        cases = [f'{case}:{direction}' for direction in directions
                 for case in ('round-trip', 'private-denial', 'unavailable-destination')]
        native = {}
        for profile in ('Debug', 'ASan-UBSan'):
            hosts = {}
            for host, address in (('sb16', '10.0.7.16'), ('sb16-2', '10.0.7.17')):
                hosts[host] = dict(source=str(deployment / 'source'),
                    bin=str(deployment / 'build' / profile),
                    python=str(deployment / 'build' / profile / 'python'),
                    xserver='/runtime/Xvfb-wrapper', evidence='/tmp/m06-evidence',
                    command_runner='/opt/run-command.sh', address=address)
            native[profile] = dict(hosts=hosts)
        plan = dict(schema=1, candidate='a' * 40, runner='tools/run_geometry_native_crosshost.py',
            directions=directions, profiles=['Debug', 'ASan-UBSan'], semantic_cases=cases,
            provisioning='actor_transition_crosshost_fixture.py:production-private-two-world-issuer-mtls',
            deployments={host: dict(path=str(deployment), provenance_sha256='b' * 64,
                                    deployment_receipt_sha256='c' * 64) for host in ('sb16', 'sb16-2')},
            local_runs={host: dict(path=str(self.root / host / 'run.json'), sha256='d' * 64)
                        for host in ('sb16', 'sb16-2')},
            native_geometry=native, evidence=dict(output=str(output)))
        plan_path = self.root / 'private-plan.json'
        plan_path.write_text(json.dumps(plan))
        return plan, plan_path

    def test_final_crosshost_plan_has_positive_runner_and_layout_control(self):
        plan, path = self.crosshost_plan()
        candidate, output = gate._validate_plan(plan, path)
        self.assertEqual(candidate, plan['candidate'])
        self.assertEqual(output, Path(plan['evidence']['output']))
        self.assertEqual(plan['runner'], 'tools/run_geometry_native_crosshost.py')

    def test_stale_candidate_and_wrong_profile_or_source_are_rejected(self):
        plan, path = self.crosshost_plan()
        plan['candidate'] = 'bad'
        with self.assertRaisesRegex(RuntimeError, 'full candidate'):
            gate._validate_plan(plan, path)
        plan, path = self.crosshost_plan()
        plan['native_geometry']['ASan-UBSan']['hosts']['sb16']['bin'] = str(self.root / 'deployment/build/Debug')
        with self.assertRaisesRegex(RuntimeError, 'wrong deployed source/profile layout'):
            gate._validate_plan(plan, path)
        plan, path = self.crosshost_plan()
        plan['native_geometry']['Debug']['hosts']['sb16']['source'] = str(self.root / 'older-source')
        with self.assertRaisesRegex(RuntimeError, 'wrong deployed source/profile layout'):
            gate._validate_plan(plan, path)

    def test_shared_requirement_mapping_accepts_complete_peer_and_rejects_missing_or_wrong(self):
        suites = dict(native_controller='host-actor-controller-native',
            held_input='host-actor-controller-native',
            real_world='actor-transition-real-world', recovery='actor-transition-recovery',
            host_session='host-session-production-native',
            native_geometry_presentation='geometry-presentation-native',
            native_geometry_crosshost='geometry-native-crosshost')
        gate._validate_requirement_suites(suites, 'sb16-2')
        missing = dict(suites)
        del missing['native_geometry_crosshost']
        with self.assertRaisesRegex(RuntimeError, 'sb16-2 local requirement mapping'):
            gate._validate_requirement_suites(missing, 'sb16-2')
        wrong = dict(suites, held_input='held-input-duplicate')
        with self.assertRaisesRegex(RuntimeError, 'sb16-2 local requirement mapping'):
            gate._validate_requirement_suites(wrong, 'sb16-2')

    def test_local_gate_positive_receipt_requires_mandatory_geometry_inventory(self):
        receipt = self.root / 'local/run.json'
        receipt.parent.mkdir(parents=True)
        report_profiles = {profile: dict(status='passed_needs_crosshost_and_review', artifact_links=[], commands=[dict(
            name='core', exit_code=0, junit_cases=len(gate.REQUIRED)-1,
            all_registered_present=True, failed_or_skipped=False)]) for profile in gate.PROFILES}
        for profile in gate.PROFILES:
            directory, *_ = self._bounded_artifact_fixture('local/' + profile)
            (directory / 'inventory.json').write_text(json.dumps(dict(
                names=sorted(gate.REQUIRED), dedicated_crosshost=['geometry-native-crosshost'],
                local_names=sorted(set(gate.REQUIRED)-gate.DEDICATED_CROSSHOST))))
        receipt.write_text(json.dumps(dict(host='sb16', candidate='a' * 40,
            status='local_passed_crosshost_pending', phase_complete=False,
            requirement_suites=dict(native_controller='host-actor-controller-native',
                held_input='host-actor-controller-native', real_world='actor-transition-real-world',
                recovery='actor-transition-recovery', host_session='host-session-production-native',
                native_geometry_presentation='geometry-presentation-native',
                native_geometry_crosshost='geometry-native-crosshost'),
            profiles=report_profiles)))
        self._allow_root_owned_fixture_probes()
        result = gate._validate_local_run(receipt, 'sb16', 'a' * 40)
        self.assertEqual(set(result['inventories']), set(gate.PROFILES))
        debug_row = report_profiles['Debug']
        del debug_row['artifact_links']
        receipt.write_text(json.dumps(dict(host='sb16', candidate='a' * 40,
            status='local_passed_crosshost_pending', phase_complete=False,
            requirement_suites=dict(native_controller='host-actor-controller-native',
                held_input='host-actor-controller-native', real_world='actor-transition-real-world',
                recovery='actor-transition-recovery', host_session='host-session-production-native',
                native_geometry_presentation='geometry-presentation-native',
                native_geometry_crosshost='geometry-native-crosshost'),
            profiles=report_profiles)))
        with self.assertRaisesRegex(RuntimeError, 'omits artifact link manifest'):
            gate._validate_local_run(receipt, 'sb16', 'a' * 40)
        debug_row['artifact_links'] = [dict(path='extra', kind='completed-execution-retirement-marker',
            target_bytes_hex='', target_sha256='')]
        receipt.write_text(json.dumps(dict(host='sb16', candidate='a' * 40,
            status='local_passed_crosshost_pending', phase_complete=False,
            requirement_suites=dict(native_controller='host-actor-controller-native',
                held_input='host-actor-controller-native', real_world='actor-transition-real-world',
                recovery='actor-transition-recovery', host_session='host-session-production-native',
                native_geometry_presentation='geometry-presentation-native',
                native_geometry_crosshost='geometry-native-crosshost'),
            profiles=report_profiles)))
        with self.assertRaisesRegex(RuntimeError, 'link manifest'):
            gate._validate_local_run(receipt, 'sb16', 'a' * 40)
        debug_row['artifact_links'] = []
        receipt.write_text(json.dumps(dict(host='sb16', candidate='a' * 40,
            status='local_passed_crosshost_pending', phase_complete=False,
            requirement_suites=dict(native_controller='host-actor-controller-native',
                held_input='host-actor-controller-native', real_world='actor-transition-real-world',
                recovery='actor-transition-recovery', host_session='host-session-production-native',
                native_geometry_presentation='geometry-presentation-native',
                native_geometry_crosshost='geometry-native-crosshost'),
            profiles=report_profiles)))
        for missing in ('geometry-presentation-native',
                        'geometry-process-identity-witness',
                        'geometry-native-fixture-regressions'):
            data = dict(names=sorted(gate.REQUIRED),
                         dedicated_crosshost=['geometry-native-crosshost'],
                         local_names=sorted(set(gate.REQUIRED)-gate.DEDICATED_CROSSHOST))
            data['names'].remove(missing)
            data['local_names'].remove(missing)
            (receipt.parent / 'Debug/inventory.json').write_text(json.dumps(data))
            with self.assertRaisesRegex(RuntimeError, 'misses mandatory'):
                gate._validate_local_run(receipt, 'sb16', 'a' * 40)

    def test_six_case_semantic_invocation_and_cleanup_positive_control(self):
        deployment = self.root / 'deployment'
        output = self.root / 'semantic'
        class FixtureProcess:
            returncode = 0
            def __init__(self, command, **kwargs):
                self.command = command
                self.target = Path(command[-1])
                self.target.mkdir(parents=True, exist_ok=False)
                (self.target / 'runtime-status.txt').write_text('PASS\n')
                (self.target / 'cleanup.json').write_text(json.dumps(dict(complete=True, errors=[])))
                (self.target / 'plan-summary.json').write_text(json.dumps(dict(cases=6, worlds={'sb16': {}, 'sb16-2': {}}, semantic_cases=[f'{case}:{direction}' for direction in ('sb16-to-sb16-2','sb16-2-to-sb16') for case in ('round-trip','private-denial','unavailable-destination')])))
                (self.target / 'harness.exit').write_text('0\n')
                (self.target / 'harness.stdout').write_text('cross-host actor transition protobuf plan passed\n')
            def communicate(self, timeout=None):
                return b'', b''
        with patch.object(gate.subprocess, 'Popen', FixtureProcess):
            result = gate._validate_semantic_fixture(deployment, 'Debug', output)
        self.assertEqual(result['status'], 'passed')
        command = json.loads((output.with_name(output.name + '.launch') / 'invocation.json').read_text())['argv']
        self.assertEqual(command[1], str(deployment / 'source/tests/core/actor_transition_crosshost_fixture.py'))
        self.assertIn('--bundle', command)
        self.assertTrue(command)


    def test_native_case_cleanup_and_all_nine_nonempty_receipts_positive_control(self):
        root = self.root / 'geometry'
        unit = 'spatial-m03-resources-m06-geometry-' + 'a' * 32 + '.service'
        for index in range(1, 10):
            case = root / f'test_{index:02d}_case'
            case.mkdir(parents=True)
            (case / 'exact-unit-terminal.json').write_text(json.dumps(dict(unit=unit,
                state='ActiveState=inactive\nLoadState=not-found\n', query_exit=4, cgroup_absent=True)))
            (case / 'peer-resource-terminal.txt').write_text('\n'.join((
                json.dumps(dict(scope_removed=True, unexpected_population=False, exit_code=0)),
                json.dumps(dict(unit=unit, unit_after='ActiveState=inactive\nLoadState=not-found\n',
                                query_exit=4, cgroup_exists=False)))) + '\n')
            (case / 'capture.sha256').write_text('b' * 64 + '\n')
            (case / 'benign.stderr').write_text('')
        result = gate._validate_local_native_cases(root, 'sb16')
        self.assertEqual(len(result), 9)
        (root / 'test_09_case/exact-unit-terminal.json').write_text(json.dumps(dict(unit=unit,
            state='ActiveState=active\nLoadState=loaded\n', query_exit=0, cgroup_absent=False)))
        with self.assertRaisesRegex(RuntimeError, 'exact-unit cleanup is incomplete'):
            gate._validate_local_native_cases(root, 'sb16')


    def test_missing_partial_or_empty_native_case_evidence_rejects(self):
        root = self.root / 'geometry'
        unit = 'spatial-m03-resources-m06-geometry-' + 'a' * 32 + '.service'
        for index in range(1, 10):
            case = root / f'test_{index:02d}_case'
            case.mkdir(parents=True)
            (case / 'exact-unit-terminal.json').write_text(json.dumps(dict(unit=unit,
                state='ActiveState=inactive\nLoadState=not-found\n', query_exit=4, cgroup_absent=True)))
            (case / 'peer-resource-terminal.txt').write_text('\n'.join((
                json.dumps(dict(scope_removed=True, unexpected_population=False, exit_code=0)),
                json.dumps(dict(unit=unit, unit_after='ActiveState=inactive\nLoadState=not-found\n',
                                query_exit=4, cgroup_exists=False)))) + '\n')
            (case / 'capture.sha256').write_text('b' * 64 + '\n')
        shutil.rmtree(root / 'test_09_case')
        with self.assertRaisesRegex(RuntimeError, 'case evidence is incomplete'):
            gate._validate_local_native_cases(root, 'sb16')
        case = root / 'test_09_case'
        case.mkdir()
        (case / 'exact-unit-terminal.json').write_text(json.dumps(dict(unit=unit,
            state='ActiveState=inactive\nLoadState=not-found\n', query_exit=4, cgroup_absent=True)))
        (case / 'peer-resource-terminal.txt').write_text('')
        (case / 'capture.sha256').write_text('')
        with self.assertRaisesRegex(RuntimeError, 'peer cleanup failed|evidence is empty'):
            gate._validate_local_native_cases(root, 'sb16')

    def native_geometry_archive(self):
        root = self.root / 'geometry-source'
        unit = 'spatial-m03-resources-m06-geometry-' + 'a' * 32 + '.service'
        for index in range(1, 10):
            case = root / 'geometry' / f'test_{index:02d}_case'
            case.mkdir(parents=True)
            (case / 'exact-unit-terminal.json').write_text(json.dumps(dict(unit=unit,
                state='ActiveState=inactive\nLoadState=not-found\n', query_exit=4, cgroup_absent=True)))
            (case / 'peer-resource-terminal.txt').write_text('\n'.join((
                json.dumps(dict(scope_removed=True, unexpected_population=False, exit_code=0)),
                json.dumps(dict(unit=unit, unit_after='ActiveState=inactive\nLoadState=not-found\n',
                                query_exit=4, cgroup_exists=False)))) + '\n')
            (case / 'peer-resource-launch.json').write_text(json.dumps(dict(unit=unit)))
            (case / 'benign.stderr').write_text('')
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:') as tar:
            tar.add(root / 'geometry', arcname='geometry')
        return archive.getvalue()

    def test_remote_authority_native_evidence_is_fetched_from_owning_host(self):
        archive = self.native_geometry_archive()
        def receipt(host, path):
            if host == 'sb16-2':
                return {'stdout.log': 'Ran 9 tests in 1s\n\nOK\n', 'stderr.log': '',
                    'terminal-status.txt': 'COMPLETE', 'command.exit': '0', 'wrapper.exit': '0'}[path.name]
            return 'observer terminal log'
        with patch.object(gate, '_run_on_host', return_value=archive) as remote, \
             patch.object(gate, '_receipt_text', side_effect=receipt):
            result = gate._validate_native_run('sb16-2', 'sb16', '/remote/authority/run',
                '/remote/observer/run', 'Debug', self.root / 'fetched')
        self.assertEqual(result['authority'], 'sb16-2')
        self.assertTrue(any(call.args[0] == 'sb16-2' for call in remote.call_args_list))
        self.assertTrue((self.root / 'fetched/geometry/test_01_case/peer-resource-launch.json').is_file())

    def test_timeout_recovery_reads_exact_units_on_authority_and_persists_query_failure(self):
        authority_evidence = self.root / 'authority-geometry'
        case = authority_evidence / 'test_01_case'
        case.mkdir(parents=True)
        unit = 'spatial-m03-resources-m06-geometry-' + 'c' * 32 + '.service'
        (case / 'peer-resource-launch.json').write_text(json.dumps(dict(unit=unit)))
        calls = []
        def host_result(host, argv, timeout=20):
            calls.append((host, argv))
            if argv[0:3] == ['systemctl', 'show', unit]:
                return dict(exit_code=4, stdout='ActiveState=inactive\nLoadState=not-found\n', stderr='')
            return dict(exit_code=0, stdout='', stderr='')
        with patch.object(gate, '_fetch_native_case_tree', return_value=authority_evidence) as fetched, \
             patch.object(gate, '_host_command_result', side_effect=host_result):
            receipt = gate._recover_native_timeout('sb16-2', 'sb16', '/remote/run', self.root / 'recovered')
        self.assertEqual(receipt['status'], 'recovered')
        self.assertEqual(fetched.call_args.args[0], 'sb16-2')
        self.assertEqual(calls[0][0], 'sb16')
        self.assertTrue((self.root / 'recovered/timeout-recovery.json').is_file())
        calls.clear()
        def failed_query(host, argv, timeout=20):
            calls.append((host, argv))
            if argv[0:3] == ['systemctl', 'show', unit]:
                return dict(exit_code=255, stdout='', stderr='SSH failed')
            return dict(exit_code=0, stdout='', stderr='')
        with patch.object(gate, '_fetch_native_case_tree', return_value=authority_evidence), \
             patch.object(gate, '_host_command_result', side_effect=failed_query):
            broken = gate._recover_native_timeout('sb16-2', 'sb16', '/remote/run', self.root / 'query-failed')
        self.assertEqual(broken['status'], 'incomplete')
        self.assertEqual(broken['exact_units'][0]['query']['exit_code'], 255)
        self.assertTrue((self.root / 'query-failed/timeout-recovery.json').is_file())

    def test_each_native_direction_starts_from_unmodified_runner_paths(self):
        original = dict(hosts={host: dict(command_runner='/opt/' + host + '/run-command.sh')
                              for host in ('sb16', 'sb16-2')})
        first = gate._fresh_native_manifest(original)
        first['hosts']['sb16']['command_runner'] = '/evidence/direction-a-wrapper.sh'
        second = gate._fresh_native_manifest(original)
        second['hosts']['sb16']['command_runner'] = '/evidence/direction-b-wrapper.sh'
        self.assertEqual(second['hosts']['sb16']['command_runner'], '/evidence/direction-b-wrapper.sh')
        self.assertEqual(second['hosts']['sb16-2']['command_runner'], '/opt/sb16-2/run-command.sh')
        self.assertEqual(original['hosts']['sb16']['command_runner'], '/opt/sb16/run-command.sh')

    def test_remote_source_pin_mismatch_is_rejected(self):
        value = json.dumps(dict(candidate='a' * 40, receipt_candidate='a' * 40,
            host='sb16-2', status='verified', source_ok=False, bad=['tests/core/fixture.py'])).encode()
        with patch.object(gate, '_run_on_host', return_value=value):
            with self.assertRaisesRegex(RuntimeError, 'deployment/source pin mismatch'):
                gate._validate_remote_deployment('sb16-2', dict(path='/deploy'), 'a' * 40)

    def test_crosshost_reconciliation_cannot_mark_phase_complete(self):
        tree = gate.ast.parse(Path(gate.__file__).read_text())
        function = next(node for node in tree.body if isinstance(node, gate.ast.FunctionDef) and node.name == 'crosshost')
        values = []
        for node in gate.ast.walk(function):
            if isinstance(node, gate.ast.Assign):
                for target in node.targets:
                    if isinstance(target, gate.ast.Subscript) and isinstance(target.slice, gate.ast.Constant) and target.slice.value == 'phase_complete':
                        values.append(node.value)
        self.assertTrue(values)
        self.assertTrue(all(isinstance(value, gate.ast.Constant) and value.value is False for value in values))

    def test_incomplete_semantic_cleanup_and_failed_or_unrun_native_receipts_reject(self):
        output = self.root / 'semantic'
        output.mkdir()
        (output / 'runtime-status.txt').write_text('PASS\n')
        (output / 'cleanup.json').write_text(json.dumps(dict(complete=False, errors=['still running'])))
        (output / 'plan-summary.json').write_text(json.dumps(dict(cases=6, worlds={'sb16': {}, 'sb16-2': {}}, semantic_cases=[f'{case}:{direction}' for direction in ('sb16-to-sb16-2','sb16-2-to-sb16') for case in ('round-trip','private-denial','unavailable-destination')])))
        (output / 'harness.exit').write_text('0\n')
        (output / 'harness.stdout').write_text('cross-host actor transition protobuf plan passed\n')
        with self.assertRaisesRegex(RuntimeError, 'cleanup is incomplete'):
            gate._validate_semantic_receipt(output)
        with self.assertRaisesRegex(RuntimeError, 'receipt is incomplete'):
            gate._validate_native_terminal('RUNNING', '', '', '', '')
        with self.assertRaisesRegex(RuntimeError, 'receipt is incomplete'):
            gate._validate_native_terminal('COMPLETE', '124', '124', 'Ran 9 tests in 1s\n\nOK\n', '')
        gate._validate_native_terminal('COMPLETE', '0', '0', 'Ran 9 tests in 1s\n\nOK\n', '')

unittest.main(verbosity=2)
