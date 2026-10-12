"""Repeat the unchanged failing case and observe process state without attaching."""
import json
import os
from pathlib import Path
import threading
import time
import unittest
import test_selection_namespace as selection

ROOT = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
ROOT.mkdir(parents=True, exist_ok=True)


class ObservedShutdown(selection.SelectionNamespaceTests):
    iteration = 0

    def start(self, *args, **kwargs):
        os.environ['SPATIAL_SECURITY_EVIDENCE'] = str(ROOT / ('case-%03d' % self.iteration))
        supervisor = super().start(*args, **kwargs)
        pid = self.server.pid
        stop = threading.Event()
        samples = []
        started = time.monotonic()

        def observe():
            while not stop.is_set():
                try:
                    process = Path('/proc') / str(pid)
                    samples.append({'elapsed': time.monotonic() - started,
                                    'wchan': (process / 'wchan').read_text(),
                                    'status': (process / 'status').read_text()})
                except FileNotFoundError:
                    break
                stop.wait(0.05)

        thread = threading.Thread(target=observe, daemon=True)
        thread.start()

        def finish():
            stop.set()
            thread.join(timeout=1)
            (ROOT / ('process-%03d.json' % self.iteration)).write_text(
                json.dumps({'pid': pid, 'samples': samples}, indent=2) + '\n')

        # Finish after the original server cleanup. Preserve its four-second limit.
        self._cleanups.insert(0, (finish, (), {}))
        return supervisor


if __name__ == '__main__':
    cases = []
    for iteration in range(100):
        case = ObservedShutdown('test_compositor_selects_namespace_and_observes_revocation_cleanup')
        case.iteration = iteration
        cases.append(case)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(cases))
    (ROOT / 'summary.json').write_text(json.dumps({
        'checks': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'original_deadline_seconds': 4,
        'observer': 'read-only proc status and wchan; no ptrace or deadline changes'}, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
