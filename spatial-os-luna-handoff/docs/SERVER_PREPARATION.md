# P06 XLibre preparation

## Source and build

The project now builds a pinned XLibre server on sb16 and sb16-2.
The source lock is server/base.lock.json.

| Component | Release | Commit |
| --- | --- | --- |
| XLibre | xlibre-xserver-25.1.9 | f844d7a86c10fd5cc89083e7daf97b862b8ca1fd |
| xorgproto | xorgproto-2024.1 | 67469711055522b8adb2d795b01e7ba98cb8816c |

Ubuntu's existing Present headers were too old for this XLibre build.
The builder installs the pinned protocol headers under local/xlibre-deps.
It builds Xorg and Xvfb under local/xlibre/build with two jobs.
It does not install a system X server or change the normal desktop session.
The patch list is empty. Native actor input and workspace security remain unfinished.

Run from this project folder on either authorized host:

~~~sh
python3 tools/build_xserver.py --evidence-dir local/new-server-build
python3 tools/qualify_core.py --through P05 --xserver local/xlibre/build/hw/vfb/Xvfb --evidence-dir local/new-server-regression
~~~

Use new evidence directories for each run.
The builder rejects a checkout with a different commit or tracked source changes.
It records source pins, commands, exit codes, and binary hashes.
The test wrapper retains xvfb-run authentication and uses a disposable display.

## Verified results

Both hosts completed the Xorg and Xvfb builds on 2026-10-07.
Both passed the upstream unit test target and modesetting symbol test.
The other upstream test targets were not run.

Both passed these Debug checks with the pinned XLibre Xvfb binary:

- 80 cumulative P00 through P05 acceptance tests.
- 56 core counterexamples.
- 39 harness self-tests.

All 70 recorded project source hashes match the reviewed files.
Reports and command logs are retained under evidence/server-2026-10-07.
These runs do not extend acceptance coverage beyond P05.
The earlier P06 pointer diagnostic still has one error. Sixty later legacy tests remain unrun.
The new server runs do not establish sanitizer coverage, native GPU output, or physical input acceptance.
Earlier P05 sanitizer results used the distribution Xvfb server.

## System changes and recovery

Each host received these build dependencies and their required development packages:

~~~sh
sudo apt-get install -y --no-install-recommends libpixman-1-dev libxkbfile-dev libxfont-dev libxcvt-dev libdrm-dev libgbm-dev libudev-dev libdbus-1-dev libepoxy-dev libxshmfence-dev libpciaccess-dev mesa-common-dev libprotobuf-c-dev protobuf-c-compiler
~~~

Each host also received dpkg-repack for package backup preparation.
sb16 upgraded libfreetype6 from 2.13.2+dfsg-1ubuntu0.1 to 2.13.2+dfsg-1ubuntu0.2.
Its previous package was repacked before that upgrade.
sb16-2 required no package upgrade during this installation.

Each host keeps its package history and recovery script under local/p06-server-preparation.
Copies of the exact recovery scripts are in the corresponding retained preparation evidence folders.
The sb16 rollback package remains on sb16. Binary package backups are not stored in Git.

Run the matching host's recovery script when these build dependencies are no longer required:

~~~sh
sh /home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/p06-server-preparation/recovery.sh
~~~

The script removes only the recorded new packages without automatic dependency removal.
On sb16, it then reinstalls the saved previous FreeType package.
Leave the private build directories in place when removing system dependencies.
The system display server, normal login, and SSH recovery remain available.

## Local model status

David authorized continued implementation without the local agent after its hardware failure.
A new check on 2026-10-07 received connection refusals on port 8080 through both recorded inference addresses.
The checks ran from both world hosts. No model response or helper review is claimed.
The work continues under that earlier instruction.
