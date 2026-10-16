"""Shared real-subprocess lifecycle assertions. This module consumes no CLI arguments."""
import json
import tempfile
from pathlib import Path


class WorldProcessLifecycleMixin:
    class Fixture:
        def __init__(self, root):
            self.root = root
            self.worlds = []
            self.service_cert = root / "service.crt"

        def certificate(self, name, common_name):
            return self.root / (name + ".key"), self.root / (name + ".crt")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-process-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = self.root / "wait-for-stdin"
        self.helper.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "for _ in sys.stdin.buffer:\n"
            "    pass\n"
        )
        self.helper.chmod(0o700)

    def emergency_reap(self, world):
        if world.proc.poll() is None:
            world.proc.kill()
            world.proc.wait(timeout=3)
        for stream in (world.proc.stdin, world.proc.stdout, world.log):
            if stream is not None and not stream.closed:
                stream.close()

    def assert_lifecycle_close(self, world):
        self.addCleanup(self.emergency_reap, world)
        self.assertIsNone(world.proc.poll())
        evidence_path = self.root / ("lifecycle-" + str(len(list(self.root.glob("lifecycle-*.json")))) + ".json")
        evidence_path.write_text("{}\n")
        world.lifecycle_evidence = evidence_path
        pid = world.proc.pid

        world.close()

        start_ticks = world.start_ticks
        self.assertIsInstance(start_ticks, int)
        self.assertGreater(start_ticks, 0)
        self.assertEqual(world.proc.poll(), 0)
        self.assertEqual(world.proc.returncode, 0)
        self.assertTrue(world.proc.stdin.closed)
        self.assertTrue(world.proc.stdout.closed)
        self.assertTrue(world.log.closed)
        lifecycle = json.loads(evidence_path.read_text())["worldd_lifecycle"]
        self.assertEqual(lifecycle["before_world_close"]["pid"], pid)
        self.assertEqual(lifecycle["before_world_close"]["start_ticks"], start_ticks)
        self.assertIsNone(lifecycle["before_world_close"]["poll"])
        self.assertEqual(lifecycle["after_world_close"]["pid"], pid)
        self.assertEqual(lifecycle["after_world_close"]["start_ticks"], start_ticks)
        self.assertEqual(lifecycle["after_world_close"]["poll"], 0)
        self.assertEqual(lifecycle["after_world_close"]["returncode"], 0)
        self.assertEqual(len(lifecycle["stderr_sha256"]), 64)
