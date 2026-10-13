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
        expected_host = receipt.get('host')
        if expected_host not in ('sb16', 'sb16-2'):
            raise RuntimeError('runtime fixture receipt must name an actual qualification host')
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
                       hosts={expected_host: {'Debug': row}})
        return source, runtime, row, expected_host

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
        receipt.write_text(json.dumps(dict(host='sb16', candidate='a' * 40,
            status='local_passed_crosshost_pending', phase_complete=False,
            requirement_suites=dict(native_controller='host-actor-controller-native',
                held_input='host-actor-controller-native', real_world='actor-transition-real-world',
                recovery='actor-transition-recovery', host_session='host-session-production-native',
                native_geometry_presentation='geometry-presentation-native',
                native_geometry_crosshost='geometry-native-crosshost'),
            profiles={profile: dict(status='passed_needs_crosshost_and_review', commands=[dict(
                name='core', exit_code=0, junit_cases=len(gate.REQUIRED)-1,
                all_registered_present=True, failed_or_skipped=False)]) for profile in gate.PROFILES})))
        for profile in gate.PROFILES:
            directory = receipt.parent / profile
            directory.mkdir()
            (directory / 'inventory.json').write_text(json.dumps(dict(
                names=sorted(gate.REQUIRED), dedicated_crosshost=['geometry-native-crosshost'],
                local_names=sorted(set(gate.REQUIRED)-gate.DEDICATED_CROSSHOST))))
        result = gate._validate_local_run(receipt, 'sb16', 'a' * 40)
        self.assertEqual(set(result['inventories']), set(gate.PROFILES))
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
