"""Run exactly one opt-in quota diagnostic through the existing safe launcher."""
import sys
from run_workspace_copy_engine import main

assert len(sys.argv) in (5,6)
if len(sys.argv)==6:assert sys.argv[5].startswith('--profile=')
mode=sys.argv[4]
selection={
    'nocopy':('test_no_copy_live_writers_sixty_second_diagnostic',120),
    'copy':('test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch',360),
    'smallquota':('test_small_source_quota_rejects_actual_writer_and_preserves_peer_application',120)}
assert mode in selection,'Unsupported exact diagnostic mode'
sys.argv[4],runtime_bound=selection[mode]
main(fixture_spec={'fixture':'diagnose_workspace_copy_quota.py',
    'discovery':'inspect_workspace_copy_quota_diagnostic.py','class':'QuotaDiagnosticTests',
    'mode':mode,'runtime_bound':runtime_bound})
