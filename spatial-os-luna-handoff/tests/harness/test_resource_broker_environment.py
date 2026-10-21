import unittest

from harness.resource_broker import resource_environment


class ResourceBrokerEnvironmentTests(unittest.TestCase):
    def test_forwards_fault_controls_but_keeps_environment_allowlisted(self):
        source = {
            'ASAN_OPTIONS': 'detect_leaks=1',
            'SPATIAL_TEST_FILE_JOURNAL_FAIL_FINISH_ONCE': '1',
            'SPATIAL_TEST_FILE_JOURNAL_FINISH_NOTIFY': '/tmp/notify',
            'SPATIAL_TEST_FILE_JOURNAL_FINISH_DELAY_MS': '700',
            'SPATIAL_TEST_REVOCATION_COMMIT_GATE': '/tmp/private-commit-gate',
            'UNRELATED_SECRET': 'must-not-pass',
        }
        self.assertEqual(resource_environment(source), {
            'ASAN_OPTIONS': 'detect_leaks=1',
            'SPATIAL_TEST_FILE_JOURNAL_FAIL_FINISH_ONCE': '1',
            'SPATIAL_TEST_FILE_JOURNAL_FINISH_NOTIFY': '/tmp/notify',
            'SPATIAL_TEST_FILE_JOURNAL_FINISH_DELAY_MS': '700',
            'SPATIAL_TEST_REVOCATION_COMMIT_GATE': '/tmp/private-commit-gate',
        })


if __name__ == '__main__':
    unittest.main()
