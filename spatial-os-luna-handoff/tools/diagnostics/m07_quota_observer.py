#!/usr/bin/env python3
"""External Btrfs quota observer supervisor. It never changes quota state."""
import argparse, hashlib, json, os, pathlib, re, select, signal, subprocess, time, uuid

ATTACHED = "QUOTA_OBSERVER_ATTACHED"
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_CHAIN_ROWS = 256
MAX_CONTEXT_ERRORS = 32

def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)

def checked_run(argv, timeout=10):
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)

def proc_identity(pid):
    raw = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return raw[raw.rfind(")") + 2:].split()[19]

def mounted_fsid(mount):
    result = checked_run(["btrfs", "filesystem", "show", "--raw", mount])
    if result.returncode:
        raise RuntimeError("btrfs filesystem show failed: " + result.stderr.strip())
    match = re.search(r"uuid:\s*([0-9a-fA-F-]{36})", result.stdout)
    if not match:
        raise RuntimeError("could not read mounted filesystem UUID")
    canonical = str(uuid.UUID(match.group(1)))
    return canonical, uuid.UUID(canonical).hex

def capture_qgroups(mount, path, tag):
    # No --sync, quota rescan, or quota mutation.
    argv = ["btrfs", "--format", "json", "qgroup", "show", "--raw", "-p", "-c", "-r", "-e", mount]
    try:
        result = checked_run(argv, timeout=2)
        record = {"tag": tag, "argv": argv, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr, "sync_requested": False,
            "rescan_requested": False, "captured_unix_ns": time.time_ns()}
        rc = result.returncode
    except Exception as exc:
        record = {"tag": tag, "argv": argv, "error": repr(exc), "sync_requested": False,
            "rescan_requested": False, "captured_unix_ns": time.time_ns()}
        rc = -1
    path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return rc

def capture_catalog(mount, path, tag):
    db = str(pathlib.Path(mount) / ".storage-owner/catalog.db")
    code = r'''import json,sqlite3,sys
p=sys.argv[1];uri="file:"+p.replace("?","%3f")+"?mode=ro"
try:
 c=sqlite3.connect(uri,uri=True,timeout=1.0);c.execute("PRAGMA query_only=ON");tables={}
 for t in ("objects","actors","domains","snapshots","configured_states","configured_state_uses","configured_launch_allocations","copy_exports","copy_stages","copy_destinations"):
  try:
   cur=c.execute("SELECT * FROM "+t);cols=[x[0] for x in cur.description]
   tables[t]={"columns":cols,"rows":[dict(zip(cols,row)) for row in cur.fetchall()]}
  except sqlite3.Error as e: tables[t]={"error":str(e)}
 c.close();print(json.dumps({"ok":True,"database":p,"tables":tables},sort_keys=True,default=lambda v:{"__bytes_hex__":v.hex()} if isinstance(v,bytes) else str(v)))
except Exception as e: print(json.dumps({"ok":False,"database":p,"error":repr(e)},sort_keys=True))
'''
    try:
        result = checked_run(["python3", "-B", "-c", code, db], timeout=2)
        try:
            payload = json.loads(result.stdout) if result.returncode == 0 else None
        except json.JSONDecodeError:
            payload = None
        record = {"tag": tag, "returncode": result.returncode, "read_only_uri": True,
            "payload": payload, "stdout": result.stdout, "stderr": result.stderr,
            "captured_unix_ns": time.time_ns()}
    except Exception as exc:
        payload = None
        record = {"tag": tag, "returncode": -1, "read_only_uri": True,
            "payload": None, "error": repr(exc), "captured_unix_ns": time.time_ns()}
    path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return bool(payload and payload.get("ok"))

def tracer_identity(pgid):
    matches = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if os.getpgid(pid) == pgid and pathlib.Path(f"/proc/{pid}/exe").resolve().name == "bpftrace":
                matches.append((pid, proc_identity(pid)))
        except (OSError, IndexError, ProcessLookupError):
            continue
    if len(matches) != 1:
        raise RuntimeError(f"expected one live bpftrace process in owned group; found {len(matches)}")
    return matches[0]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=pathlib.Path)
    parser.add_argument("--run-dir", required=True, type=pathlib.Path)
    parser.add_argument("--bpf-template", required=True, type=pathlib.Path)
    parser.add_argument("--ready-timeout", type=float, default=15)
    parser.add_argument("--request-wait-timeout", type=float, default=120)
    parser.add_argument("--max-seconds", type=float, default=360)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("observer must run as root; it launches bpftrace directly")
    request = args.request.absolute()
    run_dir = args.run_dir.resolve()
    template_path = args.bpf_template.resolve(strict=True)
    request_deadline = time.monotonic() + args.request_wait_timeout
    while not request.is_file() and time.monotonic() < request_deadline:
        time.sleep(0.05)
    if not request.is_file():
        raise SystemExit("fixture did not publish observer request before timeout")
    request = request.resolve(strict=True)
    req = json.loads(request.read_text(encoding="utf-8"))
    required = {"protocol_version", "case_id", "mode", "filesystem_id", "fixture_pid", "fixture_start_ticks", "boot_id", "volume_name", "mount_path", "volume_image"}
    missing = required - req.keys()
    if missing or req.get("protocol_version") != 1 or req.get("mode") not in ("nocopy", "copy", "smallquota"):
        raise SystemExit(f"invalid request; missing={sorted(missing)}")
    case = req["case_id"]
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", case):
        raise SystemExit("invalid case_id")
    volume_name = req["volume_name"]
    if not re.fullmatch(r"m07-engine-[0-9a-f]{16}", volume_name):
        raise SystemExit("volume_name is outside the authorized M07 diagnostic namespace")
    volume_root = pathlib.Path("/var/lib/spatial/volumes") / volume_name
    def path_without_symlink_components(path):
        absolute = pathlib.Path(os.path.abspath(path))
        current = pathlib.Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current = current / component
            if current.is_symlink():
                raise SystemExit(f"symlink path component rejected: {current}")
        return absolute
    root_abs = path_without_symlink_components(volume_root)
    mount_abs = path_without_symlink_components(req["mount_path"])
    image_abs = path_without_symlink_components(req["volume_image"])
    if not mount_abs.is_relative_to(root_abs) or not image_abs.is_relative_to(root_abs):
        raise SystemExit("mount and image must be beneath the named M07 volume root")
    if not root_abs.is_dir() or not image_abs.is_file():
        raise SystemExit("named M07 volume root or image is missing")
    req["mount_path"] = str(mount_abs)
    req["volume_image"] = str(image_abs)
    fsid = re.sub(r"[-{}]", "", req["filesystem_id"]).lower()
    if not re.fullmatch(r"[0-9a-f]{32}", fsid):
        raise SystemExit("filesystem_id must be UUID or 32 hex digits")
    canonical, actual = mounted_fsid(req["mount_path"])
    if actual != fsid:
        raise SystemExit(f"mounted FSID mismatch: requested {fsid}, found {actual}")
    fixture_pid = int(req["fixture_pid"])
    if proc_identity(fixture_pid) != str(req["fixture_start_ticks"]):
        raise SystemExit("fixture PID/start_ticks mismatch")
    boot_id = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if boot_id != req["boot_id"]:
        raise SystemExit("boot_id mismatch")
    template = template_path.read_text(encoding="utf-8")
    if not all(token in template for token in ("__FSID_LO__", "__FSID_HI__")):
        raise SystemExit("BPF template must include both exact-FSID placeholders")
    fsid_bytes = bytes.fromhex(fsid)
    lo = int.from_bytes(fsid_bytes[:8], "little")
    hi = int.from_bytes(fsid_bytes[8:], "little")
    rendered = (template.replace("__FSID_LO__", f"0x{lo:016x}")
        .replace("__FSID_HI__", f"0x{hi:016x}").replace("__FSID_HEX__", fsid))
    if any(token in rendered for token in ("__FSID_LO__", "__FSID_HI__", "__FSID_HEX__")):
        raise SystemExit("unresolved FSID template placeholder")
    if "qgroup_reserve" not in rendered or "btrfs_qgroup_update_reserve" not in rendered:
        raise SystemExit("template lacks required qgroup probes")
    run_dir.mkdir(parents=True, exist_ok=False)
    bpf = run_dir / "quota_observer.bt"
    bpf.write_text(rendered, encoding="utf-8")
    bpf_sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    capture_qgroups(req["mount_path"], run_dir / "qgroups.attach.json", "attach")
    catalog_ok = capture_catalog(req["mount_path"], run_dir / "owner-catalog.attach.json", "attach")
    proc = subprocess.Popen(["bpftrace", "-B", "line", str(bpf)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=False, bufsize=0, start_new_session=True,
        env=dict(os.environ, BPFTRACE_MAX_MAP_KEYS="256"))
    child_start = proc_identity(proc.pid)
    if pathlib.Path(f"/proc/{proc.pid}/exe").resolve().name != "bpftrace":
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)
        raise RuntimeError("launched observer PID is not bpftrace")
    def owned_group():
        try:
            return proc.poll() is None and os.getpgid(proc.pid) == proc.pid and proc_identity(proc.pid) == child_start
        except (OSError, IndexError):
            return False
    log_path = run_dir / "observer.log"
    attached = False
    edquot_seen = False
    chain_seen = False
    incomplete_seen = False
    release_seen = False
    edquot_roots = set()
    chain_rows = []
    edquot_events = []
    parse_errors = []
    trace_loss_seen = False
    end_marker_seen = False
    summary_marker_seen = False
    update_count = 0
    total_bytes = 0
    dropped_bytes = 0
    deadline = time.monotonic() + args.ready_timeout
    ready_path = request.with_name("observer-ready.json")
    context_path = request.with_name("observer-context.json")
    release_path = request.with_name("observer-release.json")
    last_context = 0
    context_errors = []
    tracer_pid = None
    tracer_start = None
    start = time.monotonic()
    observer_error = None
    def record_line(line, log):
        nonlocal attached, edquot_seen, chain_seen, incomplete_seen, trace_loss_seen
        nonlocal end_marker_seen, summary_marker_seen, update_count, total_bytes, dropped_bytes, catalog_ok
        encoded = line.encode("utf-8", errors="replace")
        total_bytes += len(encoded)
        if total_bytes <= MAX_LOG_BYTES:
            log.write(line)
        else:
            dropped_bytes += len(encoded)
        if ATTACHED in line:
            attached = True
        lower = line.lower()
        if re.search(r"(?:lost|dropped|missed).{0,80}(?:event|record)|(?:event|record).{0,80}(?:lost|dropped|missed)", lower):
            trace_loss_seen = True
        if "INCOMPLETE " in line or "OVERFLOW " in line:
            incomplete_seen = True
        if re.search(r"max_map_keys|map capacity|map full|failed to update map|map update failed", lower):
            incomplete_seen = True
        counters = dict(re.findall(r"([A-Za-z_]+)=([0-9]+)", line))
        if any(k in counters and int(counters[k]) > 0 for k in ("overflow", "lost", "dropped", "event_loss")):
            incomplete_seen = True
            if any(k in counters and int(counters[k]) > 0 for k in ("lost", "dropped", "event_loss")):
                trace_loss_seen = True
        if "QUOTA_OBSERVER_END" in line:
            fields = dict(re.findall(r"([A-Za-z_]+)=([0-9]+)", line))
            if "details" in fields and "suppressed" in fields and "updates" in fields:
                end_marker_seen = True
                summary_marker_seen = True
                update_count = int(fields["updates"])
                if int(fields["suppressed"]) > 0:
                    incomplete_seen = True
            else:
                incomplete_seen = True
                if len(parse_errors) < MAX_CONTEXT_ERRORS:
                    parse_errors.append("malformed END marker")
        if "EDQUOT " in line:
            fields = dict(re.findall(r"([A-Za-z_]+)=([^\s]+)", line))
            required_event = ("ns", "root", "bytes", "enforce", "type", "retval", "root_meta_pertrans", "root_meta_prealloc")
            if fields.get("retval") != "-122" or not all(k in fields for k in required_event) or int(fields.get("bytes", "0")) <= 0:
                incomplete_seen = True
                if len(parse_errors) < MAX_CONTEXT_ERRORS:
                    parse_errors.append("malformed EDQUOT event")
            else:
                edquot_seen = True
                edquot_roots.add(int(fields["root"]))
                if len(edquot_events) < 64:
                    edquot_events.append(fields)
                else:
                    incomplete_seen = True
                chain_seen = chain_seen or any(int(row["root"]) == int(fields["root"]) for row in chain_rows)
                if not (run_dir / "qgroups.edquot.json").exists():
                    capture_qgroups(req["mount_path"], run_dir / "qgroups.edquot.json", "first_edquot")
                    catalog_ok = capture_catalog(req["mount_path"], run_dir / "owner-catalog.edquot.json", "first_edquot")
        if "CHAIN " in line:
            fields = dict(re.findall(r"([A-Za-z_]+)=([^\s]+)", line))
            required_chain = ("ns", "root", "level", "qid", "rfer", "excl", "flags", "max_rfer", "max_excl", "data", "meta_pertrans", "meta_prealloc")
            if all(k in fields for k in required_chain):
                if len(chain_rows) < MAX_CHAIN_ROWS:
                    chain_rows.append(fields)
                else:
                    incomplete_seen = True
                if int(fields["root"]) in edquot_roots:
                    chain_seen = True
                    if not (run_dir / "qgroups.chain.json").exists():
                        capture_qgroups(req["mount_path"], run_dir / "qgroups.chain.json", "first_chain")
            else:
                incomplete_seen = True
                if len(parse_errors) < MAX_CONTEXT_ERRORS:
                    parse_errors.append("malformed CHAIN event")
    pending = bytearray()
    pipe_eof = False
    discarding_oversize = False
    max_line_bytes = 65536
    def read_lines(timeout):
        nonlocal pipe_eof, discarding_oversize, incomplete_seen
        fd = proc.stdout.fileno()
        if not pipe_eof and select.select([fd], [], [], timeout)[0]:
            chunk = os.read(fd, 4096)
            if chunk:
                pending.extend(chunk)
            else:
                pipe_eof = True
        lines = []
        while pending:
            if discarding_oversize:
                newline = pending.find(b"\n")
                if newline < 0:
                    pending.clear()
                    break
                del pending[:newline + 1]
                discarding_oversize = False
                continue
            newline = pending.find(b"\n")
            if newline < 0:
                if len(pending) > max_line_bytes:
                    incomplete_seen = True
                    pending.clear()
                    discarding_oversize = True
                break
            raw = bytes(pending[:newline + 1])
            del pending[:newline + 1]
            if len(raw) > max_line_bytes:
                incomplete_seen = True
                continue
            lines.append(raw.decode("utf-8", errors="replace"))
        if pipe_eof and pending and not discarding_oversize:
            if len(pending) <= max_line_bytes:
                lines.append(bytes(pending).decode("utf-8", errors="replace"))
            else:
                incomplete_seen = True
            pending.clear()
        return lines
    try:
        with log_path.open("w", encoding="utf-8", buffering=1) as log:
            while time.monotonic() < deadline and proc.poll() is None:
                for line in read_lines(0.1):
                    record_line(line, log)
                if attached:
                    break
            if not attached:
                raise RuntimeError("BPF program exited or did not report attachment before timeout")
            tracer_pid, tracer_start = tracer_identity(proc.pid)
            ready = {"protocol_version": 1, "case_id": case, "filesystem_id": fsid,
                "filesystem_uuid": canonical, "observer_pid": tracer_pid,
                "observer_start_ticks": tracer_start, "observer_wrapper_pid": os.getpid(), "attached": True,
                "attached_probes": ["qgroup_reserve_fexit", "qgroup_update_reserve"],
                "trace_paths": {"log": str(log_path), "program": str(bpf), "template": str(template_path)},
                "rendered_program_sha256": bpf_sha256,
                "initial_snapshots": {"qgroups": str(run_dir / "qgroups.attach.json"),
                    "owner_catalog": str(run_dir / "owner-catalog.attach.json"), "owner_catalog_ok": catalog_ok},
                "ready_unix_ns": time.time_ns()}
            atomic_json(ready_path, ready)
            deadline = time.monotonic() + args.max_seconds
            while time.monotonic() < deadline and proc.poll() is None:
                if context_path.exists():
                    try:
                        context = json.loads(context_path.read_text(encoding="utf-8"))
                        seq = int(context.get("sequence", 0))
                        if (context.get("protocol_version") != 1 or context.get("case_id") != case
                            or context.get("filesystem_id") != fsid):
                            raise ValueError("observer-context identity mismatch")
                        if seq > last_context:
                            last_context = seq
                            atomic_json(run_dir / f"context-{last_context:04d}.json", context)
                    except Exception as exc:
                        if len(context_errors) < MAX_CONTEXT_ERRORS:
                            context_errors.append(repr(exc))
                        else:
                            incomplete_seen = True
                if release_path.exists():
                    release = json.loads(release_path.read_text(encoding="utf-8"))
                    if release.get("protocol_version") == 1 and release.get("case_id") == case and release.get("filesystem_id") == fsid:
                        release_seen = True
                        break
                for line in read_lines(0.1):
                    record_line(line, log)
    except Exception as exc:
        observer_error = repr(exc)
        incomplete_seen = True
    finally:
        try:
            if proc.poll() is None:
                if owned_group():
                    os.killpg(proc.pid, signal.SIGINT)
                else:
                    incomplete_seen = True
            drain_deadline = time.monotonic() + 5
            with log_path.open("a", encoding="utf-8") as log:
                while not pipe_eof and time.monotonic() < drain_deadline:
                    for line in read_lines(0.1):
                        try:
                            record_line(line, log)
                        except Exception as exc:
                            incomplete_seen = True
                            if len(parse_errors) < MAX_CONTEXT_ERRORS:
                                parse_errors.append("line parse failed: " + repr(exc))
                if proc.poll() is None:
                    incomplete_seen = True
                    if owned_group():
                        os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
                final_drain_deadline = time.monotonic() + 2
                while not pipe_eof and time.monotonic() < final_drain_deadline:
                    for line in read_lines(0.1):
                        try:
                            record_line(line, log)
                        except Exception as exc:
                            incomplete_seen = True
                            if len(parse_errors) < MAX_CONTEXT_ERRORS:
                                parse_errors.append("final line parse failed: " + repr(exc))
                if not pipe_eof:
                    incomplete_seen = True
        except Exception as exc:
            incomplete_seen = True
            observer_error = observer_error or ("shutdown/drain failed: " + repr(exc))
            try:
                if proc.poll() is None and owned_group():
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            except Exception as cleanup_exc:
                observer_error += "; child cleanup failed: " + repr(cleanup_exc)
        try:
            proc.wait(timeout=5)
        except Exception as exc:
            incomplete_seen = True
            observer_error = observer_error or ("child wait failed: " + repr(exc))
    chains_by_event = {}
    for row in chain_rows:
        key = (int(row["root"]), int(row["ns"]))
        chains_by_event.setdefault(key, []).append(int(row["level"]))
    chain_complete = not edquot_events
    if edquot_events:
        chain_complete = True
        for event in edquot_events:
            key = (int(event["root"]), int(event["ns"]))
            levels = sorted(chains_by_event.get(key, []))
            if not levels or levels != list(range(len(levels))):
                chain_complete = False
                break
    context_valid = last_context > 0 and not context_errors
    dataset_valid = (attached and release_seen and end_marker_seen and not incomplete_seen and not trace_loss_seen
        and dropped_bytes == 0 and chain_complete and context_valid and update_count > 0
        and proc.returncode in (0, 130, -2))
    summary = {"protocol_version": 1, "case_id": case, "mode": req["mode"],
        "filesystem_id": fsid, "attached": attached, "edquot_seen": edquot_seen,
        "chain_seen": chain_seen, "chain_complete": chain_complete, "chain_rows": chain_rows,
        "edquot_events": edquot_events, "edquot_roots": sorted(edquot_roots),
        "incomplete_seen": incomplete_seen, "trace_loss_seen": trace_loss_seen, "release_seen": release_seen,
        "end_marker_seen": end_marker_seen, "summary_marker_seen": summary_marker_seen,
        "update_count": update_count,
        "context_last_sequence": last_context, "context_errors": context_errors,
        "context_valid": context_valid,
        "parse_errors": parse_errors, "observer_error": observer_error,
        "rendered_program_sha256": bpf_sha256, "bpftrace_returncode": proc.returncode,
        "elapsed_seconds": round(time.monotonic() - start, 3), "log_bytes_retained": min(total_bytes, MAX_LOG_BYTES),
        "log_bytes_dropped": dropped_bytes,
        "log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
        "dataset_valid": dataset_valid}
    summary_error = None
    try:
        atomic_json(run_dir / "observer-summary.json", summary)
    except Exception as exc:
        summary_error = repr(exc)
        dataset_valid = False
    try:
        atomic_json(request.with_name("observer-done.json"), {"protocol_version":1,
        "case_id":case,"filesystem_id":fsid,"complete":dataset_valid,"dataset_valid":dataset_valid,
        "error_seen":edquot_seen,"lost_events":trace_loss_seen,"truncated":bool(dropped_bytes),
        "incomplete_seen":incomplete_seen,"release_seen":release_seen,"end_marker_seen":end_marker_seen,
        "summary_path":str(run_dir / "observer-summary.json"), "summary_error":summary_error,
        "finished_unix_ns":time.time_ns()})
    except Exception as exc:
        raise SystemExit("could not emit observer-done receipt: " + repr(exc))
    if not dataset_valid:
        raise SystemExit("observer collection incomplete; inspect external run directory")

if __name__ == "__main__":
    main()