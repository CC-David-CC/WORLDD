"""Exact argv helpers for independent /proc process witnesses."""
from collections.abc import Sequence


def parse_proc_cmdline(raw: bytes) -> tuple[str, ...]:
    """Decode Linux NUL-delimited argv while preserving argument boundaries."""
    if not isinstance(raw, bytes):
        raise TypeError("proc cmdline must be bytes")
    if not raw:
        return ()
    parts = raw.split(b"\0")
    if parts[-1] == b"":
        parts.pop()
    return tuple(part.decode("utf-8", errors="surrogateescape") for part in parts)


def argv0_matches(argv: Sequence[str], executable: str) -> bool:
    """Match an executable only against argv[0], never its other arguments."""
    return bool(argv) and argv[0] == executable
