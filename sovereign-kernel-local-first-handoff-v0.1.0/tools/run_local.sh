#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
CC=${CC:-cc}
BIN=$(mktemp "${TMPDIR:-/tmp}/sk-no-network.XXXXXXXX")
trap 'rm -f "$BIN"' EXIT HUP INT TERM
"$CC" -std=c11 -O2 -Wall -Wextra -Werror "$ROOT/native/no_network.c" -o "$BIN"
cd "$ROOT"
# -I ignores user Python paths; kernel-level restriction is already active before imports.
"$BIN" "$PYTHON" -I "$ROOT/tools/run_suite.py" --native --launcher "$BIN" "$@"
