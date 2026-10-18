#!/usr/bin/env python3
"""M08 cabinet projection through worldd and independent native pixel witnesses."""
import os
import unittest
import core_pb2 as core
import grants_pb2 as grants
import world_session_pb2 as session
from test_workspace_presentation_actor import pose
from test_workspace_geometry_native_crosshost import LocalNativeObserver, WorkspaceGeometryNativeCrossHostTests

class CabinetNativeWitnessTests(WorkspaceGeometryNativeCrossHostTests):
    def grant_file_browse_to_second(self, owner):
        response = owner.call(grants=session.WorldGrantList(workspace='one'))
        self.assertTrue(response.HasField('grants'), response)
        page = response.grants
        parent = next(record for record in page.grants if not record.parent_id and
            record.terms.scope.domain.kind == grants.WORKSPACE and
            record.terms.scope.domain.id == 'one' and
            grants.FILE_BROWSE in record.terms.delegable_rights)
        terms = self.workspace_terms(claims=self.second, rights=[grants.FILE_BROWSE], depth=0)
        owner.action_sequence += 1
        delegated = owner.call(delegate=session.WorldGrantDelegate(workspace='one',
            seq=owner.action_sequence, parent_id=parent.id, terms=terms,
            expected_revision=page.revision))
        self.assertTrue(delegated.HasField('grant'), delegated)
        self.assertEqual(delegated.grant.parent_id, parent.id)
        self.assertEqual(delegated.grant.terms, terms)

    def assert_native_cabinet_projection(self, observation, claims, representation, locked):
        self.assertEqual(observation.actor, self.world_actor_id(claims))
        self.assertEqual(observation.room, self.active_worlds['b'].management('world.info')['entry_room'])
        matches = [view for view in observation.presentation_views
            if view.representation_id == representation.representation_id]
        self.assertEqual(len(matches), 1)
        view = matches[0]
        self.assertEqual(view.kind, core.PRESENTATION_KIND_CABINET)
        self.assertEqual(view.cabinet_locked, locked)
        # The legacy lock remains tied to WORKSPACE_ACCESS for both native actors.
        self.assertFalse(view.locked)
        self.assertEqual(view.generation, representation.generation)
        self.assertEqual(view.transform, representation.transform)
        self.assertEqual({field.name for field in view.DESCRIPTOR.fields},
            {'representation_id', 'room_id', 'transform', 'generation', 'locked', 'kind', 'cabinet_locked'})
        forbidden = {'workspace_id', 'filename', 'directory', 'branch', 'application'}
        self.assertFalse(forbidden & {field.name for field, _ in view.ListFields()})
        geometry_id = 'workspace-presentation.v1/' + representation.representation_id.hex()
        visible = next(item for item in observation.visible if item.id == geometry_id)
        self.assertGreater(visible.visible_pixels, 0)
        self.assertEqual(visible.generation, representation.generation)

    def test_cabinet_projection_and_persistent_placement_reach_native_pixels(self):
        world = self.active_worlds['b']
        owner = self.owner_channel('b')
        self.local_observer = LocalNativeObserver(self)
        remote_initial = self.displayed()
        local_initial = self.local_observer.status()
        self.assertTrue(self.transition('b').accepted)
        self.local_observer.transition(world)
        remote_initial = self.displayed(after_sequence=remote_initial.view_sequence)
        local_initial = self.local_observer.status(sequence=local_initial.view_sequence)

        # Availability and selection alone must not create a cabinet.
        world.management('workspace.available', id='one', available=True)
        self.select(owner)
        before = owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        self.assertFalse(before.presentation_views, before)
        self.stable_pixels()
        self.local_observer.stable_pixels()

        viewer = world.connect(self)
        self.assertTrue(self.authenticate(viewer, self.fourth_record, self.fourth_key,
            self.fourth_public, pose=pose(3, 0)).HasField('opened'))
        info = world.management('world.info')
        remote_before = self.stable_pixels()
        local_before = self.local_observer.stable_pixels()
        # The remote native actor gains only FILE_BROWSE. The local native actor
        # retains WORKSPACE_ACCESS alone, so only its cabinet lock differs.
        self.grant_file_browse_to_second(owner)
        created = owner.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='one',
            room_id=info['entry_room'], transform=pose(.8),
            expected_world_revision=info['world_revision'],
            kind=core.PRESENTATION_KIND_CABINET))
        self.assertTrue(created.HasField('action'), created)
        representation = created.action.presentation
        self.assertEqual(representation.kind, core.PRESENTATION_KIND_CABINET)

        self.assert_native_cabinet_projection(self.native_observation(), self.second,
            representation, locked=False)
        self.assert_native_cabinet_projection(self.local_observer.observation(), self.third,
            representation, locked=True)

        locked_geometry = viewer.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        locked = next(v for v in locked_geometry.presentation_views
            if v.representation_id == representation.representation_id)
        self.assertEqual(locked.kind, core.PRESENTATION_KIND_CABINET)
        self.assertTrue(locked.locked)
        self.assertTrue(locked.cabinet_locked)
        forbidden = {'workspace_id', 'filename', 'directory', 'branch', 'application'}
        self.assertFalse(forbidden & {field.name for field, _ in locked.ListFields()})

        self.changed(remote_before)
        self.local_observer.changed(local_before)
        row_before = self.presentation_row(world, representation.representation_id)
        remote_before_move = self.stable_pixels()
        local_before_move = self.local_observer.stable_pixels()
        moved = owner.act(presentation_move=core.PresentationMoveRequest(
            protocol_version=1, operation_id=os.urandom(16),
            representation_id=representation.representation_id,
            room_id=info['entry_room'], transform=pose(1.35),
            expected_representation_generation=representation.generation))
        self.assertTrue(moved.HasField('action'), moved)
        representation = moved.action.presentation
        self.assertEqual(representation.generation, 2)
        row_after = self.presentation_row(world, representation.representation_id)
        self.assertEqual(row_after[:2], row_before[:2])
        self.assertEqual(row_after[3], 2)
        self.assertNotEqual(row_after[2], row_before[2])
        self.assert_native_cabinet_projection(self.native_observation(), self.second,
            representation, locked=False)
        self.assert_native_cabinet_projection(self.local_observer.observation(), self.third,
            representation, locked=True)
        self.changed(remote_before_move)
        self.local_observer.changed(local_before_move)

if __name__ == '__main__':
    suite = unittest.TestSuite([CabinetNativeWitnessTests(
        'test_cabinet_projection_and_persistent_placement_reach_native_pixels')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
