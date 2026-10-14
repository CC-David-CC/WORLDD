#!/usr/bin/env python3
"""Behavior checks for persistent command receipts and exact argv handling."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

DRAFT_DIR = Path(__file__).resolve().parent
HELPER = DRAFT_DIR / "run-command.sh"
CHECKOUT = Path(os.environ["RUN_COMMAND_CHECKOUT"]) if "RUN_COMMAND_CHECKOUT" in os.environ else Path(__file__).resolve().parents[3]


def invoke(run_dir, argv, *, preexec_fn=None, cwd=None):
    return subprocess.run(
        ["bash", str(HELPER), str(cwd or CHECKOUT), str(run_dir), *map(str, argv)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        preexec_fn=preexec_fn,
    )


def process_is_running(pid):
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            return stat.read_text().split()[2] != "Z"
        except (OSError, IndexError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class RunCommandTests(unittest.TestCase):
    def test_success_exit_and_exact_argv(self):
        with tempfile.TemporaryDirectory(prefix="run-command-success-") as td:
            run = Path(td) / "run"
            marker = Path(td) / "injected-side-effect"
            injection = f"$(touch {marker})"
            args = ["space value", "quote'\"", "line1\nline2", "$HOME", "semi;pipe|", injection]
            code = "import sys; print(repr(sys.argv[1:]))"
            result = invoke(run, [sys.executable, "-c", code, *args])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((run / "command.exit").read_text().strip(), "0")
            self.assertEqual((run / "wrapper.exit").read_text().strip(), "0")
            self.assertEqual((run / "terminal-status.txt").read_text().strip(), "COMPLETE")
            recorded = (run / "argv.nul").read_bytes().split(b"\0")[:-1]
            self.assertEqual(recorded, [os.fsencode(x) for x in [sys.executable, "-c", code, *args]])
            self.assertIn("line1\\nline2", (run / "stdout.log").read_text())
            self.assertEqual((run / "stderr.log").read_text(), "")
            self.assertFalse(marker.exists())
            self.assertTrue((run / "command-receipt.txt").is_file())

    def test_exit_seven_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="run-command-exit7-") as td:
            run = Path(td) / "run"
            result = invoke(run, [sys.executable, "-c", "raise SystemExit(7)"])
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual((run / "command.exit").read_text().strip(), "7")
            self.assertEqual((run / "wrapper.exit").read_text().strip(), "7")

    def test_existing_directory_is_untouched(self):
        with tempfile.TemporaryDirectory(prefix="run-command-existing-") as td:
            run = Path(td) / "run"
            run.mkdir()
            sentinel = run / "sentinel"
            sentinel.write_bytes(b"keep exactly\0")
            before = sentinel.read_bytes()
            result = invoke(run, [sys.executable, "-c", "raise SystemExit(99)"])
            self.assertEqual(result.returncode, 73)
            self.assertEqual(sentinel.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in run.iterdir()), ["sentinel"])

    def test_invalid_checkout_refuses_before_run_directory(self):
        with tempfile.TemporaryDirectory(prefix="run-command-bad-checkout-") as td:
            run = Path(td) / "run"
            result = invoke(run, [sys.executable, "-c", "raise SystemExit(99)"], cwd=Path(td) / "missing")
            self.assertEqual(result.returncode, 66)
            self.assertFalse(run.exists())

    @unittest.skipUnless(os.name == "posix", "symlink behavior is POSIX-only")
    def test_dangling_symlink_is_untouched(self):
        with tempfile.TemporaryDirectory(prefix="run-command-dangling-") as td:
            run = Path(td) / "run"
            target = Path(td) / "missing-target"
            run.symlink_to(target)
            link_before = os.readlink(run)
            result = invoke(run, [sys.executable, "-c", "raise SystemExit(99)"])
            self.assertEqual(result.returncode, 73)
            self.assertTrue(run.is_symlink())
            self.assertEqual(os.readlink(run), link_before)
            self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == "posix", "RLIMIT_FSIZE is POSIX-only")
    def test_receipt_write_failure_never_starts_command(self):
        import resource
        with tempfile.TemporaryDirectory(prefix="run-command-receipt-fail-") as td:
            run = Path(td) / "run"
            marker = Path(td) / "command-started"
            def no_file_growth():
                resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
                signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            code = "from pathlib import Path; Path(sys.argv[1]).write_text('started')"
            result = invoke(run, [sys.executable, "-c", "import sys; " + code, str(marker)], preexec_fn=no_file_growth)
            self.assertEqual(result.returncode, 74, result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse((run / "command.exit").exists())
            self.assertNotEqual((run / "terminal-status.txt").read_text().strip() if (run / "terminal-status.txt").exists() else "", "COMPLETE")

    @unittest.skipUnless(os.name == "posix", "signal and PID assertions are POSIX-only")
    def test_term_does_not_fabricate_child_exit(self):
        with tempfile.TemporaryDirectory(prefix="run-command-term-") as td:
            run = Path(td) / "run"
            pid_file = Path(td) / "child.pid"
            child_code = "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)"
            proc = subprocess.Popen(
                ["bash", str(HELPER), str(CHECKOUT), str(run), sys.executable, "-c", child_code, str(pid_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid = None
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and proc.poll() is None and not pid_file.exists():
                    time.sleep(0.05)
                self.assertTrue(pid_file.exists(), "child did not publish its PID")
                child_pid = int(pid_file.read_text())
                os.kill(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
                self.assertFalse((run / "command.exit").exists(), "TERM must not invent a child command exit")
                self.assertTrue(process_is_running(child_pid), "wrapper termination must not be mistaken for child termination")
                print(f"TERM evidence: helper_pid={proc.pid} child_pid={child_pid} child_alive_after_helper_exit=yes command_exit=UNKNOWN")
                self.assertEqual((run / "terminal-status.txt").read_text().strip(), "RUNNING")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=3)
                if proc.stdout is not None:
                    proc.stdout.close()
                if proc.stderr is not None:
                    proc.stderr.close()
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    if process_is_running(child_pid):
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline and process_is_running(child_pid):
                            time.sleep(0.05)
                        if process_is_running(child_pid):
                            # Only the PID created and recorded by this test is targeted.
                            os.kill(child_pid, signal.SIGKILL)
                            deadline = time.monotonic() + 3
                            while time.monotonic() < deadline and process_is_running(child_pid):
                                time.sleep(0.05)
                    self.assertFalse(process_is_running(child_pid), "recorded child did not exit after targeted cleanup")
                    print(f"TERM cleanup evidence: child_pid={child_pid} signal=SIGTERM then targeted_SIGKILL_if_needed child_running_after_cleanup=no")


if __name__ == "__main__":
    unittest.main(verbosity=2)
