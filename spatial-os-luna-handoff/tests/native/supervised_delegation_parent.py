"""Pinned identity helpers for supervised test-fixture delegation parents."""
import os
import stat
from pathlib import Path


def execution_key(state_dir, participant_id):
    if not isinstance(participant_id, bytes) or not participant_id:
        raise ValueError('participant identity must be nonempty bytes')
    canonical = Path(state_dir).resolve(strict=True)
    descriptor = os.open(canonical, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        held = os.fstat(descriptor)
        named = os.stat(canonical, follow_symlinks=False)
        if not stat.S_ISDIR(held.st_mode) or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
            raise ValueError('supervised state directory changed during identity capture')
        return (held.st_dev, held.st_ino, participant_id), canonical
    finally:
        os.close(descriptor)


def verify_pinned_directory(path, descriptor, expected_uid):
    """Reject a replaced or substituted cgroup name while retaining the pinned fd."""
    named = os.stat(path, follow_symlinks=False)
    held = os.fstat(descriptor)
    if not stat.S_ISDIR(named.st_mode) or not stat.S_ISDIR(held.st_mode):
        raise AssertionError('delegation parent is not a directory')
    identity = (held.st_dev, held.st_ino, held.st_uid)
    if identity != (named.st_dev, named.st_ino, named.st_uid):
        raise AssertionError('delegation parent name no longer identifies the pinned directory')
    if held.st_uid != expected_uid:
        raise AssertionError('delegation parent owner changed')
    return identity


def cleanup_owned_parents(handles, parents, remove_parent=None):
    """Remove exact empty parents only after all generation handles have retired."""
    if any(not handle.closed for handle in handles):
        raise AssertionError('delegation parents cannot be removed before every handle retires')
    for key, parent in list(parents.items()):
        verify_pinned_directory(parent['path'], parent['fd'], parent['uid'])
        path = parent['path']
        if (path / 'cgroup.procs').read_text().split():
            raise AssertionError('delegation parent still has direct processes')
        if any(child.is_dir() for child in path.iterdir()):
            raise AssertionError('delegation parent still has generation roots')
        if remove_parent is None:
            path.rmdir()
        else:
            remove_parent(path)
        os.close(parent['fd'])
        parents.pop(key)
