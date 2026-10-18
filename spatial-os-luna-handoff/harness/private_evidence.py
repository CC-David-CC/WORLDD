"""Private per-case evidence directories for native regression fixtures."""
import os
import stat
import tempfile
from pathlib import Path

EVIDENCE_ENV = 'SPATIAL_SECURITY_EVIDENCE'


def install_case_evidence(parent, case_name, environ=None):
    """Install a retained 0700 child and return it with an environment restore callback."""
    target = os.environ if environ is None else environ
    prior = target.get(EVIDENCE_ENV)
    parent = Path(parent)
    if not parent.is_dir():
        raise NotADirectoryError(parent)
    leaf = Path(tempfile.mkdtemp(prefix=f'{case_name}-', dir=parent))
    os.chmod(leaf, 0o700)
    if stat.S_IMODE(leaf.stat().st_mode) != 0o700:
        raise PermissionError(f'case evidence directory is not 0700: {leaf}')
    target[EVIDENCE_ENV] = str(leaf)

    def restore():
        if prior is None:
            target.pop(EVIDENCE_ENV, None)
        else:
            target[EVIDENCE_ENV] = prior

    return leaf, restore


def bounded_tree_files(test_case, root, *, logs_only=False, reject_symlinks=False):
    """Return a bounded evidence inventory, using the caller's unittest assertions."""
    root = Path(root)
    pending = [(root, 0)]
    files = []
    entries_seen = 0
    while pending:
        directory, depth = pending.pop()
        test_case.assertLessEqual(depth, 8, f'nested evidence exceeds depth bound: {directory}')
        with os.scandir(directory) as entries:
            for entry in entries:
                entries_seen += 1
                test_case.assertLessEqual(entries_seen, 2048,
                                          f'evidence scan exceeds entry bound: {root}')
                if entry.is_symlink():
                    if reject_symlinks:
                        test_case.fail(f'evidence tree contains a symlink: {entry.path}')
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), depth + 1))
                elif entry.is_file(follow_symlinks=False) and (not logs_only or entry.name.endswith('.log')):
                    files.append(Path(entry.path))
                    # Includes private broker diagnostics; byte caps remain at the caller.
                    test_case.assertLessEqual(len(files), 64,
                                              f'evidence scan exceeds file bound: {root}')
    return files
