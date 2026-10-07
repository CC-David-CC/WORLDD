"""Independent Git, process, and storage witnesses for the production broker."""
import os
import io
import broker_pb2 as broker_pb
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from harness.git_fixture import GitRepo
from harness.rpc import Client, RpcError
from harness.wire import make_request, frame, read_message, pb

HOST, BROKER, JOB_PROBE = sys.argv[1:4]
sys.argv[1:4] = []


class WorkspaceGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'runtime'
        self.repo = GitRepo(self.root)
        self.client = None
        self.start()

    def start(self):
        self.client = Client([HOST], self.state, self.root / 'logs', timeout=5)
        self.addCleanup(self.client.stop)
        self.client.call('system.hello')

    def call(self, method, **args):
        return self.client.call(method, args)

    def error(self, code, method, **args):
        with self.assertRaises(RpcError) as caught:
            self.call(method, **args)
        self.assertEqual(caught.exception.code, code)

    def imported(self, ident='repo'):
        return self.call('git.repo.import', id=ident, path=str(self.repo.path))

    def create(self, **extra):
        args = dict(repo='repo', id='work', branch='feature', start='main', operation_id='create')
        args.update(extra)
        return self.call('git.worktree.create', **args)

    def registry(self):
        return sqlite3.connect(self.state / 'execution/execution.db')

    def deny_ready(self):
        with self.registry() as db:
            db.execute("CREATE TRIGGER deny_ready BEFORE UPDATE ON operations WHEN NEW.state='ready' BEGIN SELECT RAISE(FAIL,'witness write failure'); END")

    def allow_ready(self):
        with self.registry() as db:
            db.execute('DROP TRIGGER deny_ready')

    def test_broker_is_a_separate_supervised_executable_and_database_owner(self):
        identities = self.call('runtime.identities')
        broker = identities['workspaced_pid']
        self.assertNotIn(broker, [identities['worldd_pid'], self.client.proc.pid])
        self.assertEqual(Path(f'/proc/{broker}/exe').resolve(), Path(BROKER).resolve())
        self.assertIn(f'PPid:\t{self.client.proc.pid}\n', Path(f'/proc/{broker}/status').read_text())
        database = (self.state / 'execution/execution.db').resolve()
        self.assertTrue(any(fd.resolve() == database for fd in Path(f'/proc/{broker}/fd').iterdir()))
        self.assertFalse(any(fd.resolve() == database for fd in Path(f"/proc/{identities['worldd_pid']}/fd").iterdir()))

    def test_import_is_bare_independent_and_leaves_source_unchanged(self):
        before = self.repo.run('show-ref')
        self.repo.write('uncommitted-secret', 'source only')
        backing = self.imported()
        self.assertEqual(self.repo.run('rev-parse', '--is-bare-repository', cwd=backing['path']), 'true')
        commit = self.repo.head()
        relative = Path('objects') / commit[:2] / commit[2:]
        original = self.repo.path / '.git' / relative
        copied = Path(backing['path']) / relative
        self.assertEqual(original.read_bytes(), copied.read_bytes())
        self.assertNotEqual((original.stat().st_dev, original.stat().st_ino), (copied.stat().st_dev, copied.stat().st_ino))
        work = self.create()
        self.assertFalse(Path(work['path'], 'uncommitted-secret').exists())
        self.assertEqual(self.repo.run('show-ref'), before)
        self.assertEqual((self.repo.path / 'uncommitted-secret').read_text(), 'source only')
        self.assertEqual(self.imported(), backing)

    def test_opaque_ids_never_become_paths(self):
        ident = '../outside/\nproject'
        backing = self.imported(ident)
        work = self.create(repo=ident, id='../workspace/\nname')
        for value, collection in [(backing, 'repositories'), (work, 'worktrees')]:
            self.assertEqual(Path(value['path']).parent, self.state / 'execution' / collection)
        self.assertFalse((self.root / 'outside').exists())
        self.assertEqual(self.repo.branch(work['path']), 'feature')

    def test_invalid_refs_and_start_do_not_create_effects_or_intents(self):
        backing = self.imported()
        before = self.repo.run('worktree', 'list', '--porcelain', cwd=backing['path'])
        for branch in ['../escape', 'bad..name', 'a.lock', '-option', 'bad\x00value']:
            self.error('INVALID_ARGUMENT', 'git.worktree.create', repo='repo', id='bad', branch=branch, start='main', operation_id='bad')
        self.error('INVALID_ARGUMENT', 'git.worktree.create', repo='repo', id='bad', branch='good', start='--help', operation_id='bad')
        self.assertEqual(self.repo.run('worktree', 'list', '--porcelain', cwd=backing['path']), before)
        with self.registry() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 0)

    def test_existing_branch_and_active_branch_are_distinct_errors(self):
        self.imported()
        self.error('ALREADY_EXISTS', 'git.worktree.create', repo='repo', id='main-work', branch='main', start='main', operation_id='main')
        work = self.create()
        self.error('BRANCH_IN_USE', 'git.worktree.create', repo='repo', id='second', branch='feature', start='main', operation_id='second')
        self.assertEqual(self.repo.branch(work['path']), 'feature')

    def test_retry_survives_restart_without_overwriting_uncommitted_files(self):
        self.imported(); work = self.create()
        path = Path(work['path'], 'README.txt'); path.write_text('durable uncommitted edit')
        self.client.stop(crash=True); self.start()
        self.assertEqual(self.create(), work)
        self.assertEqual(path.read_text(), 'durable uncommitted edit')
        self.assertEqual(self.call('git.operation', operation_id='create')['state'], 'ready')
        self.error('OPERATION_CONFLICT', 'git.worktree.create', repo='repo', id='other', branch='other', start='main', operation_id='create')

    def test_status_is_live_and_does_not_refresh_the_index(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        clean = self.call('git.status', worktree='work')
        self.assertEqual(clean, dict(head=self.repo.head(path), branch='feature', clean=True, modified=[], untracked=[]))
        index = Path(self.repo.run('rev-parse', '--path-format=absolute', '--git-path', 'index', cwd=path))
        before = (index.read_bytes(), index.stat().st_mtime_ns)
        (path / 'README.txt').write_text('changed outside the broker\n')
        status = self.call('git.status', worktree='work')
        self.assertEqual(status['modified'], ['README.txt'])
        self.assertFalse(status['clean']); self.assertEqual(status['untracked'], [])
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), before)
        self.assertEqual((path / 'README.txt').read_text(), 'changed outside the broker\n')

    def test_status_preserves_unusual_paths_and_distinguishes_staged_changes(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        tracked = [' two spaces ', 'tab\tname', 'line\nbreak', 'quote"name', 'back\\slash', 'caf\u00e9', '-leading']
        for name in tracked: self.repo.write(name, 'base\n', path)
        self.repo.run('add', '--all', cwd=path); self.repo.run('commit', '-m', 'unusual paths', cwd=path)
        for name in tracked: self.repo.write(name, 'changed\n', path)
        self.repo.run('rm', '--', 'README.txt', cwd=path)
        self.repo.write('staged new', 'added\n', path); self.repo.run('add', '--', 'staged new', cwd=path)
        untracked = ['? looks like a record\n', 'nested/untracked\tfile']
        for name in untracked: self.repo.write(name, 'new\n', path)
        (path / 'outside-link').symlink_to(self.repo.path / 'README.txt')
        status = self.call('git.status', worktree='work')
        self.assertEqual(status['modified'], sorted(tracked + ['README.txt', 'staged new']))
        self.assertEqual(status['untracked'], sorted(untracked + ['outside-link']))
        self.assertFalse(status['clean'])

    def test_status_consumes_rename_source_as_a_path_not_a_record(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        old, new = '? old\nname', 'destination\tname'
        self.repo.run('mv', '--', 'README.txt', old, cwd=path)
        self.repo.run('commit', '-m', 'rename fixture', cwd=path)
        self.repo.run('mv', '--', old, new, cwd=path)
        status = self.call('git.status', worktree='work')
        self.assertEqual(status['modified'], sorted([old, new]))
        self.assertEqual(status['untracked'], []); self.assertFalse(status['clean'])

    def test_status_reports_real_merge_conflicts(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        self.repo.run('switch', '-c', 'other', cwd=path)
        self.repo.write('README.txt', 'other\n', path)
        self.repo.run('commit', '-am', 'other edit', cwd=path)
        self.repo.run('switch', 'feature', cwd=path)
        self.repo.write('README.txt', 'feature\n', path)
        self.repo.run('commit', '-am', 'feature edit', cwd=path)
        self.assertEqual(self.repo.run('merge', 'other', cwd=path, check=False).returncode, 1)
        status = self.call('git.status', worktree='work')
        self.assertEqual(status['modified'], ['README.txt'])
        self.assertEqual(status['untracked'], []); self.assertFalse(status['clean'])
        self.assertIn('<<<<<<<', (path / 'README.txt').read_text())

    def test_status_observes_external_head_branch_detachment_and_unborn_branch(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        self.repo.write('README.txt', 'external commit\n', path)
        self.repo.run('commit', '-am', 'external', cwd=path)
        self.repo.run('switch', '-c', 'external/branch', cwd=path)
        status = self.call('git.status', worktree='work')
        self.assertEqual(status['head'], self.repo.head(path)); self.assertNotEqual(status['head'], work['head'])
        self.assertEqual(status['branch'], 'external/branch'); self.assertTrue(status['clean'])
        self.repo.run('switch', '--detach', cwd=path)
        self.assertEqual(self.call('git.status', worktree='work')['branch'], '')
        self.repo.run('switch', '--orphan', 'new-history', cwd=path)
        status = self.call('git.status', worktree='work')
        self.assertEqual(status['head'], ''); self.assertEqual(status['branch'], 'new-history')
        self.assertTrue(status['clean'])

    def test_status_includes_ignored_data_as_untracked(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        self.repo.write('.gitignore', 'cache/\n*.secret\n', path)
        self.repo.run('add', '--', '.gitignore', cwd=path); self.repo.run('commit', '-m', 'ignore fixture', cwd=path)
        self.repo.write('cache/data', 'valuable cache\n', path); self.repo.write('local.secret', 'private data\n', path)
        status = self.call('git.status', worktree='work')
        self.assertEqual(status['modified'], []); self.assertEqual(status['untracked'], ['cache/', 'local.secret'])
        self.assertFalse(status['clean'])

    def test_status_rejects_non_utf8_paths_without_poisoning_the_channel(self):
        self.imported(); work = self.create()
        name = os.fsencode(work['path']) + b'/non-utf8-\xff'
        with open(name, 'wb') as output: output.write(b'preserve this data')
        self.error('INVALID_ARGUMENT', 'git.status', worktree='work')
        with open(name, 'rb') as source: self.assertEqual(source.read(), b'preserve this data')
        os.unlink(name)
        self.assertTrue(self.call('git.status', worktree='work')['clean'])

    def test_status_rejects_replaced_administrative_links(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        link = path / '.git'; original = link.read_bytes()
        admin = Path(original.decode().removeprefix('gitdir: ').rstrip('\n'))
        canary = self.repo.path / 'README.txt'; before = canary.read_bytes()
        for mode in ['redirect', 'symlink', 'common-redirect', 'common-symlink', 'reverse-link']:
            with self.subTest(mode=mode):
                target = link if mode in ['redirect', 'symlink'] else admin / ('gitdir' if mode == 'reverse-link' else 'commondir')
                previous = target.read_bytes()
                target.unlink()
                if mode == 'symlink': target.symlink_to(self.repo.path / '.git/HEAD')
                elif mode == 'common-symlink': target.symlink_to(self.repo.path / '.git/HEAD')
                elif mode == 'redirect': target.write_text('gitdir: ' + str(self.repo.path / '.git') + '\n')
                else: target.write_text(str(self.repo.path / '.git') + '\n')
                try: self.error('STORAGE_ERROR', 'git.status', worktree='work')
                finally: target.unlink(); target.write_bytes(previous)
                self.assertEqual(canary.read_bytes(), before)
                self.assertTrue(self.call('git.status', worktree='work')['clean'])

    def test_status_requires_a_registered_ready_worktree(self):
        self.error('NOT_FOUND', 'git.status', worktree='absent')
        self.imported(); self.deny_ready()
        self.error('STORAGE_ERROR', 'git.worktree.create', repo='repo', id='work', branch='feature', start='main', operation_id='create')
        self.error('STORAGE_ERROR', 'git.status', worktree='work')

    def test_status_bounds_its_reply_and_keeps_the_channel_usable(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        files = [path / (str(i).zfill(5) + '-' + 'x' * 230) for i in range(4500)]
        for item in files: item.touch()
        self.error('RESOURCE_EXHAUSTED', 'git.status', worktree='work')
        self.assertEqual(len(list(path.iterdir())), len(files) + 2)
        self.assertEqual(self.call('git.operation', operation_id='create')['state'], 'ready')

    def remove(self, **extra):
        args = dict(id='work', operation_id='remove'); args.update(extra)
        return self.call('git.worktree.remove', **args)

    def retirement(self):
        with self.registry() as db:
            return Path(db.execute("SELECT retired FROM removals WHERE id='remove'").fetchone()[0])

    def test_clean_removal_unregisters_git_but_retains_files_and_late_writes(self):
        backing = self.imported(); work = self.create(); path = Path(work['path'])
        inode = (path / 'README.txt').stat().st_ino
        with (path / 'README.txt').open('a') as held:
            self.assertEqual(self.remove(), {'removed': True})
            self.assertFalse(path.exists())
            retained = self.retirement() / 'data/project/README.txt'
            self.assertEqual(retained.stat().st_ino, inode)
            held.write('late edit\n'); held.flush()
            self.assertEqual(retained.read_text(), 'base\nlate edit\n')
        listing = self.repo.run('worktree', 'list', '--porcelain', cwd=backing['path'])
        self.assertEqual(listing.count('worktree '), 1)
        self.assertEqual(self.repo.run('rev-parse', 'feature', cwd=backing['path']), work['head'])
        self.assertTrue((self.retirement() / 'git/index').is_file())
        self.error('NOT_FOUND', 'git.status', worktree='work')
        self.assertEqual(self.call('git.operation', operation_id='remove')['state'], 'ready')

    def test_removal_retries_preserve_new_files_at_the_original_path(self):
        self.imported(); work = self.create(); self.remove()
        path = Path(work['path']); path.mkdir(); (path / 'new-project').write_text('keep')
        self.client.stop(crash=True); self.start()
        self.assertEqual(self.remove(), {'removed': True})
        self.assertEqual((path / 'new-project').read_text(), 'keep')
        self.error('OPERATION_CONFLICT', 'git.worktree.remove', id='other', operation_id='remove')
        self.error('OPERATION_CONFLICT', 'git.worktree.remove', id='work', operation_id='create')
        self.error('OPERATION_CONFLICT', 'git.worktree.create', repo='repo', id='new', branch='new', start='main', operation_id='remove')
        self.assertEqual(list(path.iterdir()), [path / 'new-project'])

    def test_dirty_and_ignored_files_prevent_removal_without_a_journal_intent(self):
        backing = self.imported(); work = self.create(); path = Path(work['path'])
        self.repo.write('.gitignore', '*.secret\n', path)
        self.repo.run('add', '--', '.gitignore', cwd=path); self.repo.run('commit', '-m', 'ignore', cwd=path)
        before = self.repo.run('worktree', 'list', '--porcelain', cwd=backing['path'])
        for name in ['README.txt', 'untracked', 'ignored.secret']:
            with self.subTest(name=name):
                item = path / name; original = item.read_bytes() if item.exists() else None
                item.write_text('valuable data')
                self.error('DIRTY_WORKTREE', 'git.worktree.remove', id='work', operation_id='remove')
                self.assertEqual(item.read_text(), 'valuable data')
                if original is None: item.unlink()
                else: item.write_bytes(original)
        self.assertEqual(self.repo.run('worktree', 'list', '--porcelain', cwd=backing['path']), before)
        with self.registry() as db: self.assertEqual(db.execute('SELECT count(*) FROM removals').fetchone()[0], 0)

    def test_hidden_index_changes_cannot_be_discarded(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        for flag in ['assume-unchanged', 'skip-worktree']:
            with self.subTest(flag=flag):
                self.repo.run('update-index', '--' + flag, '--', 'README.txt', cwd=path)
                (path / 'README.txt').write_text('hidden valuable edit\n')
                self.assertEqual(self.repo.run('status', '--porcelain', cwd=path), '')
                self.error('DIRTY_WORKTREE', 'git.worktree.remove', id='work', operation_id='remove')
                self.assertEqual((path / 'README.txt').read_text(), 'hidden valuable edit\n')
                (path / 'README.txt').write_text('base\n')
                self.repo.run('update-index', '--no-' + flag, '--', 'README.txt', cwd=path)

    def test_locked_worktrees_and_busy_indexes_remain_in_place(self):
        backing = self.imported(); work = self.create(); path = Path(work['path'])
        self.repo.run('worktree', 'lock', '--reason', 'independent owner', str(path), cwd=backing['path'])
        self.error('IN_USE', 'git.worktree.remove', id='work', operation_id='remove')
        self.repo.run('worktree', 'unlock', str(path), cwd=backing['path'])
        lock = Path(self.repo.run('rev-parse', '--absolute-git-dir', cwd=path)) / 'index.lock'
        lock.write_text('other Git process')
        self.error('IN_USE', 'git.worktree.remove', id='work', operation_id='remove')
        self.assertEqual(lock.read_text(), 'other Git process')
        self.assertEqual((path / 'README.txt').read_text(), 'base\n')

    def test_detached_retirement_pins_unreferenced_commits(self):
        backing = self.imported(); work = self.create(); path = Path(work['path'])
        self.repo.run('switch', '--detach', cwd=path)
        self.repo.write('README.txt', 'detached data\n', path); self.repo.run('commit', '-am', 'detached', cwd=path)
        commit = self.repo.head(path); self.remove(); retired = self.retirement()
        pin = 'refs/spatial/retired/' + retired.name
        self.assertEqual(self.repo.run('rev-parse', '--verify', pin, cwd=backing['path']), commit)
        self.assertEqual((retired / 'data/project/README.txt').read_text(), 'detached data\n')
        self.assertEqual(self.repo.run('rev-parse', 'feature', cwd=backing['path']), work['head'])

    def deny_removal_ready(self):
        with self.registry() as db:
            db.execute("CREATE TRIGGER deny_removal_ready BEFORE UPDATE ON removals WHEN NEW.state='ready' BEGIN SELECT RAISE(FAIL,'witness write failure'); END")

    def allow_removal_ready(self):
        with self.registry() as db: db.execute('DROP TRIGGER deny_removal_ready')

    def test_completed_removal_is_reconciled_after_failed_acknowledgment(self):
        self.imported(); work = self.create(); self.deny_removal_ready()
        self.error('STORAGE_ERROR', 'git.worktree.remove', id='work', operation_id='remove')
        retired = self.retirement(); self.assertFalse(Path(work['path']).exists())
        self.assertTrue((retired / 'complete').is_file())
        self.assertEqual(self.call('git.operation', operation_id='remove')['state'], 'pending')
        self.allow_removal_ready(); self.client.stop(crash=True); self.start()
        self.assertEqual(self.remove(), {'removed': True})
        self.assertEqual(self.call('git.operation', operation_id='remove')['state'], 'ready')
        self.assertEqual((retired / 'data/project/README.txt').read_text(), 'base\n')

    def test_unwitnessed_removal_remains_uncertain_and_never_replays(self):
        self.imported(); work = self.create(); self.deny_removal_ready()
        self.error('STORAGE_ERROR', 'git.worktree.remove', id='work', operation_id='remove')
        retired = self.retirement(); (retired / 'complete').unlink()
        original = Path(work['path']); original.mkdir(); (original / 'new-data').write_text('preserved')
        self.allow_removal_ready(); self.client.stop(crash=True); self.start()
        self.assertEqual(self.call('git.operation', operation_id='remove')['state'], 'uncertain')
        self.error('STORAGE_ERROR', 'git.worktree.remove', id='work', operation_id='remove')
        self.assertEqual((original / 'new-data').read_text(), 'preserved')
        self.assertEqual((retired / 'data/project/README.txt').read_text(), 'base\n')

    def test_execution_v1_migration_is_explicit_and_preserves_git_state(self):
        self.imported(); work = self.create(); self.client.stop()
        with self.registry() as db:
            db.execute('DROP TABLE processes'); db.execute('DROP TABLE workspace_members'); db.execute('DROP TABLE workspaces')
            db.execute('DROP TABLE removals'); db.execute('PRAGMA user_version=1')
            before = {name: db.execute('SELECT * FROM ' + name).fetchall() for name in ['repositories', 'worktrees', 'operations']}
        refused = subprocess.run([BROKER, '--state-dir', str(self.state / 'execution')], input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(refused.returncode, 0); self.assertIn(b'--migrate-v1', refused.stderr)
        command = [BROKER, '--state-dir', str(self.state / 'execution'), '--migrate-v1']
        for _ in range(2):
            migrated = subprocess.run(command, input=b'', capture_output=True, timeout=5)
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
        with self.registry() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
            for name, rows in before.items(): self.assertEqual(db.execute('SELECT * FROM ' + name).fetchall(), rows)
        self.start(); self.assertEqual(self.create(), work); self.remove()

    def test_submodule_index_entries_require_explicit_retirement(self):
        self.imported(); work = self.create(); path = Path(work['path'])
        self.repo.run('update-index', '--add', '--cacheinfo', '160000,' + work['head'] + ',submodule', cwd=path)
        (path / 'submodule').mkdir()
        self.repo.run('commit', '-m', 'gitlink fixture', cwd=path)
        self.assertTrue(self.call('git.status', worktree='work')['clean'])
        self.error('IN_USE', 'git.worktree.remove', id='work', operation_id='remove')
        self.assertTrue((path / 'submodule').is_dir())

    def test_partial_retirement_is_preserved_without_automatic_effect_replay(self):
        backing = self.imported(); work = self.create(); path = Path(work['path'])
        admin = Path(self.repo.run('rev-parse', '--absolute-git-dir', cwd=path))
        os.mkfifo(admin / 'unexpected-pipe')
        self.error('STORAGE_ERROR', 'git.worktree.remove', id='work', operation_id='remove')
        retired = self.retirement(); retained = retired / 'staging/project/README.txt'
        self.assertFalse(path.exists()); self.assertEqual(retained.read_text(), 'base\n')
        self.assertFalse((retired / 'complete').exists())
        self.assertEqual(self.call('git.operation', operation_id='remove')['state'], 'pending')
        listing = self.repo.run('worktree', 'list', '--porcelain', cwd=backing['path'])
        self.client.stop(crash=True); self.start()
        self.assertEqual(self.call('git.operation', operation_id='remove')['state'], 'uncertain')
        self.error('STORAGE_ERROR', 'git.worktree.remove', id='work', operation_id='remove')
        self.assertEqual(retained.read_text(), 'base\n')
        self.assertEqual(self.repo.run('worktree', 'list', '--porcelain', cwd=backing['path']), listing)

    def test_recovery_does_not_accept_a_symlink_in_place_of_retained_data(self):
        self.imported(); self.create(); self.deny_removal_ready()
        self.error('STORAGE_ERROR', 'git.worktree.remove', id='work', operation_id='remove')
        retired = self.retirement(); project = retired / 'data/project'; saved = retired / 'data/preserved'
        project.rename(saved); project.symlink_to(self.repo.path, target_is_directory=True)
        self.allow_removal_ready(); self.client.stop(crash=True); self.start()
        self.assertEqual(self.call('git.operation', operation_id='remove')['state'], 'uncertain')
        self.assertEqual((saved / 'README.txt').read_text(), 'base\n')
        self.assertEqual((self.repo.path / 'README.txt').read_text(), 'base\n')

    def test_git_completion_before_failed_registry_ack_is_reconciled(self):
        backing = self.imported(); self.deny_ready()
        self.error('STORAGE_ERROR', 'git.worktree.create', repo='repo', id='work', branch='feature', start='main', operation_id='create')
        listing = self.repo.run('worktree', 'list', '--porcelain', cwd=backing['path'])
        self.assertEqual(listing.count('branch refs/heads/feature'), 1)
        self.assertEqual(self.call('git.operation', operation_id='create')['state'], 'pending')
        self.allow_ready(); self.client.stop(crash=True); self.start()
        recovered = self.create()
        self.assertEqual(self.repo.head(recovered['path']), self.repo.head())
        self.assertEqual(self.call('git.operation', operation_id='create')['state'], 'ready')
        self.assertEqual(self.repo.run('worktree', 'list', '--porcelain', cwd=backing['path']).count('branch refs/heads/feature'), 1)

    def test_ambiguous_effect_is_preserved_and_never_replayed(self):
        backing = self.imported(); self.deny_ready()
        self.error('STORAGE_ERROR', 'git.worktree.create', repo='repo', id='work', branch='feature', start='main', operation_id='create')
        with self.registry() as db:
            path = Path(db.execute("SELECT path FROM worktrees WHERE id='work'").fetchone()[0])
        moved = path.with_name(path.name + '-preserved')
        path.rename(moved)
        self.allow_ready(); self.client.stop(crash=True); self.start()
        self.assertEqual(self.call('git.operation', operation_id='create')['state'], 'uncertain')
        self.error('STORAGE_ERROR', 'git.worktree.create', repo='repo', id='work', branch='feature', start='main', operation_id='create')
        self.assertFalse(path.exists()); self.assertEqual((moved / 'README.txt').read_text(), 'base\n')
        self.assertEqual(self.repo.run('rev-parse', 'feature', cwd=backing['path']), self.repo.head())

    def test_inherited_git_environment_and_source_hooks_cannot_redirect_execution(self):
        self.client.stop()
        trap = self.root / 'trap'; trap.mkdir()
        hook = self.repo.path / '.git/hooks/post-checkout'
        hook.write_text('#!/bin/sh\ntouch ' + str(trap / 'executed') + '\n'); hook.chmod(0o700)
        with patch.dict(os.environ, {'GIT_DIR': str(trap), 'GIT_WORK_TREE': str(trap), 'GIT_CONFIG_COUNT': '1',
                                    'GIT_CONFIG_KEY_0': 'core.hooksPath', 'GIT_CONFIG_VALUE_0': str(hook.parent)}):
            self.start()
        backing = self.imported(); work = self.create()
        self.assertEqual(self.repo.branch(work['path']), 'feature')
        self.assertFalse((trap / 'executed').exists())
        self.assertEqual(list(trap.iterdir()), [])
        self.assertFalse(Path(backing['path'], 'hooks/post-checkout').exists())

    def test_repository_filters_cannot_execute_as_the_broker(self):
        backing = self.imported()
        marker = self.root / 'filter-executed'
        self.repo.run('config', 'filter.attack.smudge', 'touch ' + str(marker), cwd=backing['path'])
        self.error('FORBIDDEN', 'git.worktree.create', repo='repo', id='work', branch='feature', start='main', operation_id='create')
        self.assertFalse(marker.exists())
        with self.registry() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 0)

    def test_unwitnessed_checkout_is_not_mistaken_for_a_completed_effect(self):
        self.imported(); self.deny_ready()
        self.error('STORAGE_ERROR', 'git.worktree.create', repo='repo', id='work', branch='feature', start='main', operation_id='create')
        receipts = list((self.state / 'execution/effects').glob('*.ready'))
        self.assertEqual(len(receipts), 1)
        receipts[0].unlink()
        self.allow_ready(); self.client.stop(crash=True); self.start()
        self.assertEqual(self.call('git.operation', operation_id='create')['state'], 'uncertain')
        self.error('STORAGE_ERROR', 'git.worktree.create', repo='repo', id='work', branch='feature', start='main', operation_id='create')

    def test_nonlocal_source_and_alternate_object_authority_are_rejected(self):
        self.error('INVALID_ARGUMENT', 'git.repo.import', id='url', path='ssh://box-git/unauthorized')
        alternate = self.repo.path / '.git/objects/info/alternates'
        alternate.write_text(str(self.root / 'outside') + '\n')
        self.error('INVALID_ARGUMENT', 'git.repo.import', id='alternate', path=str(self.repo.path))
        with self.registry() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM repositories').fetchone()[0], 0)

    def test_idle_broker_death_invalidates_runtime_then_recovers_git_state(self):
        self.imported(); work = self.create()
        os.kill(self.call('runtime.identities')['workspaced_pid'], signal.SIGKILL)
        self.assertNotEqual(self.client.proc.wait(timeout=5), 0)
        self.start(); self.assertEqual(self.create(), work)

    def test_parent_only_death_releases_broker_and_registry_lock(self):
        self.imported()
        broker = self.call('runtime.identities')['workspaced_pid']
        pidfd = os.pidfd_open(broker)
        try:
            os.kill(self.client.proc.pid, signal.SIGKILL); self.client.proc.wait(timeout=5)
            poller = select.poll(); poller.register(pidfd, select.POLLIN)
            self.assertTrue(poller.poll(4000), 'broker outlived its supervisor')
        finally:
            os.close(pidfd)
        self.start(); self.create()

    def test_duplicate_broker_authority_cannot_open_existing_registry(self):
        backing = self.imported()
        other = subprocess.run([BROKER, '--state-dir', str(self.state / 'execution')], input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(other.returncode, 0)
        self.assertEqual(other.stdout, b'')
        self.assertEqual(self.imported(), backing)

    def test_administrative_git_descendants_stop_when_the_owner_dies(self):
        process = subprocess.Popen([JOB_PROBE, str(self.repo.path)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        fds = []
        try:
            deadline = time.monotonic() + 5
            descendants = set()
            while time.monotonic() < deadline:
                pending = [process.pid]; descendants = set()
                while pending:
                    pid = pending.pop()
                    try: children = [int(n) for n in Path(f'/proc/{pid}/task/{pid}/children').read_text().split()]
                    except FileNotFoundError: children = []
                    for child in children:
                        if child not in descendants: descendants.add(child); pending.append(child)
                names = []
                for pid in descendants:
                    try: names.append(Path(f'/proc/{pid}/comm').read_text().strip())
                    except FileNotFoundError: pass
                if 'sleep' in names: break
                time.sleep(.01)
            self.assertGreaterEqual(len(descendants), 4, 'real Git did not create the supervised nested job')
            fds = [os.pidfd_open(pid) for pid in descendants]
            os.kill(process.pid, signal.SIGKILL); process.wait(timeout=5)
            for fd in fds:
                poller = select.poll(); poller.register(fd, select.POLLIN)
                self.assertTrue(poller.poll(4000), 'Git descendant outlived its owner')
        finally:
            if process.poll() is None: process.kill(); process.wait(timeout=5)
            for fd in fds:
                try: signal.pidfd_send_signal(fd, signal.SIGKILL)
                except ProcessLookupError: pass
                os.close(fd)

    def test_broker_strict_protocol_does_not_expose_world_actions(self):
        requests = []
        for index, method in enumerate(['runtime.identities', 'world.snapshot', 'system.hello']):
            request = broker_pb.Request(protocol_version=1, request_id=index + 1)
            request.management.CopyFrom(make_request(method, {})); request.management.request_id = request.request_id
            if method == 'system.hello': request.management.protocol_version = 99
            requests.append(frame(request))
        requests.append(frame(broker_pb.Request(protocol_version=99, request_id=4)))
        requests.append(frame(make_request('system.hello', {})))
        process = subprocess.Popen([BROKER, '--state-dir', str(self.root / 'independent-execution')],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, errors = process.communicate(b''.join(requests), timeout=5)
        self.assertEqual(process.returncode, 0, errors)
        stream = io.BytesIO(output); replies = [read_message(stream, broker_pb.Reply) for _ in requests]
        self.assertEqual(replies[0].management.identities.workspaced_pid, process.pid)
        self.assertEqual(replies[1].management.error.code, pb.UNIMPLEMENTED)
        self.assertEqual(replies[2].management.error.code, pb.UNSUPPORTED_VERSION)
        self.assertEqual(replies[3].error.code, pb.UNSUPPORTED_VERSION)
        self.assertEqual(replies[4].error.code, pb.INVALID_ARGUMENT)
        self.assertEqual(stream.read(), b'')


if __name__ == '__main__':
    unittest.main()
