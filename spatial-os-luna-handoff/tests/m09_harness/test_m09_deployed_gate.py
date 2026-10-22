import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import m09_deployed_gate as gate


class M09DeployedGateTests(unittest.TestCase):
    def test_case_manifest_has_seven_named_cases_and_no_duplicate_selectors(self):
        self.assertEqual(len(gate.M09_CASES), 7)
        selectors = gate._case_selectors()
        self.assertEqual(len(selectors), len(set(selectors)))
        self.assertEqual(len(gate.M09_HARNESS_TESTS), 8)

    def test_case_source_inventory_fails_closed_for_missing_future_witness(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            for selector in gate._case_selectors():
                parts = selector.split(".")
                path = source.joinpath(*parts[:2], parts[2] + ".py")
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text("class " + parts[2] + ":\n", encoding="utf-8")
                if parts[4] != "test_dialog_popup_group_control_and_foreign_hint_use_connection_identity":
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write("    def " + parts[4] + "(self):\n        pass\n")
            with self.assertRaisesRegex(RuntimeError, "test_dialog_popup_group_control"):
                gate.validate_case_source(source)

    def test_case_source_inventory_rejects_duplicate_method_definitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            for selector in gate._case_selectors():
                parts = selector.split(".")
                path = source.joinpath(*parts[:2], parts[2] + ".py")
                path.parent.mkdir(parents=True, exist_ok=True)
                class_name, method = parts[3], parts[4]
                body = "class " + class_name + ":\n    def " + method + "(self):\n        pass\n"
                if method == "test_same_application_connections_can_form_relationship":
                    body += "    def " + method + "(self):\n        pass\n"
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(body)
            with self.assertRaisesRegex(RuntimeError, "must resolve exactly once"):
                gate.validate_case_source(source)

    def inventory(self):
        return [{"name": name, "properties": []} for name in sorted(gate.REQUIRED)]

    def test_required_inventory_rejects_missing_skip_and_duplicate_registration(self):
        inventory = [row for row in self.inventory() if row["name"] != "application-observation"]
        with self.assertRaisesRegex(RuntimeError, "application-observation"):
            gate.require_m09_inventory(inventory)
        inventory = self.inventory()
        inventory[0]["properties"] = [{"name": "SKIP_RETURN_CODE", "value": "77"}]
        with self.assertRaisesRegex(RuntimeError, "can skip"):
            gate.require_m09_inventory(inventory)
        inventory = self.inventory()
        inventory.append(dict(inventory[0]))
        with self.assertRaisesRegex(RuntimeError, "duplicate CTest"):
            gate.require_m09_inventory(inventory)

    def _valid_deployment(self, root, candidate):
        (root / "deployment-receipt.json").write_text(json.dumps({
            "candidate": candidate, "status": "verified"}), encoding="utf-8")
        source = root / "source"
        for selector in gate._case_selectors():
            parts = selector.split(".")
            path = source.joinpath(*parts[:2], parts[2] + ".py")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write("class " + parts[3] + ":\n    def " + parts[4] + "(self):\n        pass\n")
        (root / "provenance.json").write_text(json.dumps({
            "candidate": candidate,
            "profiles": {profile: {"inventory": self.inventory()} for profile in gate.m06.PROFILES}
        }), encoding="utf-8")

    def args(self, deployment, candidate):
        return SimpleNamespace(
            deployment=deployment, candidate=candidate, attempt="attempt",
            native_controller="host-actor-controller-native",
            real_world="actor-transition-real-world", recovery="actor-transition-recovery",
            host_session="host-session-production-native", held_input="host-actor-controller-native",
            selected_profiles=None, collector_continuation=None,
            collector_continuation_sha256=None)

    def test_wrong_candidate_refuses_before_m06_gate_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._valid_deployment(root, "a" * 40)
            with patch.object(gate.m06, "gate") as launch:
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    gate.run_local(self.args(root, "b" * 40))
            launch.assert_not_called()

    def test_valid_preflight_forwards_all_m08_and_m09_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = "a" * 40
            self._valid_deployment(root, candidate)
            storage = {profile: {"SPATIAL_TEST_STORAGE_VOLUME": "/volume/" + profile,
                                 "SPATIAL_TEST_STORAGE_LAUNCH_CONFIG": "/policy/" + profile,
                                 "SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES": gate.m08.STORAGE_COPY_QUOTA_BYTES}
                       for profile in gate.m06.PROFILES}
            with patch.object(gate, "run_m09_harness", return_value={"status": "passed_unqualified", "receipt_path": "/evidence/m09.json"}) as m09_harness, \
                 patch.object(gate.m08, "require_storage_lane_configuration", return_value=storage), \
                 patch.object(gate.m06, "gate", return_value=0) as launch:
                self.assertEqual(gate.run_local(self.args(root, candidate)), 0)
            m09_harness.assert_called_once()
            forwarded = launch.call_args.args[0]
            self.assertEqual(set(forwarded.required_tests), gate.REQUIRED)
            self.assertEqual(set(forwarded.dedicated_crosshost), gate.m08.M08_DEDICATED_CROSSHOST)
            self.assertEqual(tuple(forwarded.expected_harness_tests), gate.m08.load_harness_test_inventory())
            self.assertEqual(forwarded.storage_lane_by_profile, storage)
            self.assertTrue(gate.m08.REQUIRED.issubset(set(forwarded.required_tests)))

    def test_separate_m09_harness_inventory_rejects_missing_duplicate_skip_and_wrong_count(self):
        expected = gate.expected_m09_harness_tests()
        self.assertEqual(len(gate.m08.load_harness_test_inventory()), 89)
        self.assertEqual(len(expected), 8)
        self.assertFalse(set(expected) & set(gate.m08.load_harness_test_inventory()))

        def output(ids, status_by_id=None, count=None):
            status_by_id = status_by_id or {}
            rows = [f"{name.rsplit('.', 1)[-1]} ({name}) ... {status_by_id.get(name, 'ok')}"
                    for name in ids]
            total = len(ids) if count is None else count
            return "\n".join(rows + ["", f"Ran {total} tests in 0.001s", "", "OK", ""])

        text = output(expected)
        self.assertTrue(gate.validate_m09_harness_output(text, 0)["valid"])
        for invalid in (
                output(expected[:-1]),
                output(expected[:-1] + (expected[0], expected[0])),
                output(expected[:-1] + ("tests.harness.unexpected.Case.test_extra",)),
                output(expected, {expected[0]: "skipped 'fixture missing'"}),
                output(expected, count=7)):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(RuntimeError, "inventory failed"):
                    gate.validate_m09_harness_output(invalid, 0)
        with self.assertRaisesRegex(RuntimeError, "inventory failed"):
            gate.validate_m09_harness_output(text, 1)

    def test_m09_suite_manifest_is_additive_to_m08(self):
        self.assertEqual(Path(__file__).parent.name, "m09_harness")
        self.assertTrue(all(name.startswith("tests.m09_harness.") for name in gate.M09_HARNESS_TESTS))
        self.assertEqual(gate.M09_REQUIRED, {
            "application-control-lease", "application-observation",
            "world-application-control", "native-surface_identity",
            "native-world_surface_groups", "native-observer-endpoint"})
        self.assertEqual(gate.REQUIRED, gate.m08.REQUIRED | gate.M09_REQUIRED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
