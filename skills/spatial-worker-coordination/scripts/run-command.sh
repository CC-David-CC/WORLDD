#!/usr/bin/env bash
# Run one argv vector from a checkout into a new, durable evidence directory.
# Usage: run-command.sh CHECKOUT NEW_RUN_DIR COMMAND [ARG ...]
set -u
set -o pipefail

usage() {
    printf 'usage: %s CHECKOUT NEW_RUN_DIR COMMAND [ARG ...]\n' "$0" >&2
}

if (( $# < 3 )); then
    usage
    exit 64
fi

checkout_arg=$1
run_dir_arg=$2
shift 2

if [[ ! -d $checkout_arg ]]; then
    printf 'checkout is not a directory: %s\n' "$checkout_arg" >&2
    exit 66
fi
checkout=$(cd -- "$checkout_arg" && pwd -P) || exit 66

case "$run_dir_arg" in
    /*) requested_run_dir=$run_dir_arg ;;
    *) requested_run_dir=$PWD/$run_dir_arg ;;
esac
run_parent=${requested_run_dir%/*}
run_base=${requested_run_dir##*/}
[[ -n $run_parent ]] || run_parent=/
case "$run_base" in
    ''|.|..)
        printf 'run directory path needs a final directory name\n' >&2
        exit 64
        ;;
esac
mkdir -p -- "$run_parent" || exit 73
run_parent=$(cd -- "$run_parent" && pwd -P) || exit 73
run_dir=$run_parent/$run_base
if [[ -e $run_dir || -L $run_dir ]]; then
    printf 'refusing existing run directory: %s\n' "$run_dir" >&2
    exit 73
fi
mkdir -- "$run_dir" || exit 73

atomic_write() {
    local destination=$1
    local temp=$run_dir/.receipt.tmp.$$.${RANDOM}
    if ! cat > "$temp"; then
        printf 'failed to write receipt: %s\n' "$destination" >&2
        return 1
    fi
    if ! mv -- "$temp" "$destination"; then
        printf 'failed to publish receipt: %s\n' "$destination" >&2
        return 1
    fi
}

command_returned=0
command_status=
wrapper_status=0

finish() {
    wrapper_status=$?
    trap - EXIT
    if (( command_returned == 1 )); then
        if ! printf '%s\n' "$command_status" | atomic_write "$run_dir/command.exit"; then
            printf 'command returned, but command.exit could not be saved\n' >&2
        elif ! printf 'COMPLETE\n' | atomic_write "$run_dir/terminal-status.txt"; then
            printf 'command.exit exists, but terminal status update failed\n' >&2
        fi
    fi
    printf '%s\n' "$wrapper_status" | atomic_write "$run_dir/wrapper.exit" || true
    exit "$wrapper_status"
}
trap finish EXIT

started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) || started_utc=unknown
host=$(hostname -f 2>/dev/null || hostname 2>/dev/null || printf unknown)
if ! {
    printf 'host=%s\n' "$host"
    printf 'started_utc=%s\n' "$started_utc"
    printf 'checkout=%s\n' "$checkout"
    printf 'commit='
    git -C "$checkout" rev-parse HEAD 2>/dev/null || printf unknown
    printf 'branch='
    git -C "$checkout" branch --show-current 2>/dev/null || printf unknown
    printf 'git_status_begin\n'
    git -C "$checkout" status --short 2>/dev/null || true
    printf 'git_status_end\n'
} | atomic_write "$run_dir/command-receipt.txt"; then
    printf 'cannot create command metadata; command was not started\n' >&2
    exit 74
fi

if ! printf '%s\0' "$@" | atomic_write "$run_dir/argv.nul"; then
    printf 'cannot create exact argv receipt; command was not started\n' >&2
    exit 74
fi
if ! {
    printf 'argv:'
    for arg in "$@"; do
        printf ' %q' "$arg"
    done
    printf '\n'
} | atomic_write "$run_dir/argv.shell-quoted.txt"; then
    printf 'cannot create readable argv receipt; command was not started\n' >&2
    exit 74
fi
if ! printf 'NOT_STARTED\n' | atomic_write "$run_dir/terminal-status.txt"; then
    printf 'cannot create terminal status receipt; command was not started\n' >&2
    exit 74
fi
if ! { exec {stdout_fd}> "$run_dir/stdout.log"; }; then
    printf 'cannot create stdout log; command was not started\n' >&2
    exit 74
fi
if ! { exec {stderr_fd}> "$run_dir/stderr.log"; }; then
    printf 'cannot create stderr log; command was not started\n' >&2
    exit 74
fi
if ! printf 'RUNNING\n' | atomic_write "$run_dir/terminal-status.txt"; then
    printf 'cannot mark command start; command was not started\n' >&2
    exit 74
fi

cd -- "$checkout" || exit 70
"$@" >&"$stdout_fd" 2>&"$stderr_fd"
command_status=$?
command_returned=1
exit "$command_status"