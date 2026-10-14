#!/usr/bin/env python3
import hashlib, json, os, re, shutil, subprocess, sys, tarfile
from datetime import datetime, timezone
from pathlib import Path

if len(sys.argv) != 5:
    raise SystemExit("usage: preflight_sb16.py ATTEMPT_ROOT BUNDLE SUPPLEMENT_DIR BINDING_JSON")
attempt, bundle, supplement_root, binding_path = map(Path, sys.argv[1:])
archive = attempt/"incoming/asan-diagnostic-supplement02.tar.gz"
binding = json.loads(binding_path.read_text())
supplement_meta = json.loads((supplement_root/"asan-diagnostic-supplement02/supplement.json").read_text())
baseline_archive = Path("/home/dflanag3/spatial-qualification/m06-geometry-run04-20261012-input/m06-geometry-asan-run04.tar.gz")
expected_baseline_archive = "82a326fb3f2982b7b78b9555c4ecf8b8d27f0c6a5ddcb13ffdd29d46b9aac336"
expected_archive = "c2533e333f59ad294120dd92ecb971731117fb41789ab9c8e2114d968db91508"
expected_commit = "5cbec99d1ad2844da1b8fd4b709c8e0444beb414"
assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected_archive == binding["archive_sha256"]
assert binding["source_commit"] == expected_commit
assert supplement_meta["source_commit"] == expected_commit
assert supplement_meta["baseline_archive_sha256"] == expected_baseline_archive
assert hashlib.sha256(baseline_archive.read_bytes()).hexdigest() == expected_baseline_archive
supp = supplement_root/"asan-diagnostic-supplement02"
manifest = (supp/"SHA256SUMS").read_bytes()
assert hashlib.sha256(manifest).hexdigest() == binding["member_manifest_sha256"]
member_rows = []
for row in manifest.decode().splitlines():
    digest, rel = row.split("  ", 1)
    path = supp/rel
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError(f"supplement member mismatch: {rel}")
    member_rows.append(rel)
for rel, digest in binding["source_sha256"].items():
    path = supp/"source"/rel
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError(f"source pin mismatch: {rel}")
fixture = supp/"source/tests/core/test_issuer_client.py"
fixture_bytes = fixture.read_bytes()
assert hashlib.sha256(fixture_bytes).hexdigest() == binding["source_sha256"]["tests/core/test_issuer_client.py"]
assert b"DIAGNOSTIC = CLIENT.parent/'spatial-issuer-failure-diagnostic'" in fixture_bytes
assert b"test_callback_failure_diagnostic_is_bounded_and_fail_closed" in fixture_bytes
compile(fixture_bytes, str(fixture), "exec")

# Verify every baseline bundle member before any overlay is applied.
base_manifest = bundle/"SHA256SUMS"
base_rows = base_manifest.read_text().splitlines()
assert base_rows and base_rows[0].startswith("base_commit ")
base_commit = base_rows[0].split()[1]
checked = 0
for row in base_rows[1:]:
    digest, rel = row.split("  ", 1)
    path = bundle/rel
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError(f"baseline member mismatch: {rel}")
    checked += 1
base_hashes = {row.split("  ",1)[1]:row.split("  ",1)[0] for row in base_rows[1:]}

# Confirm the original identities, then overlay only the three reviewed binaries and one test fixture.
for name in ("identityd", "spatial-issuer-client", "worldd"):
    p = bundle/"bin"/name
    if hashlib.sha256(p.read_bytes()).hexdigest() != base_hashes[f"bin/{name}"]:
        raise RuntimeError(f"unexpected baseline executable: {name}")
new_hashes = {}
for name, meta in binding["profiles"]["asan"]["binaries"].items():
    src = supp/"bin"/name
    if hashlib.sha256(src.read_bytes()).hexdigest() != meta["sha256"]:
        raise RuntimeError(f"candidate binary mismatch: {name}")
    dst = bundle/"bin"/name
    shutil.copy2(src, dst)
    if hashlib.sha256(dst.read_bytes()).hexdigest() != meta["sha256"]:
        raise RuntimeError(f"overlaid binary mismatch: {name}")
    new_hashes[name] = meta["sha256"]
shutil.copy2(fixture, bundle/"tests/core/test_issuer_client.py")
fixture_sha256 = binding["source_sha256"]["tests/core/test_issuer_client.py"]
assert hashlib.sha256((bundle/"tests/core/test_issuer_client.py").read_bytes()).hexdigest() == fixture_sha256

# Resolve dynamic dependencies on the test host and compare every actual library to the worker receipt.
expected_deps = json.loads((supp/"receipts/dependency-hashes.json").read_text())
expected_by_name = {Path(k).name: v for k,v in expected_deps.items()}
ldd_records = {}
for name in ("identityd", "spatial-issuer-client", "spatial-issuer-failure-diagnostic", "worldd"):
    proc = subprocess.run(["ldd", str(bundle/"bin"/name)], text=True, capture_output=True, check=False)
    output = proc.stdout + proc.stderr
    if proc.returncode != 0 or "not found" in output:
        raise RuntimeError(f"ldd failed for {name}: exit={proc.returncode}\n{output}")
    resolved = []
    for line in output.splitlines():
        match = re.match(r"\s*(\S+)\s+=>\s+(\S+)", line)
        if match:
            soname, path = match.groups()
        else:
            match = re.match(r"\s*(/\S+)\s+\(0x[0-9a-fA-F]+\)", line)
            if not match: continue
            path = match.group(1); soname = Path(path).name
        real = Path(path).resolve(strict=True)
        expected = expected_by_name.get(soname)
        if expected is None:
            raise RuntimeError(f"unbound dependency {soname} ({path}) for {name}")
        actual_hash = hashlib.sha256(real.read_bytes()).hexdigest()
        if actual_hash != expected["sha256"] or str(real) != expected["resolved"]:
            raise RuntimeError(f"dependency mismatch {soname}: {real} {actual_hash}")
        resolved.append({"soname":soname,"path":path,"resolved":str(real),"sha256":actual_hash})
    ldd_records[name] = {"exit":proc.returncode,"output":output,"resolved":resolved}

receipt = {
  "status":"PREFLIGHT_PASS", "runtime":"UNRUN", "host":os.uname().nodename,
  "utc":datetime.now(timezone.utc).isoformat(), "source_commit":binding["source_commit"],
  "base_commit":base_commit, "baseline_archive_sha256":hashlib.sha256(baseline_archive.read_bytes()).hexdigest(),
  "baseline_member_count":checked, "supplement_archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
  "supplement_manifest_sha256":hashlib.sha256(manifest).hexdigest(), "supplement_member_count":len(member_rows),
  "fixture_sha256":fixture_sha256, "binary_hashes":new_hashes,
  "identityd_sha256":hashlib.sha256((bundle/"bin/identityd").read_bytes()).hexdigest(),
  "driver_sha256":hashlib.sha256((attempt/"incoming/issuer_case_driver.py").read_bytes()).hexdigest(),
  "timeout_wrapper_sha256":hashlib.sha256((attempt/"incoming/bounded_case.py").read_bytes()).hexdigest(),
  "ldd":ldd_records,
  "selected_regression":"ClientTests.test_callback_failure_diagnostic_is_bounded_and_fail_closed",
  "selected_count_reproduction":"GeometryViewTests.test_geometry_count_limit_is_controlled_and_other_actor_survives",
  "runtime_environment":{"PYTHONPATH":str(bundle/"python")+":"+str(bundle),"PATH":str(bundle/"bin")+":/usr/bin:/bin","LD_LIBRARY_PATH":"cleared","DISPLAY":"cleared","WAYLAND_DISPLAY":"cleared","XAUTHORITY":"cleared","sanitizer_overrides":"cleared","CTest_timeout_seconds":90,"X_backend":"not used"}
}
out=attempt/"preflight.json"
out.write_text(json.dumps(receipt,sort_keys=True,indent=2)+"\n")
print(json.dumps(receipt,sort_keys=True))
