#!/usr/bin/env python3
"""Static contract check for the opt-in ProcessScope read trace."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProcessScopeReadTraceSourceTests(unittest.TestCase):
    def test_exact_population_read_remains_decisive_and_trace_is_bounded(self):
        source = (ROOT / "src/supervisor/process_scope.cpp").read_text(encoding="utf-8")
        start = source.index("bool ProcessScope::populated() const {")
        end = source.index("bool ProcessScope::child_exited() const {", start)
        body = source[start:end]
        self.assertEqual(body.count('contents(scope_, "cgroup.events")'), 1)
        self.assertIn("std::istringstream input(event_contents);", body)
        self.assertIn("write_scope_readtrace(scope_, identity_, event_contents, populated);", body)
        self.assertIn("return populated;", body)
        self.assertIn("catch (...) { /* Read tracing must never change the scope decision. */ }", body)
        for cap in (
            "kReadTraceSnapshotBytes = 4096",
            "kReadTraceFileBytes = 65536",
            "kReadTraceProcsBytes = 256",
            "kReadTracePidCount = 8",
            "kReadTraceChildDirectories = 8",
            "kReadTraceChildDepth = 2",
        ):
            self.assertIn(cap, source)
        self.assertIn('if (trace_path && *trace_path && populated && started_ && child_ > 0 && pidfd_ >= 0 && child_exited())', body)

    def test_trace_is_build_and_run_opt_in(self):
        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        testing = cmake.index("if(BUILD_TESTING)", cmake.index("include(CTest)"))
        compile_flag = cmake.index("SPATIAL_PROCESS_SCOPE_READTRACE=1")
        self.assertGreater(compile_flag, testing)
        runner = (ROOT / "tools/run_workspace_copy_engine.py").read_text(encoding="utf-8")
        self.assertIn("os.environ.get('SPATIAL_SCOPE_READTRACE') == '1'", runner)
        self.assertIn("trace_path=evidence/'scope-readtrace.raw'", runner)
        wrapper = (ROOT / "tools/with_resource_delegate.py").read_text(encoding="utf-8")
        self.assertIn("'SPATIAL_SCOPE_READTRACE_PATH'", wrapper)


if __name__ == "__main__":
    unittest.main()