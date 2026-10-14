#!/usr/bin/env bash
# Run inside tmux. Keep shell expansion on the Linux host.
set -u
if [ "$#" -ne 5 ]; then
    echo 'Usage: run-worker.sh CHECKOUT BRANCH MODEL TASK_FILE RUN_DIRECTORY' >&2
    exit 64
fi
checkout=$(realpath -e -- "$1") || exit 65
branch=$2
model=$3
task=$(realpath -e -- "$4") || exit 65
run=$(realpath -m -- "$5") || exit 65
case "$run" in
    "$checkout"/local/*) ;;
    *) echo 'Run directory must be inside CHECKOUT/local' >&2; exit 65 ;;
esac
test -r "$task" || exit 65
cd "$checkout" || exit 65
actual_branch=$(git branch --show-current) || exit 65
if [ "$actual_branch" != "$branch" ]; then
    echo 'Checkout branch does not match the assignment' >&2
    exit 65
fi
mkdir -p -- "$(dirname -- "$run")" || exit 65
mkdir -- "$run" || exit 65
finish() {
    result=$?
    trap - EXIT
    printf '%s\n' "$result" > "$run/exit.txt"
    date -u '+finished=%Y-%m-%dT%H:%M:%SZ' >> "$run/launch.txt"
    exit "$result"
}
trap finish EXIT
cp -- "$task" "$run/task.txt" || exit 65
{
    date -u '+started=%Y-%m-%dT%H:%M:%SZ'
    printf 'host=%s\ncheckout=%s\nbranch=%s\nmodel=%s\n' "$(hostname)" "$checkout" "$branch" "$model"
    git rev-parse HEAD
    git status --short --branch
} > "$run/launch.txt" || exit 65
codex_binary=${SPATIAL_CODEX_BIN:-"$HOME/.local/bin/codex"}
"$codex_binary" exec -m "$model" -c 'model_reasoning_effort=medium' \
    --sandbox workspace-write --json -o "$run/message.txt" - \
    < "$run/task.txt" > "$run/worker.jsonl" 2> "$run/stderr.txt"
result=$?
exit "$result"
