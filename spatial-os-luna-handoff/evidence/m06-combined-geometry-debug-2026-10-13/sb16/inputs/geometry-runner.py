#!/usr/bin/env python3
"""Run the frozen M06 candidate on a qualification host, with durable receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time

PIN = "fc302de44670f87737dd7ab46ef83b24c60f1612"
SUITES = (
    "actor-geometry-client-hostile-peer", "actor-geometry-world-headless",
    "actor-session-client", "actor-session-client-tls",
    "actor-transition-coordinator", "actor-transition-real-world",
    "world-identity", "world-authority", "session-application",
    "snapshot-size-regression", "worldd-lifecycle", "issuer-client",
    "world-diagnostic-timing",
)
UNITTEST_COUNTS = {
    "actor-geometry-world-headless": 9, "actor-transition-real-world": 15,
    "world-identity": 27, "world-authority": 27, "session-application": 3,
    "worldd-lifecycle": 11, "issuer-client": 31,
}
QUIET_EXECUTABLES = {"snapshot-size-regression", "world-diagnostic-timing"}
GEOMETRY_FAULTS = (
    "valid", "near-boundary", "oversize-frame", "truncated-frame", "actor",
    "world", "view", "epoch", "service", "later-epoch", "duplicate",
    "decreasing", "room", "camera", "config", "kind", "duplicate-id",
    "unknown", "count",
)
TLS_CASES = (
    "SIGPIPE-contract-no-global-side-effects", "one-record", "fragments", "dns",
    "ca", "invalid-client", "handshake-timeout", "timeout", "partial-timeout",
    "truncated-header", "truncated-body", "malformed", "rejected", "request-id",
    "version", "outcome", "unknown", "closed-write", "missing-client",
    "missing-key", "missing-ca", "timeout-low", "timeout-high", "config-version",
    "config-port", "config-address", "config-name", "config-world",
    "config-unknown", "actor-key", "mismatched-key",
)
TRANSITION_FAULTS = (
    "success", "prepare-denied", "prepare-lost", "configured-actor",
    "configured-world", "prepared-actor", "prepared-world", "prepared-transition",
    "source-world", "depart-denied", "depart-storage-error",
    "depart-internal-error", "depart-lost", "depart-timeout", "depart-actor",
    "depart-world", "depart-transition", "depart-malformed-error",
    "activate-denied", "activate-internal-error", "activate-lost",
    "activate-timeout", "activate-actor", "activate-world",
    "activate-malformed-error",
)
TRANSITION_CASES = (
    *TRANSITION_FAULTS, "forward-room", "forward-pose", "forward-room-pose",
    *("client-error-" + x for x in ("rejection", "lost", "timeout", "malformed-error")),
    *("source-inspection-" + x for x in ("rejection", "lost", "timeout")),
)
SESSION_CASES = (
    "authentication-fragments-lifecycle-departure", "prepared-inactivity-activation",
    *("challenge-" + x for x in ("purpose", "issuer", "credential", "audience", "nonce", "nonce-length", "binding", "version", "lifetime", "incarnation", "payload")),
    *("envelope-" + x for x in ("version", "request-id", "outcome", "error", "error-wrong-id", "missing-outcome", "unknown-field")),
    *(stage + "-" + fault for stage in ("opened", "prepared", "activate", "depart") for fault in (("actor", "world", "transition") if stage in ("prepared", "depart") else ("actor", "world"))),
    *("frame-" + x for x in ("header", "body", "zero", "oversize", "protobuf", "timeout", "partial-timeout")),
    *("uncertain-loss-no-replay-" + x for x in ("activate", "depart", "action")),
    *("mutating-error-no-replay-" + stage + "-" + str(code) for stage in ("depart", "activate", "action") for code in (10, 4, 13)),
    "write-backpressure", "buffered-final-reply",
)
PROBE_CASES = {
    "actor-geometry-client-hostile-peer": tuple("geometry-peer-" + x for x in GEOMETRY_FAULTS),
    "actor-session-client": SESSION_CASES,
    "actor-session-client-tls": TLS_CASES,
    "actor-transition-coordinator": TRANSITION_CASES,
}
DIAGNOSTIC = re.compile(r"AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|MemorySanitizer|runtime error:", re.I)
SKIP = re.compile(r"^OK \(skipped=|^test\S*.*\bskipped\b|^SKIP\b|\b\d+ tests? skipped\b", re.I | re.M)
BAD_SANITIZER = re.compile(r"suppressions?=|suppress|detect_leaks=0|halt_on_error=0|abort_on_error=0|handle_segv=0", re.I)
CLEAR = (
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "LD_PRELOAD", "LD_LIBRARY_PATH",
    "ASAN_OPTIONS", "UBSAN_OPTIONS", "LSAN_OPTIONS", "MSAN_OPTIONS", "PYTHONPATH",
    "SPATIAL_TEST_XSERVER", "SPATIAL_TEST_XSERVER_LOG", "SPATIAL_SECURITY_EVIDENCE",
    "SPATIAL_SCOPE_EVIDENCE", "SPATIAL_SECURITY_TEST_CORE_ONLY", "SPATIAL_CORE_ONLY",
)


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_sha(path: Path) -> str | None:
    try:
        return sha(path) if path.is_file() else None
    except OSError:
        return None


def write_json(path: Path, value: object) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def relative(value: str) -> Path:
    require(isinstance(value, str) and value and "\\" not in value, "unsafe archive path")
    pure = PurePosixPath(value)
    require(not pure.is_absolute() and value == pure.as_posix() and
            all(x not in ("", ".", "..") for x in pure.parts), "unsafe archive path")
    return Path(*pure.parts)


def regular_hash(path: Path, digest: str, label: str) -> None:
    require(isinstance(digest, str) and bool(re.fullmatch(r"[0-9a-f]{64}", digest)), label + " invalid SHA256")
    require(path.is_file() and not path.is_symlink() and sha(path) == digest, label + " missing/hash mismatch")


def no_alias(output: Path, inputs: list[Path]) -> None:
    output = output.resolve()
    for item in inputs:
        path = item.resolve()
        require(output != path and output not in path.parents and path not in output.parents,
                "attempt aliases an input path: " + str(item))


def extract_checked(archive: Path, bundle: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        names = [member.name for member in members]
        require(len(names) == len(set(names)), "duplicate archive path")
        for member in members:
            path = relative(member.name)
            require(member.isfile() and (bundle / path).resolve().is_relative_to(bundle.resolve()),
                    "link/special/escaping archive entry: " + member.name)
        bundle.mkdir()
        tar.extractall(bundle, members=members, filter="data")


def verify_bundle(bundle: Path, manifest: dict, expected_profile: str) -> dict[str, str]:
    require(manifest.get("candidate_commit") == PIN, "candidate commit mismatch")
    require(manifest.get("profile") == expected_profile, "profile mismatch")
    require(str(manifest.get("runtime_checks", "")).startswith("UNRUN"), "candidate already qualified")
    payload = manifest.get("payload_sha256")
    require(isinstance(payload, dict) and payload, "missing payload hashes")
    require(manifest.get("payload_file_count") == len(payload), "payload count mismatch")
    for name, digest in payload.items():
        regular_hash(bundle / relative(name), digest, "payload " + name)
    actual = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file() and path != bundle / "qualification-manifest.json"}
    require(actual == set(payload), "archive/manifest inventory mismatch")
    sources = manifest.get("source_closure", {}).get("sha256")
    require(isinstance(sources, dict) and sources and len(sources) == manifest["source_closure"].get("file_count"), "source closure incomplete")
    for name, digest in sources.items():
        require(payload.get(name) == digest and
                (name == "AGENTS.md" or name.startswith("spatial-os-luna-handoff/")),
                "source closure mismatch: " + name)
    for label, testing in (("testing_on", "ON"), ("testing_off_production_closure", "OFF")):
        record = manifest["build_configurations"][label]
        config = record["cmake_options"]
        required = {"CMAKE_BUILD_TYPE": "Debug", "BUILD_TESTING": testing,
                    "SPATIAL_SANITIZERS": "ON" if expected_profile == "asan" else "OFF",
                    "SPATIAL_INPUT_TESTS": "ON"}
        require(all(config.get(k) == v for k, v in required.items()), "build config mismatch: " + label)
        build = bundle / relative(manifest["build_dirs"][label])
        require(build.is_dir(), "missing build closure: " + label)
        for key, filename in (("cmake_cache_sha256", build / "CMakeCache.txt"),
                              ("build_id_sha256", bundle / relative(record["build_id_file"])),
                              ("runtime_textproto_sha256", build / "runtime.textproto")):
            require(payload.get(filename.relative_to(bundle).as_posix()) == record[key], "config/payload linkage mismatch: " + key)
        if record.get("ctest_metadata_sha256"):
            require(payload.get((build / "CTestTestfile.cmake").relative_to(bundle).as_posix()) == record["ctest_metadata_sha256"], "CTest metadata mismatch")
        helper_key = "required_on_helpers" if testing == "ON" else "required_off_helpers"
        helpers = manifest.get("runtime_closure", {}).get(helper_key)
        require(isinstance(helpers, list) and helpers, "required helper list missing: " + label)
        for helper in helpers:
            require(isinstance(helper, str) and helper and "/" not in helper, "unsafe required helper")
            path = build / helper
            require(path.is_file() and os.access(path, os.X_OK) and
                    payload.get(path.relative_to(bundle).as_posix()) == sha(path),
                    "required helper missing/hash mismatch: " + helper)
    require(manifest.get("selected_ctest_names") == list(SUITES), "manifest suite selection mismatch")
    return payload


def map_prefix(value: str, mapping: list[tuple[str, str]]) -> str:
    require(isinstance(value, str), "non-string command/environment item")
    replacements = dict(mapping)
    pattern = re.compile("|".join(re.escape(source) + r"(?=/|:|$)"
                                  for source in sorted(replacements, key=len, reverse=True)))
    return pattern.sub(lambda match: replacements[match.group()], value)


def check_environment(env: dict[str, str], label: str) -> None:
    require(not env.get("SPATIAL_CORE_ONLY") and not env.get("SPATIAL_SECURITY_TEST_CORE_ONLY"), label + " core-only forbidden")
    for name in ("ASAN_OPTIONS", "UBSAN_OPTIONS", "LSAN_OPTIONS", "MSAN_OPTIONS"):
        require(not BAD_SANITIZER.search(env.get(name, "")), label + " sanitizer policy weakened: " + name)


def mapped_inputs(argv: list[str], env: dict[str, str], cwd: Path, bundle: Path, source: str) -> list[Path]:
    require(argv and Path(argv[0]).is_file() and os.access(argv[0], os.X_OK), "mapped command missing")
    require(cwd.is_dir(), "mapped working directory missing")
    paths = []
    for value in [*argv, *env.values()]:
        require(source not in value, "source prefix unresolved")
        for token in value.split(os.pathsep):
            if token.startswith(str(bundle)):
                path = Path(token)
                require(path.exists() and not path.is_symlink(), "mapped input/helper missing: " + token)
                paths.append(path)
    return paths


def prepare(manifest: dict, inventory: dict, bundle: Path, attempt: Path,
            wrapper: Path, diagnostic_timeout: int | None) -> tuple[list[dict], list[Path]]:
    tests = inventory.get("selected_tests")
    require(isinstance(tests, list) and [test.get("name") for test in tests] == list(SUITES), "inventory suite mismatch")
    require(inventory.get("profile") == manifest["profile"], "inventory profile mismatch")
    source = manifest["source_checkout"].rstrip("/")
    build_rel = relative(manifest["build_dirs"]["testing_on"])
    old_build = source + "/" + build_rel.as_posix()
    new_build = str((bundle / build_rel).resolve())
    mapping = [(old_build, new_build), (source, str(bundle.resolve()))]
    prepared, inputs = [], []
    for index, test in enumerate(tests, 1):
        name = test["name"]
        ctest_timeout = test.get("timeout_seconds")
        timeout = ctest_timeout
        provenance = {"ctest_timeout_seconds": ctest_timeout, "qualification_override_seconds": None}
        if timeout is None:
            require(name == "world-diagnostic-timing" and diagnostic_timeout == 60,
                    name + " missing CTest TIMEOUT; explicit reviewed --diagnostic-timeout-seconds 60 required")
            require(test.get("ctest_invocation") ==
                    "ctest --test-dir <PROFILE_BUILD> --output-on-failure --timeout 600 -R '^world-diagnostic-timing$'",
                    name + " CTest invocation provenance changed")
            timeout = diagnostic_timeout
            provenance["qualification_override_seconds"] = diagnostic_timeout
        require(isinstance(timeout, (int, float)) and timeout > 0, name + " invalid timeout")
        argv = [map_prefix(value, mapping) for value in test["command"]]
        cwd = Path(map_prefix(test["working_directory"], mapping))
        declared = test.get("environment", [])
        if isinstance(declared, str):
            declared = declared.split(";") if declared else []
        require(isinstance(declared, list), name + " malformed environment")
        env = os.environ.copy()
        for key in CLEAR:
            env.pop(key, None)
        mapped = {}
        for item in declared:
            require(isinstance(item, str) and "=" in item, name + " malformed environment item")
            key, value = item.split("=", 1)
            require(key and key not in mapped, name + " duplicate environment key")
            mapped[key] = map_prefix(value, mapping)
            env[key] = mapped[key]
        check_environment(env, name)
        inputs.extend(mapped_inputs(argv, mapped, cwd, bundle, source))
        witness = attempt / "witness" / name
        overrides = {"SPATIAL_TEST_XSERVER": str(wrapper.resolve()),
                     "SPATIAL_TEST_XSERVER_LOG": str(witness / "xserver.log"),
                     "SPATIAL_SECURITY_EVIDENCE": str(witness / "security"),
                     "SPATIAL_SCOPE_EVIDENCE": str(witness / "scope")}
        prepared.append({"name": name, "argv": argv, "cwd": str(cwd), "timeout_seconds": timeout,
                         "timeout_provenance": provenance,
                         "declared_environment": mapped, "runtime_overrides": overrides,
                         "test_dir": str(attempt / f"{index:02d}-{name}")})
    return prepared, inputs


def scan_elf(bundle: Path, manifest: dict, output: Path) -> list[str]:
    require(shutil.which("ldd") is not None, "ldd missing")
    logs = output / "ldd"
    logs.mkdir()
    scanned = []
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    for label in ("testing_on", "testing_off_production_closure"):
        build = bundle / relative(manifest["build_dirs"][label])
        for file in sorted(build.iterdir()):
            if not file.is_file():
                continue
            with file.open("rb") as stream:
                magic = stream.read(4)
            if magic != b"\x7fELF":
                continue
            result = subprocess.run(["ldd", str(file)], capture_output=True, text=True, env=env, timeout=15)
            log = result.stdout + result.stderr
            (logs / f"{label}-{file.name}.log").write_text(log, encoding="utf-8")
            require("not found" not in log and (result.returncode == 0 or "not a dynamic executable" in log.lower()), "ldd closure failed: " + str(file))
            scanned.append(file.relative_to(bundle).as_posix())
    require(scanned, "ELF dependency scan found no files")
    return scanned


def group_snapshot(pgid: int) -> list[dict]:
    rows = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            stat = (proc / "stat").read_text()
            fields = stat.rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid:
                rows.append({"pid": int(proc.name), "state": fields[0], "starttime": fields[19],
                             "cmdline": (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")})
        except (OSError, ValueError, IndexError):
            pass
    return rows


def stop_group(child: subprocess.Popen, receipt: Path) -> bool:
    before = group_snapshot(child.pid)
    stages = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and group_snapshot(child.pid):
            child.poll()
            time.sleep(.1)
        stages.append({"signal": sig.name, "remaining": group_snapshot(child.pid)})
        if not stages[-1]["remaining"]:
            break
    try:
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    final = group_snapshot(child.pid)
    write_json(receipt, {"process_group": child.pid, "before": before, "stages": stages,
                         "remaining": final, "resolved": not final})
    return not final


def verify_output(name: str, test_dir: Path, witness: Path) -> None:
    texts = []
    for root in (test_dir, witness):
        for path in root.rglob("*"):
            if path.is_file() and path.name.endswith((".log", ".stderr", ".stdout", ".txt")):
                value = path.read_text(errors="replace")
                require(not DIAGNOSTIC.search(value), name + " sanitizer diagnostic: " + str(path))
                require(not SKIP.search(value), name + " skipped case: " + str(path))
                texts.append(value)
    output = "\n".join(texts)
    if name in PROBE_CASES:
        found = re.findall(r"^PASS ([^\r\n]+)$", output, re.M)
        require(len(found) == len(set(found)) and set(found) == set(PROBE_CASES[name]), name + " probe case markers mismatch")
        if name != "actor-geometry-client-hostile-peer":
            expected = len(found) + (2 if name == "actor-session-client" else 0)
            require(bool(re.search(rf"^{expected} .* cases passed$", output, re.M)), name + " probe summary mismatch")
    elif name in UNITTEST_COUNTS:
        expected = UNITTEST_COUNTS[name]
        require(bool(re.search(rf"Ran {expected} tests? in ", output)) and bool(re.search(r"^OK\s*$", output, re.M)), name + " unittest summary mismatch")
    else:
        require(name in QUIET_EXECUTABLES, name + " unknown output contract")


def receipts(test_dir: Path) -> dict[str, str | None]:
    return {name: (test_dir / name).read_text().strip() if (test_dir / name).is_file() else None
            for name in ("command.exit", "wrapper.exit", "terminal-status.txt")}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    for name in ("host", "profile", "archive", "archive-sha256", "inventory",
                 "inventory-sha256", "attempt", "xace-wrapper", "xace-wrapper-sha256",
                 "run-command", "run-command-sha256"):
        result.add_argument("--" + name, required=True)
    result.add_argument("--suite", action="append", choices=SUITES)
    result.add_argument("--diagnostic-timeout-seconds", type=int,
                        help="reviewed 60-second qualification launch bound for the diagnostic standalone test")
    result.add_argument("--preflight-only", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    require(os.geteuid() != 0, "refuse root")
    require(args.host in ("sb16", "sb16-2") and args.profile in ("debug", "asan"), "host/profile invalid")
    check_environment(os.environ, "caller")
    require(not os.environ.get("LD_PRELOAD") and not os.environ.get("LD_LIBRARY_PATH"),
            "caller loader override forbidden")
    archive, inventory = Path(args.archive), Path(args.inventory)
    wrapper, helper = Path(args.xace_wrapper), Path(args.run_command)
    attempt = Path(args.attempt).absolute()
    external = [archive, inventory, wrapper, helper, Path(__file__)]
    for path, digest, label in ((archive, args.archive_sha256, "archive"),
                                (inventory, args.inventory_sha256, "inventory"),
                                (wrapper, args.xace_wrapper_sha256, "wrapper"),
                                (helper, args.run_command_sha256, "run-command")):
        regular_hash(path, digest, label)
    require(os.access(wrapper, os.X_OK), "wrapper not executable")
    require(not attempt.exists() and not attempt.is_symlink(), "attempt already exists")
    no_alias(attempt, external)
    if not args.preflight_only:
        require(subprocess.check_output(["hostname", "-s"], text=True).strip() == args.host and bool(os.environ.get("TMUX")), "runtime host/tmux mismatch")
    chosen = args.suite or list(SUITES)
    require(len(chosen) == len(set(chosen)), "duplicate suite selection")
    attempt.mkdir(parents=True)
    status = {"host": args.host, "profile": args.profile, "candidate_commit": PIN,
              "selected": chosen, "passed": [], "failed": [], "blocked": [],
              "unrun": list(SUITES), "input_pre": {}, "input_post": {},
              "input_invariant": "UNRUN",
              "diagnostic_timeout_override_seconds": args.diagnostic_timeout_seconds}
    write_json(attempt / "results.json", status)
    bundle = attempt / "bundle"
    tracked: dict[str, Path] = {}
    try:
        extract_checked(archive, bundle)
        manifest_path = bundle / "qualification-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = verify_bundle(bundle, manifest, args.profile)
        inventory_path = bundle / relative(manifest["selected_ctest_inventory"])
        require(sha(inventory_path) == sha(inventory), "external inventory differs")
        data = json.loads(inventory.read_text(encoding="utf-8"))
        require(data == json.loads(inventory_path.read_text(encoding="utf-8")), "inventory contents differ")
        prepared, mapped = prepare(manifest, data, bundle, attempt, wrapper,
                                   args.diagnostic_timeout_seconds)
        no_alias(attempt, [p for p in mapped if not p.is_relative_to(bundle)])
        tracked = {"archive": archive, "inventory": inventory, "wrapper": wrapper,
                   "run_command": helper, "runner": Path(__file__), "manifest": manifest_path}
        tracked.update({"payload/" + name: bundle / relative(name) for name in payload})
        status["input_pre"] = {name: sha(path) for name, path in tracked.items()}
        status["input_invariant"] = "PENDING"
        write_json(attempt / "results.json", status)
        elf = scan_elf(bundle, manifest, attempt)
        write_json(attempt / "preflight.json", {"candidate_commit": PIN, "profile": args.profile,
                   "manifest_sha256": sha(manifest_path), "archive_sha256": sha(archive),
                   "inventory_sha256": sha(inventory), "wrapper_sha256": sha(wrapper),
                   "run_command_sha256": sha(helper), "runner_sha256": sha(Path(__file__)),
                   "source_closure": manifest["source_closure"]["sha256"],
                   "elf_scanned": elf, "commands": prepared, "selected": chosen})
        if not args.preflight_only:
            for item in prepared:
                name = item["name"]
                if name not in chosen:
                    continue
                directory = Path(item["test_dir"])
                witness = attempt / "witness" / name
                (witness / "security").mkdir(parents=True)
                (witness / "scope").mkdir()
                env = os.environ.copy()
                for key in CLEAR:
                    env.pop(key, None)
                env.update(item["declared_environment"])
                env.update(item["runtime_overrides"])
                env["PATH"] = str(bundle / relative(manifest["build_dirs"]["testing_on"])) + os.pathsep + env.get("PATH", "")
                check_environment(env, name)
                command = ["bash", str(helper.resolve()), item["cwd"], str(directory), *item["argv"]]
                record = {"suite": name, "argv": command, "timeout_seconds": item["timeout_seconds"],
                          "timeout_provenance": item["timeout_provenance"], "started_epoch": time.time()}
                write_json(attempt / (name + ".launch.json"), record)
                child = subprocess.Popen(command, env=env, start_new_session=True)
                timed = False
                interrupted = False
                cleanup_resolved = True
                try:
                    return_code = child.wait(timeout=item["timeout_seconds"])
                except subprocess.TimeoutExpired:
                    timed = True
                    cleanup_resolved = stop_group(child, attempt / (name + ".cleanup.json"))
                    return_code = child.poll()
                except KeyboardInterrupt:
                    interrupted = True
                    cleanup_resolved = stop_group(child, attempt / (name + ".cleanup.json"))
                    return_code = child.poll()
                remaining = group_snapshot(child.pid)
                if remaining and not timed and not interrupted:
                    cleanup_resolved = stop_group(child, attempt / (name + ".cleanup.json"))
                record.update({"ended_epoch": time.time(), "return_code": return_code,
                               "timed_out": timed, "interrupted": interrupted,
                               "cleanup_resolved": cleanup_resolved,
                               "unexpected_group_members": remaining if not timed and not interrupted else [],
                               "receipts": receipts(directory)})
                if not cleanup_resolved:
                    status["blocked"].append({"suite": name, "reason": "process group remains after bounded cleanup"})
                try:
                    require(not timed and not interrupted and cleanup_resolved and not remaining,
                            name + " timeout/interrupt/unresolved group")
                    require(return_code == 0 and record["receipts"] == {"command.exit": "0", "wrapper.exit": "0", "terminal-status.txt": "COMPLETE"}, name + " launcher/command failure")
                    verify_output(name, directory, witness)
                    record["status"] = "passed"
                    status["passed"].append(name)
                except Exception as error:
                    record["status"] = "failed"
                    record["reason"] = str(error)
                    status["failed"].append({"suite": name, "reason": str(error)})
                status["unrun"].remove(name)
                write_json(attempt / (name + ".result.json"), record)
                write_json(attempt / "results.json", status)
                if record["status"] != "passed":
                    break
        status["input_post"] = {name: safe_sha(path) for name, path in tracked.items()}
        changed = [name for name, digest in status["input_pre"].items() if status["input_post"].get(name) != digest]
        status["input_invariant"] = "PASS" if not changed else "FAIL"
        status["changed_inputs"] = changed
        if changed:
            status["failed"].append({"suite": "input-invariant", "reason": "inputs changed after run: " + ", ".join(changed)})
        write_json(attempt / "results.json", status)
        require(not status["failed"] and status["input_invariant"] == "PASS", "qualification attempt failed")
        print("PREFLIGHT_OK" if args.preflight_only else "RUNTIME_COMPLETE", attempt / "results.json")
        return 0
    except BaseException as error:
        if tracked and status["input_pre"]:
            status["input_post"] = {name: safe_sha(path)
                                    for name, path in tracked.items()}
            changed = [name for name, digest in status["input_pre"].items()
                       if status["input_post"].get(name) != digest]
            status["input_invariant"] = "FAIL" if changed else "PASS"
            status["changed_inputs"] = changed
        status["failed"].append({"suite": "runner", "reason": str(error)})
        write_json(attempt / "results.json", status)
        (attempt / "failure.txt").write_text(str(error) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as error:
        print("RUNNER_ERROR:", error, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(exit_code)
