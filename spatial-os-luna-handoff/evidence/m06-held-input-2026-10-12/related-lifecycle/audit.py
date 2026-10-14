"""Audit the selected native lifecycle evidence; does not run services."""
import json
from pathlib import Path
import re

root = Path(__file__).resolve().parent
rows = []
for host in ('sb16', 'sb16-2'):
    assert (root / host / 'runner.exit').read_text().strip() == '0'
    for profile in ('debug', 'asan'):
        for suite, count in (('session_application', 3), ('session_replacement', 4)):
            directory = root / host / profile / suite
            assert (directory / 'runtime.exit').read_text().strip() == '0'
            log = (directory / 'runtime.log').read_text()
            assert re.search(rf'Ran {count} tests in .*\n\nOK\n', log)
            assert 'skipped=' not in log
            receipts = [json.loads(line) for line in log.splitlines() if line.startswith('{')]
            assert any(row.get('scope_removed') is True and row.get('exit_code') == 0
                       and row.get('unexpected_population') is False for row in receipts)
            assert any('ActiveState=inactive' in row.get('unit_after', '')
                       and row.get('query_exit') == 0 for row in receipts)
            rows.append(dict(host=host, profile=profile, suite=suite, passed=count))
logs = list(root.glob('sb16*/**/*.log'))
for path in logs:
    assert not re.search(r'AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|runtime error:',
                         path.read_text(errors='replace')), str(path)
result = dict(status='passed', task_commit='9222918f59a4d74fa5e099496b3363baff90ac41',
              passed=sum(row['passed'] for row in rows), failed=0, skipped=0,
              cases=rows, sanitizer_logs_scanned=len(logs), phase_complete=False,
              scope='Related native lifecycle checks only; full M06 gate remains pending')
(root / 'review.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps(result, indent=2))
