#!/usr/bin/env python3
"""Remove empty BUILD/Testing directories and reject package contamination."""

import os
import stat
import sys


def fail(message):
    raise SystemExit(f"CTest package guard: {message}")


def checked_root(path):
    absolute = os.path.abspath(path)
    try:
        resolved = os.path.realpath(absolute, strict=True)
        mode = os.lstat(absolute).st_mode
    except OSError as exc:
        fail(f"cannot validate root {path}: {exc}")
    if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode) or resolved != absolute:
        fail(f"root must be a real directory without symlink components: {path}")
    return absolute


def walk_without_following(root):
    """Yield descendants without traversing symlink directories."""
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        for name in list(dirs):
            path = os.path.join(current, name)
            if os.path.islink(path):
                dirs.remove(name)
                yield path
            else:
                yield path
        for name in files:
            yield os.path.join(current, name)


def main(final_arg, build_arg):
    final = checked_root(final_arg)
    build = checked_root(build_arg)
    expected_build = os.path.join(final, "builds", "debug")
    if build != expected_build:
        fail(f"BUILD must be the exact package path {expected_build}")

    testing = os.path.join(build, "Testing")
    if os.path.lexists(testing):
        mode = os.lstat(testing).st_mode
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            fail(f"BUILD/Testing must be a real directory: {testing}")

    # Validate the complete package before deleting any eligible directories.
    for path in walk_without_following(final):
        if path == testing or path.startswith(testing + os.sep):
            continue
        if os.path.basename(path) == "Testing":
            fail(f"Testing path remains outside BUILD/Testing: {path}")
    if os.path.lexists(testing):
        for path in walk_without_following(testing):
            try:
                mode = os.lstat(path).st_mode
            except OSError as exc:
                fail(f"cannot inspect {path}: {exc}")
            if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                fail(f"non-directory CTest bookkeeping entry: {path}")

    # Every descendant was proven to be a directory. Remove only empty leaves.
    if os.path.lexists(testing):
        directories = [testing]
        directories.extend(
            path for path in walk_without_following(testing)
            if stat.S_ISDIR(os.lstat(path).st_mode)
        )
        for path in sorted(directories, key=lambda item: item.count(os.sep), reverse=True):
            os.rmdir(path)
    print("CTest package bookkeeping is clean")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        fail("usage: clean_package_ctest_bookkeeping.py FINAL BUILD")
    main(sys.argv[1], sys.argv[2])
