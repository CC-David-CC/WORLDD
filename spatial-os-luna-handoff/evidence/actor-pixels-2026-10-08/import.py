"""Import a completed host evidence archive without replacing existing evidence."""
import hashlib
import json
import pathlib
import re
import sys
import tarfile

project = pathlib.Path(__file__).resolve().parent.parent
host = sys.argv[1]
assert host in {"sb16", "sb16-2"}
archive = project / "local" / f"actor-pixels-evidence-{host}.tar"
destination = project / "evidence" / "actor-pixels-2026-10-08" / host
assert not destination.exists(), "Evidence destination already exists"
with tarfile.open(archive) as source:
    members = source.getmembers()
    assert members and len({m.name for m in members}) == len(members)
    for member in members:
        assert member.isfile(), "Only regular evidence files are accepted"
        target = (destination / member.name).resolve()
        assert target.is_relative_to(destination.resolve()), "Archive path escapes destination"
        assert member.size <= 64 * 1024 * 1024, "Unexpected evidence size"
        data = source.extractfile(member).read()
        assert not re.search(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|Bearer [A-Za-z0-9._~-]{12,}", data), "Possible credential in evidence"
    source.extractall(destination, filter="data")
summary = json.loads((destination / "summary.json").read_text())
for profile in ["debug", "runtime-sanitize"]:
    assert summary[profile] == dict(core_suites=47, core_checks=914, harness_checks=51, acceptance_checks=151, failed=0, skipped=0)
assert summary["focused"] == dict(checks=45, failed=0, skipped=0)
integrity = json.loads((destination / "source-integrity.json").read_text())
assert integrity == dict(host=host, checked=460, mismatches=[])
for name in ["tested-inputs.json", "tested-tree.json"]:
    assert json.loads((destination / name).read_text()) == json.loads((destination.parent / name).read_text())
print(json.dumps(dict(host=host, files=len(members), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), summary=summary), indent=2))
