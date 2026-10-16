"""Pure behavior checks for explicit copy fixture resource profiles."""
import os
import importlib.util
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from copy_fixture_profiles import consume_profile, manager_memory_mib, instrumentation_preflight, COPY_BINARIES

class CopyFixtureProfileTests(unittest.TestCase):
    def test_explicit_profiles_preserve_arguments_and_budget(self):
        original=['runner','BUILD','SOURCE','EVIDENCE','test_exact']
        for profile,limit in [('Debug',256),('ASan-UBSan',512)]:
            arguments,actual=consume_profile(original+['--profile='+profile])
            self.assertEqual(arguments,original);self.assertEqual(actual,profile)
            self.assertEqual(manager_memory_mib(actual),limit)
            adapter,selected=consume_profile(['--copy-profile='+profile],'--copy-profile=')
            self.assertEqual(adapter,[]);self.assertEqual(selected,profile)

    def test_default_and_other_adapter_modes_keep_debug_envelope(self):
        arguments=['--','python3','fixture.py','/path/ASan-UBSan']
        with patch.dict(os.environ,ASAN_OPTIONS='detect_leaks=1:abort_on_error=1'):
            unchanged,profile=consume_profile(arguments,'--copy-profile=')
        self.assertEqual(unchanged,arguments);self.assertEqual(profile,'Debug')
        self.assertEqual(manager_memory_mib(profile),256)

    def test_unknown_duplicate_and_misplaced_profiles_are_rejected(self):
        for arguments in (['--profile=SAN'],['--profile='],
                          ['--profile=Debug','extra'],['--profile=Debug','--profile=ASan-UBSan']):
            with self.assertRaises(ValueError):consume_profile(arguments)
        with self.assertRaises(ValueError):manager_memory_mib('unknown')

    def test_san_preflight_rejects_mixed_and_records_actual_binaries(self):
        both=' (NEEDED) Shared library: [libasan.so.8]\n (NEEDED) Shared library: [libubsan.so.1]\n'
        with tempfile.TemporaryDirectory() as directory:
            for name in COPY_BINARIES:(Path(directory)/name).write_bytes(name.encode())
            def result(argv,**kwargs):
                return subprocess.CompletedProcess(argv,0,both if Path(argv[-1]).name!='worldd' else '', '')
            with patch('copy_fixture_profiles.subprocess.run',side_effect=result):
                with self.assertRaisesRegex(ValueError,'worldd'):instrumentation_preflight(directory,'ASan-UBSan')
            with patch('copy_fixture_profiles.subprocess.run',return_value=subprocess.CompletedProcess([],0,both,'')):
                proof=instrumentation_preflight(directory,'ASan-UBSan')
            self.assertEqual(set(proof['binaries']),set(COPY_BINARIES))
            self.assertEqual(proof['manager_memory_bytes'],512*2**20)
            for value in proof['binaries'].values():self.assertEqual(len(value['sha256']),64)

    def test_debug_preflight_rejects_san_and_mixed_executables(self):
        plain=' (NEEDED) Shared library: [libc.so.6]\n'
        sanitized=' (NEEDED) Shared library: [libasan.so.8]\n (NEEDED) Shared library: [libubsan.so.1]\n'
        with tempfile.TemporaryDirectory() as directory:
            for name in COPY_BINARIES:(Path(directory)/name).write_bytes(name.encode())
            with patch('copy_fixture_profiles.subprocess.run',return_value=subprocess.CompletedProcess([],0,sanitized,'')):
                with self.assertRaisesRegex(ValueError,'workspaced'):instrumentation_preflight(directory,'Debug')
            def mixed(argv,**kwargs):
                return subprocess.CompletedProcess(argv,0,sanitized if Path(argv[-1]).name=='worldd' else plain,'')
            with patch('copy_fixture_profiles.subprocess.run',side_effect=mixed):
                with self.assertRaisesRegex(ValueError,'worldd'):instrumentation_preflight(directory,'Debug')
            with patch('copy_fixture_profiles.subprocess.run',return_value=subprocess.CompletedProcess([],0,plain,'')):
                proof=instrumentation_preflight(directory,'Debug')
            self.assertEqual(set(proof['binaries']),set(COPY_BINARIES))
            self.assertEqual(proof['manager_memory_bytes'],256*2**20)

    def test_supervisor_preflight_checks_the_added_actual_executable(self):
        plain=' (NEEDED) Shared library: [libc.so.6]\n'
        sanitized=' (NEEDED) Shared library: [libasan.so.8]\n (NEEDED) Shared library: [libubsan.so.1]\n'
        with tempfile.TemporaryDirectory() as directory:
            for name in (*COPY_BINARIES,'spatial-authority-supervisor'):(Path(directory)/name).write_bytes(name.encode())
            def mixed(argv,**kwargs):
                return subprocess.CompletedProcess(argv,0,sanitized if Path(argv[-1]).name=='spatial-authority-supervisor' else plain,'')
            with patch('copy_fixture_profiles.subprocess.run',side_effect=mixed):
                with self.assertRaisesRegex(ValueError,'spatial-authority-supervisor'):
                    instrumentation_preflight(directory,'Debug',additional_binaries=('spatial-authority-supervisor',))
            with patch('copy_fixture_profiles.subprocess.run',return_value=subprocess.CompletedProcess([],0,plain,'')):
                proof=instrumentation_preflight(directory,'Debug',additional_binaries=('spatial-authority-supervisor',))
            self.assertEqual(set(proof['binaries']),set(COPY_BINARIES)|{'spatial-authority-supervisor'})

    def test_file_based_runner_imports_find_adjacent_helper(self):
        tools=Path(__file__).resolve().parent
        for name in ('run_workspace_copy_engine','run_workspace_copy_faults'):
            with patch.object(sys,'path',[item for item in sys.path if Path(item or '.').resolve()!=tools]), \
                 patch.dict(sys.modules):
                sys.modules.pop('copy_fixture_profiles',None)
                spec=importlib.util.spec_from_file_location('profile_import_'+name,tools/(name+'.py'))
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                self.assertTrue(callable(module.main))
                self.assertEqual(module.consume_profile(['runner'])[1],'Debug')

if __name__=='__main__':unittest.main(verbosity=2)
