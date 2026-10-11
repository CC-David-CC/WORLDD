#!/usr/bin/env python3
"""Resolve an explicitly chosen upstream ref to an exact immutable commit.
This records a source decision; it does not build/install/replace the display server.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import subprocess

def main():
    p=argparse.ArgumentParser();p.add_argument("--url",default="https://github.com/X11Libre/xserver.git")
    p.add_argument("--ref",required=True,help="Fully qualified ref, e.g. refs/tags/xlibre-xserver-25.1.9")
    p.add_argument("--output",default="local/base.lock.json");a=p.parse_args()
    if not a.ref.startswith(("refs/tags/","refs/heads/")):p.error("Use a fully qualified ref")
    if not a.url.startswith("https://"):p.error("Use an explicit HTTPS upstream repository URL")
    r=subprocess.run(["git","ls-remote","--exit-code",a.url,a.ref,a.ref+"^{}"],check=True,capture_output=True,text=True,timeout=60)
    found={ref:oid for line in r.stdout.splitlines() for oid,ref in [line.split()]}
    oid=found.get(a.ref+"^{}",found.get(a.ref))
    if not oid or not re.fullmatch("[0-9a-f]{40}|[0-9a-f]{64}",oid):raise SystemExit("No exact commit resolved")
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({"repository":a.url,"ref":a.ref,"commit":oid,"resolved_at":datetime.now(timezone.utc).isoformat(),
                               "gpu_verified":False,"input_verified":False},indent=2)+"\n");print(out)
if __name__=="__main__":main()
