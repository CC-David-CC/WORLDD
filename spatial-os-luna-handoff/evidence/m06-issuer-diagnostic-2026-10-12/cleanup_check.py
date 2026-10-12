#!/usr/bin/env python3
import json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

attempt=Path(sys.argv[1]).resolve()
known={
  "issuer-regression": {"pid":1753215,"start_ticks":11942551},
  "geometry-count-01": {"pid":1753750,"start_ticks":11948998},
  "geometry-count-02": {"pid":1754603,"start_ticks":11970654},
  "run-command-regression": {"pid":1753358},
  "run-command-count-01": {"pid":1753715},
  "run-command-count-02": {"pid":1754567},
  "bounded-count-01": {"pid":1753749},
  "bounded-count-02": {"pid":1754602},
}
pids={}
for name,meta in known.items():
    pid=meta["pid"]; proc=Path(f"/proc/{pid}")
    row={"pid":pid,"present":proc.exists()}
    if proc.exists():
        try:
            raw=(proc/"stat").read_text(); tail=raw[raw.rfind(")")+2:].split(); ticks=int(tail[19])
            cmd=(proc/"cmdline").read_bytes().replace(b"\0",b" ").decode(errors="replace").strip()
            row.update({"start_ticks":ticks,"cmdline":cmd,"same_start_identity":meta.get("start_ticks",ticks)==ticks})
        except OSError as exc: row["read_error"]=str(exc)
    pids[name]=row
matches=[]
markers=["/bundle/bin/identityd","/bundle/bin/spatial-issuer-client","/bundle/bin/spatial-issuer-failure-diagnostic","/bundle/bin/worldd","/incoming/single_case_driver.py","/incoming/issuer_case_driver.py","/incoming/bounded_case.py"]
for item in Path("/proc").iterdir():
    if not item.name.isdigit() or int(item.name)==os.getpid(): continue
    try: cmd=(item/"cmdline").read_bytes().replace(b"\0",b" ").decode(errors="replace").strip()
    except OSError: continue
    if any(marker in cmd for marker in markers): matches.append({"pid":int(item.name),"cmdline":cmd})
tmux=subprocess.run(["tmux","list-sessions","-F","#{session_name}"],capture_output=True,text=True)
sessions=tmux.stdout.splitlines() if tmux.returncode==0 else []
expected_sessions=["m06-issuer-regression-sb16-01","m06-geometry-count-sb16-01","m06-geometry-count-sb16-02"]
receipt={"host":os.uname().nodename,"captured_utc":datetime.now(timezone.utc).isoformat(),"queries":{"proc_pids":list(known),"command_markers":markers,"tmux_list_sessions_exit":tmux.returncode,"tmux_stdout":tmux.stdout,"tmux_stderr":tmux.stderr},"known_pids":pids,"matching_attempt_processes":matches,"expected_tmux_sessions":{s:s in sessions for s in expected_sessions},"status":"PASS" if not matches and not any(x in sessions for x in expected_sessions) and all(not x.get("present") or not x.get("same_start_identity",False) for x in pids.values()) else "REVIEW"}
out=attempt/"cleanup-check.json"
out.write_text(json.dumps(receipt,sort_keys=True,indent=2)+"\n")
print(json.dumps(receipt,sort_keys=True))
