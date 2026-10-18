#!/usr/bin/env python3
"""Run the nested-mount regression inside a temporary private mount namespace."""
import argparse, os, pathlib, re, shutil, subprocess, sys, tempfile

def mountpoints():
    out = []
    for line in pathlib.Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        if len(fields) > 5:
            out.append(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4]))
    return out

def run_inside(args):
    if os.geteuid() != 0: raise RuntimeError("namespace setup lacks root fixture authority")
    if os.readlink("/proc/self/ns/mnt") == args.parent_namespace:
        raise RuntimeError("unshare did not create a distinct mount namespace")
    subprocess.run(["mount", "--make-rprivate", "/"], check=True, timeout=5)
    uid, gid = args.uid, args.gid
    groups = [int(x) for x in args.groups.split(",") if x]
    if uid == 0 or not groups: raise RuntimeError("refusing root worker or missing caller groups")
    root = pathlib.Path(tempfile.mkdtemp(prefix="spatial-copy-nested-mount-"))
    source, dest, outside = (root / n for n in ("source", "destination", "outside"))
    mountpoint = source / "mounted"
    try:
        for path in (source, dest, outside, mountpoint):
            path.mkdir(); os.chown(path, uid, gid)
        for path, data in ((outside / "payload", b"mounted"),):
            path.write_bytes(data); os.chown(path, uid, gid)
        os.chown(root, uid, gid)
        mounted = subprocess.run(["mount", "--bind", str(outside), str(mountpoint)],
                                 capture_output=True, text=True, timeout=5)
        if mounted.returncode: raise RuntimeError("bind fixture setup failed: " + mounted.stderr.strip())
        if str(mountpoint) not in mountpoints(): raise RuntimeError("bind absent from namespace mount table")
        def drop_identity():
            os.setgroups(groups); os.setgid(gid); os.setuid(uid)
        env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
               "SPATIAL_COPY_FIXTURE_UID": str(uid),
               "SPATIAL_COPY_NESTED_MOUNT_FIXTURE_ROOT": str(root)}
        for variable, value in (("ASAN_OPTIONS", args.asan_options), ("UBSAN_OPTIONS", args.ubsan_options)):
            if value is not None: env[variable] = value
        cmd = [sys.executable, args.test_script, args.worker,
               "CopyTreeWorkerNestedMountTests.test_cabinet_protocol_rejects_nested_mounts"]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                timeout=50, preexec_fn=drop_identity)
        sys.stdout.write(result.stdout); sys.stderr.write(result.stderr)
        return result.returncode
    finally:
        if str(mountpoint) in mountpoints(): subprocess.run(["umount", str(mountpoint)], check=True, timeout=5)
        shutil.rmtree(root)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--inside", action="store_true"); p.add_argument("--uid", type=int)
    p.add_argument("--gid", type=int); p.add_argument("--groups", default="")
    p.add_argument("--parent-namespace", default="")
    p.add_argument("--asan-options"); p.add_argument("--ubsan-options")
    p.add_argument("worker"); p.add_argument("test_script")
    a = p.parse_args()
    a.worker = str(pathlib.Path(a.worker).resolve()); a.test_script = str(pathlib.Path(a.test_script).resolve())
    if a.inside: return run_inside(a)
    if os.geteuid() == 0: raise RuntimeError("CTest must invoke driver as an unprivileged user")
    uid, gid, groups = os.getuid(), os.getgid(), os.getgroups()
    if not groups: raise RuntimeError("invoking user has no supplementary groups")
    ns = os.readlink("/proc/self/ns/mnt"); before = pathlib.Path("/proc/self/mountinfo").read_bytes()
    cmd = ["sudo", "-n", "timeout", "--signal=TERM", "--kill-after=5s", "60s",
           "unshare", "--mount", "--propagation", "private", "--",
           sys.executable, str(pathlib.Path(__file__).resolve()), "--inside",
           "--uid", str(uid), "--gid", str(gid), "--groups", ",".join(map(str, groups)),
           "--parent-namespace", ns, a.worker, a.test_script]
    for variable, option in (("ASAN_OPTIONS", "--asan-options="), ("UBSAN_OPTIONS", "--ubsan-options=")):
        if variable in os.environ: cmd.append(option + os.environ[variable])
    try: result = subprocess.run(cmd, capture_output=True, text=True, timeout=70)
    except (OSError, subprocess.TimeoutExpired) as e: raise RuntimeError("private namespace setup failed: " + str(e))
    sys.stdout.write(result.stdout); sys.stderr.write(result.stderr)
    if pathlib.Path("/proc/self/mountinfo").read_bytes() != before:
        raise RuntimeError("caller mount table changed after private fixture")
    return result.returncode

if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as e:
        print(f"nested-mount fixture setup failed: {e}", file=sys.stderr)
        raise SystemExit(1)
