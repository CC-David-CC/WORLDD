#!/usr/bin/env python3
"""Behavioral regressions for startup host-control sequence adoption."""
from pathlib import Path
import unittest

import host_session_pb2 as host
from geometry_host_control_handoff import (
    HostControlHandoffError, adopt_initial_host_control,
)
# Consume fixture binary argv before unittest.main without exposing its TestCase to discovery.
import test_geometry_native_crosshost as native_crosshost
from native_display_geometry import screen_argument


class HostControlHandoffTests(unittest.TestCase):
    def valid_reply(self, *, request_id=1, generation=0):
        reply = host.HostControlReply(protocol_version=1, request_id=request_id,
                                      generation=generation, accepted=True)
        reply.inspection.protocol_version = 1
        reply.inspection.request_id = 19
        reply.inspection.state.id = 'actor-initial'
        return reply

    def test_adopts_actual_initial_request_sequence_and_generation(self):
        reply = self.valid_reply(generation=37)
        cursor = adopt_initial_host_control(reply, expected_request_id=1)
        self.assertEqual((cursor.sequence, cursor.generation), (1, 37))

    def test_rejects_malformed_or_uncorrelated_initial_context(self):
        valid = self.valid_reply(generation=37)
        malformed = []
        item = host.HostControlReply(); item.CopyFrom(valid); item.protocol_version = 2; malformed.append(item)
        item = host.HostControlReply(); item.CopyFrom(valid); item.request_id = 2; malformed.append(item)
        item = host.HostControlReply(); item.CopyFrom(valid); item.accepted = False; malformed.append(item)
        item = host.HostControlReply(protocol_version=1, request_id=1, generation=37, accepted=True); malformed.append(item)
        item = host.HostControlReply(); item.CopyFrom(valid); item.action.SetInParent(); malformed.append(item)
        item = host.HostControlReply(); item.CopyFrom(valid); item.inspection.request_id = 0; malformed.append(item)
        item = host.HostControlReply(); item.CopyFrom(valid); item.inspection.ClearField('state'); malformed.append(item)
        item = host.HostControlReply(); item.CopyFrom(valid); item.inspection.ClearField('state'); item.inspection.error.SetInParent(); malformed.append(item)
        for reply in malformed:
            with self.subTest(reply=reply), self.assertRaises(HostControlHandoffError):
                adopt_initial_host_control(reply, expected_request_id=1)

    def test_actor_state_method_does_not_shadow_inherited_state_path(self):
        fixture = native_crosshost.NativeCrossHostTests('test_07_forged_native_application_fields_and_descriptors_reject')
        state_path = Path('/tmp/inherited-spatial-state')
        fixture.state = state_path
        self.assertIs(fixture.state, state_path)
        self.assertTrue(callable(fixture.actor_state))

    def test_owner_channel_method_preserves_inherited_owner_path(self):
        fixture = native_crosshost.NativeCrossHostTests('test_07_forged_native_application_fields_and_descriptors_reject')
        owner_path = Path('/tmp/inherited-owner-key')
        fixture.owner = owner_path

        class Peer:
            def __init__(self): self.closed = False
            def close(self): self.closed = True

        class World:
            def __init__(self, peer): self.peer = peer
            def connect(self, _fixture): return self.peer

        previous, replacement = Peer(), Peer()
        fixture.owners = {'a': previous}
        fixture.active_worlds = {'a': World(replacement)}
        class Opened:
            @staticmethod
            def HasField(name): return name == 'opened'
        fixture.authenticate = lambda _peer: Opened()

        result = fixture.owner_channel('a')

        self.assertIs(result, replacement)
        self.assertTrue(previous.closed)
        self.assertIs(fixture.owner, owner_path)

    def test_native_screen_selector_defaults_for_borrowed_start_and_bounds_override(self):
        class BorrowedStartFixture: pass
        class GeometryFixture: native_screen_geometry = '512x384x24'

        self.assertEqual(screen_argument(BorrowedStartFixture()), '128x128x24')
        self.assertEqual(screen_argument(GeometryFixture()), '512x384x24')

    def test_native_screen_selector_rejects_invalid_geometry(self):
        class GeometryFixture: pass
        for value in ('', '128x128', '0x128x24', '128x0x24', '513x384x24',
                      '512x385x24', '128x128x32', '0128x128x24', '128x128x24junk'):
            with self.subTest(value=value):
                GeometryFixture.native_screen_geometry = value
                with self.assertRaises(ValueError): screen_argument(GeometryFixture())


if __name__ == '__main__':
    unittest.main(verbosity=2)
