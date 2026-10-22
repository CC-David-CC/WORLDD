"""Offline regression for P10 evidence path creation and systemd forwarding."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load('m06_gate_evidence_forwarding', TOOLS / 'm06_deployed_gate.py')
delegate = load('resource_delegate_evidence_forwarding', TOOLS / 'with_resource_delegate.py')


class P10EvidenceForwardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'frozen-source'
        self.source.mkdir()
        self.source_file = self.source / 'pinned-input.txt'
        self.source_file.write_text('frozen\n', encoding='utf-8')
        self.source_hash = self.source_file.read_bytes()
        self.inside_profile = self.source / 'attempt' / 'Debug'
        self.inside_profile.mkdir(parents=True)
        self.source.chmod(0o555)
        self.profile = self.root / 'external-artifacts with space' / 'Debug'
        self.profile.mkdir(parents=True)
        self.profile_link = self.root / 'Debug-link'
        self.profile_link.symlink_to(self.profile, target_is_directory=True)

    def tearDown(self):
        self.source.chmod(0o755)
        self.temp.cleanup()

    def test_p10_gate_path_reaches_real_delegate_command_outside_frozen_source(self):
        environment = {
            'SPATIAL_EVIDENCE_DIR': str(self.source / 'stale'),
            'SPATIAL_SCOPE_EVIDENCE': str(self.profile / 'scopes'),
            'UNRELATED_SECRET': 'must-not-cross-systemd-boundary',
        }
        selected = gate.stage_environment(
            environment, 'P10', self.root / 'production', self.source, self.profile)
        evidence = Path(selected['SPATIAL_EVIDENCE_DIR'])
        self.assertEqual(evidence, self.profile / 'acceptance')
        self.assertEqual(stat.S_IMODE(evidence.lstat().st_mode), 0o700)
        self.assertEqual(evidence.stat().st_uid, os.getuid())
        self.assertNotIn(self.source.resolve(), evidence.resolve().parents)
        self.assertEqual(self.source_file.read_bytes(), self.source_hash)
        self.assertFalse((self.source / 'evidence').exists())

        command = delegate.build_service_command(
            unit='spatial-m03-resources-fixture.service', username='dflanag3',
            native=True, copy_engine_v2=False, working_directory=self.source,
            environment=selected, argv=['python3', 'tools/gate.py', '--through', 'P10'])
        forwarded = [arg for arg in command if arg.startswith('--setenv=SPATIAL_EVIDENCE_DIR=')]
        self.assertEqual(forwarded, ['--setenv=SPATIAL_EVIDENCE_DIR=' + str(evidence)])
        self.assertIn('--setenv=SPATIAL_SCOPE_EVIDENCE=' + str(self.profile / 'scopes'), command)
        self.assertFalse(any('UNRELATED_SECRET' in arg for arg in command))
        self.assertEqual(command[-5:], ['--', 'python3', 'tools/gate.py', '--through', 'P10'])
        self.assertEqual(self.source_file.read_bytes(), self.source_hash)
        self.assertEqual(stat.S_IMODE(self.source.lstat().st_mode), 0o555)

    def test_non_p10_stage_drops_stale_evidence_path_without_creating_one(self):
        selected = gate.stage_environment(
            {'SPATIAL_EVIDENCE_DIR': '/caller/controlled/path'},
            'core', self.root / 'production', self.source, self.profile)
        self.assertNotIn('SPATIAL_EVIDENCE_DIR', selected)
        self.assertFalse((self.profile / 'acceptance').exists())
        self.assertEqual(self.source_file.read_bytes(), self.source_hash)

    def test_p10_rejects_profile_artifacts_inside_frozen_source(self):
        with self.assertRaisesRegex(RuntimeError, 'outside frozen source'):
            gate.stage_environment({}, 'P10', self.root / 'production',
                                   self.source, self.inside_profile)
        self.assertFalse((self.inside_profile / 'acceptance').exists())
        self.assertEqual(self.source_file.read_bytes(), self.source_hash)

    def test_p10_requires_a_fresh_external_profile_directory(self):
        selected = gate.stage_environment({}, 'P10', self.root / 'production',
                                          self.source, self.profile)
        with self.assertRaisesRegex(RuntimeError, 'must be fresh'):
            gate.stage_environment({}, 'P10', self.root / 'production',
                                   self.source, self.profile)
        self.assertEqual(Path(selected['SPATIAL_EVIDENCE_DIR']), self.profile / 'acceptance')

    def test_p10_rejects_symlinked_profile_directory(self):
        with self.assertRaisesRegex(RuntimeError, 'real directory'):
            gate.stage_environment({}, 'P10', self.root / 'production',
                                   self.source, self.profile_link)
        self.assertFalse((self.profile / 'acceptance').exists())
        self.assertEqual(self.source_file.read_bytes(), self.source_hash)

    def test_delegate_forwards_python_bytecode_suppression_through_sudo(self):
        command = delegate.build_service_command(
            unit='spatial-test.service', username='dflanag3', native=True,
            copy_engine_v2=False, working_directory=self.source,
            environment={'PYTHONDONTWRITEBYTECODE': '1'},
            argv=['python3', 'test.py'])
        self.assertIn('--setenv=PYTHONDONTWRITEBYTECODE=1', command)
        self.assertEqual(command[-3:], ['--', 'python3', 'test.py'])

    def test_delegate_forwards_verified_production_and_private_observer_selectors(self):
        production=str(self.root/'production server'/'Xvfb')
        observer=str(self.root/'observer server'/'Xvfb')
        runtime={'xace_wrapper':{'path':production},'deployed_binary':{'sha256':'a'*64},
            'qualification_observer':{'xace_wrapper':{'path':observer},'deployed_binary':{'sha256':'b'*64}}}
        environment=gate.runtime_environment({'UNRELATED_SECRET':'must-not-cross-systemd-boundary'},runtime)
        command=delegate.build_service_command(unit='spatial-test.service',username='dflanag3',
            native=True,copy_engine_v2=False,working_directory=self.source,
            environment=environment,argv=['python3','native-witness.py'])
        expected={'SPATIAL_TEST_XSERVER':production,'SPATIAL_TEST_XSERVER_SHA256':'a'*64,
            'SPATIAL_TEST_DEFAULT_XSERVER':production,'SPATIAL_TEST_OBSERVER_XSERVER':observer,
            'SPATIAL_TEST_OBSERVER_XSERVER_SHA256':'b'*64}
        for name,value in expected.items():
            self.assertEqual(command.count('--setenv='+name+'='+value),1,name)
        self.assertFalse(any('UNRELATED_SECRET' in arg for arg in command))
        self.assertEqual(command[-3:],['--','python3','native-witness.py'])

    def test_delegate_preserves_existing_service_limits(self):
        base = dict(unit='spatial-test.service', username='dflanag3',
                    working_directory=self.source, environment={}, argv=['true'])
        native = delegate.build_service_command(**base, native=True, copy_engine_v2=False)
        copy = delegate.build_service_command(**base, native=True, copy_engine_v2=True)
        for setting in ('MemoryMax=1536M', 'MemorySwapMax=0', 'TasksMax=256',
                        'CPUQuota=400%', 'RuntimeMaxSec=240', 'TimeoutStopSec=5s'):
            self.assertIn(setting, native)
        self.assertIn('RuntimeMaxSec=900', copy)


if __name__ == '__main__':
    unittest.main(verbosity=2)
