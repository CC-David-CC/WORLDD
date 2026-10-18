import json
from contextlib import contextmanager
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tools import m08_deployed_gate as gate


class M08InventoryGateTests(unittest.TestCase):
    def inventory(self, omit=None):
        names = set(gate.REQUIRED)
        if omit:
            names.remove(omit)
        return [{"name": name, "properties": []} for name in sorted(names)]

    def deployment(self, inventory):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "provenance.json").write_text(json.dumps({
            "profiles": {profile: {"inventory": inventory} for profile in gate.m06.PROFILES}
        }))
        self.addCleanup(temp.cleanup)
        return root

    def args(self, deployment):
        return SimpleNamespace(
            deployment=deployment, attempt="attempt",
            native_controller="host-actor-controller-native",
            real_world="actor-transition-real-world", recovery="actor-transition-recovery",
            host_session="host-session-production-native",
            held_input="host-actor-controller-native", selected_profiles=None,
            collector_continuation=None, collector_continuation_sha256=None)

    def test_missing_m08_cabinet_registration_refuses_before_gate_launch(self):
        inventory = self.inventory("workspace-file-action-broker")
        deployment = self.deployment(inventory)
        with patch.object(gate.m06, "gate") as launch:
            with self.assertRaisesRegex(RuntimeError, "workspace-file-action-broker"):
                gate.run_local(self.args(deployment))
        launch.assert_not_called()

    def test_missing_nested_mount_refuses_in_each_profile_before_gate_launch(self):
        for profile in gate.m06.PROFILES:
            with self.subTest(profile=profile):
                deployment = self.deployment(self.inventory())
                provenance = json.loads((deployment / "provenance.json").read_text())
                provenance["profiles"][profile]["inventory"] = self.inventory("copy-tree-worker-nested-mount")
                (deployment / "provenance.json").write_text(json.dumps(provenance))
                with patch.object(gate.m06, "gate") as launch:
                    with self.assertRaisesRegex(RuntimeError, "copy-tree-worker-nested-mount"):
                        gate.run_local(self.args(deployment))
                launch.assert_not_called()

    def test_missing_inherited_m07_registration_refuses_before_gate_launch(self):
        inventory = self.inventory("workspace-copy-engine")
        deployment = self.deployment(inventory)
        with patch.object(gate.m06, "gate") as launch:
            with self.assertRaisesRegex(RuntimeError, "workspace-copy-engine"):
                gate.run_local(self.args(deployment))
        launch.assert_not_called()

    def test_missing_inherited_m06_registration_refuses_before_gate_launch(self):
        inventory = self.inventory("native-parent-metadata")
        deployment = self.deployment(inventory)
        with patch.object(gate.m06, "gate") as launch:
            with self.assertRaisesRegex(RuntimeError, "native-parent-metadata"):
                gate.run_local(self.args(deployment))
        launch.assert_not_called()

    def test_valid_inventory_passes_extras_and_preserves_effective_required_set(self):
        inventory = self.inventory()
        deployment = self.deployment(inventory)
        storage_by_profile = {profile: {"SPATIAL_TEST_STORAGE_VOLUME": "/volume/" + profile,
                                        "SPATIAL_TEST_STORAGE_LAUNCH_CONFIG": "/policy/" + profile,
                                        "SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES": gate.STORAGE_COPY_QUOTA_BYTES}
                              for profile in gate.m06.PROFILES}
        with patch.object(gate, "require_storage_lane_configuration", return_value=storage_by_profile), \
             patch.object(gate.m06, "gate", return_value=0) as launch:
            self.assertEqual(gate.run_local(self.args(deployment)), 0)
        forwarded = launch.call_args.args[0]
        self.assertEqual(set(forwarded.required_tests), gate.m07.M07_REQUIRED | gate.M08_REQUIRED)
        effective = gate.m06.required_test_names(forwarded.required_tests, ())
        self.assertEqual(set(effective), gate.REQUIRED)
        self.assertEqual(set(forwarded.dedicated_crosshost), gate.M08_DEDICATED_CROSSHOST)
        self.assertEqual(forwarded.storage_lane_by_profile, storage_by_profile)
        self.assertEqual(tuple(forwarded.expected_harness_tests), gate.load_harness_test_inventory())
        self.assertEqual(set(forwarded.storage_expected_results), gate.M08_STORAGE_EXPECTED_RESULTS)

    def harness_output(self, test_ids, status_by_id=None, summary_count=None):
        status_by_id = status_by_id or {}
        lines = [f"{name.rsplit('.', 1)[-1]} ({name}) ... {status_by_id.get(name, 'ok')}"
                 for name in test_ids]
        count = len(test_ids) if summary_count is None else summary_count
        return "\n".join(lines + ["", f"Ran {count} tests in 0.001s", "", "OK", ""])

    def test_m08_harness_inventory_preserves_51_baseline_plus_38_additions(self):
        test_ids = gate.load_harness_test_inventory()
        self.assertEqual(len(test_ids), 89)
        self.assertEqual(len(set(test_ids)), 89)
        self.assertEqual(gate.M08_BASELINE_SHA256, "e4c14f05414e9cbd39f881fbb5d6fe45ffd5301f8f5c0a693bd36ec1808853f2")
        self.assertEqual(gate.M08_HARNESS_GROUP_COUNTS, {
            "test_oracles": 16, "test_tools": 11, "test_recovery_wire": 8,
            "test_transport": 12, "test_workspace_wire": 4,
            "test_m08_deployed_gate": 32, "test_private_evidence_scope": 5,
            "test_resource_broker_environment": 1})
        self.assertEqual(gate.m06.validate_harness_test_inventory(
            self.harness_output(test_ids), test_ids)["harness_inventory_valid"], True)
        self.assertEqual(gate.m06.validate_harness_test_inventory(
            "Ran 51 tests in 0.001s\n\nOK\n")["expected_51"], True)

    def test_m08_harness_inventory_rejects_missing_extra_duplicate_wrong_count_and_skip(self):
        expected = gate.load_harness_test_inventory()
        missing = list(expected[:-1])
        self.assertFalse(gate.m06.validate_harness_test_inventory(
            self.harness_output(missing), expected)["harness_inventory_valid"])
        unexpected = list(expected[:-1]) + ["tests.harness.extra.Case.test_extra"]
        result = gate.m06.validate_harness_test_inventory(self.harness_output(unexpected), expected)
        self.assertFalse(result["harness_inventory_valid"])
        self.assertIn("tests.harness.extra.Case.test_extra", result["unexpected_harness_tests"])
        duplicated = list(expected[:-1]) + [expected[0], expected[0]]
        self.assertFalse(gate.m06.validate_harness_test_inventory(
            self.harness_output(duplicated), expected)["harness_inventory_valid"])
        self.assertFalse(gate.m06.validate_harness_test_inventory(
            self.harness_output(expected, summary_count=82), expected)["harness_inventory_valid"])
        skipped = {expected[0]: "skipped 'missing fixture'"}
        result = gate.m06.validate_harness_test_inventory(self.harness_output(expected, skipped), expected)
        self.assertFalse(result["harness_inventory_valid"])
        self.assertEqual(result["skipped_harness_tests"], [expected[0]])

    def test_m08_harness_manifest_matches_unittest_discovery_without_running_tests(self):
        suite = unittest.defaultTestLoader.discover("tests/harness", top_level_dir=".")
        def flatten(node):
            for item in node:
                if isinstance(item, unittest.TestSuite):
                    yield from flatten(item)
                else:
                    yield item.id()
        self.assertEqual(tuple(sorted(flatten(suite))), gate.load_harness_test_inventory())

    def test_m08_harness_manifest_refuses_missing_required_source_case(self):
        manifest = json.loads(gate.M08_HARNESS_MANIFEST.read_text())
        manifest["m08_addition_tests"].pop()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inventory.json"
            path.write_text(json.dumps(manifest))
            with patch.object(gate, "M08_HARNESS_MANIFEST", path):
                with self.assertRaisesRegex(RuntimeError, "count contract"):
                    gate.load_harness_test_inventory()

    def test_m06_storage_five_and_m08_storage_six_contracts_are_exact(self):
        m06_default = set(gate.m06.storage_scenario_result_names())
        self.assertEqual(m06_default, {"large_import_retirement", "application_and_actor_quota",
            "read_only", "sibling_survival", "restart"})
        self.assertEqual(set(gate.m06.storage_scenario_result_names(m06_default)), m06_default)
        self.assertEqual(set(gate.m06.storage_scenario_result_names(gate.M08_STORAGE_EXPECTED_RESULTS)),
                         gate.M08_STORAGE_EXPECTED_RESULTS)
        self.assertIn("production_application_copy_stage_mask", gate.M08_STORAGE_EXPECTED_RESULTS)
        five_results = {name: {"passed": True} for name in m06_default}
        six_results = {name: {"passed": True} for name in gate.M08_STORAGE_EXPECTED_RESULTS}
        self.assertEqual(set(gate.m06.validate_storage_scenario_receipt({"results": five_results})), m06_default)
        with self.assertRaisesRegex(RuntimeError, "required result inventory"):
            gate.m06.validate_storage_scenario_receipt({"results": five_results}, gate.M08_STORAGE_EXPECTED_RESULTS)
        self.assertEqual(set(gate.m06.validate_storage_scenario_receipt(
            {"results": six_results}, gate.M08_STORAGE_EXPECTED_RESULTS)), gate.M08_STORAGE_EXPECTED_RESULTS)
        for malformed in (dict(six_results, unexpected={"passed": True}),
                          {key: value for key, value in six_results.items()
                           if key != "production_application_copy_stage_mask"}):
            with self.subTest(malformed=sorted(malformed)):
                with self.assertRaisesRegex(RuntimeError, "required result inventory"):
                    gate.m06.validate_storage_scenario_receipt(
                        {"results": malformed}, gate.M08_STORAGE_EXPECTED_RESULTS)
        for invalid in (m06_default | {"unexpected"},
                        gate.M08_STORAGE_EXPECTED_RESULTS - {"restart"}):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(RuntimeError, "missing required or contains unexpected"):
                    gate.m06.storage_scenario_result_names(invalid)

    def test_copy_tests_are_not_substituted_by_m07_workspace_copy_engine(self):
        self.assertIn("cabinet-copy-path", gate.M08_REQUIRED)
        self.assertIn("copy-tree-worker", gate.M08_REQUIRED)
        self.assertNotIn("workspace-copy-engine", gate.M08_REQUIRED)
        self.assertIn("workspace-copy-engine", gate.INHERITED_REQUIRED)

    def test_debug_and_sanitizer_inventories_are_both_required(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "provenance.json").write_text(json.dumps({
            "profiles": {"Debug": {"inventory": self.inventory()}}
        }))
        with self.assertRaisesRegex(RuntimeError, "Debug and ASan-UBSan"):
            gate.require_deployment_inventory(root)
        temp.cleanup()


    def test_inventory_refusals_cover_each_profile_before_gate_launch(self):
        for profile in gate.m06.PROFILES:
            for defect, expected in (("missing", "workspace-file-action-broker"),
                                     ("disabled", "disabled CTest registrations"),
                                     ("duplicate", "duplicate CTest registrations")):
                with self.subTest(profile=profile, defect=defect):
                    deployment = self.deployment(self.inventory())
                    provenance = json.loads((deployment / "provenance.json").read_text())
                    inventory = provenance["profiles"][profile]["inventory"]
                    if defect == "missing":
                        inventory[:] = [r for r in inventory if r["name"] != "workspace-file-action-broker"]
                    elif defect == "disabled":
                        inventory[0]["properties"] = [{"name": "DISABLED", "value": True}]
                    else:
                        inventory.append(inventory[0])
                    (deployment / "provenance.json").write_text(json.dumps(provenance))
                    with patch.object(gate.m06, "gate") as launch:
                        with self.assertRaisesRegex(RuntimeError, expected):
                            gate.run_local(self.args(deployment))
                    launch.assert_not_called()

    def test_malformed_inventory_fails_closed_under_inherited_audit(self):
        # Preserve inherited audit semantics, including structural exceptions.
        for malformed, error in (([{}], KeyError), ([None], TypeError),
                                  ([{"name": []}], TypeError)):
            with self.subTest(inventory=malformed):
                deployment = self.deployment(malformed)
                with patch.object(gate.m06, "gate") as launch:
                    with self.assertRaises(error):
                        gate.run_local(self.args(deployment))
                launch.assert_not_called()

    def test_malformed_profile_inventory_refuses_before_gate_launch(self):
        for profile in gate.m06.PROFILES:
            for row in ({}, {"inventory": {}}, None):
                with self.subTest(profile=profile, row=row):
                    deployment = self.deployment(self.inventory())
                    provenance = json.loads((deployment / "provenance.json").read_text())
                    provenance["profiles"][profile] = row
                    (deployment / "provenance.json").write_text(json.dumps(provenance))
                    with patch.object(gate.m06, "gate") as launch:
                        with self.assertRaisesRegex(RuntimeError, "missing inventory for " + profile):
                            gate.run_local(self.args(deployment))
                    launch.assert_not_called()

    def test_each_new_required_registration_is_checked_in_both_profiles(self):
        names = ("copy-worker-stream", "workspace-copy-worker-helpers-tooling", "process-scope-named-regression",
                 "workspace-file-action-storage-owner-btrfs", "storage-owner-fixture-tooling",
                 "workspace-cabinet-native-crosshost")
        for profile in gate.m06.PROFILES:
            for name in names:
                with self.subTest(profile=profile, name=name):
                    deployment = self.deployment(self.inventory())
                    provenance = json.loads((deployment / "provenance.json").read_text())
                    provenance["profiles"][profile]["inventory"] = self.inventory(name)
                    (deployment / "provenance.json").write_text(json.dumps(provenance))
                    with patch.object(gate.m06, "gate") as launch:
                        with self.assertRaisesRegex(RuntimeError, name):
                            gate.run_local(self.args(deployment))
                    launch.assert_not_called()

    def test_required_test_skip_properties_fail_closed(self):
        for skip_property in ("SKIP_RETURN_CODE", "SKIP_REGULAR_EXPRESSION"):
            with self.subTest(skip_property=skip_property):
                rows = self.inventory()
                target = next(row for row in rows if row["name"] == "workspace-file-action-storage-owner-btrfs")
                target["properties"] = [{"name": skip_property, "value": 77 if skip_property == "SKIP_RETURN_CODE" else "SKIP"}]
                with self.assertRaisesRegex(RuntimeError, "can skip"):
                    gate.require_inventory(rows)

    def test_storage_lane_missing_or_invalid_configuration_refuses_before_gate(self):
        with self.assertRaisesRegex(RuntimeError, "SPATIAL_TEST_STORAGE_VOLUME_DEBUG"):
            gate.require_storage_lane_configuration({})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = {}
            for profile in gate.m06.PROFILES:
                suffix = gate.STORAGE_PROFILE_KEYS[profile]
                volume = root / ("volume-" + suffix)
                volume.mkdir()
                config = root / ("launch-" + suffix + ".bin")
                config.write_bytes(b"fixture")
                base["SPATIAL_TEST_STORAGE_VOLUME_" + suffix] = str(volume)
                base["SPATIAL_TEST_STORAGE_LAUNCH_CONFIG_" + suffix] = str(config)
                base["SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES_" + suffix] = gate.STORAGE_COPY_QUOTA_BYTES
            with patch.object(gate, "validate_storage_volume") as volume_check:
                prepared = gate.require_storage_lane_configuration(base)
                self.assertEqual(set(prepared), set(gate.m06.PROFILES))
                self.assertEqual(volume_check.call_count, len(gate.m06.PROFILES))
                suffix = gate.STORAGE_PROFILE_KEYS["Debug"]
                for key, value, expected in (
                        ("SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES_" + suffix, "12", "exactly"),
                        ("SPATIAL_TEST_STORAGE_LAUNCH_CONFIG_" + suffix, str(root / "absent"), "readable"),
                        ("SPATIAL_TEST_STORAGE_VOLUME_" + suffix, "relative", "absolute")):
                    with self.subTest(key=key):
                        env = dict(base)
                        env[key] = value
                        with self.assertRaisesRegex(RuntimeError, expected):
                            gate.require_storage_lane_configuration(env)
            duplicate = dict(base)
            duplicate["SPATIAL_TEST_STORAGE_VOLUME_ASAN_UBSAN"] = duplicate["SPATIAL_TEST_STORAGE_VOLUME_DEBUG"]
            with patch.object(gate, "validate_storage_volume"):
                with self.assertRaisesRegex(RuntimeError, "distinct StorageOwner Btrfs volume"):
                    gate.require_storage_lane_configuration(duplicate)
            incomplete = dict(base)
            for variable in ("VOLUME", "LAUNCH_CONFIG", "COPY_QUOTA_BYTES"):
                incomplete.pop("SPATIAL_TEST_STORAGE_" + variable + "_ASAN_UBSAN")
            with self.assertRaisesRegex(RuntimeError, "SPATIAL_TEST_STORAGE_VOLUME_ASAN_UBSAN"):
                gate.require_storage_lane_configuration(incomplete, profiles=("ASan-UBSan",))

    def test_storage_volume_rejects_non_btrfs_and_nested_mounts(self):
        with tempfile.TemporaryDirectory() as temp:
            volume = Path(temp).resolve()
            target = str(volume)
            ext4 = "30 25 8:1 / " + target + " rw,relatime - ext4 /dev/sda1 rw"
            with self.assertRaisesRegex(RuntimeError, "writable top-level Btrfs"):
                gate.validate_storage_volume(volume, mountinfo=ext4)
            nested = "30 25 8:1 /subvol " + target + " rw,relatime - btrfs /dev/sda1 rw"
            with self.assertRaisesRegex(RuntimeError, "writable top-level Btrfs"):
                gate.validate_storage_volume(volume, mountinfo=nested)
            readonly = "30 25 8:1 / " + target + " ro,relatime - btrfs /dev/sda1 rw"
            with self.assertRaisesRegex(RuntimeError, "writable top-level Btrfs"):
                gate.validate_storage_volume(volume, mountinfo=readonly)
            for option in ("compress=zstd", "compress=lzo", "compress-force", "compress-force=no"):
                with self.subTest(option=option):
                    compressed = "30 25 8:1 / " + target + " rw," + option + " - btrfs /dev/sda1 rw"
                    with self.assertRaisesRegex(RuntimeError, "compression disabled"):
                        gate.validate_storage_volume(volume, mountinfo=compressed)

    def test_storage_volume_accepts_absent_or_explicitly_disabled_compression(self):
        with tempfile.TemporaryDirectory() as temp:
            volume = Path(temp).resolve()
            rootid = subprocess.CompletedProcess([], 0, stdout="5\n", stderr="")
            devices = subprocess.CompletedProcess([], 0,
                stdout="Label: none\n\tTotal devices 1 FS bytes used 0\n", stderr="")
            for option in ("", "compress=no"):
                with self.subTest(compression=option or "absent"):
                    options = "rw,relatime" + (("," + option) if option else "")
                    mountinfo = "30 25 8:1 / " + str(volume) + " " + options + " - btrfs /dev/sda1 rw"
                    with patch.object(type(volume), "stat", return_value=SimpleNamespace(st_ino=256)), \
                         patch.object(gate.subprocess, "run", side_effect=[rootid, devices]) as run:
                        gate.validate_storage_volume(volume, mountinfo=mountinfo)
                    self.assertEqual(run.call_count, 2)
                    self.assertEqual(run.call_args_list[0].args[0][:3],
                                     ["btrfs", "inspect-internal", "rootid"])
                    self.assertEqual(run.call_args_list[1].args[0][:3],
                                     ["btrfs", "filesystem", "show"])

    def test_storage_lane_prerequisite_failure_never_launches_gate(self):
        deployment = self.deployment(self.inventory())
        with patch.object(gate, "require_storage_lane_configuration",
                          side_effect=RuntimeError("Btrfs fixture missing")), \
             patch.object(gate.m06, "gate") as launch:
            with self.assertRaisesRegex(RuntimeError, "Btrfs fixture missing"):
                gate.run_local(self.args(deployment))
        launch.assert_not_called()

    def test_cabinet_crosshost_is_dedicated_and_m06_m07_defaults_are_unchanged(self):
        self.assertEqual(gate.m06.ctest_exclusion_expression(),
                         "^geometry-native-crosshost$")
        self.assertEqual(gate.m06.ctest_exclusion_expression(gate.m07.M07_DEDICATED),
                         "^(geometry-native-crosshost|workspace-geometry-native-crosshost)$")
        self.assertEqual(gate.m07.M07_DEDICATED,
                         frozenset({"geometry-native-crosshost", "workspace-geometry-native-crosshost"}))
        self.assertEqual(gate.m07.workspace_crosshost_core_case_count(90), 88)
        self.assertEqual(gate.m07.workspace_crosshost_core_case_count(
                         90, "workspace-cabinet-native-crosshost"), 87)
        expected = set(gate.m07.M07_DEDICATED) | {"workspace-cabinet-native-crosshost"}
        self.assertEqual(set(gate.M08_DEDICATED_CROSSHOST), expected)
        self.assertEqual(gate.m06.ctest_exclusion_expression(gate.M08_DEDICATED_CROSSHOST),
                         "^(geometry-native-crosshost|workspace-geometry-native-crosshost|workspace-cabinet-native-crosshost)$")

    def test_workspace_crosshost_runner_selects_cabinet_ctest_explicitly(self):
        geometry = {"name": "workspace-geometry-native-crosshost",
                    "command": ["test_workspace_geometry_native_crosshost.py"]}
        cabinet = {"name": "workspace-cabinet-native-crosshost",
                   "command": ["tests/core/test_cabinet_native_crosshost.py"]}
        self.assertEqual(gate.m07.workspace_crosshost_registration([geometry, cabinet]), geometry)
        selected = gate.m07.workspace_crosshost_registration([geometry, cabinet],
                    "workspace-cabinet-native-crosshost")
        self.assertEqual(selected, cabinet)
        self.assertIn("test_cabinet_native_crosshost.py", selected["command"][0])
        self.assertEqual(gate.m07.WORKSPACE_CROSSHOST_CASES["workspace-cabinet-native-crosshost"],
                         "test_cabinet_projection_and_persistent_placement_reach_native_pixels")
        with self.assertRaisesRegex(RuntimeError, "missing or duplicated"):
            gate.m07.workspace_crosshost_registration([geometry], "workspace-cabinet-native-crosshost")
        self.assertEqual(gate.m07.workspace_crosshost_core_case_count(90), 88)
        self.assertEqual(gate.m07.workspace_crosshost_core_case_count(
                         90, "workspace-geometry-native-crosshost"), 88)


    @contextmanager
    def local_receipts(self, required, dedicated):
        names = sorted(set(required) | set(dedicated))
        candidate = "a" * 40
        profile_rows = {profile: {"commands": [{"name": "core", "exit_code": 0,
                         "junit_cases": len(names) - len(dedicated),
                         "all_registered_present": True, "failed_or_skipped": False}]}
                        for profile in gate.m06.PROFILES}
        report = {"candidate": candidate, "required_tests": sorted(required),
                  "dedicated_crosshost": sorted(dedicated), "profiles": profile_rows}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sb16.json"
            path.write_text(json.dumps(report))
            receipts = {
                "sb16": {"path": str(path), "sha256": gate.m06.sha(path),
                         "inventories": {profile: {"names": names} for profile in gate.m06.PROFILES}},
                "sb16-2": {"remote_receipt": report,
                           "inventories": {profile: {"names": names} for profile in gate.m06.PROFILES}},
            }
            yield candidate, receipts

    def test_m08_matrix_receipts_require_every_m08_group_and_all_three_exclusions(self):
        dedicated = gate.M08_DEDICATED_CROSSHOST
        for missing in sorted(gate.M08_REQUIRED):
            with self.subTest(missing=missing):
                reported_required = set(gate.REQUIRED) - {missing}
                with self.local_receipts(reported_required, dedicated) as (candidate, receipts):
                    with self.assertRaisesRegex(RuntimeError, "mandatory suites|incomplete"):
                        gate.m07.validate_workspace_local_receipts(receipts, candidate,
                            "workspace-geometry-native-crosshost", gate.REQUIRED, dedicated)
        for missing in ("workspace-geometry-native-crosshost", "workspace-cabinet-native-crosshost"):
            with self.subTest(missing_dedicated=missing):
                reduced = set(dedicated) - {missing}
                with self.local_receipts(gate.REQUIRED, reduced) as (candidate, receipts):
                    with self.assertRaisesRegex(RuntimeError, "mandatory suites"):
                        gate.m07.validate_workspace_local_receipts(receipts, candidate,
                            "workspace-cabinet-native-crosshost", gate.REQUIRED, dedicated)

    def test_m08_requires_both_independent_crosshost_result_indexes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            geometry = root / "geometry.json"
            cabinet = root / "cabinet.json"
            geometry.write_text('[{"path":"geometry-receipt.json"}]')
            cabinet.write_text('[{"path":"cabinet-receipt.json"}]')
            self.assertEqual(set(gate.require_crosshost_result_indexes(geometry, cabinet)),
                             {"workspace-geometry-native-crosshost", "workspace-cabinet-native-crosshost"})
            for missing in (geometry, cabinet):
                with self.subTest(missing=missing.name):
                    missing.write_text("[]")
                    with self.assertRaisesRegex(RuntimeError, "nonempty JSON array"):
                        gate.require_crosshost_result_indexes(geometry, cabinet)
                    missing.unlink()
                    with self.assertRaisesRegex(RuntimeError, "separate result index"):
                        gate.require_crosshost_result_indexes(geometry, cabinet)
                    missing.write_text('[{"path":"receipt.json"}]')

    def test_m08_reconcile_routes_both_matrices_and_writes_separate_report(self):
        candidate = "b" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            geometry_index = root / "geometry-results.json"
            cabinet_index = root / "cabinet-results.json"
            geometry_index.write_text('[{"path":"geometry-row.json"}]')
            cabinet_index.write_text('[{"path":"cabinet-row.json"}]')
            output = root / "m08-output"
            m06_receipt = root / "m06-reconciliation.json"
            m06_receipt.write_text("{}")
            invocations = []

            def validate(args):
                invocations.append(args)
                args.output.mkdir(parents=True)
                receipt = {"test_name": args.test_name, "receipt_id": "receipt-for-" + args.test_name}
                gate.m06.write_json(args.output / "m07-reconciliation.json",
                                    {"workspace_crosshost": [receipt]})
                return 0

            args = SimpleNamespace(candidate=candidate,
                m06_reconciliation=m06_receipt,
                workspace_geometry_results=geometry_index,
                cabinet_crosshost_results=cabinet_index, output=output)
            with patch.object(gate.m07, "reconcile", side_effect=validate):
                self.assertEqual(gate.reconcile_workspace_cabinet_crosshost(args), 0)
            self.assertEqual([call.test_name for call in invocations], [
                "workspace-geometry-native-crosshost", "workspace-cabinet-native-crosshost"])
            self.assertEqual([call.workspace_results for call in invocations], [geometry_index, cabinet_index])
            for call in invocations:
                self.assertEqual(set(call.required_tests), gate.REQUIRED)
                self.assertEqual(set(call.dedicated_tests), gate.M08_DEDICATED_CROSSHOST)
            report_path = output / "m08-reconciliation.json"
            report = json.loads(report_path.read_text())
            self.assertEqual(report["candidate"], candidate)
            self.assertIs(report["phase_complete"], False)
            self.assertEqual(report["workspace_geometry_crosshost"]["result_index"], str(geometry_index.resolve()))
            self.assertEqual(report["workspace_geometry_crosshost"]["result_index_sha256"], gate.m06.sha(geometry_index))
            self.assertEqual(report["workspace_geometry_crosshost"]["receipts"][0]["receipt_id"],
                             "receipt-for-workspace-geometry-native-crosshost")
            self.assertEqual(report["workspace_cabinet_crosshost"]["result_index"], str(cabinet_index.resolve()))
            self.assertEqual(report["workspace_cabinet_crosshost"]["result_index_sha256"], gate.m06.sha(cabinet_index))
            self.assertEqual(report["workspace_cabinet_crosshost"]["receipts"][0]["receipt_id"],
                             "receipt-for-workspace-cabinet-native-crosshost")
            self.assertEqual(report["status"], "passed_needs_primary_review")
            self.assertEqual(report["qualification"], "UNQUALIFIED")

    def test_m08_reconcile_second_matrix_failure_does_not_publish_report(self):
        candidate = "c" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            geometry_index = root / "geometry-results.json"
            cabinet_index = root / "cabinet-results.json"
            geometry_index.write_text('[{"path":"geometry-row.json"}]')
            cabinet_index.write_text('[{"path":"cabinet-row.json"}]')
            output = root / "m08-output"
            m06_receipt = root / "m06-reconciliation.json"
            m06_receipt.write_text("{}")
            calls = []

            def validate(args):
                calls.append(args.test_name)
                if len(calls) == 2:
                    raise RuntimeError("cabinet matrix receipt validation failed")
                args.output.mkdir(parents=True)
                gate.m06.write_json(args.output / "m07-reconciliation.json", {"workspace_crosshost": []})
                return 0

            args = SimpleNamespace(candidate=candidate,
                m06_reconciliation=m06_receipt,
                workspace_geometry_results=geometry_index,
                cabinet_crosshost_results=cabinet_index, output=output)
            with patch.object(gate.m07, "reconcile", side_effect=validate):
                with self.assertRaisesRegex(RuntimeError, "cabinet matrix receipt validation failed"):
                    gate.reconcile_workspace_cabinet_crosshost(args)
            self.assertEqual(calls, ["workspace-geometry-native-crosshost",
                                     "workspace-cabinet-native-crosshost"])
            self.assertFalse(output.exists())

    def test_m07_default_receipt_contract_remains_two_dedicated_tests(self):
        required = set(gate.m07.M07_REQUIRED)
        dedicated = set(gate.m07.M07_DEDICATED)
        with self.local_receipts(required, dedicated) as (candidate, receipts):
            self.assertIsNone(gate.m07.validate_workspace_local_receipts(
                receipts, candidate, "workspace-geometry-native-crosshost"))
        self.assertEqual(gate.m07.workspace_crosshost_core_case_count(90), 88)

    def test_storage_profile_environment_is_scoped_and_validated(self):
        debug = {"SPATIAL_TEST_STORAGE_VOLUME": "/btrfs/debug",
                 "SPATIAL_TEST_STORAGE_LAUNCH_CONFIG": "/policy/debug",
                 "SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES": gate.STORAGE_COPY_QUOTA_BYTES}
        asan = {"SPATIAL_TEST_STORAGE_VOLUME": "/btrfs/asan",
                "SPATIAL_TEST_STORAGE_LAUNCH_CONFIG": "/policy/asan",
                "SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES": gate.STORAGE_COPY_QUOTA_BYTES}
        lanes = {"Debug": debug, "ASan-UBSan": asan}
        base = {"KEEP": "yes", "SPATIAL_TEST_STORAGE_VOLUME": "/stale"}
        debug_env = gate.m06.apply_profile_storage_environment(base, "Debug", lanes)
        asan_env = gate.m06.apply_profile_storage_environment(base, "ASan-UBSan", lanes)
        self.assertEqual(debug_env["SPATIAL_TEST_STORAGE_VOLUME"], "/btrfs/debug")
        self.assertEqual(asan_env["SPATIAL_TEST_STORAGE_VOLUME"], "/btrfs/asan")
        self.assertEqual(debug_env["KEEP"], "yes")
        self.assertEqual(base["SPATIAL_TEST_STORAGE_VOLUME"], "/stale")
        self.assertEqual(gate.m06.apply_profile_storage_environment(base, "Debug", None), base)
        with self.assertRaisesRegex(RuntimeError, "environment is incomplete"):
            gate.m06.apply_profile_storage_environment(base, "Debug", {"Debug": {"SPATIAL_TEST_STORAGE_VOLUME": "/bad"}})

    def test_m08_inventory_validator_keeps_cabinet_test_out_of_per_profile_core(self):
        names = sorted(gate.REQUIRED)
        dedicated = gate.m06.DEDICATED_CROSSHOST | {"workspace-geometry-native-crosshost",
                                                     "workspace-cabinet-native-crosshost"}
        inventory = {"names": names, "dedicated_crosshost": sorted(dedicated),
                     "local_names": sorted(set(names) - dedicated)}
        self.assertIn("workspace-cabinet-native-crosshost",
                      gate.m06._validate_geometry_inventory(inventory, "sb16", "Debug",
                                                            gate.REQUIRED, dedicated))

    def cli(self, deployment):
        # Execute the real argument parser and command dispatcher with only external work stubbed.
        code = """
import importlib.util, json, sys
path = sys.argv.pop(1)
m08_expected = set(json.loads(sys.argv.pop(1)))
effective_expected = set(json.loads(sys.argv.pop(1)))
spec = importlib.util.spec_from_file_location('m08_under_test', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def launch(args):
    expected = module.m07.M07_REQUIRED | m08_expected
    assert set(args.required_tests) == expected
    assert set(module.m06.required_test_names(args.required_tests, ())) == effective_expected
    assert args.selected_profiles == ['Debug']
    assert args.collector_continuation_sha256 == 'abc'
    print('STUB_GATE_REACHED')
    return 0
module.m06.gate = launch
module.require_storage_lane_configuration = lambda **kwargs: {}
sys.argv = [path] + sys.argv[1:]
raise SystemExit(module.main())
"""
        return subprocess.run([sys.executable, '-B', '-c', code,
            str(Path(gate.__file__).resolve()), json.dumps(sorted(gate.M08_REQUIRED)),
            json.dumps(sorted(gate.REQUIRED)), 'run-local', '--deployment', str(deployment),
            '--native-controller', 'controller', '--real-world', 'world', '--recovery', 'recovery',
            '--host-session', 'session', '--held-input', 'held', '--selected-profile', 'Debug',
            '--collector-continuation-sha256', 'abc'], capture_output=True, text=True)

    def test_actual_cli_success_reaches_inherited_gate_with_required_propagation(self):
        result = self.cli(self.deployment(self.inventory()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('STUB_GATE_REACHED', result.stdout)

    def test_actual_cli_invalid_inventory_refuses_before_inherited_gate(self):
        result = self.cli(self.deployment(self.inventory('workspace-file-engine')))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('workspace-file-engine', result.stderr)
        self.assertNotIn('STUB_GATE_REACHED', result.stdout)


if __name__ == "__main__":
    unittest.main()
