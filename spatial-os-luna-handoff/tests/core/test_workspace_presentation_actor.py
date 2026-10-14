"""Actor envelope regressions for durable M07 presentation receipts."""
import os
from pathlib import Path
import sqlite3
import time
import unittest
import core_pb2 as core
import grants_pb2 as grant
import world_session_pb2 as session
import test_world_authority as world_authority
import test_workspace_authority as workspace_authority


WORLD_RIGHTS = [
    grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE,
    grant.WORLD_MANIPULATE, grant.WORLD_PLACEMENT_CREATE, grant.WORLD_PLACEMENT_REMOVE,
    grant.REPRESENTATION_VIEW, grant.REPRESENTATION_MOVE, grant.REPRESENTATION_USE,
    grant.POLICY_ADMINISTER,
]
WORKSPACE_RIGHTS = [
    grant.WORKSPACE_ACCESS, grant.FILE_BROWSE, grant.FILE_READ, grant.FILE_CREATE,
    grant.FILE_EDIT, grant.FILE_RENAME, grant.FILE_MOVE, grant.FILE_COPY,
    grant.FILE_TRASH, grant.FILE_RESTORE, grant.FILE_PERMANENT_DELETE,
    grant.PROCESS_EXECUTE, grant.POLICY_ADMINISTER, grant.WORKSPACE_PRESENTATION_ATTACH,
]


def pose(x=0, z=-2):
    return core.Pose(position=core.Vec3(x=x, z=z), orientation=core.Quaternion(w=1))


class WorkspacePresentationActorTests(workspace_authority.WorkspaceAuthorityTests):
    def terms(self, claims=None, rights=None, depth=3, components=(), delegable=None):
        # grants.proto defines Scope.components as repeated bytes; Domain.id stays a string.
        scope_components = tuple(component.encode("utf-8") if isinstance(component, str) else component
                                 for component in components)
        self.assertTrue(all(isinstance(component, bytes) for component in scope_components))
        terms = world_authority.WorldAuthorityTests.terms(
            self, claims, rights=WORLD_RIGHTS if rights is None else rights,
            depth=depth, components=scope_components, delegable=delegable)
        self.assertEqual(terms.scope.domain.id, self.world_info["world_id"])
        self.assertIsInstance(terms.scope.domain.id, str)
        return terms

    def workspace_terms(self, claims=None, rights=None, depth=3, components=(), delegable=None):
        terms = super().workspace_terms(claims=claims,
            rights=WORKSPACE_RIGHTS if rights is None else rights,
            depth=depth, components=components, delegable=delegable)
        self.assertEqual(terms.scope.domain.kind, grant.WORKSPACE)
        self.assertEqual(terms.scope.domain.id, 'one')
        return terms

    def scoped_world(self, *args, **kwargs):
        kwargs['native'] = True
        return super().scoped_world(*args, **kwargs)

    def principal(self, claims=None):
        return world_authority.WorldAuthorityTests.principal(self, claims)

    def world_grant_page(self, peer):
        request = session.WorldGrantList()
        self.assertFalse(request.HasField('workspace'))
        reply = peer.call(grants=request)
        self.assertTrue(reply.HasField('grants'), reply)
        return reply.grants

    def world_root_grant(self, peer):
        page = self.world_grant_page(peer)
        return next(record for record in page.grants if not record.parent_id)

    def world_delegate(self, peer, parent, terms):
        page = self.world_grant_page(peer)
        peer.action_sequence += 1
        request = session.WorldGrantDelegate(seq=peer.action_sequence, parent_id=parent.id,
            terms=terms, expected_revision=page.revision)
        self.assertFalse(request.HasField('workspace'))
        reply = peer.call(delegate=request)
        self.assertTrue(reply.HasField('grant'), reply)
        return reply.grant

    def world_grant_second(self, world, rights, components=()):
        peer = world.peers[0]
        terms = self.terms(self.second, rights=rights, depth=0, components=components)
        return self.world_delegate(peer, self.world_root_grant(peer), terms)

    def world_revoke(self, peer, record):
        page = self.world_grant_page(peer)
        peer.action_sequence += 1
        request = session.WorldGrantRevoke(seq=peer.action_sequence,
            id=record.id, expected_revision=page.revision)
        self.assertFalse(request.HasField('workspace'))
        reply = peer.call(revoke=request)
        self.assertTrue(reply.HasField('grant'), reply)
        return reply.grant

    def world_grant_second_via_ancestor(self, world):
        # Only the broad world grant depends on this enrolled actor.
        # Workspace grants and exact representation grants retain the admin parent.
        ancestor_key, ancestor_public = self.key('presentation-grant-ancestor')
        ancestor_record = self.decide(self.owner_peer, self.enroll(self.owner_peer,
            self.intent(public=ancestor_public), ancestor_key, ancestor_public)).enrollment
        ancestor = self.claims(ancestor_record, self.info)
        peer = world.peers[0]
        parent = self.world_delegate(peer, self.world_root_grant(peer),
            self.terms(ancestor, rights=WORLD_RIGHTS, depth=1))
        self.assertTrue(peer.call(close=session.WorldSessionClose()).closed.closed)
        self.assertTrue(self.authenticate(peer, ancestor_record, ancestor_key, ancestor_public).HasField('opened'))
        child = self.world_delegate(peer, parent, self.terms(self.second, rights=WORLD_RIGHTS, depth=0))
        self.assertEqual(child.parent_id, parent.id)
        self.assertTrue(peer.call(close=session.WorldSessionClose()).closed.closed)
        self.assertTrue(self.authenticate(peer).HasField('opened'))
        return ancestor

    def revoke_world_grant_ancestor(self, ancestor):
        # The independent issuer remains serviceable while worldd waits for workspaced.
        operation = world_authority.issuer.IssuerOperation(identity_revoke=world_authority.issuer.IdentityRevoke(
            target=world_authority.issuer.IdentityReference(
                kind=world_authority.issuer.ACTOR_TARGET, id=ancestor.actor_id),
            expected_security_revision=0, reason='presentation world-grant ancestor regression'))
        reply = self.execute(self.owner_peer, self.challenge(self.owner_peer, operation))
        self.assertTrue(reply.HasField('identity_status'), reply)
        self.assertEqual(reply.identity_status.actor_id, ancestor.actor_id)
        self.assertTrue(reply.identity_status.actor_revoked)
        self.assertFalse(reply.identity_status.active)

    def presentation_state(self, world):
        database = world.state_path / 'world.db'
        uri = 'file:' + str(database) + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as connection:
            return {
                'available': connection.execute(
                    "SELECT COUNT(*) FROM world_workspaces WHERE id='one'").fetchone()[0],
                'selected': connection.execute(
                    'SELECT COUNT(*) FROM actor_workspaces').fetchone()[0],
                'presentations': connection.execute(
                    'SELECT COUNT(*) FROM workspace_presentations').fetchone()[0],
                'operations': connection.execute(
                    'SELECT COUNT(*) FROM workspace_presentation_ops').fetchone()[0],
            }

    @staticmethod
    def process_tree(root_pid):
        pending = [root_pid]
        seen = set()
        while pending:
            parent = pending.pop()
            try:
                children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
            except FileNotFoundError:
                continue
            for child in map(int, children):
                if child not in seen:
                    seen.add(child)
                    pending.append(child)
        return seen

    def test_create_without_broker_denies_without_side_effects(self):
        # This valid request succeeds with the configured authority broker.
        control = self.workspace()
        control_peer = control.peers[0]
        control_room = control.management('world.info')['entry_room']
        control_reply = control_peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='one',
            room_id=control_room, transform=pose(.8),
            expected_world_revision=control.management('world.info')['world_revision']))
        self.assertTrue(control_reply.HasField('action'), control_reply)
        control_state = self.presentation_state(control)
        self.assertEqual(control_state, {
            'available': 1, 'selected': 0, 'presentations': 1, 'operations': 1})

        # Geometry remains available without the optional workspace broker.
        world = world_authority.WorldAuthorityTests.scoped_world(self, native=False)
        peer = world.peers[0]
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))
        before = world.management('world.info')
        geometry = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(geometry.HasField('geometry_view'), geometry)
        self.assertFalse(geometry.geometry_view.presentation_views)
        before_state = self.presentation_state(world)
        children_before = self.process_tree(world.proc.pid)

        # The same valid action fails because the authority broker is absent.
        denied = peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='one',
            room_id=before['entry_room'], transform=pose(.8),
            expected_world_revision=before['world_revision']))
        self.assertTrue(denied.HasField('error'), denied)
        self.assertEqual(denied.error.code, core.UNAVAILABLE)
        self.assertEqual(denied.error.message, 'Workspace authorization broker is unavailable')

        after = world.management('world.info')
        geometry_after = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(geometry_after.HasField('geometry_view'), geometry_after)
        self.assertFalse(geometry_after.geometry_view.presentation_views)
        self.assertEqual(after['world_revision'], before['world_revision'])
        self.assertEqual(self.presentation_state(world), before_state)
        self.assertEqual(before_state, {
            'available': 0, 'selected': 0, 'presentations': 0, 'operations': 0})
        self.assertEqual(self.process_tree(world.proc.pid), children_before)
        self.assertNotIn('--workspace-fd', world.proc.args)

    def setUp(self):
        super().setUp()

    def test_move_replay_rechecks_world_grants_after_blocking_workspace_query(self):
        self.enroll_second()
        race = {}
        relay = world_authority.RelayingIssuer(self,
            lambda: self.revoke_world_grant_ancestor(race['ancestor']))
        # The scoped workspace check batches actor statuses into one issuer request.
        relay.trigger_after_status = 1
        world = self.workspace(issuer_port=relay.port)
        admin = world.peers[0]
        race['ancestor'] = self.world_grant_second_via_ancestor(world)
        self.grant_second(world, [grant.WORKSPACE_ACCESS, grant.WORKSPACE_PRESENTATION_ATTACH])
        peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        destination = 'presentation-race-destination'
        world.management('world.create', entity={'id': destination, 'kind': 'room', 'label': destination})
        entry = world.management('world.info')['entry_room']
        created = peer.act(presentation_create=core.PresentationCreateRequest(protocol_version=1,
            operation_id=os.urandom(16), workspace_id='one', room_id=entry, transform=pose(.8),
            expected_world_revision=world.management('world.info')['world_revision']))
        self.assertTrue(created.HasField('action'), created)
        move = core.PresentationMoveRequest(protocol_version=1, operation_id=os.urandom(16),
            representation_id=created.action.presentation.representation_id, room_id=destination,
            transform=pose(4), expected_representation_generation=1)
        moved = peer.act(presentation_move=move)
        self.assertTrue(moved.HasField('action'), moved)
        # Exact view grants survive; only the ancestor supplies move authority.
        for room in (entry, destination):
            self.world_grant_second(world, [grant.WORLD_OBSERVE, grant.REPRESENTATION_VIEW],
                components=[room, move.representation_id])
        revision_before_replay = world.management('world.info')['world_revision']
        state_before_replay = self.presentation_state(world)

        relay.armed.set()
        replay = peer.act(presentation_move=move)
        triggered = relay.triggered.wait(.3)
        with relay.status_lock:
            status_count = relay.status_seen
        self.assertTrue(triggered,
            f'the workspace access query did not reach the revocation barrier; observed {status_count} actor_status request(s)')
        self.assertEqual(status_count, 1)
        self.assertTrue(replay.HasField('error'), replay)
        self.assertEqual(replay.error.code, core.FORBIDDEN)
        self.assertEqual(replay.error.message, 'Presentation action authority changed')
        self.assertTrue(relay.delivered.is_set())
        self.assertFalse(relay.errors)
        self.assertEqual(world.management('world.info')['world_revision'], revision_before_replay)
        self.assertEqual(self.presentation_state(world), state_before_replay)
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        # Restore only move authority. A successful replay proves that exact world
        # view grants and independent workspace access survived ancestor revocation.
        self.world_grant_second(world, [grant.REPRESENTATION_MOVE])
        restored = peer.act(presentation_move=move)
        self.assertTrue(restored.HasField('action'), restored)
        self.assertEqual(restored.action.presentation, moved.action.presentation)
        self.assertFalse(restored.action.presentation.locked)
        self.assertEqual(world.management('world.info')['world_revision'], revision_before_replay)
        self.assertEqual(self.presentation_state(world), state_before_replay)

    def workspace_grant_second_domain(self, world, domain, rights):
        peer = world.peers[0]
        request = session.WorldGrantList(workspace=domain)
        response = peer.call(grants=request)
        self.assertTrue(response.HasField('grants'), response)
        page = response.grants
        parent = next(record for record in page.grants if not record.parent_id)
        terms = super().workspace_terms(claims=self.second, rights=rights, depth=0)
        terms.scope.domain.id = domain
        peer.action_sequence += 1
        delegated = peer.call(delegate=session.WorldGrantDelegate(workspace=domain,
            seq=peer.action_sequence, parent_id=parent.id, terms=terms, expected_revision=page.revision))
        self.assertTrue(delegated.HasField('grant'), delegated)
        return delegated.grant

    def test_geometry_view_rechecks_generic_world_observe_after_workspace_query(self):
        self.enroll_second()
        race = {}
        relay = world_authority.RelayingIssuer(self,
            lambda: self.revoke_world_grant_ancestor(race["ancestor"]))
        relay.trigger_after_status = 1
        world = self.workspace(issuer_port=relay.port)
        admin = world.peers[0]
        race["ancestor"] = self.world_grant_second_via_ancestor(world)
        self.grant_second(world, [grant.WORKSPACE_ACCESS, grant.WORKSPACE_PRESENTATION_ATTACH])
        peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField("opened"), peer)
        entry = world.management("world.info")["entry_room"]
        created = peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id="one", room_id=entry,
            transform=pose(.8), expected_world_revision=world.management("world.info")["world_revision"]))
        self.assertTrue(created.HasField("action"), created)
        representation = created.action.presentation.representation_id
        self.world_grant_second(world, [grant.WORLD_OBSERVE, grant.REPRESENTATION_VIEW],
            components=[entry, representation])
        valid_geometry = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(valid_geometry.HasField("geometry_view"), valid_geometry)
        self.assertEqual(len(valid_geometry.geometry_view.presentation_views), 1)

        revision_before_race = world.management('world.info')['world_revision']
        state_before_race = self.presentation_state(world)
        relay.armed.set()
        raced = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(relay.triggered.wait(.5),
            "workspace query did not trigger the world-observe revocation")
        with relay.status_lock:
            self.assertEqual(relay.status_seen, 1)
        self.assertTrue(raced.HasField("error"), raced)
        self.assertEqual(raced.error.code, core.FORBIDDEN)
        self.assertFalse(raced.HasField("geometry_view"))
        self.assertTrue(relay.delivered.is_set())
        self.assertFalse(relay.errors)
        self.assertEqual(world.management('world.info')['world_revision'], revision_before_race)
        self.assertEqual(self.presentation_state(world), state_before_race)
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        # Restore generic observation only. The exact view grant and workspace
        # access must still produce the original unlocked presentation.
        self.world_grant_second(world, [grant.WORLD_OBSERVE])
        restored = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(restored.HasField('geometry_view'), restored)
        self.assertEqual(list(restored.geometry_view.presentation_views),
            list(valid_geometry.geometry_view.presentation_views))
        self.assertFalse(restored.geometry_view.presentation_views[0].locked)
        self.assertEqual(world.management('world.info')['world_revision'], revision_before_race)
        self.assertEqual(self.presentation_state(world), state_before_race)

    def test_two_workspace_scene_fence_rejects_later_binding_revocation(self):
        self.enroll_second()
        race = {}
        relay = world_authority.RelayingIssuer(self, lambda: self.revoke(kind=world_authority.issuer.ACTOR_TARGET))
        relay.trigger_after_status = 2
        world = self.workspace(issuer_port=relay.port, pair=True)
        admin = world.peers[0]
        self.world_grant_second(world, WORLD_RIGHTS)
        self.grant_second(world, [grant.WORKSPACE_ACCESS, grant.WORKSPACE_PRESENTATION_ATTACH])
        self.workspace_grant_second_domain(world, 'two',
            [grant.WORKSPACE_ACCESS, grant.WORKSPACE_PRESENTATION_ATTACH])
        peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        entry = world.management('world.info')['entry_room']
        self.world_grant_second(world, [grant.WORLD_PLACEMENT_CREATE], components=[entry])
        for index, workspace_id, x in [(1, 'one', -.8), (2, 'two', .8)]:
            created = peer.act(presentation_create=core.PresentationCreateRequest(protocol_version=1,
                operation_id=os.urandom(16), workspace_id=workspace_id, room_id=entry,
                transform=pose(x), expected_world_revision=world.management('world.info')['world_revision']))
            self.assertTrue(created.HasField('action'), created)
        baseline = peer.act(observe=core.ActorObserveRequest())
        self.assertTrue(baseline.HasField('action'), baseline)
        self.assertEqual(len(baseline.action.observation.presentation_views), 2)
        relay.armed.set()
        raced = peer.act(observe=core.ActorObserveRequest())
        self.assertTrue(relay.triggered.wait(.5), 'later workspace authority query did not trigger revocation')
        with relay.status_lock:
            self.assertEqual(relay.status_seen, 2)
        self.assertTrue(raced.HasField('error'), raced)
        self.assertIn(raced.error.code, (core.FORBIDDEN, core.UNAVAILABLE))
        self.assertFalse(relay.errors)

    def test_cross_room_move_and_remove_replays_are_authorized_and_idempotent(self):
        self.enroll_second()
        world = self.workspace()
        admin = world.peers[0]
        self.world_grant_second(world, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE])
        self.grant_second(world, [grant.WORKSPACE_ACCESS, grant.WORKSPACE_PRESENTATION_ATTACH])
        peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        entry = world.management('world.info')['entry_room']
        world.management('world.create', entity={'id': 'presentation-destination',
            'kind': 'room', 'label': 'presentation-destination'})
        world.management('world.create', entity={'id': 'presentation-away',
            'kind': 'room', 'label': 'presentation-away'})
        world.management('world.create', entity={'id': 'presentation-remove-away',
            'kind': 'room', 'label': 'presentation-remove-away'})
        world.management('world.create', entity={'id': 'presentation-replay-portal', 'kind': 'portal',
            'room': entry, 'pose': {'p': [0, 0, -.5], 'q': [0, 0, 0, 1]}, 'size': [2, 2],
            'destination': {'room': 'presentation-away', 'pose': {'p': [4, 0, 2], 'q': [0, 0, 0, 1]}}})
        world.management('world.create', entity={'id': 'presentation-remove-portal', 'kind': 'portal',
            'room': 'presentation-away', 'pose': {'p': [4, 0, 1], 'q': [0, 0, 0, 1]}, 'size': [2, 2],
            'destination': {'room': 'presentation-remove-away', 'pose': {'p': [8, 0, 2], 'q': [0, 0, 0, 1]}}})
        world.management('world.create', entity={'id': 'placement-obstacle', 'kind': 'object',
            'room': entry, 'pose': {'p': [0, 0, -2], 'q': [0, 0, 0, 1]}})
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))
        for room in (entry, 'presentation-away', 'presentation-remove-away'):
            self.world_grant_second(world, [grant.WORLD_OBSERVE], components=(room,))
        self.world_grant_second(world, [grant.WORLD_PLACEMENT_CREATE], components=(entry,))
        temporary_entry_view = self.world_grant_second(
            world, [grant.REPRESENTATION_VIEW], components=(entry,))
        self.world_grant_second(world, [grant.WORLD_PLACEMENT_CREATE], components=('presentation-away',))
        initial_revision = world.management('world.info')['world_revision']

        blocked = core.PresentationCreateRequest(protocol_version=1, operation_id=os.urandom(16),
            workspace_id='one', room_id=entry, transform=pose(), expected_world_revision=initial_revision)
        rejected = peer.act(presentation_create=blocked)
        self.assertTrue(rejected.HasField('error'), rejected)
        self.assertEqual(rejected.error.code, core.INVALID_ARGUMENT)

        create = core.PresentationCreateRequest(protocol_version=1, operation_id=os.urandom(16),
            workspace_id='one', room_id=entry, transform=pose(.8), expected_world_revision=initial_revision)
        first = peer.act(presentation_create=create)
        self.assertTrue(first.HasField('action'), first)
        view = first.action.presentation
        self.assertEqual(len(view.representation_id), 16)
        self.assertEqual(view.room_id, entry)
        self.assertEqual(view.generation, 1)
        move_scope_rights = [grant.WORLD_OBSERVE, grant.REPRESENTATION_VIEW, grant.REPRESENTATION_MOVE]
        move_source_grant = self.world_grant_second(
            world, move_scope_rights, components=(entry, view.representation_id))
        self.world_grant_second(
            world, move_scope_rights, components=('presentation-destination', view.representation_id))
        self.world_revoke(admin, temporary_entry_view)
        created = peer.act(presentation_create=create)
        self.assertTrue(created.HasField('action'), created)
        self.assertEqual(created.action.presentation, view)
        changed_create = core.PresentationCreateRequest.FromString(create.SerializeToString())
        changed_create.transform.position.x = 3
        conflict = peer.act(presentation_create=changed_create)
        self.assertTrue(conflict.HasField('error'), conflict)
        self.assertEqual(conflict.error.code, core.REVISION_CONFLICT)

        move = core.PresentationMoveRequest(protocol_version=1, operation_id=os.urandom(16),
            representation_id=view.representation_id, room_id='presentation-destination',
            transform=pose(4), expected_representation_generation=1)
        moved = peer.act(presentation_move=move)
        self.assertTrue(moved.HasField('action'), moved)
        self.assertEqual(moved.action.presentation.room_id, 'presentation-destination')
        self.assertEqual(moved.action.presentation.generation, 2)
        # Move the actor to a third room. An exact retry uses recorded source/destination
        # scopes, not the actor's new room or a target lookup.
        peer.act(input=core.ActorInputRequest(axes=[0, 0, -1, 0, 0, 0]))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            actor_state = peer.call(inspect=session.WorldSessionInspect()).state
            if actor_state.room == 'presentation-away':
                break
            time.sleep(.05)
        self.assertEqual(actor_state.room, 'presentation-away')
        stopped = peer.act(stop=core.ActorStopRequest())
        self.assertTrue(stopped.HasField('action'), stopped)
        moved_replay = peer.act(presentation_move=move)
        self.assertTrue(moved_replay.HasField('action'), moved_replay)
        self.assertEqual(moved_replay.action.presentation, moved.action.presentation)
        changed_move = core.PresentationMoveRequest.FromString(move.SerializeToString())
        changed_move.transform.position.x = 5
        conflict = peer.act(presentation_move=changed_move)
        self.assertTrue(conflict.HasField('error'), conflict)
        self.assertEqual(conflict.error.code, core.REVISION_CONFLICT)

        temporary_away_view = self.world_grant_second(
            world, [grant.REPRESENTATION_VIEW], components=('presentation-away',))
        second = peer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='one', room_id='presentation-away',
            transform=pose(4, 0), expected_world_revision=world.management('world.info')['world_revision']))
        self.assertTrue(second.HasField('action'), second)
        self.world_grant_second(world,
            [grant.WORLD_OBSERVE, grant.REPRESENTATION_VIEW, grant.WORLD_PLACEMENT_REMOVE],
            components=('presentation-away', second.action.presentation.representation_id))
        self.world_revoke(admin, temporary_away_view)
        remove = core.PresentationRemoveRequest(protocol_version=1, operation_id=os.urandom(16),
            representation_id=second.action.presentation.representation_id,
            expected_representation_generation=1)
        removed = peer.act(presentation_remove=remove)
        self.assertTrue(removed.HasField('action'), removed)
        peer.act(input=core.ActorInputRequest(axes=[0, 0, -1, 0, 0, 0]))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            actor_state = peer.call(inspect=session.WorldSessionInspect()).state
            if actor_state.room == 'presentation-remove-away':
                break
            time.sleep(.05)
        self.assertEqual(actor_state.room, 'presentation-remove-away')
        stopped = peer.act(stop=core.ActorStopRequest())
        self.assertTrue(stopped.HasField('action'), stopped)
        removed_replay = peer.act(presentation_remove=remove)
        self.assertTrue(removed_replay.HasField('action'), removed_replay)
        changed_remove = core.PresentationRemoveRequest.FromString(remove.SerializeToString())
        changed_remove.expected_representation_generation = 2
        conflict = peer.act(presentation_remove=changed_remove)
        self.assertTrue(conflict.HasField('error'), conflict)
        self.assertEqual(conflict.error.code, core.REVISION_CONFLICT)

        self.world_revoke(admin, move_source_grant)
        revoked_replay = peer.act(presentation_move=move)
        self.assertTrue(revoked_replay.HasField('error'), revoked_replay)
        self.assertEqual(revoked_replay.error.code, core.FORBIDDEN)

        info = world.management('world.info')
        self.assertEqual(info['world_revision'], initial_revision + 4)
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))


# This fixture inherits useful authority setup helpers, but unittest would also
# discover every inherited authority regression under the broadened M07 grants.
# Preserve only this file's explicitly named presentation tests.
_INHERITED_AUTHORITY_TESTS = tuple(
    name for name in dir(workspace_authority.WorkspaceAuthorityTests)
    if name.startswith('test_') and callable(getattr(workspace_authority.WorkspaceAuthorityTests, name)))
INHERITED_AUTHORITY_TEST_COUNT = len(_INHERITED_AUTHORITY_TESTS)
for _name in _INHERITED_AUTHORITY_TESTS:
    setattr(WorkspacePresentationActorTests, _name, None)

if __name__ == '__main__':
    unittest.main()
