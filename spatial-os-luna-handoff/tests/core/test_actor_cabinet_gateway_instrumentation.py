"""Real protobuf transcript controls for the live final-window recognizer."""
import ast
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import broker_pb2 as broker
import grants_pb2 as grant
from gateway_trace import FinalWindowBrokerTrace, record_file_action_attempt, require_create_payload


class GatewayTraceInstrumentationTests(unittest.TestCase):
    def setUp(self):
        self.principal = broker.ScopedAuthorityCheckRequest().principal
        self.principal.issuer_id = b'i' * 32
        self.principal.actor_id = b'a' * 32
        self.operation = b'o' * 16
        self.forwarded = []

    def recognizer(self):
        return FinalWindowBrokerTrace(
            workspace='one', principal=self.principal, worktree=b'tree-one',
            operation=self.operation, basename=b'final-window-revocation.txt', content=b'effect',
            workspace_access=grant.WORKSPACE_ACCESS, browse=grant.FILE_BROWSE,
            read=grant.FILE_READ, create=grant.FILE_CREATE)

    def scoped(self, right, allowed):
        request, reply = broker.Request(), broker.Reply()
        check = request.scoped_authority_check
        check.workspace = 'one'
        check.principal.CopyFrom(self.principal)
        check.right = right
        if right != grant.WORKSPACE_ACCESS:
            check.components.append(b'tree-one')
        reply.scoped_authority_check.allowed = allowed
        reply.scoped_authority_check.incarnation = b's' * 16
        reply.scoped_authority_check.epoch = 7
        return request, reply

    def binding(self):
        request, reply = broker.Request(), broker.Reply()
        request.management.workspace_get.id = 'one'
        reply.management.workspace.id = 'one'
        reply.management.workspace.worktree = 'tree-one'
        reply.management.workspace.revision = 3
        return request, reply

    def fence(self):
        request, reply = broker.Request(), broker.Reply()
        request.authorization_fence.principal.CopyFrom(self.principal)
        reply.authorization_fence.incarnation = b's' * 16
        reply.authorization_fence.epoch = 7
        return request, reply

    def operation_complete(self):
        request, reply = broker.Request(), broker.Reply()
        request.file_action.workspace_id = 'one'
        request.file_action.operation_id = self.operation
        request.file_action.principal.CopyFrom(self.principal)
        request.file_action.create.basename = b'final-window-revocation.txt'
        request.file_action.create.content = b'effect'
        reply.file_action.operation_id = self.operation
        reply.file_action.object.handle = b'created-object'
        reply.file_action.object.generation = 1
        return request, reply

    def transcript(self):
        # Independent source mapping: actors.cpp 765-784, 108-140, 1188-1192,
        # 1284, 1298, 1307. These are raw broker messages, not expected event labels.
        return [
            self.operation_complete(),
            self.scoped(grant.WORKSPACE_ACCESS, False),
            self.binding(),
            self.scoped(grant.FILE_BROWSE, False),
            self.scoped(grant.FILE_READ, False),
            self.scoped(grant.FILE_CREATE, True),
            self.binding(),
            self.scoped(grant.WORKSPACE_ACCESS, False),
            self.scoped(grant.WORKSPACE_ACCESS, False),
            self.fence(),
            self.binding(),
            self.scoped(grant.FILE_CREATE, True),
            self.binding(),
            self.fence(),
        ]

    def feed(self, recognizer, pair):
        request, reply = pair
        def exchange(actual):
            self.assertIs(actual, request)
            self.forwarded.append(actual.SerializeToString())
            return reply
        return recognizer.exchange(request, exchange)

    def feed_prefix(self, recognizer, transcript, count):
        for pair in transcript[:count]:
            self.feed(recognizer, pair)

    def assert_request_rejected_before_forwarding(self, recognizer, pair):
        before = len(self.forwarded)
        with self.assertRaises(AssertionError):
            self.feed(recognizer, pair)
        self.assertEqual(len(self.forwarded), before)

    def test_complete_raw_transcript_reaches_final_barrier_once(self):
        recognizer = self.recognizer()
        transitions = 0
        for pair in self.transcript():
            before = recognizer.final_fence_reached
            self.feed(recognizer, pair)
            transitions += not before and recognizer.final_fence_reached
        self.assertEqual(transitions, 1)
        self.assertEqual(len(self.forwarded), 14)
        recognizer.finish()

    def test_full_projection_create_and_observation_fence_cannot_activate_final_barrier(self):
        recognizer = self.recognizer()
        self.feed_prefix(recognizer, self.transcript(), 10)
        self.assertEqual(len(self.forwarded), 10)
        self.assertFalse(recognizer.final_fence_reached)
        with self.assertRaises(AssertionError):
            recognizer.finish()
        # Another observation fence cannot replace the separate post-observe binding.
        self.assert_request_rejected_before_forwarding(recognizer, self.fence())
        self.assertFalse(recognizer.final_fence_reached)

    def test_missing_final_create_after_complete_projection_rejects_real_next_binding(self):
        recognizer = self.recognizer()
        transcript = self.transcript()
        self.feed_prefix(recognizer, transcript, 11)
        # Delete the operation-specific CREATE, so its confirmation lookup comes next.
        self.assert_request_rejected_before_forwarding(recognizer, transcript[12])
        self.assertFalse(recognizer.final_fence_reached)
        with self.assertRaises(AssertionError):
            recognizer.finish()

    def test_final_edit_after_complete_projection_is_rejected(self):
        recognizer = self.recognizer()
        self.feed_prefix(recognizer, self.transcript(), 11)
        self.assert_request_rejected_before_forwarding(recognizer, self.scoped(grant.FILE_EDIT, True))
        self.assertFalse(recognizer.final_fence_reached)

    def test_wrong_final_workspace_principal_and_scope_are_rejected(self):
        for field in ('workspace', 'principal', 'components'):
            with self.subTest(field=field):
                recognizer = self.recognizer()
                self.feed_prefix(recognizer, self.transcript(), 11)
                request, reply = self.scoped(grant.FILE_CREATE, True)
                check = request.scoped_authority_check
                if field == 'workspace':
                    check.workspace = 'two'
                elif field == 'principal':
                    check.principal.actor_id = b'b' * 32
                else:
                    check.components[0] = b'tree-two'
                self.assert_request_rejected_before_forwarding(recognizer, (request, reply))

    def test_final_fence_before_final_create_is_rejected(self):
        recognizer = self.recognizer()
        self.feed_prefix(recognizer, self.transcript(), 11)
        self.assert_request_rejected_before_forwarding(recognizer, self.fence())

    def test_duplicate_final_create_before_confirmation_is_rejected(self):
        recognizer = self.recognizer()
        self.feed_prefix(recognizer, self.transcript(), 12)
        self.assert_request_rejected_before_forwarding(recognizer, self.scoped(grant.FILE_CREATE, True))

    def test_duplicate_gateway_traffic_after_final_fence_is_rejected(self):
        for extra in (self.scoped(grant.FILE_CREATE, True), self.fence(),
                      self.binding(), self.operation_complete()):
            with self.subTest(request=extra[0].WhichOneof('operation')):
                recognizer = self.recognizer()
                self.feed_prefix(recognizer, self.transcript(), 14)
                self.assert_request_rejected_before_forwarding(recognizer, extra)

    def test_wrong_projection_decisions_and_reply_shapes_are_rejected(self):
        for mutation in ('browse_allowed', 'create_denied', 'wrong_reply'):
            with self.subTest(mutation=mutation):
                transcript = self.transcript()
                index = 3 if mutation == 'browse_allowed' else 5
                request, reply = transcript[index]
                if mutation == 'browse_allowed':
                    reply.scoped_authority_check.allowed = True
                elif mutation == 'create_denied':
                    reply.scoped_authority_check.allowed = False
                else:
                    reply = self.fence()[1]
                recognizer = self.recognizer()
                self.feed_prefix(recognizer, transcript, index)
                with self.assertRaises(AssertionError):
                    self.feed(recognizer, (request, reply))
                self.assertFalse(recognizer.final_fence_reached)

    def test_changed_binding_or_final_fence_epoch_is_rejected(self):
        for index in (12, 13):
            with self.subTest(index=index):
                transcript = self.transcript()
                if index == 12:
                    transcript[index][1].management.workspace.revision += 1
                else:
                    transcript[index][1].authorization_fence.epoch += 1
                recognizer = self.recognizer()
                self.feed_prefix(recognizer, transcript, index)
                with self.assertRaises(AssertionError):
                    self.feed(recognizer, transcript[index])
                self.assertFalse(recognizer.final_fence_reached)

    def test_unrelated_file_action_before_selected_effect_is_rejected(self):
        recognizer = self.recognizer()
        request, reply = self.operation_complete()
        request.file_action.workspace_id = 'two'
        self.assert_request_rejected_before_forwarding(recognizer, (request, reply))

    def test_any_file_action_attempt_is_counted_before_identity_filter(self):
        request, _ = self.operation_complete()
        request.file_action.workspace_id = 'two'
        request.file_action.operation_id = b'x' * 16
        counters = {'file_action_attempts': 0}
        record_file_action_attempt(request, counters)
        self.assertEqual(counters['file_action_attempts'], 1)

    def test_shared_create_payload_accessor_used_by_both_hooks(self):
        request, _ = self.operation_complete()
        payload = require_create_payload(request, basename=b'final-window-revocation.txt', content=b'effect')
        self.assertEqual(payload.basename, b'final-window-revocation.txt')
        request.file_action.create.content = b'wrong'
        with self.assertRaises(AssertionError):
            require_create_payload(request, basename=b'final-window-revocation.txt', content=b'effect')
        source = Path(__file__).with_name('test_actor_cabinet_gateway.py')
        tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
        hook = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                    and node.name == 'exchange_with_phase_checked_binding')
        self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                            and node.func.id == 'require_create_payload' for node in ast.walk(hook)))

    def test_live_hook_uses_shared_exchange_and_final_barrier_state(self):
        source = Path(__file__).with_name('test_actor_cabinet_gateway.py')
        tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
        scenario = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
            and node.name == 'test_actor_cabinet_gateway_revocation_in_final_post_operation_window_is_uncertain')
        hook = next(node for node in ast.walk(scenario) if isinstance(node, ast.FunctionDef)
            and node.name == 'exchange_with_fence_barrier')
        calls = [ast.unparse(node.func) for node in ast.walk(hook) if isinstance(node, ast.Call)]
        self.assertIn('trace_contract.exchange', calls)
        barriers = [node for node in ast.walk(hook) if isinstance(node, ast.If)
                    and 'trace_contract.final_fence_reached' in ast.unparse(node.test)]
        self.assertEqual(len(barriers), 1)
        barrier_calls = [ast.unparse(node.func) for node in ast.walk(barriers[0]) if isinstance(node, ast.Call)]
        self.assertIn('fence_held.set', barrier_calls)


    def test_cabinet_gateway_registration_keeps_all_local_cases(self):
        discovery_argv = ['cabinet-discovery'] + [
            f'/unused/discovery-argument-{index}' for index in range(64)
        ]
        with patch('sys.argv', discovery_argv):
            import test_actor_cabinet_gateway as gateway

        suite = unittest.TestLoader().loadTestsFromModule(gateway)
        selected = [case.id().rsplit('.', 1)[-1] for case in suite]
        selected_names = set(selected)
        cabinet_case = gateway.ActorCabinetGatewayTests
        local_names = {
            name for name, value in cabinet_case.__dict__.items()
            if name.startswith('test_') and callable(value)
        }
        base_names = {
            name for base in cabinet_case.__mro__[1:]
            for name, value in base.__dict__.items()
            if name.startswith('test_') and callable(value)
        }

        self.assertEqual(len(selected), 28)
        self.assertEqual(selected_names, local_names)
        self.assertFalse(selected_names & (base_names - local_names))
        expected_unprefixed = {
            'test_cabinet_view_move_and_use_authority_are_separate',
            'test_cabinet_placement_requires_effective_file_authority',
            'test_registered_worktree_scope_controls_cabinet_placement_and_file_use',
        }
        self.assertTrue(expected_unprefixed <= selected_names)
        self.assertTrue({name for name in local_names
                         if name.startswith('test_actor_cabinet_')} <= selected_names)

        legacy_loader = unittest.TestLoader()
        legacy_loader.testNamePatterns = ['*actor_cabinet_*']
        # CMake executes this file as __main__, so -k matches the method name,
        # not the imported module name containing "actor_cabinet".
        with patch.object(cabinet_case, '__module__', '__main__'):
            legacy_suite = legacy_loader.loadTestsFromModule(gateway)
        legacy_names = {case.id().rsplit('.', 1)[-1] for case in legacy_suite}
        self.assertEqual(legacy_names, {
            name for name in selected_names if 'actor_cabinet_' in name
        })
        self.assertEqual(selected_names - legacy_names, expected_unprefixed)

        cmake = Path(__file__).resolve().parents[2] / 'CMakeLists.txt'
        source = cmake.read_text(encoding='utf-8')
        registration = re.search(
            r'add_test\(NAME workspace-actor-cabinet-gateway COMMAND(.*?)\)\s*'
            r'set_tests_properties\(workspace-actor-cabinet-gateway',
            source, re.DOTALL)
        self.assertIsNotNone(registration)
        self.assertRegex(registration.group(1), r'\$<TARGET_FILE:worldd>\s*$')


if __name__ == '__main__':
    unittest.main(verbosity=2)
