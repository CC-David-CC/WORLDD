#!/usr/bin/env python3
"""Build the independent witness; default is core-X11, --xi2 is required by P06+."""
import argparse
from pathlib import Path
import shlex
import subprocess
p=argparse.ArgumentParser();p.add_argument("--xi2",action="store_true");p.add_argument("--cc",default="cc")
a=p.parse_args();root=Path(__file__).resolve().parents[1];out=root/"build";out.mkdir(exist_ok=True)
argv=[a.cc,"-std=c11","-O2","-g","-Wall","-Wextra","-Werror",str(root/"native/xprobe.c"),"-o",str(out/"xprobe"),"-lX11"]
if a.xi2:argv += ["-DSPATIAL_HAVE_XI2","-lXi"]
print(shlex.join(argv));subprocess.run(argv,check=True)
print(out/"xprobe")
