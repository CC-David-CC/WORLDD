"""Bounded, read-only privileged inspection for fixture process identity."""
import json
import subprocess


_INSPECT = r"""
import json, os, sys

pid = int(sys.argv[1])
if pid <= 0:
    raise SystemExit(2)

def fields():
    with open(f"/proc/{pid}/stat", "r", encoding="ascii") as source:
        text = source.read()
    tail = text[text.rfind(")") + 2:].split()
    return int(text.split(None, 1)[0]), int(tail[1]), int(tail[19])

try:
    before = fields()
    executable = os.readlink(f"/proc/{pid}/exe")
    after = fields()
except FileNotFoundError:
    print(json.dumps({"error": "process-disappeared"}))
    raise SystemExit(3)
except PermissionError:
    print(json.dumps({"error": "permission-denied"}))
    raise SystemExit(4)

if before != after or before[0] != pid:
    print(json.dumps({"error": "process-identity-changed"}))
    raise SystemExit(5)

print(json.dumps({"pid": before[0], "ppid": before[1],
                  "starttime": before[2], "exe": executable}))
"""


def inspect_process_identity(pid):
    """Return exact exe, parent, and starttime using bounded sudo proc inspection.

    The caller must hold a pidfd opened before this call. It must check that
    pidfd before and after inspection, then retain that same descriptor.
    """
    if type(pid) is not int or pid <= 0:
        raise ValueError("pid must be a positive integer")
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/bin/timeout", "--signal=TERM",
             "--kill-after=1s", "4s", "/usr/bin/python3", "-c", _INSPECT,
             str(pid)],
            capture_output=True, text=True, timeout=6, check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("bounded proc identity inspection timed out") from error
    try:
        record = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError("proc identity inspection returned malformed data") from error
    if not isinstance(record, dict):
        raise RuntimeError("proc identity inspection returned a non-object")
    if result.returncode == 3 and record.get("error") == "process-disappeared":
        raise ProcessLookupError(pid)
    if result.returncode != 0:
        raise RuntimeError("proc identity inspection failed: " +
                           str(record.get("error", result.stderr.strip())))
    if (set(record) != {"pid", "ppid", "starttime", "exe"} or
            record["pid"] != pid or type(record["ppid"]) is not int or
            type(record["starttime"]) is not int or record["starttime"] <= 0 or
            not isinstance(record["exe"], str) or not record["exe"].startswith("/")):
        raise RuntimeError("proc identity inspection returned invalid identity")
    return record
