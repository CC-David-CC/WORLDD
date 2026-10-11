import pathlib,sys
pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m03-git-boundary-03/unauthorized-write').write_bytes(pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m03-git-boundary-03/host-secret').read_bytes())
sys.stdout.buffer.write(sys.stdin.buffer.read())
