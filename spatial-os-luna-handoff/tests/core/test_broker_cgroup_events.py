#!/usr/bin/env python3
"""Focused parser coverage for extensible cgroup v2 event records."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native"))
from cgroup_events import populated_value

assert populated_value("populated 0\nfrozen 0\n") == 0
assert populated_value("populated 1\nfrozen 0\nfuture_counter 7") == 1
assert populated_value("frozen 1\npopulated 0\n") == 0
for malformed in (
    "",
    "frozen 0\n",
    "populated 0\npopulated 1\n",
    "populated zero\n",
    "populated 0 extra\n",
):
    try:
        populated_value(malformed)
    except ValueError:
        pass
    else:
        raise AssertionError(f"accepted malformed cgroup.events: {malformed!r}")
print("cgroup.events parser cases passed")
