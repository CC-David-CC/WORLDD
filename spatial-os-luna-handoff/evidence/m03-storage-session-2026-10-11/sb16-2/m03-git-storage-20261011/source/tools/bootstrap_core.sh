#!/bin/sh
# Run only on the two authorized Linux hosts. Downloads require an explicit flag.
set -eu
case "$(hostname -s)" in
    sb16|sb16-2) ;;
    *) printf '%s\n' 'Core bootstrap is restricted to sb16 and sb16-2.' >&2; exit 2 ;;
esac
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mkdir -p local
for package in protobuf-compiler libprotobuf-dev python3-protobuf libsqlite3-dev; do
if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
    if [ "${1:-}" != '--install-deps' ]; then
        printf 'Missing %s. Re-run with --install-deps for host preparation.\n' "$package" >&2
        exit 2
    fi
    {
        printf '\n## %s: core dependency %s\n' "$(date -u +%FT%TZ)" "$package"
        printf 'Previous state: %s not installed.\n' "$package"
        printf 'Change: sudo -n apt-get install -y --no-install-recommends %s\n' "$package"
        printf 'Recovery: sudo -n apt-get remove %s\n' "$package"
        printf '%s\n' 'Inspect the apt log before removing any newly installed shared dependencies.'
        printf '%s\n' 'No session, service, SSH, or login configuration changes.'
    } >> local/system-changes.md
    sudo -n apt-get install -y --no-install-recommends "$package" >> local/core-apt.log 2>&1
fi
done
dpkg-query -W build-essential cmake ninja-build protobuf-compiler libprotobuf-dev python3-protobuf libsqlite3-dev python3 > local/core-packages.txt
printf '%s\n' 'Core dependencies are ready. Build with cmake --build build --parallel 2.'
