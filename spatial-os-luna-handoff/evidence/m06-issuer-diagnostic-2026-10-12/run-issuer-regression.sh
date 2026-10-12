#!/usr/bin/env bash
set -euo pipefail
attempt=/home/dflanag3/spatial-qualification/m06-issuer-diagnostic-sb16-20261012-02
checkout="$attempt/bundle"
run_dir="$attempt/runs/issuer-regression-01"
helper=/home/dflanag3/spatial-qualification/m06-geometry-run04-command.sh
exec bash "$helper" "$checkout" "$run_dir" /usr/bin/env -i \
  HOME=/home/dflanag3 USER=dflanag3 LOGNAME=dflanag3 \
  PATH="$checkout/bin:/usr/bin:/bin" \
  PYTHONPATH="$checkout/python:$checkout" \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  /usr/bin/python3 "$attempt/incoming/bounded_case.py" 90 \
  "$run_dir/case-receipt.json" "$run_dir/case-stdout.log" "$run_dir/case-stderr.log" \
  /usr/bin/python3 "$attempt/incoming/issuer_case_driver.py" \
  "$checkout/tests/core/test_issuer_client.py" \
  "$checkout/bin/identityd" "$checkout/bin/spatial-issuer-client" \
  test_callback_failure_diagnostic_is_bounded_and_fail_closed
