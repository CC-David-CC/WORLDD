#!/usr/bin/env python3
"""Run an unchanged native test entrypoint with one pinned healthy fixture overlay."""
import argparse
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import runpy
import stat
import sys


def _regular_pinned_file(value, label, expected_sha256):
    path = Path(os.path.abspath(value))
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected_sha256}, got {digest}")
    return path, digest


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--overlay-sha256", required=True)
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--entrypoint-sha256", required=True)
    parser.add_argument("original_args", nargs=argparse.REMAINDER)
    return parser


class _PinnedOverlayLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, fullname, overlay, expected_sha256):
        super().__init__(fullname, str(overlay))
        self.expected_sha256 = expected_sha256

    def get_code(self, fullname):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "rb") as source_file:
                if not stat.S_ISREG(os.fstat(source_file.fileno()).st_mode):
                    raise ImportError("healthy issuer overlay is no longer a regular file")
                source = source_file.read()
        except OSError as exc:
            raise ImportError(f"healthy issuer overlay source is unavailable: {exc}") from exc
        digest = hashlib.sha256(source).hexdigest()
        if digest != self.expected_sha256:
            raise ImportError(f"healthy issuer overlay SHA-256 mismatch at import: expected {self.expected_sha256}, got {digest}")
        return compile(source, self.path, "exec", dont_inherit=True)


class _HealthyIssuerOverlayFinder(importlib.abc.MetaPathFinder):
    def __init__(self, overlay, expected_sha256):
        self.overlay = overlay
        self.expected_sha256 = expected_sha256

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "healthy_issuer_setup":
            return None
        loader = _PinnedOverlayLoader(fullname, self.overlay, self.expected_sha256)
        return importlib.util.spec_from_file_location(fullname, self.overlay, loader=loader)


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.overlay_sha256 or len(args.overlay_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.overlay_sha256):
        parser.error("--overlay-sha256 must be 64 lowercase hexadecimal characters")
    if not args.entrypoint_sha256 or len(args.entrypoint_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.entrypoint_sha256):
        parser.error("--entrypoint-sha256 must be 64 lowercase hexadecimal characters")
    try:
        overlay, overlay_digest = _regular_pinned_file(args.overlay, "overlay", args.overlay_sha256)
        entrypoint, entrypoint_digest = _regular_pinned_file(args.entrypoint, "entrypoint", args.entrypoint_sha256)
    except ValueError as exc:
        parser.error(str(exc))

    original_args = list(args.original_args)
    if original_args and original_args[0] == "--":
        original_args.pop(0)
    sys.argv[:] = [str(entrypoint), *original_args]
    script_dir = str(entrypoint.parent)
    if sys.path:
        sys.path[0] = script_dir
    else:
        sys.path.insert(0, script_dir)
    sys.meta_path.insert(0, _HealthyIssuerOverlayFinder(overlay, overlay_digest))
    print(f"fixture_overlay_source={overlay}", file=sys.stderr)
    print(f"fixture_overlay_sha256={overlay_digest}", file=sys.stderr)
    print(f"fixture_entrypoint={entrypoint}", file=sys.stderr)
    print(f"fixture_entrypoint_sha256={entrypoint_digest}", file=sys.stderr)
    runpy.run_path(str(entrypoint), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
