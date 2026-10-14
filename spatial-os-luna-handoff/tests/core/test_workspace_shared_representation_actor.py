#!/usr/bin/env python3
"""Focused local two-world workspace representation regression."""
import os
import sqlite3
from contextlib import closing
import time
from pathlib import Path
import unittest

import core_pb2 as core
import grants_pb2 as grant
import world_session_pb2 as session
from test_workspace_presentation_actor import pose
from test_world_authority import WorldAuthorityTests
from test_workspace_presentation_actor import WorkspacePresentationActorTests


class SharedWorkspaceRepresentationCase(WorkspacePresentationActorTests):
    @staticmethod
    def file_identity(path):
        details = path.stat()
        return path.read_bytes(), details.st_dev, details.st_ino

    def process_identity(self, token):
        matches = []
        for pid in self.descendants():
            proc = Path(f'/proc/{pid}')
            try:
                argv = (proc / 'cmdline').read_bytes().split(b'\0')
                exe = os.readlink(proc / 'exe')
                if argv[0] != b'/usr/bin/python3' or len(argv) < 3 or argv[1] != b'-c' or token.encode() not in argv[2]:
                    continue
                if not Path(exe).name.startswith('python3'):
                    continue
                stat_fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
                executable = (proc / 'exe').stat()
                namespaces = tuple(os.readlink(proc / 'ns' / name) for name in ('mnt', 'pid', 'user'))
                cgroup = (proc / 'cgroup').read_bytes()
                matches.append((pid, int(stat_fields[19]), exe, executable.st_dev, executable.st_ino,
                                namespaces, cgroup))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
        self.assertEqual(len(matches), 1, f'Expected one exact Python -c payload, found {matches}')
        return matches[0]

    def execution_inventory(self):
        uri = 'file:' + str(self.execution / 'execution.db') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            repositories = tuple(db.execute('SELECT id,path,state FROM repositories ORDER BY id'))
            worktrees = tuple(db.execute('SELECT id,repo,path,state FROM worktrees ORDER BY id'))
            workspaces = tuple(db.execute('SELECT id,worktree,repo FROM workspaces ORDER BY id'))
            processes = tuple(db.execute('SELECT id,workspace,state FROM processes ORDER BY id'))
        return repositories, worktrees, workspaces, processes

    def presentation_row(self, world, representation_id, expect_present=True):
        self.assertEqual(len(representation_id), 16, f'Expected raw 16-byte presentation ID, got {representation_id!r}')
        uri = 'file:' + str(world.state_path / 'world.db') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            row = db.execute('SELECT workspace_id,room_id,pose,generation FROM workspace_presentations WHERE id=?',
                             (representation_id.hex(),)).fetchone()
        if expect_present:
            self.assertIsNotNone(row, f'No persisted row for binary presentation ID {representation_id.hex()} in {world.state_path}')
        return row

    def presentation_state(self, world):
        uri = 'file:' + str(world.state_path / 'world.db') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            return {
                'available': db.execute("SELECT COUNT(*) FROM world_workspaces WHERE id='one'").fetchone()[0],
                'selected': db.execute('SELECT COUNT(*) FROM actor_workspaces').fetchone()[0],
                'presentations': db.execute('SELECT COUNT(*) FROM workspace_presentations').fetchone()[0],
                'operations': db.execute('SELECT COUNT(*) FROM workspace_presentation_ops').fetchone()[0],
            }

    def create_presentation(self, world, peer, x):
        info = world.management('world.info')
        result = peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='one',
            room_id=info['entry_room'], transform=pose(x),
            expected_world_revision=info['world_revision']))
        self.assertTrue(result.HasField('action'), result)
        self.assertTrue(result.action.HasField('presentation'), result)
        return result.action.presentation

    def actor_pixels(self, peer):
        reply = peer.act(observe=core.ActorObserveRequest(include_pixels=True, width=127, height=95))
        self.assertTrue(reply.HasField('action'), reply)
        value = reply.action.observation
        self.assertTrue(value.HasField('image'), 'Native geometry pixels were not returned')
        return value

    def pixel_change(self, peer, before):
        deadline = time.monotonic() + 5
        while True:
            value = self.actor_pixels(peer)
            if value.image.rgb8 != before:
                return value
            self.assertLess(time.monotonic(), deadline, 'Native workspace geometry did not repaint')
            time.sleep(.05)

    def test_native_shared_workspace_representations_keep_one_live_authority(self):
        from test_world_identity import WorldProcess
        self.enroll_second()
        first = self.workspace()
        first_info = first.management('world.info')
        self.world_info = first_info
        self.world_grant_second(first, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE,
            grant.WORLD_OBSERVE, grant.REPRESENTATION_VIEW])
        owner, viewer = first.peers
        self.assertTrue(self.authenticate_second(viewer).HasField('opened'))

        # Keep the second authority headless; this case owns one native display.
        second = WorldAuthorityTests.scoped_world(self, session.REGISTERED_ACTORS, native=False,
                                                  authenticate=False, broker=self.broker)
        second.management('workspace.available', id='one', available=True)
        second_info = second.management('world.info')
        self.assertNotEqual(first_info['world_id'], second_info['world_id'])
        self.world_info = second_info
        second_owner, second_viewer = second.peers
        self.assertTrue(self.authenticate(second_owner).HasField('opened'))
        self.world_grant_second(second, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE,
            grant.WORLD_OBSERVE, grant.REPRESENTATION_VIEW])
        self.assertTrue(self.authenticate_second(second_viewer).HasField('opened'))

        inventory = self.execution_inventory()
        self.assertEqual(len(inventory[0]), 1, inventory)
        self.assertEqual(len(inventory[1]), 1, inventory)
        self.assertEqual(len(inventory[2]), 1, inventory)
        repo_id, repo_path, repo_state = inventory[0][0]
        self.assertTrue(Path(repo_path).is_dir())
        tree_id, tree_repo, tree_path, tree_state = inventory[1][0]
        self.assertEqual(inventory[2], (('one', tree_id, repo_id),))
        self.assertEqual(tree_repo, repo_id)
        self.assertEqual(Path(tree_path).resolve(), self.project.resolve())
        self.assertEqual(repo_state, 'ready')
        self.assertEqual(tree_state, 'ready')

        self.assertEqual(self.presentation_state(first),
            {'available': 1, 'selected': 0, 'presentations': 0, 'operations': 0})
        self.assertEqual(self.presentation_state(second),
            {'available': 1, 'selected': 0, 'presentations': 0, 'operations': 0})
        self.select(owner)
        self.assertEqual(self.presentation_state(first)['selected'], 1)
        self.assertFalse(second_owner.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))
        self.assertEqual(self.execution_inventory(), inventory)

        token = 'm07-native-witness-' + os.urandom(12).hex()
        code = ("from pathlib import Path\nimport time\n" + f"token={token!r}\n"
                "Path('/project/m07-nonce').write_text(token)\nPath('/project/m07-ready').touch()\n"
                "while not Path('/project/m07-release').exists(): time.sleep(.02)\n")
        process = self.launch_process(owner, code, operation='m07-native-' + os.urandom(8).hex())
        self.until(lambda: (self.project / 'm07-ready').exists(), bool)
        nonce = self.project / 'm07-nonce'
        file_before = self.file_identity(nonce)
        pid_before = self.process_identity(token)
        self.assertEqual(file_before[0], token.encode())
        inventory = self.execution_inventory()
        self.assertEqual(len(inventory[3]), 1, inventory)
        self.assertEqual(inventory[3][0][1], 'one')

        pixels_before = self.actor_pixels(owner)
        left = self.create_presentation(first, owner, .8)
        pixels_after = self.pixel_change(owner, pixels_before.image.rgb8)
        self.assertTrue(any(v.representation_id == left.representation_id and not v.locked
                            for v in pixels_after.presentation_views))
        left_row = self.presentation_row(first, left.representation_id)
        self.assertEqual(left_row[:2], ('one', first_info['entry_room']))
        self.assertEqual(left_row[3], 1)
        self.assertEqual(self.execution_inventory(), inventory)
        self.assertEqual(self.process_identity(token), pid_before)

        right = self.create_presentation(second, second_owner, -.8)
        right_row = self.presentation_row(second, right.representation_id)
        self.assertEqual(right_row[:2], ('one', second_info['entry_room']))
        self.assertEqual(right_row[3], 1)
        self.assertEqual(self.execution_inventory(), inventory)
        second_geometry = second_owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        right_view = next(v for v in second_geometry.presentation_views if v.representation_id == right.representation_id)
        self.assertFalse(right_view.locked)

        private_view = second_viewer.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        locked = next(v for v in private_view.presentation_views if v.representation_id == right.representation_id)
        self.assertTrue(locked.locked)
        self.assertEqual({f.name for f in locked.DESCRIPTOR.fields},
            {'representation_id', 'room_id', 'transform', 'generation', 'locked'})
        self.assertNotIn(b'one', locked.SerializeToString())
        self.denied(second_viewer.act(workspace_select=core.WorkspaceIdRequest(id='one')), core.FORBIDDEN)
        self.assertTrue(any(v.locked for v in private_view.presentation_views))
        self.assertNotIn(str(self.project).encode(), private_view.SerializeToString())

        destination = 'm07-native-destination'
        first.management('world.create', entity={'id': destination, 'kind': 'room', 'label': destination})
        move = owner.act(presentation_move=core.PresentationMoveRequest(protocol_version=1,
            operation_id=os.urandom(16), representation_id=left.representation_id,
            room_id=destination, transform=pose(4), expected_representation_generation=1))
        self.assertTrue(move.HasField('action'), move)
        moved = move.action.presentation
        self.assertEqual(moved.generation, 2)
        left_row = self.presentation_row(first, left.representation_id)
        self.assertEqual(left_row[:2], ('one', destination))
        self.assertEqual(left_row[3], 2)
        self.assertEqual(self.presentation_row(second, right.representation_id), right_row)
        self.assertEqual(self.execution_inventory(), inventory)
        self.assertEqual(self.file_identity(self.project / 'm07-nonce'), file_before)
        self.assertEqual(self.process_identity(token), pid_before)

        before_return = self.actor_pixels(owner)
        returned = owner.act(presentation_move=core.PresentationMoveRequest(protocol_version=1,
            operation_id=os.urandom(16), representation_id=left.representation_id,
            room_id=first_info['entry_room'], transform=pose(.2), expected_representation_generation=2))
        self.assertTrue(returned.HasField('action'), returned)
        left = returned.action.presentation
        self.pixel_change(owner, before_return.image.rgb8)
        left_row = self.presentation_row(first, left.representation_id)
        self.assertEqual(left_row[:2], ('one', first_info['entry_room']))
        self.assertEqual(left_row[3], 3)
        self.assertEqual(self.execution_inventory(), inventory)
        self.assertEqual(self.process_identity(token), pid_before)

        geometry_before_toggle = self.actor_pixels(owner)
        first.management('workspace.available', id='one', available=False)
        self.assertEqual(self.presentation_state(first),
            {'available': 0, 'selected': 0, 'presentations': 1, 'operations': 3})
        self.assertEqual(self.presentation_state(second),
            {'available': 1, 'selected': 0, 'presentations': 1, 'operations': 1})
        self.denied(owner.act(workspace_select=core.WorkspaceIdRequest(id='one')), core.UNAVAILABLE)
        self.assertEqual(self.actor_pixels(owner).image.rgb8, geometry_before_toggle.image.rgb8)
        self.assertEqual(self.presentation_row(first, left.representation_id), left_row)
        self.assertEqual(self.execution_inventory(), inventory)
        self.assertEqual(self.process_identity(token), pid_before)
        first.management('workspace.available', id='one', available=True)
        self.assertEqual(self.presentation_state(first)['selected'], 0)
        self.assertEqual(self.actor_pixels(owner).image.rgb8, geometry_before_toggle.image.rgb8)
        self.assertEqual(self.presentation_row(first, left.representation_id), left_row)
        self.assertEqual(self.execution_inventory(), inventory)
        self.assertEqual(self.process_identity(token), pid_before)
        self.select(owner)
        self.assertEqual(self.presentation_state(first)['selected'], 1)
        self.assertEqual(self.execution_inventory(), inventory)

        before_remove = self.actor_pixels(owner)
        removed = owner.act(presentation_remove=core.PresentationRemoveRequest(
            protocol_version=1, operation_id=os.urandom(16),
            representation_id=left.representation_id, expected_representation_generation=left.generation))
        self.assertTrue(removed.HasField('action'), removed)
        self.pixel_change(owner, before_remove.image.rgb8)
        self.assertEqual(self.presentation_state(first),
            {'available': 1, 'selected': 1, 'presentations': 0, 'operations': 4})
        self.assertIsNone(self.presentation_row(first, left.representation_id, expect_present=False))
        self.assertEqual(self.presentation_row(second, right.representation_id), right_row)
        self.assertEqual(self.execution_inventory(), inventory)
        self.assertEqual(self.file_identity(self.project / 'm07-nonce'), file_before)
        self.assertEqual(self.process_identity(token), pid_before)

        second_state, second_config, second_runtime = second.state_path, second.config_path, second.runtime_path
        second.close(); self.worlds.remove(second)
        restarted = WorldProcess(self, second_config, second_state, native=False,
                                 runtime_config=second_runtime, broker=self.broker)
        restarted.state_path, restarted.config_path, restarted.runtime_path = second_state, second_config, second_runtime
        self.worlds.append(restarted); restarted.management('system.hello')
        restored_owner = restarted.peers[0]
        self.assertTrue(self.authenticate(restored_owner).HasField('opened'))
        restored_row = self.presentation_row(restarted, right.representation_id)
        self.assertEqual(restored_row, right_row)
        self.assertEqual(self.presentation_state(restarted),
            {'available': 1, 'selected': 0, 'presentations': 1, 'operations': 1})
        restored_geometry = restored_owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        self.assertTrue(any(v.representation_id == right.representation_id and not v.locked
                            for v in restored_geometry.presentation_views))
        self.assertEqual(self.execution_inventory(), inventory)
        self.assertEqual(self.file_identity(self.project / 'm07-nonce'), file_before)
        self.assertEqual(self.process_identity(token), pid_before)

        (self.project / 'm07-release').touch()
        self.wait_job(restarted, process)
        self.assertEqual((self.project / 'm07-nonce').read_text(), token)

for _name in dir(WorkspacePresentationActorTests):
    if _name.startswith('test_'):
        setattr(SharedWorkspaceRepresentationCase, _name, None)


if __name__ == '__main__':
    suite = unittest.TestSuite([SharedWorkspaceRepresentationCase(
        'test_native_shared_workspace_representations_keep_one_live_authority')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
