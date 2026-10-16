import importlib.util
from pathlib import Path
import tempfile

runner_path=Path(__file__).with_name('run_workspace_copy_engine.py')
spec=importlib.util.spec_from_file_location('_copy_runner',runner_path)
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
method='test_copy_broker_wait_keeps_action_issuer_alive_beyond_tls_idle'
assert runner.selected_method_passed(f'{method} (__main__.WorkspaceCopyEngineTests) ... ok\n',method)
assert runner.selected_method_passed(f'{method} (__main__.WorkspaceCopyEngineTests.{method}) ... ok\n',method)
assert not runner.selected_method_passed(f'test_other (__main__.WorkspaceCopyEngineTests.test_other) ... ok\n',method)
with tempfile.TemporaryDirectory() as temp:
    build=Path(temp)
    try:
        runner.runtime_config_preflight(build)
    except AssertionError as error:
        assert str(error)=='Required runtime.textproto is missing or not a regular file'
        assert not (build/'spatial-copy-tree-worker').exists()
        assert not (build/'spatial-copy-finalize-worker').exists()
    else:
        raise AssertionError('Missing runtime.textproto passed preflight')
print('missing-runtime-config rejected before helper use')