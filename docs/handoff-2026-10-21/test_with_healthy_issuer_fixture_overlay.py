#!/usr/bin/env python3
"""Pure synthetic checks for the pinned healthy issuer fixture overlay."""
import hashlib
import json
import importlib.util
import marshal
import struct
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).with_name("with_healthy_issuer_fixture_overlay.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FixtureOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.entry_dir = self.root / "entry"
        self.entry_dir.mkdir()
        self.overlay = self.root / "healthy_issuer_setup.py"
        self.baseline = self.entry_dir / "healthy_issuer_setup.py"
        self.unrelated = self.entry_dir / "unrelated_fixture.py"
        self.marker = self.root / "entry-started.json"
        self.result = self.root / "result.json"

    def tearDown(self):
        self.temp.cleanup()

    def put(self, path, content):
        path.write_text(content, encoding="utf-8")
        return path

    def command(self, overlay_hash=None, entry_hash=None):
        return [sys.executable, str(TOOL), "--overlay", str(self.overlay),
                "--overlay-sha256", overlay_hash or digest(self.overlay),
                "--entrypoint", str(self.baseline),
                "--entrypoint-sha256", entry_hash or digest(self.baseline),
                "--", "--mode", "fixture-value", "two words"]

    def test_lazy_single_module_overlay_preserves_entrypoint_args_and_path(self):
        self.put(self.baseline, f'''from pathlib import Path
import json, sys
Path({str(self.marker)!r}).write_text(json.dumps({{"event": "entry-before-import", "argv": sys.argv[1:]}}))
sys.argv[:] = ["native-rewrite", "late-import"]
import healthy_issuer_setup as selected
import unrelated_fixture as untouched
Path({str(self.result)!r}).write_text(json.dumps({{"overlay": selected.OVERLAY, "poisoned": getattr(selected, "POISONED", False), "import_argv": selected.IMPORT_ARGV, "entry_ran_first": selected.ENTRY_RAN_FIRST, "unrelated": untouched.VALUE, "argv": sys.argv, "sys_path0": sys.path[0]}}))
''')
        self.put(self.overlay, f'''from pathlib import Path
import json, sys
OVERLAY = "pinned"
IMPORT_ARGV = list(sys.argv)
ENTRY_RAN_FIRST = json.loads(Path({str(self.marker)!r}).read_text())["event"] == "entry-before-import"
''')
        self.put(self.unrelated, "VALUE = 'ordinary-import'\n")
        cache = Path(importlib.util.cache_from_source(str(self.overlay)))
        cache.parent.mkdir(parents=True)
        poison = compile("OVERLAY = 'poison'\nPOISONED = True\n", str(self.overlay), "exec", dont_inherit=True)
        source_stat = self.overlay.stat()
        cache.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(source_stat.st_mtime), source_stat.st_size)
                          + marshal.dumps(poison))
        cached_bytes = cache.read_bytes()
        header = cached_bytes[4:16]
        self.assertEqual(struct.unpack("<III", header), (0, int(source_stat.st_mtime), source_stat.st_size))
        self.assertIn("poison", marshal.loads(cached_bytes[16:]).co_consts)
        before = {p: digest(p) for p in (TOOL, self.baseline, self.overlay, self.unrelated)}
        completed = subprocess.run(self.command(), capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(self.result.read_text())
        self.assertEqual(result["overlay"], "pinned")
        self.assertFalse(result["poisoned"])
        self.assertEqual(result["entry_ran_first"], True)
        self.assertEqual(result["import_argv"], ["native-rewrite", "late-import"])
        self.assertEqual(result["unrelated"], "ordinary-import")
        self.assertEqual(result["argv"], ["native-rewrite", "late-import"])
        self.assertEqual(result["sys_path0"], str(self.entry_dir))
        self.assertEqual(json.loads(self.marker.read_text())["argv"], ["--mode", "fixture-value", "two words"])
        self.assertIn(f"fixture_overlay_source={self.overlay}", completed.stderr)
        self.assertEqual(before, {p: digest(p) for p in before})

    def test_both_pin_mismatches_reject_before_entrypoint_execution(self):
        self.put(self.baseline, f'''from pathlib import Path
Path({str(self.marker)!r}).write_text("executed")
''')
        self.put(self.overlay, "VALUE = 'overlay'\n")
        bad_overlay = self.command(overlay_hash="0" * 64)
        first = subprocess.run(bad_overlay, capture_output=True, text=True)
        self.assertNotEqual(first.returncode, 0)
        self.assertIn("overlay SHA-256 mismatch", first.stderr)
        self.assertFalse(self.marker.exists())
        bad_entry = self.command(entry_hash="0" * 64)
        second = subprocess.run(bad_entry, capture_output=True, text=True)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("entrypoint SHA-256 mismatch", second.stderr)
        self.assertFalse(self.marker.exists())

    def test_overlay_changed_after_entrypoint_start_is_rejected_at_lazy_import(self):
        imported = self.root / "overlay-executed.txt"
        self.put(self.baseline, f'''from pathlib import Path
Path({str(self.marker)!r}).write_text("entrypoint started")
Path({str(self.overlay)!r}).write_text("__import__(\'pathlib\').Path({str(imported)!r}).write_text(\'unverified\')")
import healthy_issuer_setup
''')
        self.put(self.overlay, "VALUE = 'verified-before-entrypoint'\n")
        result = subprocess.run(self.command(), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch at import", result.stderr)
        self.assertEqual(self.marker.read_text(), "entrypoint started")
        self.assertFalse(imported.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
