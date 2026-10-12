#!/usr/bin/env python3
"""Behavior checks in temporary Git repositories; only file transport is allowed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).with_name('git-checkpoint.py')


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='spatial-checkpoint-test-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / 'checkout'
        self.remote = self.base / 'origin.git'
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL='file')
        for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
                     'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS'):
            self.env.pop(name, None)
        self.command('git', 'init', '--bare', str(self.remote))
        self.command('git', 'init', '-b', 'task', str(self.repo))
        self.git('config', 'user.name', 'David')
        self.git('config', 'user.email', 'david@code.contractors')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'core.hooksPath', str(self.base / 'empty-hooks'))
        self.git('remote', 'add', 'origin', str(self.remote))
        self.write('base.txt', 'base\n')
        self.git('add', '--', 'base.txt')
        self.git('commit', '-m', 'Initial fixture')
        self.head = self.git('rev-parse', 'HEAD')
        self.message = self.base / 'message.txt'
        self.message.write_text('Reviewed checkpoint\n', encoding='utf-8')
        self.sequence = 0

    def command(self, *args, ok=True):
        result = subprocess.run(args, env=self.env, capture_output=True, text=True, encoding='utf-8')
        if ok:
            self.assertEqual(result.returncode, 0, (args, result.stdout, result.stderr))
        return result

    def git(self, *args):
        return self.command('git', '-C', str(self.repo), *args).stdout.strip()

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    def stage(self):
        self.write('reviewed.txt', 'reviewed\n')
        self.git('add', '--', 'reviewed.txt')

    def helper(self, action='commit', *, branch='task', head=None, paths=None, receipt=None):
        self.sequence += 1
        receipt = receipt or self.base / ('receipt-' + str(self.sequence) + '.json')
        args = [sys.executable, str(HELPER), action, '--checkout', str(self.repo),
                '--branch', branch, '--head', head or self.head, '--origin', str(self.remote),
                '--qualification', 'UNQUALIFIED', '--receipt', str(receipt)]
        if action == 'commit':
            args += ['--message-file', str(self.message)]
            for name in paths if paths is not None else ['reviewed.txt']:
                args += ['--path', name]
        result = self.command(*args, ok=False)
        record = json.loads(receipt.read_text()) if receipt.exists() else None
        return result, record

    def assert_refused_before_commit(self, result, record, text):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(text, record['error'])
        self.assertEqual(record['state'], 'PREFLIGHT')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)

    def test_commit_then_verified_local_bare_push(self):
        self.stage()
        tree = self.git('write-tree')
        result, record = self.helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(record['state'], 'LOCAL COMMIT')
        new_head = self.git('rev-parse', 'HEAD')
        self.assertEqual(record['commit'], new_head)
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}'), tree)
        self.assertEqual(self.git('rev-list', '--parents', '-n', '1', 'HEAD').split(), [new_head, self.head])
        result, pushed = self.helper('push', head=new_head)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pushed['state'], 'BOX-GIT VERIFIED')
        self.assertEqual(pushed['remote_commit'], new_head)
        self.assertEqual(self.command('git', '--git-dir', str(self.remote), 'rev-parse', 'refs/heads/task').stdout.strip(), new_head)

    def test_wrong_branch(self):
        self.stage()
        self.assert_refused_before_commit(*self.helper(branch='wrong'), 'Branch differs')

    def test_wrong_head(self):
        self.stage()
        self.assert_refused_before_commit(*self.helper(head='0' * 40), 'HEAD differs')

    def test_origin_mismatch(self):
        self.stage()
        self.git('remote', 'set-url', 'origin', str(self.base / 'wrong-origin.git'))
        self.assert_refused_before_commit(*self.helper(), 'destination differs')

    def test_repository_identity_mismatch(self):
        self.stage()
        for key, value, restore in (('user.name', 'Wrong Name', 'David'),
                                    ('user.email', 'wrong@example.invalid', 'david@code.contractors')):
            with self.subTest(key=key):
                self.git('config', '--local', key, value)
                self.assert_refused_before_commit(*self.helper(), 'Git identity differs')
                self.git('config', '--local', key, restore)

    def test_author_and_committer_environment_overrides_refused(self):
        self.stage()
        for role in ('AUTHOR', 'COMMITTER'):
            with self.subTest(role=role):
                self.env['GIT_' + role + '_NAME'] = 'Unexpected Identity'
                self.env['GIT_' + role + '_EMAIL'] = 'unexpected@example.invalid'
                result, record = self.helper()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(record['state'], 'PREFLIGHT')
                self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)
                self.assertEqual(record['observed_head'], self.head)
                self.assertIn('identity', record['error'].lower())
                self.env.pop('GIT_' + role + '_NAME')
                self.env.pop('GIT_' + role + '_EMAIL')

    def test_extra_staged_path(self):
        self.stage()
        self.write('unreviewed.txt', 'other\n')
        self.git('add', '--', 'unreviewed.txt')
        self.assert_refused_before_commit(*self.helper(), 'Staged files differ')

    def test_missing_or_duplicate_reviewed_path(self):
        self.stage()
        for paths in ([], ['missing.txt'], ['reviewed.txt', 'reviewed.txt']):
            with self.subTest(paths=paths):
                self.assert_refused_before_commit(*self.helper(paths=paths), 'Staged files differ')

    def test_existing_receipt_is_unchanged(self):
        self.stage()
        receipt = self.base / 'existing.json'
        receipt.write_bytes(b'{"preserved": true}\n')
        result, record = self.helper(receipt=receipt)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt.read_bytes(), b'{"preserved": true}\n')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)

    def test_multiple_remote_urls_rejected_before_push(self):
        other = self.base / 'other.git'
        self.command('git', 'init', '--bare', str(other))
        for key in ('remote.origin.url', 'remote.origin.pushurl'):
            with self.subTest(key=key):
                if key.endswith('pushurl'):
                    self.git('config', '--add', key, str(self.remote))
                self.git('config', '--add', key, str(other))
                result, record = self.helper('push')
                self.assert_refused_before_commit(result, record, 'destination differs')
                self.assertEqual(self.command('git', '--git-dir', str(other), 'show-ref', ok=False).returncode, 1)
                self.git('config', '--unset-all', key)
                if key.endswith('.url'):
                    self.git('config', key, str(self.remote))

    def test_rejected_push_retains_local_commit(self):
        self.git('push', 'origin', 'HEAD:refs/heads/task')
        self.git('checkout', '-b', 'other-history')
        self.write('remote.txt', 'remote advance\n')
        self.git('add', '--', 'remote.txt')
        self.git('commit', '-m', 'Remote history')
        remote_head = self.git('rev-parse', 'HEAD')
        self.git('push', 'origin', 'HEAD:refs/heads/task')
        self.git('checkout', 'task')
        self.stage()
        result, committed = self.helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        local_head = committed['commit']
        result, rejected = self.helper('push', head=local_head)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(rejected['state'], 'LOCAL COMMIT')
        self.assertEqual(rejected['commit'], local_head)
        self.assertEqual(rejected['observed_head'], local_head)
        self.assertEqual(self.git('rev-parse', 'HEAD'), local_head)
        self.assertEqual(self.command('git', '--git-dir', str(self.remote), 'rev-parse', 'refs/heads/task').stdout.strip(), remote_head)

    def test_real_pending_merge_refused_before_commit(self):
        self.git('checkout', '-b', 'merge-source')
        self.write('reviewed.txt', 'merge addition\n')
        self.git('add', '--', 'reviewed.txt')
        self.git('commit', '-m', 'Other branch')
        self.git('checkout', 'task')
        self.git('merge', '--no-commit', '--no-ff', 'merge-source')
        self.assert_refused_before_commit(*self.helper(), 'MERGE_HEAD')
        self.assertTrue((self.repo / '.git/MERGE_HEAD').exists())

    def test_pending_sequencer_markers_refused(self):
        self.stage()
        for marker in ('CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer'):
            with self.subTest(marker=marker):
                path = self.repo / '.git' / marker
                if marker.endswith('_HEAD'):
                    path.write_text(self.head + '\n')
                else:
                    path.mkdir()
                self.assert_refused_before_commit(*self.helper(), marker)
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()

    def test_whitespace_refused_before_commit(self):
        self.write('reviewed.txt', 'trailing space \n')
        self.git('add', '--', 'reviewed.txt')
        self.assert_refused_before_commit(*self.helper(), 'git diff exited')

    def test_push_refuses_staged_changes(self):
        self.stage()
        self.assert_refused_before_commit(*self.helper('push'), 'Push requires no staged')


if __name__ == '__main__':
    unittest.main(verbosity=2)
