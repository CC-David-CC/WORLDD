#!/usr/bin/env python3
"""Run a command with exact profile-matched production copy helpers installed."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve()
HELPER_NAMES = ("spatial-copy-tree-worker", "spatial-copy-finalize-worker")
HELPER_ROOT = Path("/usr/local/libexec")
PROFILES = ("Debug", "ASan-UBSan")
CHILD_TIMEOUT_SECONDS = 14340
APPROVED_HOSTS = ("sb16", "sb16-2")
CHILD_TERM_GRACE_SECONDS = 5
CHILD_KILL_GRACE_SECONDS = 10
ROOT_COMMAND_TIMEOUT_SECONDS = 30
LOCK_ROOT = Path("/run/user")


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _helper_state(path):
    path = Path(path)
    if not os.path.lexists(path):
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("Protected copy helper is not a root-owned safe regular file: " + str(path))
    return {"sha256": sha256(path), "uid": info.st_uid, "gid": info.st_gid,
            "mode": stat.S_IMODE(info.st_mode)}


def _validate_helper_root(root):
    root = Path(root)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("Protected helper directory is not a root-owned safe directory: " + str(root))


def _acquire_lock():
    uid = os.getuid()
    runtime = LOCK_ROOT / str(uid)
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise RuntimeError("Per-user runtime directory is not private and owned by this actor")
    path = runtime / "spatial-copy-worker-helpers.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600:
        os.close(descriptor)
        raise RuntimeError("Per-user helper lock has unsafe ownership or mode")
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


PROCESS_INSPECTOR_SOURCE = r'''import json, os, sys
expected = set(sys.argv[1:])
own_pid = os.getpid()
excluded_pids = {own_pid}
matches = []
visibility_errors = []
ancestor_pid = os.getppid()
try:
    ancestor_executable = os.readlink("/proc/" + str(ancestor_pid) + "/exe")
    normalized_ancestor = ancestor_executable[:-10] if ancestor_executable.endswith(" (deleted)") else ancestor_executable
    while normalized_ancestor.endswith("/sudo"):
        excluded_pids.add(ancestor_pid)
        with open("/proc/" + str(ancestor_pid) + "/stat", "rb") as stream:
            fields = stream.read().rsplit(b")", 1)[1].split()
        ancestor_pid = int(fields[1])
        ancestor_executable = os.readlink("/proc/" + str(ancestor_pid) + "/exe")
        normalized_ancestor = ancestor_executable[:-10] if ancestor_executable.endswith(" (deleted)") else ancestor_executable
except FileNotFoundError:
    pass
except OSError as error:
    visibility_errors.append({"pid": ancestor_pid, "error": type(error).__name__})
for entry in os.scandir("/proc"):
    if not entry.name.isdecimal():
        continue
    pid = int(entry.name)
    if pid in excluded_pids:
        continue
    try:
        executable = os.readlink("/proc/" + entry.name + "/exe")
        with open("/proc/" + entry.name + "/cmdline", "rb") as stream:
            raw = stream.read()
    except FileNotFoundError:
        continue
    except OSError as error:
        visibility_errors.append({"pid": pid, "error": type(error).__name__})
        continue
    normalized = executable[:-10] if executable.endswith(" (deleted)") else executable
    arguments = raw.rstrip(b"\0").split(b"\0")
    argument_paths = {value.decode(errors="replace") for value in arguments}
    if normalized in expected or expected.intersection(argument_paths):
        matches.append({"pid": pid, "executable": executable})
print(json.dumps({"schema": 1, "inspector_pid": own_pid,
                  "matches": matches, "visibility_errors": visibility_errors}, sort_keys=True))
sys.exit(2 if visibility_errors else 0)
'''


def _inspect_helper_processes(paths, report, evidence):
    paths = tuple(str(Path(path)) for path in paths)
    command = ["sudo", "-n", "/usr/bin/python3", "-I", "-S", "-c",
               PROCESS_INSPECTOR_SOURCE, *paths]
    row = {"argv": command, "status": "running", "helper_paths": list(paths)}
    report.setdefault("process_inspections", []).append(row)
    _atomic_json(evidence / "lifecycle.json", report)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except BaseException as error:
        row.update(status="launch-failed", error=repr(error))
        _atomic_json(evidence / "lifecycle.json", report)
        raise RuntimeError("Privileged copy helper process inspection failed") from error
    row.update(status="returned", exit=result.returncode, stdout=result.stdout, stderr=result.stderr)
    _atomic_json(evidence / "lifecycle.json", report)
    if result.returncode:
        raise RuntimeError("Privileged process inspector could not verify process visibility")
    try:
        payload = json.loads(result.stdout)
        if payload.get("schema") != 1 or not isinstance(payload.get("matches"), list) or not isinstance(payload.get("visibility_errors"), list):
            raise ValueError("unexpected process inspection payload")
    except (ValueError, AttributeError, TypeError) as error:
        raise RuntimeError("Privileged process inspector returned invalid evidence") from error
    row["inspector_pid"] = payload.get("inspector_pid")
    row["matches"] = payload["matches"]
    row["visibility_errors"] = payload["visibility_errors"]
    _atomic_json(evidence / "lifecycle.json", report)
    if payload["visibility_errors"]:
        raise RuntimeError("Privileged process inspector reported incomplete /proc visibility")
    if payload["matches"]:
        raise RuntimeError("Copy helper process is active: " + json.dumps(payload["matches"], sort_keys=True))


def _group_has_live_processes(process_group):
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="ascii")
        except FileNotFoundError:
            continue
        except PermissionError as error:
            raise RuntimeError("Cannot verify owned child process group") from error
        close = raw.rfind(")")
        if close < 0:
            raise RuntimeError("Malformed process stat while verifying owned child group")
        fields = raw[close + 2:].split()
        if len(fields) < 3:
            raise RuntimeError("Truncated process stat while verifying owned child group")
        state = fields[0]
        try:
            group = int(fields[2])
        except ValueError as error:
            raise RuntimeError("Invalid process group in proc stat") from error
        if group == process_group and state not in ("Z", "X"):
            return True
    return False


def _wait_for_group_exit(process_group, timeout):
    deadline = time.monotonic() + timeout
    while True:
        if not _group_has_live_processes(process_group):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _signal_owned_group(process_group, signum, report, evidence):
    row = {"process_group": process_group, "signal": signum, "status": "planned"}
    report.setdefault("child_signals", []).append(row)
    _atomic_json(evidence / "lifecycle.json", report)
    try:
        os.killpg(process_group, signum)
        row["status"] = "sent"
    except ProcessLookupError:
        row["status"] = "already-exited"
    except OSError as error:
        row.update(status="failed", error=repr(error))
        _atomic_json(evidence / "lifecycle.json", report)
        raise RuntimeError("Cannot signal the owned child process group") from error
    _atomic_json(evidence / "lifecycle.json", report)


def _retire_owned_group(child, report, evidence, reason):
    process_group = child.pid
    report["child_process"]["termination_reason"] = reason
    _atomic_json(evidence / "lifecycle.json", report)
    _signal_owned_group(process_group, signal.SIGTERM, report, evidence)
    try:
        child.wait(timeout=CHILD_TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    if _group_has_live_processes(process_group):
        _signal_owned_group(process_group, signal.SIGKILL, report, evidence)
        try:
            child.wait(timeout=CHILD_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    retired = _wait_for_group_exit(process_group, CHILD_KILL_GRACE_SECONDS)
    report["child_process"].update(status="retired" if retired else "retirement-unconfirmed",
                                   returncode=child.poll(), process_group_empty=retired)
    _atomic_json(evidence / "lifecycle.json", report)
    return retired


def _run_owned_child(command, report, evidence, cancellations):
    process = report["child_process"] = {
        "command": list(command), "status": "launch-pending", "started": time.time(),
        "new_session": True,
    }
    _atomic_json(evidence / "lifecycle.json", report)
    if cancellations:
        process.update(status="cancelled-before-launch", cancellation_signals=list(cancellations),
                       process_group_empty=True)
        return 128 + cancellations[-1], True, "wrapper cancelled before child launch"
    try:
        child = subprocess.Popen(list(command), start_new_session=True)
    except BaseException as error:
        process.update(status="launch-failed", error=repr(error), process_group_empty=True)
        _atomic_json(evidence / "lifecycle.json", report)
        return None, True, repr(error)
    process.update(pid=child.pid, process_group=child.pid, status="running")
    _atomic_json(evidence / "lifecycle.json", report)
    deadline = time.monotonic() + CHILD_TIMEOUT_SECONDS
    reason = None
    try:
        while child.poll() is None:
            if cancellations:
                reason = "cancelled by wrapper signal"
                process["cancellation_signals"] = list(cancellations)
                break
            if time.monotonic() >= deadline:
                reason = "internal child deadline exceeded"
                break
            try:
                child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
        if child.poll() is None:
            retired = _retire_owned_group(child, report, evidence, reason)
            code = 124 if reason == "internal child deadline exceeded" else 128 + (cancellations[-1] if cancellations else signal.SIGTERM)
            error = "wrapped child timed out" if code == 124 else "wrapped child cancelled"
            if not retired:
                error += "; owned process group retirement is unconfirmed"
            return code, retired, error
        process.update(status="leader-exited", returncode=child.returncode)
        _atomic_json(evidence / "lifecycle.json", report)
        if _group_has_live_processes(child.pid):
            retired = _retire_owned_group(child, report, evidence, "owned descendants survived child leader")
        else:
            retired = True
            process.update(status="retired", process_group_empty=True)
            _atomic_json(evidence / "lifecycle.json", report)
        return child.returncode, retired, None if retired else "owned process group retirement is unconfirmed"
    except BaseException as error:
        process.update(status="supervision-failed", error=repr(error))
        _atomic_json(evidence / "lifecycle.json", report)
        if child.poll() is not None and not _group_has_live_processes(child.pid):
            process.update(status="retired", process_group_empty=True)
            return child.returncode, True, repr(error)
        try:
            retired = _retire_owned_group(child, report, evidence, "supervision failure")
        except BaseException as retirement_error:
            process.update(status="retirement-unconfirmed", retirement_error=repr(retirement_error))
            _atomic_json(evidence / "lifecycle.json", report)
            return child.poll(), False, repr(error) + "; " + repr(retirement_error)
        return child.returncode, retired, repr(error)


def _candidate_helpers(build, profile):
    if profile not in PROFILES:
        raise ValueError("Unsupported copy helper profile: " + str(profile))
    sys.path.insert(0, str(HERE.parent))
    from copy_fixture_profiles import instrumentation_preflight
    preflight = instrumentation_preflight(build, profile)
    candidates = {}
    for name in HELPER_NAMES:
        path = Path(build) / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            raise RuntimeError("Profile helper must be a regular executable file: " + name)
        digest = sha256(path)
        reported = preflight["binaries"].get(name, {}).get("sha256")
        if digest != reported:
            raise RuntimeError("Profile helper changed after instrumentation preflight: " + name)
        candidates[name] = {"path": path, "sha256": digest}
    return candidates, preflight


def _validate_evidence(path, build):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Helper lifecycle evidence path must be absolute")
    path = path.resolve(strict=False)
    if path == Path.cwd().resolve() or path.is_relative_to(Path.cwd().resolve()):
        raise ValueError("Helper lifecycle evidence must remain outside the source checkout")
    if path == Path(build).resolve() or path.is_relative_to(Path(build).resolve()):
        raise ValueError("Helper lifecycle evidence must remain outside the profile build")
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError("Helper lifecycle evidence must be new and have an existing parent")
    return path


def _root_command(argv, report, evidence):
    row = {"argv": ["sudo", "-n", *map(str, argv)], "status": "running"}
    report.setdefault("root_commands", []).append(row)
    _atomic_json(evidence / "lifecycle.json", report)
    try:
        result = subprocess.run(row["argv"], capture_output=True, text=True,
                               timeout=ROOT_COMMAND_TIMEOUT_SECONDS)
    except BaseException as error:
        row.update(status="launch-failed", error=repr(error))
        _atomic_json(evidence / "lifecycle.json", report)
        raise
    row.update(status="returned", exit=result.returncode, stdout=result.stdout, stderr=result.stderr)
    _atomic_json(evidence / "lifecycle.json", report)
    if result.returncode:
        raise RuntimeError("Privileged helper lifecycle command failed; see lifecycle.json")
    return result.stdout


def _snapshot(path, backup):
    before = _helper_state(path)
    if before is not None:
        with Path(path).open("rb") as source, backup.open("xb") as destination:
            os.chmod(backup, 0o600)
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())
        if sha256(backup) != before["sha256"]:
            raise RuntimeError("Helper backup hash mismatch: " + str(path))
    return before


def run_with_helpers(build, profile, evidence, command, *, preflight_only=False):
    host = socket.gethostname().split(".")[0]
    if host not in APPROVED_HOSTS:
        raise RuntimeError("Copy helper lifecycle is allowed only on sb16 or sb16-2")
    if os.getuid() == 0:
        raise RuntimeError("Copy helper lifecycle must run as an unprivileged actor")
    build = Path(build).resolve(strict=True)
    if not build.is_dir():
        raise ValueError("Profile build path is not a directory")
    evidence = _validate_evidence(evidence, build)
    if preflight_only and command:
        raise ValueError("Preflight-only mode does not accept a child command")
    if not preflight_only and not command:
        raise ValueError("A command is required after --")
    evidence.mkdir(mode=0o700)
    owner = pwd.getpwuid(os.getuid()).pw_uid
    info = evidence.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("Evidence directory must be private and owned by this actor")
    lock = _acquire_lock()
    report = {"schema": 1, "host": host, "profile": profile, "build": str(build),
              "preflight_only": bool(preflight_only), "command": list(command),
              "candidate_helpers": {}, "previous_helpers": {}, "root_commands": [],
              "process_inspections": [], "restoration": [], "child_exit": None,
              "cleanup_status": "pending", "started": time.time()}
    _atomic_json(evidence / "lifecycle.json", report)
    candidates = {}
    installed = []
    backups = evidence / "backups"
    root_paths = {name: HELPER_ROOT / name for name in HELPER_NAMES}
    child_exit = None
    child_error = None
    child_retired = True
    cleanup_errors = []
    cancellations = []
    previous_handlers = {}

    def record_cancellation(signum, _frame):
        cancellations.append(signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, record_cancellation)
    try:
        _validate_helper_root(HELPER_ROOT)
        candidates, preflight = _candidate_helpers(build, profile)
        report["instrumentation_preflight"] = preflight
        report["candidate_helpers"] = {name: dict(path=str(row["path"]), sha256=row["sha256"])
                                       for name, row in candidates.items()}
        _inspect_helper_processes(root_paths.values(), report, evidence)
        if preflight_only:
            for name, target in root_paths.items():
                report["previous_helpers"][name] = _helper_state(target)
            report["preflight_status"] = "ready"
            report["recovery_state_recorded"] = False
            _atomic_json(evidence / "lifecycle.json", report)
        else:
            backups.mkdir(mode=0o700)
            for name, target in root_paths.items():
                report["previous_helpers"][name] = _snapshot(target, backups / name)
            report["recovery_state_recorded"] = True
            report["recovery_actions"] = [
                {"target": str(root_paths[name]), "candidate_sha256": candidates[name]["sha256"],
                 "previous": report["previous_helpers"][name],
                 "backup": str(backups / name) if report["previous_helpers"][name] is not None else None}
                for name in HELPER_NAMES]
            _atomic_json(evidence / "lifecycle.json", report)
            for name, target in root_paths.items():
                if cancellations:
                    raise InterruptedError("Copy helper setup cancelled before installation")
                candidate = candidates[name]
                _inspect_helper_processes(root_paths.values(), report, evidence)
                if _helper_state(target) != report["previous_helpers"][name]:
                    raise RuntimeError("Protected helper changed after recovery snapshot: " + name)
                installed.append(name)
                _atomic_json(evidence / "lifecycle.json", report)
                _root_command(["install", "-o", "root", "-g", "root", "-m", "755",
                               candidate["path"], target], report, evidence)
                expected = {"sha256": candidate["sha256"], "uid": 0, "gid": 0, "mode": 0o755}
                if _helper_state(target) != expected:
                    raise RuntimeError("Installed helper does not match selected profile candidate: " + name)
                report.setdefault("installed_helpers", {})[name] = expected
                _atomic_json(evidence / "lifecycle.json", report)
            if cancellations:
                child_exit = 128 + cancellations[-1]
                child_error = "Copy helper setup cancelled before child launch"
            else:
                child_retired = False
                child_exit, child_retired, child_error = _run_owned_child(
                    command, report, evidence, cancellations)
        report["child_exit"] = child_exit
    except BaseException as error:
        child_error = repr(error)
        report["execution_error"] = child_error
    finally:
        for name in reversed(installed):
            target = root_paths[name]
            before = report.get("previous_helpers", {}).get(name)
            candidate = candidates.get(name)
            row = {"path": str(target), "restored": False}
            if not child_retired:
                row["state"] = "preserved-child-retirement-unconfirmed"
                row["error"] = "Owned child process group is not confirmed empty; refusing helper removal"
                cleanup_errors.append(row["error"])
            else:
                try:
                    _inspect_helper_processes(root_paths.values(), report, evidence)
                    current = _helper_state(target)
                    if current == before:
                        row["restored"] = True
                        row["state"] = "unchanged"
                    else:
                        expected = {"sha256": candidate["sha256"], "uid": 0,
                                    "gid": 0, "mode": 0o755}
                        if current != expected:
                            raise RuntimeError("Refusing to overwrite a helper changed after installation: " + name)
                        if before is None:
                            _root_command(["rm", "--", target], report, evidence)
                            row["state"] = "removed-to-restore-absence"
                        else:
                            _root_command(["install", "-o", str(before["uid"]), "-g", str(before["gid"]),
                                           "-m", format(before["mode"], "o"), backups / name, target], report, evidence)
                            row["state"] = "restored-from-backup"
                        if _helper_state(target) != before:
                            raise RuntimeError("Helper restoration verification failed: " + name)
                        row["restored"] = True
                except BaseException as error:
                    row["error"] = repr(error)
                    cleanup_errors.append(row["error"])
            report["restoration"].append(row)
            try:
                _atomic_json(evidence / "lifecycle.json", report)
            except BaseException as error:
                cleanup_errors.append("Cannot persist restoration evidence: " + repr(error))
        report["child_exit"] = child_exit
        report["execution_error"] = child_error
        report["cleanup_status"] = ("not-needed" if preflight_only and not cleanup_errors else
                                    "failed" if cleanup_errors else "confirmed")
        report["cleanup_errors"] = cleanup_errors
        report["finished"] = time.time()
        try:
            _atomic_json(evidence / "lifecycle.json", report)
        except BaseException as error:
            cleanup_errors.append("Cannot persist final lifecycle evidence: " + repr(error))
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        os.close(lock)
    if cleanup_errors:
        raise RuntimeError("Helper cleanup is unconfirmed; child exit and root command logs are in " + str(evidence / "lifecycle.json"))
    if child_error:
        raise RuntimeError("Helper-wrapped command failed before completion: " + child_error)
    if preflight_only:
        return 0
    return child_exit if child_exit is not None else 125


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    try:
        return run_with_helpers(args.build, args.profile, args.evidence, command,
                                preflight_only=args.preflight_only)
    except BaseException as error:
        print("copy helper lifecycle failed: " + repr(error), file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
