"""Compare an explicit 2000 ms client budget; do not change qualification fixtures."""
import importlib.util
from pathlib import Path
import socket
import sys

helper = Path(__file__).with_name('diagnose-m02-durability-pressure-01.py')
spec = importlib.util.spec_from_file_location('durability_diagnostic', helper)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)
diagnostic.ROOT = diagnostic.BASE / 'm02-durability-budget-02'
diagnostic.__file__ = __file__
original_save = diagnostic.save
original_worker = diagnostic.worker


def save(path, value):
    if path == diagnostic.ROOT / 'review.json':
        value.update(deadlines_changed=True, diagnostic_client_timeout_ms=2000,
                     original_client_timeout_ms=700, server_timeout_ms=700,
                     configuration_scope='Diagnostic client configuration only. Production defaults and tracked tests unchanged.',
                     base_helper_sha256=diagnostic.sha(helper), qualification_result=False)
        copy = diagnostic.ROOT / helper.name
        if not copy.exists():
            copy.write_bytes(helper.read_bytes())
    original_save(path, value)


def worker(out):
    sys.path.insert(0, str(diagnostic.SOURCE / 'tests/native'))
    import test_authority_supervisor  # Consume the original CTest positional arguments.
    import test_issuer_client as clients
    original = clients.ClientFixture.config_file
    changes = []

    def config_file(fixture, endpoint_port=None, **overrides):
        assert 'io_timeout_ms' not in overrides, 'Do not override an explicit fault-test deadline'
        path = original(fixture, endpoint_port, io_timeout_ms=2000, **overrides)
        config = clients.text_format.Parse(path.read_text(), clients.pb.IssuerTlsClientConfig())
        assert config.io_timeout_ms == 2000
        changes.append(dict(monotonic_ns=diagnostic.time.monotonic_ns(), io_timeout_ms=2000))
        save(out / 'client-config-budget.json', changes)
        return path

    clients.ClientFixture.config_file = config_file
    try:
        return original_worker(out)
    finally:
        clients.ClientFixture.config_file = original


diagnostic.save = save
diagnostic.worker = worker

if __name__ == '__main__':
    assert socket.gethostname().split('.')[0] == 'sb16'
    if len(sys.argv) > 1 and sys.argv[1] in ('--writer', '--worker'):
        mode = sys.argv.pop(1)
        directory = Path(sys.argv.pop(1))
        assert directory.parent == diagnostic.ROOT and directory.name in ('baseline', 'pressure', 'recovery')
        if mode == '--writer':
            diagnostic.writer(directory)
        else:
            raise SystemExit(worker(directory))
    else:
        assert len(sys.argv) == 1
        diagnostic.run()
