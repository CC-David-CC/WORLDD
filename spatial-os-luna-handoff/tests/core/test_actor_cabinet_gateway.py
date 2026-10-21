"""Actor cabinet gateway regressions. Runtime belongs to qualified test hosts."""
import itertools
import os
import stat
import sqlite3
from pathlib import Path
import socket
import threading
from unittest.mock import patch
import time
import unittest
from harness.private_evidence import bounded_tree_files, install_case_evidence
import core_pb2 as core
import file_actions_pb2 as cabinet
import world_session_pb2 as session
import grants_pb2 as grant
import test_workspace_presentation_actor as presentation
from gateway_trace import (FinalWindowBrokerTrace, FINAL_WINDOW_SEQUENCE,
                            record_file_action_attempt, require_create_payload, scoped_check_matches)


class ActorCabinetGatewayTests(presentation.WorkspacePresentationActorTests):
    def setUp(self):
        evidence_parent = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if evidence_parent:
            case_root, restore_environment = install_case_evidence(
                evidence_parent, self._testMethodName)
            self.case_evidence_root = case_root
            # Register restoration before inherited setup can fail or launch children.
            self.addCleanup(restore_environment)
        super().setUp()

    def make_presentation(self, world, peer, x=.8, workspace_id='one'):
        info = world.management('world.info')
        result = peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id=workspace_id,
            room_id=info['entry_room'], transform=presentation.pose(x),
            expected_world_revision=info['world_revision'], kind=core.PRESENTATION_KIND_GENERIC))
        self.assertTrue(result.HasField('action'), result)
        return result.action.presentation

    def make_cabinet(self, world, peer, x=.8, workspace_id='one'):
        info = world.management('world.info')
        result = peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id=workspace_id,
            room_id=info['entry_room'], transform=presentation.pose(x),
            expected_world_revision=info['world_revision'], kind=core.PRESENTATION_KIND_CABINET))
        self.assertTrue(result.HasField('action'), result)
        return result.action.presentation

    def cabinet_request(self, presentation_view, operation_id=None, expected_generation=None, **action):
        return cabinet.ActorCabinetRequest(
            representation_id=presentation_view.representation_id,
            expected_generation=(presentation_view.generation if expected_generation is None else expected_generation),
            operation_id=operation_id or os.urandom(16), **action)

    def private_error_diagnostic(self, operation_id, expected_source):
        evidence_dir = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if not evidence_dir:
            return None
        directory = Path(evidence_dir)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        diagnostic = directory / 'broker-file-action-private.log'
        self.assertTrue(diagnostic.is_file(), diagnostic)
        self.assertEqual(stat.S_IMODE(diagnostic.stat().st_mode), 0o600)
        self.assertLessEqual(diagnostic.stat().st_size, 65536)
        matches = []
        for line in diagnostic.read_text(encoding='ascii').splitlines():
            fields = dict(part.split('=', 1) for part in line.split())
            if fields.get('operation_id') == operation_id.hex():
                matches.append(fields)
        self.assertEqual(len(matches), 1, 'expected one bounded private diagnostic record')
        fields = matches[0]
        self.assertEqual(fields.get('code'), 'STALE_HANDLE')
        if expected_source is not None:
            self.assertEqual(fields.get('source'), expected_source)
        detail = bytes.fromhex(fields['detail_hex']).decode('utf-8')
        self.assertTrue(detail, 'private diagnostic detail is empty')
        return detail

    def bounded_tree_files(self, root, *, logs_only=False, reject_symlinks=False):
        return bounded_tree_files(self, root, logs_only=logs_only,
                                  reject_symlinks=reject_symlinks)

    def assert_private_error_not_leaked(self, operation_id, expected_source, public_reply):
        detail = self.private_error_diagnostic(operation_id, expected_source)
        if detail is None:
            return
        self.assertNotIn('detail', [field.name for field, _ in public_reply.action.cabinet.error.ListFields()])
        markers = (detail.encode(), detail.encode().hex().encode())
        self.assertTrue(all(marker not in public_reply.SerializeToString() for marker in markers))
        fixture_logs = self.bounded_tree_files(self.root, logs_only=True)
        fixture_total = 0
        for path in fixture_logs:
            size = path.stat().st_size
            self.assertLessEqual(size, 4 * 1024 * 1024, path.name)
            fixture_total += size
            self.assertLessEqual(fixture_total, 16 * 1024 * 1024,
                                 f'fixture log scan exceeds byte bound: {path}')
            data = path.read_bytes()
            self.assertTrue(all(marker not in data for marker in markers), path)
        self.assertLessEqual(fixture_total, 16 * 1024 * 1024)
        evidence_dir = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
        evidence_files = self.bounded_tree_files(evidence_dir, reject_symlinks=True)
        private_log = evidence_dir / 'broker-file-action-private.log'
        total = 0
        for path in evidence_files:
            size = path.stat().st_size
            self.assertLessEqual(size, 4 * 1024 * 1024, path)
            total += size
            self.assertLessEqual(total, 16 * 1024 * 1024,
                                 f'private evidence scan exceeds byte bound: {path}')
            if path != private_log:
                data = path.read_bytes()
                self.assertTrue(all(marker not in data for marker in markers), path)
        self.assertLessEqual(total, 16 * 1024 * 1024)

    def test_actor_cabinet_gateway_authorized_browse_read_create_and_retry(self):
        world = self.workspace(empty_initial_commit=True)
        peer = world.peers[0]
        target = self.make_cabinet(world, peer)
        browsed = peer.act(cabinet=self.cabinet_request(target,
            browse=cabinet.FileBrowse(page_size=32)))
        self.assertTrue(browsed.HasField('action'), browsed)
        page = browsed.action.cabinet
        self.assertTrue(page.HasField('directory'), page)
        self.assertEqual(len(page.directory.entries), 0, 'an accessible empty workspace stays empty')
        self.assertEqual(page.operation_id, browsed.action.cabinet.operation_id)

        operation = os.urandom(16)
        create = self.cabinet_request(target, operation,
            create=cabinet.FileCreate(directory_handle=page.directory.handle,
                basename=b'actor-cabinet-created.txt', content=b'actor route\n'))
        created = peer.act(cabinet=create)
        self.assertTrue(created.HasField('action'), created)
        self.assertTrue(created.action.cabinet.HasField('object'), created)
        self.assertEqual((self.project / 'actor-cabinet-created.txt').read_bytes(), b'actor route\n')
        replay = peer.act(cabinet=create)
        self.assertTrue(replay.HasField('action'), replay)
        self.assertEqual(replay.action.cabinet.object, created.action.cabinet.object)
        read = peer.act(cabinet=self.cabinet_request(target,
            read=cabinet.FileRead(file_handle=created.action.cabinet.object.handle,
                                  offset=0, maximum_bytes=64)))
        self.assertTrue(read.HasField('action'), read)
        self.assertEqual(read.action.cabinet.content.data, b'actor route\n')

    def test_actor_cabinet_gateway_create_only_and_selection_does_not_rebind(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner, peer = world.peers
        target = self.make_cabinet(world, owner, workspace_id='one')
        room = target.room_id
        self.world_grant_second(world, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE])
        self.world_grant_second(world, [grant.REPRESENTATION_VIEW], components=[room, target.representation_id])
        self.world_grant_second(world, [grant.REPRESENTATION_USE], components=[room, target.representation_id])
        self.workspace_grant_second_domain(world, 'two', [grant.WORKSPACE_ACCESS, grant.FILE_CREATE])
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        observation = peer.act(observe=core.ActorObserveRequest())
        self.assertTrue(observation.HasField('action'), observation)
        cabinet_view = next(item for item in observation.action.observation.presentation_views
                            if item.representation_id == target.representation_id)
        self.assertTrue(cabinet_view.locked)
        self.assertTrue(cabinet_view.cabinet_locked)
        self.assertEqual(cabinet_view.kind, core.PRESENTATION_KIND_CABINET)
        public_fields = {field.name for field, _ in cabinet_view.ListFields()}
        self.assertFalse({'workspace_id', 'workspace', 'file_name', 'directory', 'branch'} & public_fields)
        world.management('workspace.available', id='two', available=True)
        self.assertTrue(peer.act(workspace_select=core.WorkspaceIdRequest(id='two')).HasField('action'))

        # FILE_CREATE on selected workspace two cannot authorize presentation one.
        denied = peer.act(cabinet=self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=b'', basename=b'wrong-workspace.txt', content=b'x')))
        self.assertTrue(denied.HasField('action'), denied)
        self.assertTrue(denied.action.cabinet.HasField('error'), denied)
        self.assertEqual(denied.action.cabinet.error.code, cabinet.FileActionError.UNAUTHORIZED)
        self.assertFalse((self.project / 'wrong-workspace.txt').exists())

        # Grant only FILE_CREATE to the presentation's actual workspace. No browse,
        # read, or WORKSPACE_ACCESS grant is added, yet create remains authorized.
        self.workspace_grant_second_domain(world, 'one', [grant.FILE_CREATE])
        create_only_view = peer.act(observe=core.ActorObserveRequest())
        self.assertTrue(create_only_view.HasField('action'), create_only_view)
        cabinet_view = next(item for item in create_only_view.action.observation.presentation_views
                            if item.representation_id == target.representation_id)
        self.assertEqual(cabinet_view.kind, core.PRESENTATION_KIND_CABINET)
        self.assertTrue(cabinet_view.locked, 'generic M07 lock stays tied to WORKSPACE_ACCESS')
        self.assertFalse(cabinet_view.cabinet_locked, 'create-only effective file authority unlocks cabinet status')
        created = peer.act(cabinet=self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=b'', basename=b'create-only.txt', content=b'yes')))
        self.assertTrue(created.HasField('action'), created)
        self.assertTrue(created.action.cabinet.HasField('object'), created)
        self.assertEqual((self.project / 'create-only.txt').read_bytes(), b'yes')
        no_browse = peer.act(cabinet=self.cabinet_request(target,
            browse=cabinet.FileBrowse(page_size=8)))
        self.assertTrue(no_browse.action.cabinet.HasField('error'), no_browse)
        self.assertEqual(no_browse.action.cabinet.error.code, cabinet.FileActionError.UNAUTHORIZED)

    def test_actor_cabinet_gateway_rejects_generic_presentation_target(self):
        world = self.workspace()
        peer = world.peers[0]
        generic = self.make_presentation(world, peer)
        result = peer.act(cabinet=self.cabinet_request(generic, browse=cabinet.FileBrowse(page_size=8)))
        self.assertTrue(result.HasField('error'), result)
        self.assertEqual(result.error.code, core.NOT_VISIBLE)

    def test_cabinet_view_move_and_use_authority_are_separate(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner, peer = world.peers
        target = self.make_cabinet(world, owner)
        room = target.room_id
        self.world_grant_second(world, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE])
        self.world_grant_second(world, [grant.REPRESENTATION_VIEW], components=[room, target.representation_id])
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        observation = peer.act(observe=core.ActorObserveRequest())
        self.assertTrue(observation.HasField('action'), observation)
        cabinet_view = next(item for item in observation.action.observation.presentation_views
                            if item.representation_id == target.representation_id)
        self.assertTrue(cabinet_view.cabinet_locked)
        denied_use = peer.act(cabinet=self.cabinet_request(target, browse=cabinet.FileBrowse(page_size=8)))
        self.assertTrue(denied_use.HasField('error'), denied_use)
        self.assertEqual(denied_use.error.code, core.FORBIDDEN)
        self.world_grant_second(world, [grant.REPRESENTATION_MOVE], components=[room, target.representation_id])
        moved = peer.act(presentation_move=core.PresentationMoveRequest(
            protocol_version=1, operation_id=os.urandom(16), representation_id=target.representation_id,
            room_id=room, transform=presentation.pose(1.2),
            expected_representation_generation=target.generation))
        self.assertTrue(moved.HasField('action'), moved)
        self.assertEqual(moved.action.presentation.kind, core.PRESENTATION_KIND_CABINET)
        self.assertTrue(moved.action.presentation.cabinet_locked)

    def test_cabinet_placement_requires_effective_file_authority(self):
        world = self.workspace(policy=['workspace.select'])
        peer = world.peers[0]
        info = world.management('world.info')
        request = lambda kind: core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='one',
            room_id=info['entry_room'], transform=presentation.pose(.8),
            expected_world_revision=world.management('world.info')['world_revision'], kind=kind)
        denied = peer.act(presentation_create=request(core.PRESENTATION_KIND_CABINET))
        self.assertTrue(denied.HasField('error'), denied)
        self.assertEqual(denied.error.code, core.FORBIDDEN)
        generic = peer.act(presentation_create=request(core.PRESENTATION_KIND_GENERIC))
        self.assertTrue(generic.HasField('action'), generic)
        self.assertEqual(generic.action.presentation.kind, core.PRESENTATION_KIND_GENERIC)

    def test_actor_cabinet_gateway_workspace_policy_denies_exact_right(self):
        world = self.workspace(policy=['workspace.select', 'files.read'])
        peer = world.peers[0]
        target = self.make_cabinet(world, peer)
        operation = os.urandom(16)
        denied = peer.act(cabinet=self.cabinet_request(target, operation,
            create=cabinet.FileCreate(directory_handle=b'', basename=b'policy-denied.txt', content=b'x')))
        self.assertTrue(denied.HasField('action'), denied)
        self.assertTrue(denied.action.cabinet.HasField('error'), denied)
        self.assertEqual(denied.action.cabinet.error.code, cabinet.FileActionError.UNAUTHORIZED)
        self.assertFalse((self.project / 'policy-denied.txt').exists())
        journal = self.execution / 'file-operations' / 'file-operations.db'
        if journal.exists():
            with sqlite3.connect(f'file:{journal}?mode=ro', uri=True) as db:
                row = db.execute('SELECT state FROM file_operations WHERE workspace=? AND operation=?',
                                 ('one', operation)).fetchone()
            self.assertIsNone(row, 'policy denial must happen before durable file-operation dispatch')

    def test_actor_cabinet_gateway_policy_denial_stays_unauthorized_after_use_revocation(self):
        self.enroll_second()
        race = {}
        relay, release_broker_feed = self.world_grant_race(race)
        world = self.workspace(policy=['workspace.select', 'files.read'], issuer_port=relay.port)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_ancestor_revocable_use_actor(world, target, race)
        operation = os.urandom(16)
        path = self.project / 'policy-denied-during-revocation.txt'
        original, held, release, error_codes, barrier_errors = self.hold_broker_file_error(operation)
        result_box, worker_errors = [], []

        def create():
            try:
                result_box.append(peer.act(cabinet=self.cabinet_request(target, operation,
                    create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(), content=b'x'))))
            except BaseException as error:
                worker_errors.append(error)

        worker = threading.Thread(target=create)
        worker_started = False
        try:
            worker.start()
            worker_started = True
            self.assertTrue(held.wait(5), 'broker did not return the workspace-policy denial')
            self.assertEqual(error_codes, [cabinet.FileActionError.UNAUTHORIZED], barrier_errors)
            self.revoke_world_grant_ancestor(race['ancestor'])
            self.assertTrue(self.world_watch_relay.delivered.wait(.5),
                            'representation-use revocation did not reach worldd')
            release_broker_feed.set()
            time.sleep(.08)
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive(), 'policy-denied request did not finish after feed release')
        finally:
            release_broker_feed.set()
            release.set()
            self.broker.exchange = original
            if worker_started:
                self.stop_actor_worker(peer, worker, (release_broker_feed, release))
        self.assertFalse(worker_errors, worker_errors)
        self.assertFalse(barrier_errors, barrier_errors)
        self.assertEqual(len(result_box), 1)
        result = result_box[0]
        self.assertTrue(result.HasField('action'), result)
        failure = result.action.cabinet
        self.assertTrue(failure.HasField('error'), failure)
        self.assertEqual(failure.error.code, cabinet.FileActionError.UNAUTHORIZED)
        self.assertEqual(failure.operation_id, operation)
        self.assertFalse(path.exists())
        journal = self.execution / 'file-operations' / 'file-operations.db'
        if journal.exists():
            with sqlite3.connect(f'file:{journal}?mode=ro', uri=True) as db:
                row = db.execute('SELECT state FROM file_operations WHERE workspace=? AND operation=?',
                                 ('one', operation)).fetchone()
            self.assertIsNone(row, 'policy denial must remain before durable file-operation dispatch')
        self.assertFalse(relay.errors)
        self.assertFalse(self.world_watch_relay.errors)

    def test_actor_cabinet_gateway_hidden_use_stale_and_reach_guards(self):
        self.enroll_second()
        world = self.workspace()
        owner, peer = world.peers
        target = self.make_cabinet(world, owner)
        room = target.room_id
        self.world_grant_second(world, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE])
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        hidden = peer.act(cabinet=self.cabinet_request(target,
            browse=cabinet.FileBrowse(page_size=8)))
        self.assertTrue(hidden.HasField('error'), hidden)
        self.assertEqual(hidden.error.code, core.FORBIDDEN)
        self.assertNotIn('tree-one', hidden.error.message)

        self.world_grant_second(world, [grant.REPRESENTATION_VIEW], components=[room, target.representation_id])
        no_use = peer.act(cabinet=self.cabinet_request(target,
            browse=cabinet.FileBrowse(page_size=8)))
        self.assertTrue(no_use.HasField('error'), no_use)
        self.assertEqual(no_use.error.code, core.FORBIDDEN)

        stale = owner.act(cabinet=self.cabinet_request(target, expected_generation=target.generation + 1,
            browse=cabinet.FileBrowse(page_size=8)))
        self.assertTrue(stale.HasField('error'), stale)
        self.assertEqual(stale.error.code, core.STALE_TARGET)

        far = self.make_cabinet(world, owner, x=40)
        out_of_reach = owner.act(cabinet=self.cabinet_request(far,
            browse=cabinet.FileBrowse(page_size=8)))
        self.assertTrue(out_of_reach.HasField('error'), out_of_reach)
        self.assertEqual(out_of_reach.error.code, core.OUT_OF_REACH)

    def test_actor_cabinet_gateway_private_stale_error_diagnostic(self):
        self.assertTrue(os.environ.get('SPATIAL_SECURITY_EVIDENCE'),
                        'diagnostic regression requires a private evidence directory')
        world = self.workspace()
        peer = world.peers[0]
        target = self.make_cabinet(world, peer)
        binding = self.broker.call('workspace.get', id='one')
        worktree_id = binding['worktree']
        expected_root = self.project
        process_root = Path(f'/proc/{self.broker.pid}/root') / str(expected_root).lstrip('/')
        self.assertEqual(expected_root.parent, Path(self.execution) / 'worktrees',
                         f'workspace path escaped its managed root: state={self.execution} '
                         f'worktree={worktree_id} project={self.project}')
        for label, candidate in (('host', expected_root), ('workspaced', process_root)):
            try:
                mode = candidate.lstat().st_mode
            except OSError as error:
                self.fail(f'{label} registered root is missing: candidate={candidate} error={error!r}; '
                          f'workspaced_pid={self.broker.pid} state={self.execution} worktree={worktree_id}')
            self.assertTrue(stat.S_ISDIR(mode) and not stat.S_ISLNK(mode),
                            f'{label} registered root is unsafe: candidate={candidate} '
                            f'mode={oct(mode)} workspaced_pid={self.broker.pid}')
        request = self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=os.urandom(16),
                basename=b'diagnostic-control.txt', content=b'never written'))
        reply = peer.act(cabinet=request)
        self.assertTrue(reply.HasField('action'), reply)
        self.assertTrue(reply.action.cabinet.HasField('error'), reply)
        self.assertEqual(reply.action.cabinet.error.code, cabinet.FileActionError.STALE_HANDLE)
        self.assertFalse((self.project / 'diagnostic-control.txt').exists())
        self.assert_private_error_not_leaked(
            request.operation_id, 'file_engine.handle_missing', reply)

    def test_actor_cabinet_gateway_uses_authenticated_principal_not_presentation_owner(self):
        self.enroll_second()
        world = self.workspace()
        owner, peer = world.peers
        target = self.make_cabinet(world, owner)
        room = target.room_id
        self.world_grant_second(world, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE])
        self.world_grant_second(world, [grant.REPRESENTATION_VIEW], components=[room, target.representation_id])
        self.world_grant_second(world, [grant.REPRESENTATION_USE], components=[room, target.representation_id])
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))

        owner_path = self.project / 'principal-control.txt'
        owner_request = self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=b'', basename=owner_path.name.encode(),
                                      content=b'owner authorized'))
        owner_result = owner.act(cabinet=owner_request)
        self.assertTrue(owner_result.HasField('action'), owner_result)
        if owner_result.action.cabinet.HasField('error'):
            self.assert_private_error_not_leaked(
                owner_request.operation_id, None, owner_result)
        self.assertTrue(owner_result.action.cabinet.HasField('object'), owner_result)
        self.assertEqual(owner_path.read_bytes(), b'owner authorized')

        denied_path = self.project / 'owner-right-must-not-transfer.txt'
        denied = peer.act(cabinet=self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=b'', basename=denied_path.name.encode(),
                                      content=b'wrong principal')))
        self.assertTrue(denied.HasField('action'), denied)
        self.assertTrue(denied.action.cabinet.HasField('error'), denied)
        self.assertEqual(denied.action.cabinet.error.code, cabinet.FileActionError.UNAUTHORIZED)
        self.assertFalse(denied_path.exists())

    def test_actor_cabinet_gateway_rejects_wrong_registered_workspace_binding(self):
        world = self.workspace()
        peer = world.peers[0]
        target = self.make_cabinet(world, peer)
        original_exchange = self.broker.exchange
        principal = self.principal()
        worktree = self.registered_worktree_component('one')

        def run_create(path, corrupt_dispatch_binding):
            state = {'stage': 0, 'binding': None, 'stamps': [],
                     'dispatch_seen': False, 'file_action_attempts': 0, 'file_action_seen': False,
                     'wrong_binding': False, 'trace': []}

            def check_request(request, right, expected_allowed, label):
                self.assertTrue(request.HasField('scoped_authority_check'), request)
                check = request.scoped_authority_check
                self.assertTrue(scoped_check_matches(check, 'one', principal, right,
                    [worktree] if right != grant.WORKSPACE_ACCESS else []), (label, check))
                response = original_exchange(request)
                self.assertTrue(response.HasField('scoped_authority_check'), response)
                decision = response.scoped_authority_check
                self.assertEqual(decision.allowed, expected_allowed, (label, decision))
                self.assertEqual(len(decision.incarnation), 16, (label, decision))
                self.assertGreater(decision.epoch, 0, (label, decision))
                state['stamps'].append((decision.incarnation, decision.epoch))
                state['trace'].append(label)
                state['stage'] += 1
                return response

            def workspace_reply(request, label):
                self.assertTrue(request.HasField('management'), request)
                self.assertTrue(request.management.HasField('workspace_get'), request)
                self.assertEqual(request.management.workspace_get.id, 'one', request)
                response = original_exchange(request)
                self.assertTrue(response.HasField('management'), response)
                self.assertTrue(response.management.HasField('workspace'), response)
                binding = response.management.workspace
                self.assertEqual(binding.id, 'one', binding)
                self.assertEqual(binding.worktree.encode('utf-8'), worktree, binding)
                if state['binding'] is None:
                    state['binding'] = (binding.id, binding.worktree, binding.revision)
                else:
                    self.assertEqual((binding.id, binding.worktree, binding.revision), state['binding'], binding)
                state['trace'].append(label)
                state['stage'] += 1
                if label == 'dispatch_binding':
                    state['dispatch_seen'] = True
                    if corrupt_dispatch_binding:
                        response.management.workspace.id = 'different-workspace'
                        state['wrong_binding'] = True
                return response

            control_operation = os.urandom(16)

            def exchange_with_phase_checked_binding(request):
                record_file_action_attempt(request, state)
                if state['stage'] == 0:
                    return check_request(request, grant.WORKSPACE_ACCESS, True, 'projection_workspace_access')
                if state['stage'] == 1:
                    return workspace_reply(request, 'projection_binding')
                if state['stage'] == 2:
                    return check_request(request, grant.FILE_BROWSE, True, 'projection_browse_allowed')
                if state['stage'] == 3:
                    return workspace_reply(request, 'projection_binding_confirmation')
                if state['stage'] in (4, 5):
                    label = ('projection_workspace_access_render_before' if state['stage'] == 4
                             else 'projection_workspace_access_render_after')
                    return check_request(request, grant.WORKSPACE_ACCESS, True, label)
                if state['stage'] == 6:
                    self.assertTrue(request.HasField('authorization_fence'), request)
                    self.assertEqual(request.authorization_fence.principal, principal, request)
                    response = original_exchange(request)
                    self.assertTrue(response.HasField('authorization_fence'), response)
                    fence = response.authorization_fence
                    self.assertTrue(all(stamp == (fence.incarnation, fence.epoch)
                                        for stamp in state['stamps']), (state['stamps'], fence))
                    state['trace'].append('projection_observation_fence')
                    state['stage'] += 1
                    return response
                if state['stage'] == 7:
                    return workspace_reply(request, 'dispatch_binding')
                if state['stage'] == 8 and not corrupt_dispatch_binding:
                    self.assertTrue(request.HasField('management'), request)
                    self.assertTrue(request.management.HasField('workspace_get'), request)
                    self.assertEqual(request.management.workspace_get.id, 'one', request)
                    response = original_exchange(request)
                    self.assertTrue(response.HasField('management'), response)
                    self.assertTrue(response.management.HasField('workspace'), response)
                    binding = response.management.workspace
                    self.assertEqual((binding.id, binding.worktree, binding.revision), state['binding'], binding)
                    state['trace'].append('dispatch_binding_confirmation')
                    state['stage'] += 1
                    return response
                if state['stage'] >= 9:
                    if request.HasField('file_action'):
                        self.assertEqual(request.file_action.workspace_id, 'one', request)
                        self.assertEqual(request.file_action.operation_id, control_operation, request)
                        self.assertEqual(request.file_action.principal, principal, request)
                        require_create_payload(request, basename=path.name.encode(), content=b'x')
                        state['file_action_seen'] = True
                    return original_exchange(request)
                self.fail(f'unexpected broker request in projection/dispatch phase {state["stage"]}: {request!r}')

            self.broker.exchange = exchange_with_phase_checked_binding
            try:
                result = peer.act(cabinet=self.cabinet_request(target, control_operation,
                    create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(), content=b'x')))
            finally:
                self.broker.exchange = original_exchange
            return result, state

        control_path = self.project / 'authorized-binding-control.txt'
        control, control_state = run_create(control_path, False)
        self.assertTrue(control_state['dispatch_seen'], control_state['trace'])
        self.assertTrue(control_state['file_action_seen'], control_state['trace'])
        self.assertEqual(control_state['file_action_attempts'], 1, control_state)
        self.assertEqual(control_state['trace'], [
            'projection_workspace_access', 'projection_binding', 'projection_browse_allowed',
            'projection_binding_confirmation', 'projection_workspace_access_render_before',
            'projection_workspace_access_render_after', 'projection_observation_fence',
            'dispatch_binding', 'dispatch_binding_confirmation'])
        self.assertTrue(control.HasField('action'), control)
        self.assertTrue(control.action.cabinet.HasField('object'), control)
        self.assertEqual(control_path.read_bytes(), b'x')

        path = self.project / 'wrong-binding-must-not-write.txt'
        denied, injected = run_create(path, True)
        self.assertTrue(injected['wrong_binding'], injected['trace'])
        self.assertTrue(injected['dispatch_seen'], injected['trace'])
        self.assertFalse(injected['file_action_seen'], 'gateway dispatched after a mismatched dispatch binding')
        self.assertEqual(injected['file_action_attempts'], 0, injected)
        self.assertEqual(injected['trace'], [
            'projection_workspace_access', 'projection_binding', 'projection_browse_allowed',
            'projection_binding_confirmation', 'projection_workspace_access_render_before',
            'projection_workspace_access_render_after', 'projection_observation_fence',
            'dispatch_binding'])
        self.assertTrue(denied.HasField('error'), denied)
        self.assertEqual(denied.error.code, core.STALE_TARGET)
        self.assertFalse(path.exists())

    def test_actor_cabinet_gateway_rejects_actor_movement_during_authority_wait(self):
        world = self.workspace()
        peer = world.peers[0]
        target = self.make_cabinet(world, peer)
        path = self.project / 'movement-during-wait-must-not-write.txt'
        initial_reply = peer.call(inspect=session.WorldSessionInspect())
        self.assertTrue(initial_reply.HasField('state'), initial_reply)
        initial_pose = initial_reply.state.pose
        target_position = target.transform.position

        def distance_from_target(actor_pose):
            dx = actor_pose.position.x - target_position.x
            dy = actor_pose.position.y - target_position.y
            dz = actor_pose.position.z - target_position.z
            return (dx * dx + dy * dy + dz * dz) ** .5

        initial_distance = distance_from_target(initial_pose)
        self.assertLess(initial_distance, 3.0, 'fixture target must start within configured interaction reach')
        # Queue motion before the cabinet request. Its first broker wait is the
        # workspace-scoped authority check. Do not issue another request through
        # this SessionPeer while its worker is active.
        moving = peer.act(input=core.ActorInputRequest(axes=[-1, 0, 0, 0, 0, 0]))
        self.assertTrue(moving.HasField('action'), moving)
        operation = os.urandom(16)

        original_exchange = self.broker.exchange
        authority_wait_started = threading.Event()
        release_authority = threading.Event()
        injected = {'authority_wait': False, 'file_action': False}
        result_box, worker_errors = [], []

        def exchange_with_authority_barrier(request):
            if request.HasField('file_action'):
                injected['file_action'] = True
            if (request.HasField('scoped_authority_check') and
                    request.scoped_authority_check.workspace == 'one' and not injected['authority_wait']):
                injected['authority_wait'] = True
                authority_wait_started.set()
                if not release_authority.wait(5):
                    raise AssertionError('bounded workspace authority barrier was not released')
            return original_exchange(request)

        def create():
            try:
                result_box.append(peer.act(cabinet=self.cabinet_request(target, operation_id=operation,
                    create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(), content=b'x'))))
            except BaseException as error:
                worker_errors.append(error)

        worker = threading.Thread(target=create)
        worker_started = False
        self.broker.exchange = exchange_with_authority_barrier
        try:
            worker.start()
            worker_started = True
            self.assertTrue(authority_wait_started.wait(3), 'gateway did not reach its workspace-scoped authority check')
            # BrokerClient pumps actor ticks while waiting. The conformance input
            # timeout is 10 seconds here; 2.7 seconds of ticks moves the actor beyond
            # the target's three metre reach while the authority barrier remains held.
            time.sleep(2.7)
        finally:
            release_authority.set()
            self.broker.exchange = original_exchange
            if worker_started:
                worker.join(2)
                self.stop_actor_worker(peer, worker, (release_authority,))
        self.assertFalse(worker_errors, worker_errors)
        self.assertEqual(len(result_box), 1)
        final_reply = peer.call(inspect=session.WorldSessionInspect())
        self.assertTrue(final_reply.HasField('state'), final_reply)
        final_distance = distance_from_target(final_reply.state.pose)
        self.assertGreater(final_distance, 3.0,
                           'authoritative actor pose must move beyond configured reach during the held wait')
        self.assertGreater(final_distance, initial_distance,
                           'authoritative actor pose must move away from the cabinet target')
        self.assertTrue(injected['authority_wait'])
        self.assertFalse(injected['file_action'], 'gateway dispatched after actor movement changed its context')
        self.assertTrue(result_box[0].HasField('error'), result_box[0])
        self.assertEqual(result_box[0].error.code, core.STALE_TARGET)
        self.assertFalse(path.exists())
        journal = self.execution / 'file-operations' / 'file-operations.db'
        if journal.exists():
            with sqlite3.connect(f'file:{journal}?mode=ro', uri=True) as db:
                row = db.execute('SELECT state FROM file_operations WHERE workspace=? AND operation=?',
                                 ('one', operation)).fetchone()
            self.assertIsNone(row, 'stale actor context must not create a file-operation journal row')

    def test_actor_cabinet_gateway_revocation_after_effect_suppresses_metadata(self):
        self.enroll_second()
        race = {}
        relay, release_broker_feed = self.world_grant_race(race)
        marker = self.root / 'gateway-after-effect-finish.marker'
        env = {'SPATIAL_TEST_FILE_JOURNAL_FINISH_NOTIFY': str(marker),
               'SPATIAL_TEST_FILE_JOURNAL_FINISH_DELAY_MS': '700'}
        with patch.dict(os.environ, env):
            world = self.workspace(issuer_port=relay.port)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_ancestor_revocable_use_actor(world, target, race)

        operation = os.urandom(16)
        path = self.project / 'revoked-after-effect.bin'
        result_box, errors = [], []
        def create():
            try:
                result_box.append(peer.act(cabinet=self.cabinet_request(target, operation,
                    create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(),
                                               content=b'x' * 900000))))
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=create)
        worker_started = False
        try:
            worker.start()
            worker_started = True
            deadline = time.monotonic() + 5
            while not marker.exists() and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(.002)
            self.assertTrue(marker.exists(),
                f'file journal finish boundary was not reached; row_state={self.file_operation_state(operation)!r}; '
                f'path_exists={path.exists()} thread_alive={worker.is_alive()} errors={errors!r}')
            self.assertTrue(path.is_file(), 'journal finish boundary must follow the filesystem effect')
            self.assertEqual(path.read_bytes(), b'x' * 900000)

            phase_seen = threading.Event()
            previous_reply_hook = relay.before_reply
            def check_terminal_phase(request, response):
                if request.HasField('actor_status'):
                    self.assertIn(self.second.actor_id, request.actor_status.actor_ids,
                                  'issuer status request did not include the cabinet principal')
                    self.assert_completed_file_operation(operation)
                    phase_seen.set()
                if previous_reply_hook:
                    previous_reply_hook(request, response)
            relay.before_reply = check_terminal_phase
            relay.trigger_after_status = 1
            relay.armed.set()
            self.assertTrue(relay.triggered.wait(3), 'post-effect issuer authority phase was not reached')
            self.assertTrue(phase_seen.wait(3), 'terminal status reply did not confirm principal and journal phase')
            self.assertTrue(self.world_watch_relay.delivered.wait(.5), 'world revocation feed was not forwarded')
            release_broker_feed.set()
            worker.join(5)
            self.assertFalse(worker.is_alive(), 'cabinet actor call did not finish after feed release')
            self.assertFalse(errors, errors)
            self.assertEqual(len(result_box), 1)
            result = result_box[0]
            self.assertTrue(result.HasField('action'), result)
            cabinet_result = result.action.cabinet
            self.assertTrue(cabinet_result.HasField('error'), cabinet_result)
            self.assertEqual(cabinet_result.error.code, cabinet.FileActionError.UNCERTAIN)
            self.assertEqual(cabinet_result.operation_id, operation)
            self.assertFalse(cabinet_result.HasField('object'))
            self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'),
                            'ancestor revocation must preserve the authenticated actor session')
            self.assertFalse(relay.errors)
            self.assertFalse(self.world_watch_relay.errors)
        finally:
            if worker_started:
                self.stop_actor_worker(peer, worker, (release_broker_feed,))
            else:
                release_broker_feed.set()

    def registered_worktree_component(self, workspace_id):
        binding = self.broker.call('workspace.get', id=workspace_id)
        self.assertEqual(binding['id'], workspace_id)
        self.assertTrue(binding['worktree'])
        return binding['worktree'].encode('utf-8')

    def prepare_ancestor_revocable_use_actor(self, world, target, race):
        race['ancestor'] = self.world_grant_second_via_ancestor(world)
        independent = [grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE,
                       grant.WORLD_MANIPULATE, grant.WORLD_PLACEMENT_CREATE,
                       grant.WORLD_PLACEMENT_REMOVE]
        self.world_grant_second(world, independent)
        self.world_grant_second(world, [grant.REPRESENTATION_VIEW],
                                components=[target.room_id, target.representation_id])
        self.workspace_grant_second_domain(world, 'one', [grant.FILE_CREATE])
        peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        return peer

    def file_operation_record(self, operation):
        journal = self.execution / 'file-operations' / 'file-operations.db'
        if not journal.exists():
            return None
        try:
            with sqlite3.connect(f'file:{journal}?mode=ro', uri=True) as db:
                row = db.execute('SELECT state,outcome FROM file_operations WHERE workspace=? AND operation=?',
                                 ('one', operation)).fetchone()
            if row is None:
                return None
            outcome = cabinet.FileActionReply.FromString(bytes(row[1])) if row[1] else None
            return row[0], outcome
        except sqlite3.Error:
            return None

    def file_operation_state(self, operation):
        record = self.file_operation_record(operation)
        return None if record is None else record[0]

    def assert_completed_file_operation(self, operation):
        record = self.file_operation_record(operation)
        self.assertIsNotNone(record, 'file operation journal row is missing')
        state, outcome = record
        self.assertEqual(state, 2, 'complete journal state must be INTEGER 2')
        self.assertIsNotNone(outcome, 'complete journal row must retain its typed outcome')
        self.assertEqual(outcome.operation_id, operation)
        self.assertTrue(outcome.HasField('object'), outcome)
        return outcome

    def stop_actor_worker(self, peer, worker, release_events=()):
        for event in release_events:
            event.set()
        if worker.is_alive():
            try:
                peer.stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            peer.close()
        worker.join(5)
        self.assertFalse(worker.is_alive(), 'actor worker remained alive after bounded cleanup')

    def grant_file_scope_to_second(self, world, workspace_id, rights, components):
        peer = world.peers[0]
        response = peer.call(grants=session.WorldGrantList(workspace=workspace_id))
        self.assertTrue(response.HasField('grants'), response)
        parent = next(record for record in response.grants.grants if not record.parent_id)
        terms = super().workspace_terms(claims=self.second, rights=rights, depth=0)
        terms.scope.domain.id = workspace_id
        terms.scope.components.extend(components)
        peer.action_sequence += 1
        delegated = peer.call(delegate=session.WorldGrantDelegate(workspace=workspace_id,
            seq=peer.action_sequence, parent_id=parent.id, terms=terms,
            expected_revision=response.grants.revision))
        self.assertTrue(delegated.HasField('grant'), delegated)
        return delegated.grant

    def test_actor_cabinet_gateway_accepts_exact_registered_worktree_scope(self):
        self.enroll_second()
        world = self.workspace()
        owner, peer = world.peers
        target = self.make_cabinet(world, owner)
        room = target.room_id
        self.world_grant_second(world, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE])
        self.world_grant_second(world, [grant.REPRESENTATION_VIEW], components=[room, target.representation_id])
        self.world_grant_second(world, [grant.REPRESENTATION_USE], components=[room, target.representation_id])
        # Derive the exact scope from the trusted registry reply, then grant only it.
        worktree_component = self.registered_worktree_component('one')
        self.assertEqual(worktree_component, b'tree-one')
        self.grant_file_scope_to_second(world, 'one', [grant.FILE_CREATE], [worktree_component])
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        created = peer.act(cabinet=self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=b'', basename=b'worktree-scoped.txt', content=b'scoped')))
        self.assertTrue(created.HasField('action'), created)
        self.assertTrue(created.action.cabinet.HasField('object'), created)
        self.assertEqual((self.project / 'worktree-scoped.txt').read_bytes(), b'scoped')

    def test_registered_worktree_scope_controls_cabinet_placement_and_file_use(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner, peer = world.peers
        room = world.management('world.info')['entry_room']
        self.world_grant_second(world, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE,
            grant.WORLD_OBSERVE, grant.WORLD_PLACEMENT_CREATE])
        self.world_grant_second(world, [grant.REPRESENTATION_VIEW], components=[room])
        self.workspace_grant_second_domain(world, 'one', [grant.WORKSPACE_PRESENTATION_ATTACH])
        self.workspace_grant_second_domain(world, 'two', [grant.WORKSPACE_PRESENTATION_ATTACH])
        tree_one = self.registered_worktree_component('one')
        tree_two = self.registered_worktree_component('two')
        self.assertNotEqual(tree_one, tree_two)
        self.grant_file_scope_to_second(world, 'one', [grant.FILE_CREATE], [tree_one])
        # This otherwise valid FILE_CREATE grant names workspace one's worktree.
        self.grant_file_scope_to_second(world, 'two', [grant.FILE_CREATE], [tree_one])
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))

        info = world.management('world.info')
        exact = peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='one',
            room_id=room, transform=presentation.pose(.8),
            expected_world_revision=info['world_revision'],
            kind=core.PRESENTATION_KIND_CABINET))
        self.assertTrue(exact.HasField('action'), exact)
        exact_target = exact.action.presentation
        self.assertEqual(exact_target.kind, core.PRESENTATION_KIND_CABINET)
        self.assertFalse(exact_target.cabinet_locked)
        self.assertTrue(exact_target.locked, 'FILE_CREATE does not imply WORKSPACE_ACCESS')
        exact_observation = peer.act(observe=core.ActorObserveRequest())
        self.assertTrue(exact_observation.HasField('action'), exact_observation)
        projected = next(view for view in exact_observation.action.observation.presentation_views
                         if view.representation_id == exact_target.representation_id)
        self.assertEqual(projected.kind, core.PRESENTATION_KIND_CABINET)
        self.assertTrue(projected.locked, 'generic M07 lock stays tied to WORKSPACE_ACCESS')
        self.assertFalse(projected.cabinet_locked)
        self.world_grant_second(world, [grant.REPRESENTATION_USE],
                                components=[room, exact_target.representation_id])
        created = peer.act(cabinet=self.cabinet_request(exact_target,
            create=cabinet.FileCreate(directory_handle=b'', basename=b'exact-worktree.txt', content=b'exact')))
        self.assertTrue(created.HasField('action'), created)
        self.assertTrue(created.action.cabinet.HasField('object'), created)
        self.assertEqual((self.project / 'exact-worktree.txt').read_bytes(), b'exact')

        # Generic placement is a positive control for the otherwise valid room and attach rights.
        generic = peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='two',
            room_id=room, transform=presentation.pose(1.6),
            expected_world_revision=world.management('world.info')['world_revision'],
            kind=core.PRESENTATION_KIND_GENERIC))
        self.assertTrue(generic.HasField('action'), generic)
        before = owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        cabinet_count = sum(view.kind == core.PRESENTATION_KIND_CABINET
                            for view in before.presentation_views)
        denied_placement = peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='two',
            room_id=room, transform=presentation.pose(2.4),
            expected_world_revision=world.management('world.info')['world_revision'],
            kind=core.PRESENTATION_KIND_CABINET))
        self.assertTrue(denied_placement.HasField('error'), denied_placement)
        self.assertEqual(denied_placement.error.code, core.FORBIDDEN)
        after = owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        self.assertEqual(sum(view.kind == core.PRESENTATION_KIND_CABINET
                             for view in after.presentation_views), cabinet_count)

        wrong_target = self.make_cabinet(world, owner, x=1.2, workspace_id='two')
        self.world_grant_second(world, [grant.REPRESENTATION_USE],
                                components=[room, wrong_target.representation_id])
        observation = peer.act(observe=core.ActorObserveRequest())
        self.assertTrue(observation.HasField('action'), observation)
        wrong_views = [view for view in observation.action.observation.presentation_views
                       if view.representation_id == wrong_target.representation_id]
        self.assertEqual([view.representation_id for view in wrong_views],
                         [wrong_target.representation_id],
                         'wrong-worktree cabinet must be visible before testing its file-use denial')
        wrong_view = wrong_views[0]
        self.assertTrue(wrong_view.cabinet_locked)
        denied_use = peer.act(cabinet=self.cabinet_request(wrong_target,
            create=cabinet.FileCreate(directory_handle=b'', basename=b'wrong-worktree.txt', content=b'wrong')))
        self.assertTrue(denied_use.HasField('action'), denied_use)
        self.assertTrue(denied_use.action.cabinet.HasField('error'), denied_use)
        self.assertEqual(denied_use.action.cabinet.error.code, cabinet.FileActionError.UNAUTHORIZED)
        self.assertFalse((self.project / 'wrong-worktree.txt').exists())

    def test_actor_cabinet_gateway_revocation_during_observation_is_clean_rejection(self):
        self.enroll_second()
        race = {}
        relay, release_broker_feed = self.world_grant_race(race)
        world = self.workspace(issuer_port=relay.port)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_ancestor_revocable_use_actor(world, target, race)
        path = self.project / 'revoked-during-observation.txt'
        result_box, errors = [], []
        def create():
            try:
                result_box.append(peer.act(cabinet=self.cabinet_request(target,
                    create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(), content=b'no'))))
            except BaseException as error:
                errors.append(error)
        observation_phase_seen = threading.Event()
        previous_reply_hook = relay.before_reply
        def verify_observation_phase(request, response):
            if request.HasField('actor_status'):
                self.assertIn(self.second.actor_id, request.actor_status.actor_ids,
                              'observation issuer request did not include cabinet principal')
                self.assertEqual(relay.status_seen, 1,
                                 'revocation barrier must match the single observation workspace check')
                observation_phase_seen.set()
            if previous_reply_hook:
                previous_reply_hook(request, response)
        relay.before_reply = verify_observation_phase
        relay.armed.set()
        worker = threading.Thread(target=create)
        worker_started = False
        try:
            worker.start()
            worker_started = True
            self.assertTrue(relay.triggered.wait(5), 'pre-dispatch workspace observation missed issuer barrier')
            self.assertTrue(observation_phase_seen.wait(.5),
                            'issuer callback did not verify principal and observation workspace-check phase')
            self.assertTrue(self.world_watch_relay.delivered.wait(.5), 'ancestor revocation did not reach world authority')
            release_broker_feed.set()
            worker.join(5)
            self.assertFalse(worker.is_alive(), 'pre-dispatch actor call did not finish after feed release')
            observed_reply = result_box[0] if result_box else None
            diagnostic = (
                f'actor_error={observed_reply.error.message!r}'
                if observed_reply is not None and observed_reply.HasField('error')
                else f'actor_reply={observed_reply!r}'
            )
            diagnostic += (f'; worker_errors={errors!r}; broker_relay_errors={relay.errors!r}; '
                           f'world_relay_errors={self.world_watch_relay.errors!r}')
            self.assertFalse(errors, diagnostic)
            self.assertEqual(len(result_box), 1, diagnostic)
            denied = result_box[0]
            self.assertTrue(denied.HasField('error'), f'{denied!r}; {diagnostic}')
            branch_evidence = (f'branch=pre_dispatch_observation status_seen={relay.status_seen} '
                f'observation_verified={observation_phase_seen.is_set()} '
                f'watch_delivered={self.world_watch_relay.delivered.is_set()} '
                f'world_watch_errors={len(self.world_watch_relay.errors)} '
                f'broker_relay_errors={len(relay.errors)} result_code={denied.error.code} '
                f'result_message={denied.error.message[:160]!r}')
            self.assertEqual(denied.error.code, core.FORBIDDEN, branch_evidence)
            self.assertFalse(path.exists(), 'revoked presentation-use grant must stop before file dispatch')
            self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'),
                            'ancestor revocation must preserve the authenticated actor session')
            self.assertEqual(relay.status_seen, 1,
                             'the single status request must be the observation workspace check')
            self.assertFalse(relay.errors)
            self.assertFalse(self.world_watch_relay.errors)
        finally:
            if worker_started:
                self.stop_actor_worker(peer, worker, (release_broker_feed,))
            else:
                release_broker_feed.set()

    def test_actor_cabinet_gateway_requester_revocation_during_observation_invalidates_session(self):
        self._requester_revocation_during_observation(presentation.world_authority.issuer.ACTOR_TARGET)

    def test_actor_cabinet_gateway_requester_credential_revocation_during_observation_invalidates_session(self):
        self._requester_revocation_during_observation(presentation.world_authority.issuer.CREDENTIAL_TARGET)

    def _requester_revocation_during_observation(self, target_kind):
        self.enroll_second()
        race = {'ancestor': self.second, 'target_kind': target_kind}
        relay, release_broker_feed = self.world_grant_race(race)
        world = self.workspace(issuer_port=relay.port)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        independent = [grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE,
                       grant.WORLD_MANIPULATE, grant.WORLD_PLACEMENT_CREATE,
                       grant.WORLD_PLACEMENT_REMOVE]
        self.world_grant_second(world, independent)
        self.world_grant_second(world, [grant.REPRESENTATION_VIEW],
                                components=[target.room_id, target.representation_id])
        self.world_grant_second(world, [grant.REPRESENTATION_USE],
                                components=[target.room_id, target.representation_id])
        self.workspace_grant_second_domain(world, 'one', [grant.FILE_CREATE])
        peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        path = self.project / 'requester-revoked-during-observation.txt'
        result_box, errors = [], []

        def create():
            try:
                result_box.append(peer.act(cabinet=self.cabinet_request(target,
                    create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(), content=b'no'))))
            except BaseException as error:
                errors.append(error)

        observation_phase_seen = threading.Event()
        previous_reply_hook = relay.before_reply

        def verify_observation_phase(request, response):
            if request.HasField('actor_status'):
                self.assertIn(self.second.actor_id, request.actor_status.actor_ids,
                              'observation issuer request did not include the requester')
                self.assertEqual(relay.status_seen, 1,
                                 'revocation barrier must match the single observation workspace check')
                observation_phase_seen.set()
            if previous_reply_hook:
                previous_reply_hook(request, response)

        relay.before_reply = verify_observation_phase
        relay.armed.set()
        worker = threading.Thread(target=create)
        worker_started = False
        try:
            worker.start()
            worker_started = True
            self.assertTrue(relay.triggered.wait(5), 'pre-dispatch workspace observation missed issuer barrier')
            self.assertTrue(observation_phase_seen.wait(.5),
                            'issuer callback did not verify requester and observation workspace-check phase')
            self.assertTrue(self.world_watch_relay.delivered.wait(.5), 'requester revocation did not reach world authority')
            release_broker_feed.set()
            worker.join(5)
            self.assertFalse(worker.is_alive(), 'pre-dispatch actor call did not finish after feed release')
            self.assertFalse(errors, errors)
            self.assertEqual(len(result_box), 1)
            denied = result_box[0]
            self.assertTrue(denied.HasField('error'), denied)
            self.assertEqual(denied.protocol_version, 1, denied)
            self.assertEqual(denied.request_id, peer.sequence, denied)
            self.assertEqual(denied.WhichOneof('outcome'), 'error', denied)
            branch_evidence = (f'branch=requester_self_revocation target_kind={target_kind} status_seen={relay.status_seen} '
                f'observation_verified={observation_phase_seen.is_set()} '
                f'watch_delivered={self.world_watch_relay.delivered.is_set()} '
                f'world_watch_errors={len(self.world_watch_relay.errors)} '
                f'broker_relay_errors={len(relay.errors)} result_code={denied.error.code} '
                f'result_message={denied.error.message[:160]!r}')
            self.assertEqual(denied.error.code, core.UNAUTHORIZED, branch_evidence)
            self.assertFalse(path.exists(), 'requester revocation must stop before file dispatch')
            self.denied(peer.call(inspect=session.WorldSessionInspect()), core.UNAUTHORIZED)
            self.assertEqual(relay.status_seen, 1,
                             'the single status request must be the observation workspace check')
            self.assertFalse(relay.errors)
            self.assertFalse(self.world_watch_relay.errors)
        finally:
            if worker_started:
                self.stop_actor_worker(peer, worker, (release_broker_feed,))
            else:
                release_broker_feed.set()

    def test_actor_cabinet_gateway_revocation_in_final_post_operation_window_is_uncertain(self):
        self.enroll_second()
        race = {}
        relay, release_broker_feed = self.world_grant_race(race)
        relay.trigger_after_status = 1000000
        relay.armed.set()
        world = self.workspace(issuer_port=relay.port)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_ancestor_revocable_use_actor(world, target, race)
        operation = os.urandom(16)
        principal = self.principal(self.second)
        worktree = self.registered_worktree_component('one')
        path = self.project / 'final-window-revocation.txt'
        fence_held = threading.Event()
        release_fence = threading.Event()
        response_box, worker_errors, exchange_errors = [], [], []
        trace_contract = FinalWindowBrokerTrace(
            workspace='one', principal=principal, worktree=worktree, operation=operation,
            basename=path.name.encode(), content=b'effect', workspace_access=grant.WORKSPACE_ACCESS,
            browse=grant.FILE_BROWSE, read=grant.FILE_READ, create=grant.FILE_CREATE)
        trace = trace_contract.events
        phase_lock = threading.Lock()
        original_exchange = self.broker.exchange
        revocation_done = threading.Event()
        revocation_errors = []
        revoker = None
        revoker_started = False

        def exchange_with_fence_barrier(request):
            try:
                if trace_contract.stage == 0 and not request.HasField('file_action'):
                    # Initial visibility and dispatch validation precede the selected effect.
                    return original_exchange(request)
                response = trace_contract.exchange(request, original_exchange)
                if trace_contract.final_fence_reached and not fence_held.is_set():
                    trace_contract.finish()
                    with phase_lock:
                        fence_held.set()
                    if not release_fence.wait(5):
                        exchange_errors.append('bounded authorization-fence release expired')
                return response
            except BaseException as error:
                exchange_errors.append(repr(error))
                raise

        def create():
            try:
                response_box.append(peer.act(cabinet=self.cabinet_request(target, operation,
                    create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(),
                                              content=b'effect'))))
            except BaseException as error:
                worker_errors.append(error)

        worker = threading.Thread(target=create)
        worker_started = False
        self.broker.exchange = exchange_with_fence_barrier
        try:
            worker.start()
            worker_started = True
            self.assertTrue(fence_held.wait(5),
                f'gateway missed final authorization fence; trace={trace!r}, '
                f'exchange_errors={exchange_errors!r}, worker_alive={worker.is_alive()}')
            self.assert_completed_file_operation(operation)
            self.assertTrue(path.is_file(), 'file operation must complete before the final fence')
            self.assertEqual(path.read_bytes(), b'effect')
            self.assertEqual(trace, list(FINAL_WINDOW_SEQUENCE))
            self.assertFalse(exchange_errors, exchange_errors)

            def revoke_ancestor():
                try:
                    self.revoke_world_grant_ancestor(race['ancestor'])
                except BaseException as error:
                    revocation_errors.append(error)
                finally:
                    revocation_done.set()

            revoker = threading.Thread(target=revoke_ancestor)
            revoker.start()
            revoker_started = True
            self.assertTrue(revocation_done.wait(5), 'ancestor revocation did not finish within its bound')
            self.assertFalse(revocation_errors, revocation_errors)
            self.assertTrue(self.world_watch_relay.delivered.wait(1),
                            'real ancestor revocation did not reach the world watch')
            time.sleep(.08)
            release_broker_feed.set()
            release_fence.set()
            worker.join(5)
            self.assertFalse(worker.is_alive(), 'gateway call did not finish after fence release')
            self.assertFalse(worker_errors, worker_errors)
            self.assertFalse(exchange_errors, exchange_errors)
            self.broker.exchange = original_exchange
            self.assertEqual(len(response_box), 1)
            result = response_box[0]
            self.assertTrue(result.HasField('action'), result)
            cabinet_result = result.action.cabinet
            self.assertTrue(cabinet_result.HasField('error'), cabinet_result)
            self.assertEqual(cabinet_result.error.code, cabinet.FileActionError.UNCERTAIN)
            self.assertEqual(cabinet_result.operation_id, operation)
            self.assertFalse(cabinet_result.HasField('object'))
            self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'),
                            'ancestor revocation must preserve the authenticated actor session')
            self.assertFalse(relay.errors)
            self.assertFalse(self.world_watch_relay.errors)
        finally:
            release_fence.set()
            release_broker_feed.set()
            self.broker.exchange = original_exchange
            if worker_started:
                self.stop_actor_worker(peer, worker, (release_broker_feed, release_fence))
            if revoker_started:
                revoker.join(5)
                self.assertFalse(revoker.is_alive(), 'ancestor-revocation worker remained alive')

    def hold_broker_file_error(self, operation):
        original_exchange = self.broker.exchange
        held = threading.Event()
        release = threading.Event()
        error_codes = []
        barrier_errors = []

        def exchange_with_error_barrier(request):
            response = original_exchange(request)
            if request.HasField('file_action') and request.file_action.operation_id == operation:
                if response.HasField('file_action') and response.file_action.HasField('error'):
                    error_codes.append(response.file_action.error.code)
                else:
                    barrier_errors.append(f'expected a broker file-action error, received {response!r}')
                held.set()
                if not release.wait(5):
                    barrier_errors.append('bounded broker-error barrier expired')
            return response

        self.broker.exchange = exchange_with_error_barrier
        return original_exchange, held, release, error_codes, barrier_errors

    def test_actor_cabinet_gateway_authorized_file_state_error_is_preserved(self):
        world = self.workspace()
        peer = world.peers[0]
        target = self.make_cabinet(world, peer)
        path = self.project / 'authorized-conflict.txt'
        first = peer.act(cabinet=self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(), content=b'original')))
        self.assertTrue(first.HasField('action'), first)
        self.assertTrue(first.action.cabinet.HasField('object'), first)
        operation = os.urandom(16)
        original, held, release, error_codes, barrier_errors = self.hold_broker_file_error(operation)
        result_box, worker_errors = [], []

        def duplicate_create():
            try:
                result_box.append(peer.act(cabinet=self.cabinet_request(target, operation,
                    create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(),
                                              content=b'replacement'))))
            except BaseException as error:
                worker_errors.append(error)

        worker = threading.Thread(target=duplicate_create)
        worker_started = False
        try:
            worker.start()
            worker_started = True
            self.assertTrue(held.wait(5), 'broker did not return the duplicate-file error')
            self.assertEqual(error_codes, [cabinet.FileActionError.CONFLICT], barrier_errors)
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive(), 'authorized error request did not finish')
        finally:
            release.set()
            self.broker.exchange = original
            if worker_started:
                self.stop_actor_worker(peer, worker, (release,))
        self.assertFalse(worker_errors, worker_errors)
        self.assertFalse(barrier_errors, barrier_errors)
        self.assertEqual(len(result_box), 1)
        result = result_box[0]
        self.assertTrue(result.HasField('action'), result)
        failure = result.action.cabinet
        self.assertTrue(failure.HasField('error'), failure)
        self.assertEqual(failure.error.code, cabinet.FileActionError.CONFLICT)
        self.assertEqual(failure.operation_id, operation)
        self.assertEqual(failure.WhichOneof('outcome'), 'error')
        self.assertEqual(path.read_bytes(), b'original')

    def test_actor_cabinet_gateway_suppresses_file_error_after_use_revocation(self):
        self.enroll_second()
        race = {}
        relay, release_broker_feed = self.world_grant_race(race)
        world = self.workspace(issuer_port=relay.port)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_ancestor_revocable_use_actor(world, target, race)
        path = self.project / 'revoked-error-conflict.txt'
        first = peer.act(cabinet=self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(), content=b'original')))
        self.assertTrue(first.HasField('action'), first)
        self.assertTrue(first.action.cabinet.HasField('object'), first)
        operation = os.urandom(16)
        original, held, release, error_codes, barrier_errors = self.hold_broker_file_error(operation)
        result_box, worker_errors = [], []

        def duplicate_create():
            try:
                result_box.append(peer.act(cabinet=self.cabinet_request(target, operation,
                    create=cabinet.FileCreate(directory_handle=b'', basename=path.name.encode(),
                                              content=b'replacement'))))
            except BaseException as error:
                worker_errors.append(error)

        worker = threading.Thread(target=duplicate_create)
        worker_started = False
        try:
            worker.start()
            worker_started = True
            self.assertTrue(held.wait(5), 'broker did not hold the duplicate-file error')
            self.assertEqual(error_codes, [cabinet.FileActionError.CONFLICT], barrier_errors)
            self.revoke_world_grant_ancestor(race['ancestor'])
            self.assertTrue(self.world_watch_relay.delivered.wait(.5),
                            'representation-use revocation did not reach worldd')
            release_broker_feed.set()
            time.sleep(.08)
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive(), 'revoked error request did not finish')
        finally:
            release_broker_feed.set()
            release.set()
            self.broker.exchange = original
            if worker_started:
                self.stop_actor_worker(peer, worker, (release_broker_feed, release))
        self.assertFalse(worker_errors, worker_errors)
        self.assertFalse(barrier_errors, barrier_errors)
        self.assertEqual(len(result_box), 1)
        result = result_box[0]
        self.assertTrue(result.HasField('action'), result)
        failure = result.action.cabinet
        self.assertTrue(failure.HasField('error'), failure)
        self.assertEqual(failure.error.code, cabinet.FileActionError.UNCERTAIN)
        self.assertEqual(failure.operation_id, operation)
        self.assertEqual(failure.WhichOneof('outcome'), 'error')
        self.assertEqual(path.read_bytes(), b'original')
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'),
                        'use revocation must preserve the authenticated actor session')
        self.assertFalse(relay.errors)
        self.assertFalse(self.world_watch_relay.errors)

    def test_actor_cabinet_copy_requires_file_copy_and_completes_through_actor_path(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_file_operation_peer(world, target, grant.FILE_COPY)
        source_bytes = b'actor cabinet copy regression\n'
        self.create_gateway_file(owner, target, b'copy-source.txt', source_bytes)

        peer_root = peer.act(cabinet=self.cabinet_request(target,
            browse=cabinet.FileBrowse(directory_handle=b'', page_size=32)))
        self.assertTrue(peer_root.HasField('action'), peer_root)
        self.assertTrue(peer_root.action.cabinet.HasField('directory'), peer_root)
        peer_source = next(entry for entry in peer_root.action.cabinet.directory.entries
                           if entry.name == b'copy-source.txt')
        denied = peer.act(cabinet=self.cabinet_request(target,
            copy_job=cabinet.FileCopyJob(command=cabinet.FileCopyJob.START,
                source_handle=peer_source.handle,
                destination_directory_handle=peer_root.action.cabinet.directory.handle,
                destination_basename=b'denied-copy.txt', maximum_bytes=1024, maximum_entries=8)))
        self.assertTrue(denied.HasField('action'), denied)
        self.assertTrue(denied.action.cabinet.HasField('error'), denied)
        self.assertEqual(denied.action.cabinet.error.code, cabinet.FileActionError.UNAUTHORIZED)
        self.assertFalse((self.project / 'denied-copy.txt').exists())

        owner_root = owner.act(cabinet=self.cabinet_request(target,
            browse=cabinet.FileBrowse(directory_handle=b'', page_size=32)))
        self.assertTrue(owner_root.HasField('action'), owner_root)
        self.assertTrue(owner_root.action.cabinet.HasField('directory'), owner_root)
        owner_source = next(entry for entry in owner_root.action.cabinet.directory.entries
                            if entry.name == b'copy-source.txt')
        started = owner.act(cabinet=self.cabinet_request(target,
            copy_job=cabinet.FileCopyJob(command=cabinet.FileCopyJob.START,
                source_handle=owner_source.handle,
                destination_directory_handle=owner_root.action.cabinet.directory.handle,
                destination_basename=b'actor-copy.txt', maximum_bytes=1024, maximum_entries=8)))
        self.assertTrue(started.HasField('action'), started)
        self.assertTrue(started.action.cabinet.HasField('copy_job'), started)
        job_id = started.action.cabinet.copy_job.job_id
        self.assertEqual(len(job_id), 16)
        deadline = time.monotonic() + 10
        terminal = None
        while time.monotonic() < deadline:
            polled = owner.act(cabinet=self.cabinet_request(target,
                copy_job=cabinet.FileCopyJob(command=cabinet.FileCopyJob.POLL, job_id=job_id)))
            self.assertTrue(polled.HasField('action'), polled)
            self.assertTrue(polled.action.cabinet.HasField('copy_job'), polled)
            receipt = polled.action.cabinet.copy_job
            if receipt.state in (cabinet.FileCopyJobReceipt.COMPLETE, cabinet.FileCopyJobReceipt.CANCELLED,
                                 cabinet.FileCopyJobReceipt.CONFLICT, cabinet.FileCopyJobReceipt.UNCERTAIN,
                                 cabinet.FileCopyJobReceipt.FAILED):
                terminal = receipt
                break
            self.assertIn(receipt.state, (cabinet.FileCopyJobReceipt.QUEUED, cabinet.FileCopyJobReceipt.RUNNING))
            time.sleep(.02)
        self.assertIsNotNone(terminal, 'actor copy job did not reach a terminal state')
        self.assertEqual(terminal.state, cabinet.FileCopyJobReceipt.COMPLETE)
        self.assertEqual(terminal.bytes_copied, len(source_bytes))
        self.assertEqual(terminal.entries_copied, 1)
        self.assertEqual(len(terminal.content_digest), 32)
        destination = self.project / 'actor-copy.txt'
        self.assertEqual(destination.read_bytes(), source_bytes)
        repeated = owner.act(cabinet=self.cabinet_request(target,
            copy_job=cabinet.FileCopyJob(command=cabinet.FileCopyJob.POLL, job_id=job_id)))
        self.assertTrue(repeated.HasField('action'), repeated)
        self.assertTrue(repeated.action.cabinet.HasField('copy_job'), repeated)
        self.assertEqual(repeated.action.cabinet.copy_job.SerializeToString(deterministic=True),
                         terminal.SerializeToString(deterministic=True),
                         'terminal copy polling is stable')

        malformed = cabinet.ActorCabinetRequest(representation_id=target.representation_id,
            expected_generation=target.generation, operation_id=b'short',
            browse=cabinet.FileBrowse(page_size=8))
        invalid = owner.act(cabinet=malformed)
        self.assertTrue(invalid.HasField('error'), invalid)
        self.assertEqual(invalid.error.code, core.INVALID_ARGUMENT)
        bad_name = owner.act(cabinet=self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=b'', basename=b'../escape.txt', content=b'x')))
        self.assertTrue(bad_name.HasField('action'), bad_name)
        self.assertTrue(bad_name.action.cabinet.HasField('error'), bad_name)
        self.assertEqual(bad_name.action.cabinet.error.code, cabinet.FileActionError.INVALID_ARGUMENT)
        self.assertFalse((self.project.parent / 'escape.txt').exists())



    def prepare_file_operation_peer(self, world, target, excluded_right):
        _, peer = world.peers
        room = target.room_id
        self.world_grant_second(world, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE])
        self.world_grant_second(world, [grant.REPRESENTATION_VIEW], components=[room, target.representation_id])
        self.world_grant_second(world, [grant.REPRESENTATION_USE], components=[room, target.representation_id])
        file_rights = (
            grant.FILE_BROWSE, grant.FILE_READ, grant.FILE_CREATE, grant.FILE_EDIT,
            grant.FILE_RENAME, grant.FILE_MOVE, grant.FILE_COPY, grant.FILE_TRASH,
            grant.FILE_RESTORE, grant.FILE_PERMANENT_DELETE,
        )
        rights = [grant.WORKSPACE_ACCESS, *(right for right in file_rights if right != excluded_right)]
        self.workspace_grant_second_domain(world, 'one', rights)
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        return peer

    def create_gateway_file(self, peer, target, basename, content):
        reply = peer.act(cabinet=self.cabinet_request(target,
            create=cabinet.FileCreate(directory_handle=b'', basename=basename, content=content)))
        self.assertTrue(reply.HasField('action'), reply)
        self.assertTrue(reply.action.cabinet.HasField('object'), reply)
        return reply.action.cabinet.object

    def assert_gateway_denied_then_allowed(self, peer, owner, request, unchanged_path, expected_bytes):
        denied = peer.act(cabinet=request)
        self.assertTrue(denied.HasField('action'), denied)
        self.assertTrue(denied.action.cabinet.HasField('error'), denied)
        self.assertEqual(denied.action.cabinet.error.code, cabinet.FileActionError.UNAUTHORIZED)
        self.assertEqual(denied.action.cabinet.operation_id, request.operation_id)
        if unchanged_path is not None:
            self.assertEqual(unchanged_path.read_bytes(), expected_bytes)

        allowed_request = cabinet.ActorCabinetRequest()
        allowed_request.CopyFrom(request)
        allowed_request.operation_id = os.urandom(16)
        allowed = owner.act(cabinet=allowed_request)
        self.assertTrue(allowed.HasField('action'), allowed)
        self.assertFalse(allowed.action.cabinet.HasField('error'), allowed)
        return allowed.action.cabinet

    def test_actor_cabinet_gateway_edit_requires_file_edit_right(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_file_operation_peer(world, target, grant.FILE_EDIT)
        obj = self.create_gateway_file(owner, target, b'edit-right.txt', b'before')
        path = self.project / 'edit-right.txt'
        request = self.cabinet_request(target,
            edit=cabinet.FileEdit(file_handle=obj.handle, expected_generation=obj.generation, content=b'after'))
        result = self.assert_gateway_denied_then_allowed(peer, owner, request, path, b'before')
        self.assertTrue(result.HasField('object'), result)
        self.assertEqual(path.read_bytes(), b'after')

    def test_actor_cabinet_gateway_rename_requires_file_rename_right(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_file_operation_peer(world, target, grant.FILE_RENAME)
        obj = self.create_gateway_file(owner, target, b'rename-source.txt', b'rename')
        source = self.project / 'rename-source.txt'
        destination = self.project / 'rename-destination.txt'
        request = self.cabinet_request(target,
            rename=cabinet.FileRename(file_handle=obj.handle, expected_generation=obj.generation,
                                      new_basename=destination.name.encode()))
        result = self.assert_gateway_denied_then_allowed(peer, owner, request, source, b'rename')
        self.assertTrue(result.HasField('object'), result)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b'rename')

    def test_actor_cabinet_gateway_move_requires_file_move_right(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_file_operation_peer(world, target, grant.FILE_MOVE)
        destination_dir = self.project / 'move-destination'
        destination_dir.mkdir()
        page = owner.act(cabinet=self.cabinet_request(target, browse=cabinet.FileBrowse(page_size=32)))
        self.assertTrue(page.HasField('action'), page)
        entry = next(item for item in page.action.cabinet.directory.entries
                     if item.name == b'move-destination')
        self.assertTrue(entry.is_directory)
        obj = self.create_gateway_file(owner, target, b'move-source.txt', b'move')
        source = self.project / 'move-source.txt'
        destination = destination_dir / 'move-destination.txt'
        request = self.cabinet_request(target,
            move=cabinet.FileMove(file_handle=obj.handle, expected_generation=obj.generation,
                destination_directory_handle=entry.handle, new_basename=destination.name.encode()))
        result = self.assert_gateway_denied_then_allowed(peer, owner, request, source, b'move')
        self.assertTrue(result.HasField('object'), result)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b'move')

    def test_actor_cabinet_gateway_trash_requires_file_trash_right(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_file_operation_peer(world, target, grant.FILE_TRASH)
        obj = self.create_gateway_file(owner, target, b'trash-right.txt', b'trash')
        path = self.project / 'trash-right.txt'
        request = self.cabinet_request(target,
            trash=cabinet.FileTrash(file_handle=obj.handle, expected_generation=obj.generation))
        result = self.assert_gateway_denied_then_allowed(peer, owner, request, path, b'trash')
        self.assertTrue(result.HasField('trash'), result)
        self.assertFalse(path.exists())
        self.assertEqual((self.project / '.spatial-trash' / result.trash.handle.hex()).read_bytes(), b'trash')

    def test_actor_cabinet_gateway_restore_requires_file_restore_right(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_file_operation_peer(world, target, grant.FILE_RESTORE)
        obj = self.create_gateway_file(owner, target, b'restore-right.txt', b'restore')
        path = self.project / 'restore-right.txt'
        trashed = owner.act(cabinet=self.cabinet_request(target,
            trash=cabinet.FileTrash(file_handle=obj.handle, expected_generation=obj.generation)))
        self.assertTrue(trashed.HasField('action'), trashed)
        self.assertTrue(trashed.action.cabinet.HasField('trash'), trashed)
        receipt = trashed.action.cabinet.trash
        trash_path = self.project / '.spatial-trash' / receipt.handle.hex()
        request = self.cabinet_request(target,
            restore=cabinet.FileRestore(trash_handle=receipt.handle,
                destination_directory_handle=b'', destination_basename=b'restore-right.txt',
                expected_generation=receipt.generation))
        result = self.assert_gateway_denied_then_allowed(peer, owner, request, trash_path, b'restore')
        self.assertTrue(result.HasField('object'), result)
        self.assertEqual(path.read_bytes(), b'restore')
        self.assertFalse(trash_path.exists())

    def test_actor_cabinet_gateway_permanent_delete_requires_separate_right(self):
        self.enroll_second()
        world = self.workspace(pair=True)
        owner = world.peers[0]
        target = self.make_cabinet(world, owner)
        peer = self.prepare_file_operation_peer(world, target, grant.FILE_PERMANENT_DELETE)
        obj = self.create_gateway_file(owner, target, b'permanent-right.txt', b'delete')
        path = self.project / 'permanent-right.txt'
        trashed = owner.act(cabinet=self.cabinet_request(target,
            trash=cabinet.FileTrash(file_handle=obj.handle, expected_generation=obj.generation)))
        self.assertTrue(trashed.HasField('action'), trashed)
        self.assertTrue(trashed.action.cabinet.HasField('trash'), trashed)
        receipt = trashed.action.cabinet.trash
        trash_path = self.project / '.spatial-trash' / receipt.handle.hex()
        self.assertFalse(path.exists())
        self.assertTrue(trash_path.is_file())
        request = self.cabinet_request(target,
            permanent_delete=cabinet.FilePermanentDelete(trash_handle=receipt.handle,
                expected_generation=receipt.generation))
        result = self.assert_gateway_denied_then_allowed(peer, owner, request, trash_path, b'delete')
        self.assertTrue(result.HasField('object'), result)
        self.assertFalse(trash_path.exists())


# Keep this focused entrypoint from rediscovering its base presentation suite.
for _name in dir(presentation.WorkspacePresentationActorTests):
    if _name.startswith('test_') and callable(getattr(presentation.WorkspacePresentationActorTests, _name)):
        setattr(ActorCabinetGatewayTests, _name, None)

def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(ActorCabinetGatewayTests)

if __name__ == '__main__':
    unittest.main()
