"""Host-independent regressions for private evidence case scoping."""
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest

from harness.private_evidence import EVIDENCE_ENV, bounded_tree_files, install_case_evidence


class PrivateEvidenceScopeTests(unittest.TestCase):
    def test_repeated_cases_get_distinct_retained_private_leaves(self):
        with tempfile.TemporaryDirectory() as root:
            env = {EVIDENCE_ENV: root}
            first, restore_first = install_case_evidence(root, 'case-a', env)
            first_value = env[EVIDENCE_ENV]
            restore_first()
            second, restore_second = install_case_evidence(root, 'case-a', env)
            try:
                self.assertNotEqual(first, second)
                self.assertEqual(first.stat().st_mode & 0o777, 0o700)
                self.assertEqual(second.stat().st_mode & 0o777, 0o700)
                self.assertEqual(Path(env[EVIDENCE_ENV]), second)
                self.assertTrue(first.is_dir(), 'prior case evidence must remain retained')
                self.assertNotEqual(first_value, env[EVIDENCE_ENV])
            finally:
                restore_second()
            self.assertEqual(env[EVIDENCE_ENV], root)

    def test_child_process_inherits_the_case_leaf(self):
        with tempfile.TemporaryDirectory() as root:
            prior = os.environ.get(EVIDENCE_ENV)
            os.environ[EVIDENCE_ENV] = root
            leaf, restore = install_case_evidence(root, 'child-propagation')
            try:
                child = subprocess.run(
                    [sys.executable, '-c',
                     'import os; print(os.environ["SPATIAL_SECURITY_EVIDENCE"])'],
                    check=True, capture_output=True, text=True)
                self.assertEqual(child.stdout.strip(), str(leaf))
            finally:
                restore()
                if prior is None:
                    os.environ.pop(EVIDENCE_ENV, None)
                else:
                    os.environ[EVIDENCE_ENV] = prior

    def test_cleanup_callback_restores_environment_after_setup_failure(self):
        with tempfile.TemporaryDirectory() as root:
            env = {EVIDENCE_ENV: root}
            leaf_holder = []

            class BrokenFixture(unittest.TestCase):
                def setUp(inner_self):
                    leaf, restore = install_case_evidence(root, 'setup-failure', env)
                    leaf_holder.append(leaf)
                    inner_self.addCleanup(restore)
                    raise RuntimeError('simulated inherited setup failure')

                def test_never_reached(inner_self):
                    self.fail('test body must not run')

            result = unittest.TestResult()
            BrokenFixture('test_never_reached').run(result)
            self.assertEqual(len(result.errors), 1)
            self.assertEqual(env[EVIDENCE_ENV], root)
            self.assertTrue(leaf_holder[0].is_dir(), 'failure evidence must remain retained')

    def test_bounded_scan_still_rejects_more_than_64_files(self):
        with tempfile.TemporaryDirectory() as root:
            for index in range(65):
                (Path(root) / f'{index:02}.log').touch()
            with self.assertRaisesRegex(AssertionError, 'evidence scan exceeds file bound'):
                bounded_tree_files(self, root)

    def test_bounded_scan_still_rejects_excess_entries_and_depth(self):
        with tempfile.TemporaryDirectory() as root:
            for index in range(2049):
                (Path(root) / f'{index:04}.link').symlink_to('missing-target')
            with self.assertRaisesRegex(AssertionError, 'evidence scan exceeds entry bound'):
                bounded_tree_files(self, root)
        with tempfile.TemporaryDirectory() as root:
            current = Path(root)
            for index in range(9):
                current = current / f'd{index}'
                current.mkdir()
            with self.assertRaisesRegex(AssertionError, 'nested evidence exceeds depth bound'):
                bounded_tree_files(self, root)


if __name__ == '__main__':
    unittest.main()
