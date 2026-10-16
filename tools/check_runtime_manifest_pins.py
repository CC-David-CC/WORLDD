#!/usr/bin/env python3
"""Validate that one runtime path has one SHA-256 across each host/profile."""
from __future__ import annotations
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HOSTS = ("sb16", "sb16-2")
PROFILES = ("Debug", "ASan-UBSan")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REQUIRED_HELPERS = {"spatial-bwrap", "spatial-storage-owner"}

class ManifestError(ValueError):
    pass

def _entry(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ManifestError(f"{where}: expected object with path and sha256")
    path, digest = value.get("path"), value.get("sha256")
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        raise ManifestError(f"{where}: path must be a non-root absolute path")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise ManifestError(f"{where}: sha256 must be 64 lowercase hexadecimal characters")
    return {"path": path, "sha256": digest}

def _name_list(manifest: dict[str, Any], key: str) -> set[str]:
    values = manifest.get(key)
    if (not isinstance(values, list) or not values
            or any(not isinstance(item, str) or not item for item in values)
            or len(values) != len(set(values))):
        raise ManifestError(f"{key}: expected a nonempty list of unique names")
    return set(values)

def validate_manifest(manifest: Any) -> list[str]:
    """Return conflict diagnostics, raising ManifestError for malformed metadata."""
    if not isinstance(manifest, dict):
        raise ManifestError("manifest root must be an object")
    required_egl = _name_list(manifest, "required_egl_names")
    required_helpers = _name_list(manifest, "required_helper_names")
    if not REQUIRED_HELPERS <= required_helpers:
        raise ManifestError("required_helper_names: required production helpers are missing")
    hosts = manifest.get("hosts")
    if not isinstance(hosts, dict) or set(hosts) != set(HOSTS):
        raise ManifestError(f"hosts: expected exactly {', '.join(HOSTS)}")
    conflicts: list[str] = []
    for host in HOSTS:
        host_rows = hosts[host]
        if not isinstance(host_rows, dict) or set(host_rows) != set(PROFILES):
            raise ManifestError(f"hosts/{host}: expected exactly profiles {', '.join(PROFILES)}")
        for profile in PROFILES:
            row = host_rows[profile]
            if not isinstance(row, dict):
                raise ManifestError(f"{host}/{profile}: profile row must be an object")
            for category in ("egl_files", "installed_helpers"):
                if not isinstance(row.get(category), list) or not row[category]:
                    raise ManifestError(f"{host}/{profile}: {category} must be a nonempty list")
            entries: list[tuple[str, dict[str, str]]] = []
            for category in ("server_receipt", "xace_wrapper"):
                value = row.get(category)
                entries.append((category, _entry(value, f"{host}/{profile}/{category}")))
            for category in ("egl_files", "installed_helpers"):
                for index, item in enumerate(row[category]):
                    entries.append((category, _entry(item, f"{host}/{profile}/{category}[{index}]")))
            for category, required, label in (
                ("egl_files", required_egl, "required EGL"),
                ("installed_helpers", required_helpers, "required helpers"),
            ):
                names = {Path(entry["path"]).name for cat, entry in entries if cat == category}
                if not required <= names:
                    missing = ", ".join(sorted(required - names))
                    raise ManifestError(f"{host}/{profile}: {label} missing: {missing}")
            by_path: dict[str, list[tuple[str, str]]] = defaultdict(list)
            for category, entry in entries:
                by_path[entry["path"]].append((category, entry["sha256"]))
            for path, refs in sorted(by_path.items()):
                if len({digest for _, digest in refs}) > 1:
                    details = ", ".join(f"{category}={digest}" for category, digest in refs)
                    conflicts.append(f"{host}/{profile}: conflicting SHA-256 for {path}: {details}")
    return conflicts

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only check for conflicting runtime path pins.")
    parser.add_argument("manifest", type=Path, help="runtime manifest JSON file")
    args = parser.parse_args(argv)
    try:
        with args.manifest.open("r", encoding="utf-8") as stream:
            conflicts = validate_manifest(json.load(stream))
    except (OSError, json.JSONDecodeError, ManifestError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if conflicts:
        for conflict in conflicts:
            print(f"CONFLICT: {conflict}", file=sys.stderr)
        return 1
    print(f"OK: consistent runtime pins in {args.manifest}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
