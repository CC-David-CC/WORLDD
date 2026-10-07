#!/bin/sh
# Run only on the two authorized Linux hosts. Downloads require an explicit flag.
set -eu
case "$(hostname -s)" in
    sb16|sb16-2) ;;
    *) printf '%s\n' 'Core bootstrap is restricted to sb16 and sb16-2.' >&2; exit 2 ;;
esac
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mkdir -p local
if ! dpkg-query -W -f='${Status}' nlohmann-json3-dev 2>/dev/null | grep -qx 'install ok installed'; then
    if [ "${1:-}" != '--install-deps' ]; then
        printf '%s\n' 'Missing nlohmann-json3-dev. Re-run with --install-deps for host preparation.' >&2
        exit 2
    fi
    {
        printf '\n## %s: core JSON dependency\n' "$(date -u +%FT%TZ)"
        printf '%s\n' 'Previous state: nlohmann-json3-dev not installed.'
        printf '%s\n' 'Change: sudo -n apt-get install -y --no-install-recommends nlohmann-json3-dev'
        printf '%s\n' 'Recovery: sudo -n apt-get remove nlohmann-json3-dev'
        printf '%s\n' 'No session, service, SSH, or login configuration changes.'
    } >> local/system-changes.md
    sudo -n apt-get install -y --no-install-recommends nlohmann-json3-dev
fi
dpkg-query -W build-essential cmake ninja-build nlohmann-json3-dev python3 > local/core-packages.txt
printf '%s\n' 'Core dependencies are ready. Build with cmake --build build --parallel 2.'
