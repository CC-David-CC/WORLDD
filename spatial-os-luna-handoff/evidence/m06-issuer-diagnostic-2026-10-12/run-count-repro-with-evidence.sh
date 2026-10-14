#!/usr/bin/env bash
set -euo pipefail
attempt=/home/dflanag3/spatial-qualification/m06-issuer-diagnostic-sb16-20261012-02
checkout="$attempt/bundle"
run_dir="$attempt/runs/geometry-count-02"
evidence_dir="$attempt/runs/geometry-count-02-security-evidence"
helper=/home/dflanag3/spatial-qualification/m06-geometry-run04-command.sh
test ! -e "$run_dir"
test ! -e "$evidence_dir"
mkdir -m 700 -- "$evidence_dir"
exec bash "$helper" "$checkout" "$run_dir" /usr/bin/env -i \
  HOME=/home/dflanag3 USER=dflanag3 LOGNAME=dflanag3 \
  PATH="$checkout/bin:/usr/bin:/bin" \
  PYTHONPATH="$checkout/python:$checkout" \
  SPATIAL_SECURITY_EVIDENCE="$evidence_dir" \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  /usr/bin/python3 "$attempt/incoming/bounded_case.py" 600 \
  "$run_dir/case-receipt.json" "$run_dir/case-stdout.log" "$run_dir/case-stderr.log" \
  /usr/bin/python3 "$attempt/incoming/single_case_driver.py" \
  "$checkout/tests/core/test_geometry_view.py" \
  "$checkout/bin/identityd" "$checkout/bin/spatial-issuer-client" "$checkout/bin/worldd" \
  test_geometry_count_limit_is_controlled_and_other_actor_survives
