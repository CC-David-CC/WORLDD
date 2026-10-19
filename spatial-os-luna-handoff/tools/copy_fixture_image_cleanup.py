"""Pass-only cleanup for explicit M07 copy fixture images; never profile volumes."""
import fcntl, hashlib, json, os, re, socket, stat, subprocess, sys, tempfile, time, uuid
from pathlib import Path

ROOT=Path("/var/lib/spatial/volumes")
NAME=re.compile(r"m07-engine-[0-9a-f]{16}")
SELF=Path(__file__).resolve()

def _need(ok,msg):
    if not ok: raise ValueError(msg)

def _load(path,uid):
    p=Path(path); s=p.lstat(); parent=p.parent.lstat()
    _need(stat.S_ISDIR(parent.st_mode) and parent.st_uid==uid and not parent.st_mode&0o077 and
          stat.S_ISREG(s.st_mode) and s.st_uid==uid and not s.st_mode&0o022 and s.st_nlink==1,
          "receipt is not in a private owned directory or is not a safe single-link file")
    r=json.loads(p.read_text())
    n=r.get("name",""); entry=ROOT/n
    _need(NAME.fullmatch(n) is not None and r.get("entry")==str(entry) and
          r.get("volume")==str(entry/"mount"),"receipt does not name one exact M07 fixture")
    _need(r.get("cleanup")=="unmounted" and r.get("cleanup_failures")==[],
          "fixture teardown did not confirm clean unmount")
    created=r.get("created",{})
    _need(re.fullmatch(r"[0-9a-f]{32}",created.get("filesystem_id","")) is not None,
          "fixture receipt lacks creation filesystem identity")
    definition=r.get("definition_hex","")
    _need(isinstance(definition,str) and re.fullmatch(r"(?:[0-9a-f]{2})+",definition) is not None,
          "fixture receipt lacks exact definition")
    return dict(path=str(p.resolve()),name=n,entry=str(entry),volume=str(entry/"mount"),
                image=str(entry/"volume.img"),filesystem_id=created["filesystem_id"],
                definition_hex=definition,receipt=r)

def _read_private_json(path,uid):
    p=Path(path); info=p.lstat(); parent=p.parent.lstat()
    _need(stat.S_ISDIR(parent.st_mode) and parent.st_uid==uid and not parent.st_mode&0o077 and
          stat.S_ISREG(info.st_mode) and info.st_uid==uid and not info.st_mode&0o077 and
          info.st_nlink==1,"cleanup proof is not a private owned single-link file")
    value=json.loads(p.read_text())
    _need(isinstance(value,dict),"cleanup proof is not a JSON object")
    return value

def validate_candidate_facts(expected,f):
    _need(f.get("mountpoint_absent") is True,"mount is live or cannot be inspected")
    _need(f.get("loop_detached") is True,"loop is attached or cannot be inspected")
    _need(f.get("lock_acquired") is True,"provision lock is busy")
    _need(f.get("record_valid") is True,"root record does not match fixture receipt")
    image=f.get("image",{})
    _need(image.get("regular") is True and image.get("uid")==0 and image.get("nlink")==1 and image.get("mode")==0o600,
          "image ownership, type, link count, or mode is unsafe")
    _need(image.get("device")==f.get("record_device") and image.get("inode")==f.get("record_inode") and
          image.get("size")==f.get("record_size"),"image identity or capacity differs from root record")
    _need(image.get("filesystem_id")==expected["filesystem_id"],"filesystem id differs from creation receipt")
    return True

def _cmd(args):
    return subprocess.run(args,capture_output=True,text=True,timeout=15)

def _mount_absent(path):
    r=_cmd(["findmnt","--mountpoint",path,"--noheadings","--output","TARGET"])
    if r.returncode==1 and not r.stdout.strip(): return True
    if r.returncode==0 and r.stdout.strip(): return False
    raise RuntimeError("mountpoint state unobservable: "+r.stderr.strip())

def _loop_detached(path):
    r=_cmd(["losetup","--json","--list","--output","NAME,BACK-FILE"])
    _need(r.returncode==0,"loop inventory unavailable: "+r.stderr.strip())
    rows=json.loads(r.stdout).get("loopdevices")
    _need(isinstance(rows,list),"malformed loop inventory")
    return not any(os.path.realpath(x.get("back-file",""))==path for x in rows)

def _root_candidate(path,uid,bindings):
    sys.path.insert(0,str(Path(bindings).resolve(strict=True)))
    import broker_pb2
    e=_load(path,uid); entry=Path(e["entry"])
    es=entry.lstat()
    _need(stat.S_ISDIR(es.st_mode) and es.st_uid==0 and not es.st_mode&0o022 and entry.resolve()==entry,
          "fixture entry is unsafe")
    lockfd=imagefd=entryfd=None
    try:
        lockfd=os.open(entry/".provision.lock",os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
        ls=os.fstat(lockfd)
        _need(stat.S_ISREG(ls.st_mode) and ls.st_uid==0 and ls.st_nlink==1 and stat.S_IMODE(ls.st_mode)==0o600,
              "provision lock is unsafe")
        try: fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError("provision lock is busy")
        rp=entry/"record.pb"; rs=rp.lstat()
        _need(stat.S_ISREG(rs.st_mode) and rs.st_uid==0 and rs.st_nlink==1 and stat.S_IMODE(rs.st_mode)==0o600,
              "persistent record is unsafe")
        record=broker_pb2.StorageVolumeRecord.FromString(rp.read_bytes())
        _need(record.protocol_version==1 and record.definition.name==e["name"] and
              record.definition.SerializeToString(deterministic=True)==bytes.fromhex(e["definition_hex"]) and
              record.filesystem_id.hex()==e["filesystem_id"],"root record differs from fixture receipt")
        vp=Path(e["volume"]); vs=vp.lstat()
        _need(stat.S_ISDIR(vs.st_mode) and vs.st_uid==0 and stat.S_IMODE(vs.st_mode)==0o700 and
              vp.resolve()==vp and not any(vp.iterdir()),"mount directory is unsafe or nonempty")
        imagefd=os.open(e["image"],os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        im=os.fstat(imagefd)
        blkid=_cmd(["blkid","-p","-s","UUID","-o","value",e["image"]])
        _need(blkid.returncode==0 and blkid.stdout.strip().replace("-","").lower()==record.filesystem_id.hex(),
              "image filesystem UUID differs from its persistent root record")
        facts=dict(mountpoint_absent=_mount_absent(e["volume"]),loop_detached=_loop_detached(e["image"]),
           lock_acquired=True,record_valid=True,record_device=record.image_device,record_inode=record.image_inode,
           record_size=record.definition.owner_policy.volume_bytes,
           image=dict(regular=stat.S_ISREG(im.st_mode),uid=im.st_uid,nlink=im.st_nlink,mode=stat.S_IMODE(im.st_mode),
                     device=im.st_dev,inode=im.st_ino,size=im.st_size,filesystem_id=record.filesystem_id.hex()))
        validate_candidate_facts(e,facts)
        entryfd=os.open(entry,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        return dict(expected=e,lockfd=lockfd,imagefd=imagefd,entryfd=entryfd,facts=facts)
    except BaseException:
        for fd in (entryfd,imagefd,lockfd):
            if fd is not None:
                try: os.close(fd)
                except OSError: pass
        raise

def root_cleanup(paths,uid,bindings):
    _need(os.getuid()==0 and socket.gethostname().split(".")[0] in ("sb16","sb16-2"),
          "root cleanup is limited to authorized world hosts")
    rs=ROOT.lstat()
    _need(stat.S_ISDIR(rs.st_mode) and rs.st_uid==0 and not rs.st_mode&0o022,"volume root is unsafe")
    held=[]; names=set(); removed=[]
    try:
        for p in paths:
            c=_root_candidate(p,uid,bindings); held.append(c); n=c["expected"]["name"]
            _need(n not in names,"duplicate fixture receipt")
            names.add(n)
        for c in held:
            e=c["expected"]
            _need(_mount_absent(e["volume"]) and _loop_detached(e["image"]),"mount or loop appeared after preflight")
            current=os.stat("volume.img",dir_fd=c["entryfd"],follow_symlinks=False); pinned=os.fstat(c["imagefd"])
            _need((current.st_dev,current.st_ino,current.st_size)==(pinned.st_dev,pinned.st_ino,pinned.st_size),
                  "image inode changed after preflight")
            os.unlink("volume.img",dir_fd=c["entryfd"])
            _need(not os.path.lexists(e["image"]) and os.fstat(c["imagefd"]).st_nlink==0,
                  "image unlink was not confirmed on the pinned inode")
            f=c["facts"]
            proof=dict(status="removed",name=e["name"],entry=e["entry"],image=e["image"],
                filesystem_id=e["filesystem_id"],image_device=f["image"]["device"],image_inode=f["image"]["inode"],
                image_size=f["image"]["size"],record_size=f["record_size"],record_device=f["record_device"],record_inode=f["record_inode"],
                mountpoint_absent=True,loop_detached=True,lock_acquired=True,removed_at=time.time())
            removed.append(dict(receipt=e["path"],proof=proof))
        return dict(status="removed",items=removed,recovered_bytes=sum(x["proof"]["image_size"] for x in removed))
    except BaseException as err:
        return dict(status="incomplete",items=removed,recovered_bytes=sum(x["proof"]["image_size"] for x in removed),
                    failure=repr(err))
    finally:
        for c in held:
            for k in ("imagefd","entryfd","lockfd"):
                try: os.close(c[k])
                except (OSError,KeyError): pass

def root_verify(path,uid,bindings):
    _need(os.getuid()==0 and socket.gethostname().split(".")[0] in ("sb16","sb16-2"),
          "cleanup verification is limited to authorized world hosts")
    sys.path.insert(0,str(Path(bindings).resolve(strict=True)))
    import broker_pb2
    e=_load(path,uid); p=Path(path); r=json.loads(p.read_text())
    proof_path=p.parent/"image-cleanup.json"; proof=_read_private_json(proof_path,uid)
    _need(r.get("image_cleanup")==proof and proof.get("status")=="removed" and
          proof.get("name")==e["name"] and proof.get("entry")==e["entry"] and
          proof.get("image")==e["image"] and proof.get("filesystem_id")==e["filesystem_id"],
          "cleanup receipt does not match fixture receipt")
    entry=Path(e["entry"]); es=entry.lstat()
    _need(stat.S_ISDIR(es.st_mode) and es.st_uid==0 and not es.st_mode&0o022 and entry.resolve()==entry,
          "retained fixture entry is unsafe")
    _need(not os.path.lexists(e["image"]),"previously removed image exists")
    rp=entry/"record.pb"; rs=rp.lstat()
    _need(stat.S_ISREG(rs.st_mode) and rs.st_uid==0 and rs.st_nlink==1 and stat.S_IMODE(rs.st_mode)==0o600,
          "retained root record is unsafe")
    lock_path=entry/".provision.lock"; lock_info=lock_path.lstat()
    _need(stat.S_ISREG(lock_info.st_mode) and lock_info.st_uid==0 and lock_info.st_nlink==1 and
          stat.S_IMODE(lock_info.st_mode)==0o600,"retained provision lock is unsafe")
    record=broker_pb2.StorageVolumeRecord.FromString(rp.read_bytes())
    _need(record.definition.SerializeToString(deterministic=True)==bytes.fromhex(e["definition_hex"]) and
          record.filesystem_id.hex()==proof["filesystem_id"] and
          record.image_device==proof["record_device"]==proof["image_device"] and
          record.image_inode==proof["record_inode"]==proof["image_inode"] and
          record.definition.owner_policy.volume_bytes==proof["image_size"],"retained root record mismatches cleanup receipt")
    volume_path=Path(e["volume"]); vs=volume_path.lstat()
    _need(stat.S_ISDIR(vs.st_mode) and vs.st_uid==0 and stat.S_IMODE(vs.st_mode)==0o700 and
          volume_path.resolve()==volume_path and not any(volume_path.iterdir()),
          "retained fixture mount directory is unsafe or nonempty")
    _need(_mount_absent(e["volume"]) and _loop_detached(e["image"]),"cleaned fixture has a live mount or loop")
    lock=os.open(Path(e["entry"])/".provision.lock",os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError("cleaned fixture provision lock is busy")
    finally:
        os.close(lock)
    return dict(status="verified-already-removed",name=e["name"],filesystem_id=e["filesystem_id"],
                image_inode=proof["image_inode"],image_size=proof["image_size"])

def _atomic(path,data):
    path=Path(path); fd,tmp=tempfile.mkstemp(prefix="."+path.name+"-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as f:
            json.dump(data,f,sort_keys=True,indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno()); os.fchmod(f.fileno(),0o600)
        os.replace(tmp,path)
        d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(d)
        finally: os.close(d)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass

def _root_call(args,runner):
    r=runner(args,capture_output=True,text=True,timeout=120)
    try: return r.returncode,json.loads(r.stdout)
    except Exception as err: raise RuntimeError("root helper returned no JSON proof") from err

def _flatten(test):
    import unittest
    if isinstance(test,(unittest.TestSuite,list,tuple)):
        for case in test:
            yield from _flatten(case)
    else:
        yield test

def require_group_capacity(evidence_root,image_count,*,reserve_bytes=1*2**30):
    root=Path(evidence_root).resolve(strict=True)
    info=root.lstat()
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o022,
          "fixture evidence root is unsafe")
    space=os.statvfs(root)
    available=space.f_bavail*space.f_frsize
    _need(isinstance(image_count,int) and image_count>0,"fixture image count must be positive")
    required=image_count*512*2**20+reserve_bytes
    _need(available>=required,"insufficient free space for fixture image group")
    return dict(available_bytes=available,required_bytes=required,image_count=image_count)

def run_after_capacity_preflight(evidence_root,image_count,run,*,reserve_bytes=1*2**30):
    capacity=require_group_capacity(evidence_root,image_count,reserve_bytes=reserve_bytes)
    return capacity,run()

def cleanup_engine_group(receipts,build_python,evidence_root,*,report,completed_status,recovery,restoration,runner=subprocess.run):
    passed=engine_group_passed(report,completed_status,recovery,restoration)
    return cleanup_passed_fixture_images(receipts,build_python,evidence_root,
        suite_success=passed,expected_count=report.get("selected_methods"),runner=runner)

def cleanup_file_action_group(result,test_suite,engine_case_type,build_python,*,runner=subprocess.run):
    all_cases=list(_flatten(test_suite))
    cases=[case for case in all_cases if isinstance(case,engine_case_type)]
    if not cases:
        return dict(status="no-fixture-images",removed=0,recovered_bytes=0)
    passed=file_action_group_passed(result,len(all_cases))
    receipts=[Path(case.copy_evidence)/"volume.json" for case in cases]
    root=Path(cases[0].copy_evidence).parent
    return cleanup_passed_fixture_images(receipts,build_python,root,
        suite_success=passed,expected_count=len(cases),runner=runner)

def cleanup_passed_fixture_images(receipts,build_python,evidence_root,*,suite_success,expected_count=None,runner=subprocess.run):
    if not suite_success: return dict(status="retained-suite-failed",removed=0,recovered_bytes=0)
    paths=[Path(p).resolve(strict=True) for p in receipts]
    if expected_count is not None and len(paths)!=expected_count: raise RuntimeError("successful fixture suite lacks a full receipt set")
    if not paths: return dict(status="no-fixture-images",removed=0,recovered_bytes=0)
    uid=os.getuid(); expected=[_load(p,uid) for p in paths]
    if len({x["name"] for x in expected})!=len(expected): raise RuntimeError("duplicate fixture image receipt")
    root=Path(evidence_root).resolve(strict=True); ri=root.lstat()
    if not stat.S_ISDIR(ri.st_mode) or ri.st_uid!=uid or ri.st_mode&0o022: raise RuntimeError("evidence root is not safely actor-owned")
    if any(not Path(x["path"]).is_relative_to(root) for x in expected): raise RuntimeError("receipt escaped current attempt")
    cmd=["sudo","-n",sys.executable,"-B",str(SELF),"--root-cleanup","--bindings-dir",
         str(Path(build_python).resolve(strict=True)),"--owner-uid",str(uid),"--receipts",*[x["path"] for x in expected]]
    code,report=_root_call(cmd,runner)
    group=dict(schema=1,status=report.get("status"),source="M07 copy fixture",
               cleanup_helper_sha256=hashlib.sha256(SELF.read_bytes()).hexdigest(),command=cmd,
               receipts=[x["path"] for x in expected],result=report,command_exit=code,completed_at=time.time())
    group_path=root/("copy-image-cleanup-"+uuid.uuid4().hex+".json"); _atomic(group_path,group)
    expected_by_path={x["path"]:x for x in expected}
    items=report.get("items",[])
    report_paths=[item.get("receipt") for item in items] if isinstance(items,list) else []
    _need(isinstance(items,list) and len(items)<=len(expected) and
          set(report_paths)<=set(expected_by_path) and len(set(report_paths))==len(report_paths),
          "root cleanup report contains unknown or duplicate fixtures")
    verified=[]
    for item in items:
        proof=item.get("proof",{}); receipt_path=item.get("receipt")
        candidate=expected_by_path[receipt_path]
        _need(proof.get("status")=="removed" and proof.get("name")==candidate["name"] and
              proof.get("entry")==candidate["entry"] and proof.get("image")==candidate["image"] and
              proof.get("filesystem_id")==candidate["filesystem_id"] and
              proof.get("image_device")==proof.get("record_device") and
              proof.get("image_inode")==proof.get("record_inode") and
              proof.get("image_size")==proof.get("record_size") and proof.get("image_size")>0 and
              proof.get("mountpoint_absent") is True and proof.get("loop_detached") is True and
              proof.get("lock_acquired") is True,
              "root cleanup proof does not match the exact fixture receipt")
        verified.append((Path(receipt_path),proof))
    _need(report.get("recovered_bytes")==sum(proof["image_size"] for _,proof in verified),
          "root cleanup recovered-byte total mismatches per-image proofs")
    for p,proof in verified:
        value=json.loads(p.read_text()); value["image_cleanup"]=proof
        _atomic(p,value); _atomic(p.parent/"image-cleanup.json",proof)
    if code or report.get("status")!="removed" or len(verified)!=len(expected):
        raise RuntimeError("fixture cleanup incomplete; see "+str(group_path))
    return dict(status="removed",removed=len(expected),recovered_bytes=report["recovered_bytes"],receipt=str(group_path))

def verify_previous_cleanup(receipt,build_python,*,runner=subprocess.run):
    p=Path(receipt).resolve(strict=True); uid=os.getuid(); value=_load(p,uid)
    raw=value["receipt"]
    proof=raw.get("image_cleanup")
    if not proof: return None
    if proof.get("status")!="removed": raise RuntimeError("malformed prior cleanup marker")
    cmd=["sudo","-n",sys.executable,"-B",str(SELF),"--root-verify","--bindings-dir",
         str(Path(build_python).resolve(strict=True)),"--owner-uid",str(uid),"--receipt",str(p)]
    code,report=_root_call(cmd,runner)
    if code or report.get("status")!="verified-already-removed": raise RuntimeError("prior image removal failed verification")
    return report

def engine_group_passed(report,completed,recovery,restoration):
    selected=report.get("selected_methods")
    expected_helpers={"spatial-copy-tree-worker","spatial-copy-finalize-worker"}
    return (report.get("runtime_status")==completed and isinstance(selected,int) and selected>0 and
        len(recovery)==selected and all(x.get("confirmed") is True for x in recovery) and
        len({x.get("inventory") for x in recovery})==selected and
        len(restoration)==len(expected_helpers) and
        {Path(x.get("path","")).name for x in restoration}==expected_helpers and
        all(x.get("restored") is True for x in restoration))

def file_action_group_passed(result,expected):
    return result.wasSuccessful() and result.testsRun==expected and not getattr(result,"skipped",())

def main(argv=None):
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--root-cleanup",action="store_true"); p.add_argument("--root-verify",action="store_true")
    p.add_argument("--bindings-dir",type=Path,required=True); p.add_argument("--owner-uid",type=int,required=True)
    p.add_argument("--receipts",nargs="*"); p.add_argument("--receipt")
    a=p.parse_args(argv)
    try:
        if a.root_cleanup and not a.root_verify and a.receipts and not a.receipt: r=root_cleanup(a.receipts,a.owner_uid,a.bindings_dir)
        elif a.root_verify and not a.root_cleanup and a.receipt and not a.receipts: r=root_verify(a.receipt,a.owner_uid,a.bindings_dir)
        else: p.error("choose exactly one root cleanup or verify")
        print(json.dumps(r,sort_keys=True),flush=True)
        return 0 if r.get("status") in ("removed","verified-already-removed") else 2
    except BaseException as err:
        print(json.dumps(dict(status="refused",failure=repr(err))),flush=True); return 2

if __name__=="__main__": raise SystemExit(main())
