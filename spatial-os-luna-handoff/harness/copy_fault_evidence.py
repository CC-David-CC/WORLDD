"""Durable evidence writes within the runner-configured copy fault evidence root."""
import os
from pathlib import Path


def durable_write(path,data):
    root=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']).resolve(strict=True)
    destination=Path(path).resolve(strict=False)
    if not destination.is_relative_to(root):
        raise ValueError('Retirement evidence must stay in the configured evidence root')
    with destination.open('wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    directory=destination.parent
    # This also persists new ancestor directory entries, without imposing a location policy.
    while True:
        descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
        if directory.parent==directory:break
        directory=directory.parent
