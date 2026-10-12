#!/usr/bin/env python3
"""Read-only target facts. Does not install packages, change a session, or dump secrets."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
COMMANDS={"kernel":["uname","-a"],"git":["git","--version"],"cc":["cc","--version"],
          "cmake":["cmake","--version"],"meson":["meson","--version"],
          "xserver":["Xorg","-version"],"xdisplay":["xdpyinfo"],"opengl":["glxinfo","-B"],
          "input":["xinput","list","--short"],"usb":["lsusb"]}
def main():
    p=argparse.ArgumentParser();p.add_argument("--output",default="evidence/machine.json");a=p.parse_args()
    report={"utc":datetime.now(timezone.utc).isoformat(),"python":platform.python_version(),"platform":platform.platform(),
            "display":os.environ.get("DISPLAY"),"test_display":os.environ.get("SPATIAL_TEST_DISPLAY"),"commands":{}}
    release=Path("/etc/os-release")
    if release.exists():report["os_release"]=release.read_text()
    for name,argv in COMMANDS.items():
        if not shutil.which(argv[0]):report["commands"][name]={"status":"missing"};continue
        try:
            r=subprocess.run(argv,text=True,capture_output=True,timeout=10)
            report["commands"][name]={"argv":argv,"returncode":r.returncode,"stdout":r.stdout,"stderr":r.stderr}
        except subprocess.TimeoutExpired:report["commands"][name]={"status":"timeout"}
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+"\n");print(out)
if __name__=="__main__":main()
