#!/usr/bin/env python3
"""Run a test with a fresh private evidence directory outside frozen packages."""
import argparse
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Mapping


PACKAGE_MANIFEST_MARKERS = (
    'PACKAGE_MANIFEST.sha256',
    'PACKAGE_MANIFEST.SHA256SUMS',
)


def resolve_root(override: str | None = None, *, environ: Mapping[str, str] | None = None,
                 home: Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = override or env.get('SPATIAL_SECURITY_EVIDENCE')
    if configured:
        return Path(configured).expanduser().resolve()
    user_home = Path.home() if home is None else home
    return (user_home / 'spatial-artifacts' / 'security-evidence' /
            'actor-cabinet-gateway').resolve()


def frozen_package_root(script: Path, cwd: Path) -> Path | None:
    for candidate in (script.resolve(), cwd.resolve()):
        for parent in (candidate, *candidate.parents):
            if any((parent / marker).is_file() for marker in PACKAGE_MANIFEST_MARKERS):
                return parent
    return None


def create_private_evidence(root: Path, *, script: Path, cwd: Path) -> Path:
    root = root.resolve()
    package = frozen_package_root(script, cwd)
    if package is not None and root.is_relative_to(package):
        raise RuntimeError(f'evidence root is inside frozen package: {root}')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    evidence = Path(tempfile.mkdtemp(prefix='actor-cabinet-', dir=root))
    os.chmod(evidence, 0o700)
    if stat.S_IMODE(evidence.stat().st_mode) != 0o700:
        raise RuntimeError('private evidence directory permissions are not owner-only')
    return evidence


def create_private_evidence_children(evidence: Path) -> tuple[Path, Path]:
    """Create fresh protected cases/scopes roots beneath an owned evidence directory."""
    evidence = Path(evidence)
    info = evidence.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError('evidence parent must be an owned mode-0700 directory')
    children = (evidence / 'cases', evidence / 'scopes')
    if any(os.path.lexists(child) for child in children):
        raise FileExistsError('evidence child already exists')
    for child in children:
        child.mkdir(mode=0o700)
    return children

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', help='writable evidence parent; defaults to SPATIAL_SECURITY_EVIDENCE')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        parser.error('a test command is required after --')

    root = resolve_root(args.root)
    evidence = create_private_evidence(root, script=Path(__file__), cwd=Path.cwd())
    env = os.environ.copy()
    env['SPATIAL_SECURITY_EVIDENCE'] = str(evidence)
    return subprocess.run(command, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
