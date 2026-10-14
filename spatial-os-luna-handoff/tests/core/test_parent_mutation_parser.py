"""A positive-case fixture error must invalidate a native mutation result."""
import unittest
from tools.qualify_parent_mutations import mutation_result


class ParentMutationParserTests(unittest.TestCase):
    def test_expected_failures_are_detected(self):
        text = 'FAIL: test_guard_a\nFAIL: test_guard_b\nRan 47 tests in 1.0s\n'
        self.assertTrue(mutation_result(text, 1, {'test_guard_a', 'test_guard_b'})[0])

    def test_additional_positive_case_error_invalidates_detection(self):
        text = 'FAIL: test_guard_a\nERROR: test_positive_case\nRan 47 tests in 1.0s\n'
        result = mutation_result(text, 1, {'test_guard_a'})
        self.assertFalse(result[0])
        self.assertEqual(result[2], {'test_positive_case'})

    def test_additional_failure_invalidates_detection(self):
        text = 'FAIL: test_guard_a\nFAIL: test_positive_case\nRan 47 tests in 1.0s\n'
        self.assertFalse(mutation_result(text, 1, {'test_guard_a'})[0])

    def test_incomplete_suite_invalidates_detection(self):
        text = 'FAIL: test_guard_a\nRan 27 tests in 1.0s\n'
        self.assertFalse(mutation_result(text, 1, {'test_guard_a'})[0])

    def test_successful_exit_invalidates_detection(self):
        text = 'FAIL: test_guard_a\nRan 47 tests in 1.0s\n'
        self.assertFalse(mutation_result(text, 0, {'test_guard_a'})[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
