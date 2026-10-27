"""Fail-closed checks for the additive M10 deployed qualification profile."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("m10_deployed_gate", ROOT / "tools/m10_deployed_gate.py")
m10 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m10)


class M10DeployedGateTests(unittest.TestCase):
    def test_required_profile_is_additive_to_m09(self):
        self.assertTrue(m10.m09.REQUIRED.issubset(m10.REQUIRED))
        self.assertEqual(m10.REQUIRED, m10.m09.REQUIRED | m10.M10_REQUIRED)

    def test_every_required_suite_name_is_unique_and_m10_scoped(self):
        self.assertEqual(len(m10.M10_REQUIRED), 55)
        self.assertTrue(all(isinstance(name, str) and name for name in m10.M10_REQUIRED))

    def test_named_case_suite_map_covers_every_module(self):
        selectors = m10._flatten_selectors()
        modules = {".".join(row.split(".")[:3]) for row in selectors}
        self.assertEqual(modules, set(m10.M10_SUITE_BY_MODULE))

    def test_named_sources_resolve_exactly_once(self):
        cases = m10.validate_case_source(ROOT)
        self.assertEqual(set(cases), set(m10.M10_SELECTORS))

    def test_deployment_inventory_preserves_m09_and_m10_case_sources(self):
        m09_sources = {"m09-case": {"suite": "m09"}}
        m10_sources = {"m10-case": {"suite": "m10"}}
        with tempfile.TemporaryDirectory() as temporary:
            deployment = Path(temporary)
            checked = {
                "provenance": {
                    "profiles": {profile: {"inventory": []} for profile in m10.m06.PROFILES}
                },
                "receipt": {},
                "source": deployment / "source",
                "case_sources": m09_sources,
            }
            with patch.object(m10.m09, "require_deployment_inventory", return_value=checked), \
                 patch.object(m10.m08, "require_deployment_inventory"), \
                 patch.object(m10, "require_m10_inventory") as inventory_check, \
                 patch.object(m10, "validate_case_source", return_value=m10_sources):
                result = m10.require_deployment_inventory(deployment, "a" * 40)
        self.assertEqual(result["case_sources"], m09_sources)
        self.assertEqual(result["m10_case_sources"], m10_sources)
        self.assertEqual(inventory_check.call_count, len(m10.m06.PROFILES))

    def test_named_method_log_requires_every_case_once(self):
        inventory = [{"name": name, "properties": []} for name in m10.REQUIRED]
        suites = {}
        for selector in m10.M10_SELECTORS:
            parts = selector.split(".")
            suite = m10.M10_SUITE_BY_MODULE[".".join(parts[:3])]
            suites.setdefault(suite, []).append((".".join(parts[:3]) + "." + parts[-2], parts[-1]))
        lines = []
        for number, (suite, methods) in enumerate(sorted(suites.items()), 1):
            lines.append(f"Start {number}: {suite}")
            for identity, method in methods:
                lines.append(f"{number}: {method} ({identity}) ... ok")
        result = m10.validate_m10_method_log("\n".join(lines), inventory)
        self.assertEqual(set(result), set(m10.M10_SELECTORS))

    def test_named_method_log_rejects_skipped_case(self):
        inventory = [{"name": name, "properties": []} for name in m10.REQUIRED]
        selector = m10.M10_SELECTORS[0]
        parts = selector.split(".")
        suite = m10.M10_SUITE_BY_MODULE[".".join(parts[:3])]
        identity = ".".join(parts[:3]) + "." + parts[-2]
        output = f"Start 1: {suite}\n1: {parts[-1]} ({identity}) ... skipped required"
        with self.assertRaisesRegex(RuntimeError, "coverage is incomplete"):
            m10.validate_m10_method_log(output, inventory)

    def test_inventory_rejects_missing_m10_suite(self):
        inventory = [{"name": name, "properties": []} for name in m10.REQUIRED if name != "application-slots"]
        with self.assertRaisesRegex(RuntimeError, "missing required CTest registrations"):
            m10.require_m10_inventory(inventory)

    def test_inventory_rejects_skippable_m10_suite(self):
        inventory = [{"name": name, "properties": []} for name in m10.REQUIRED]
        row = next(item for item in inventory if item["name"] == "application-slots")
        row["properties"].append({"name": "SKIP_RETURN_CODE", "value": 77})
        with self.assertRaisesRegex(RuntimeError, "can skip"):
            m10.require_m10_inventory(inventory)


if __name__ == "__main__":
    unittest.main(verbosity=2)
