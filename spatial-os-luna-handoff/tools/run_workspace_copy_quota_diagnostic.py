"""Run exactly one opt-in quota diagnostic through the existing safe launcher."""
import sys
from run_workspace_copy_engine import main

assert len(sys.argv)==5
mode=sys.argv[4];assert mode in ('nocopy','copy')
sys.argv[4]='test_no_copy_live_writers_sixty_second_diagnostic' if mode=='nocopy' else 'test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch'
main(fixture_spec={'fixture':'diagnose_workspace_copy_quota.py',
    'discovery':'inspect_workspace_copy_quota_diagnostic.py','class':'QuotaDiagnosticTests',
    'mode':mode,'runtime_bound':120 if mode=='nocopy' else 360})
