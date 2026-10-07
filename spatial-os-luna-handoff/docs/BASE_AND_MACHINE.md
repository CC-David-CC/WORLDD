# Base selection and authorized-machine operation

## Scope

The user authorizes changes only on `sb16` and `sb16-2` and permits modifying XLibre or another X11 server base. These are the only allowed hosts for this project, including builds, tests, native sessions, and remote helper workloads. Do not use another host unless the user explicitly changes this restriction. There is no requirement to keep old applications or a conventional desktop working. This removes a compatibility burden; it does not make a lost boot/login/SSH recovery route useful.

Each host has 6 GB VRAM, 16 GB RAM, and an SSD, as reported by the user. Plan build parallelism, model sizes, and runtime resource use within those limits. SSH access as `dflanag3` and passwordless sudo were verified on both hosts on 2026-10-06.

Do not infer additional machine addresses, credentials, installed distribution, GPU driver or SpaceMouse device model from this archive. Inspect the actual authorized target. Keep a working rescue route while iterating on the display server. No Docker is required.

## Verified package baseline, 2026-10-06

All of the following packages were verified installed with `dpkg-query` on both hosts: `build-essential`, `git`, `cmake`, `meson`, `ninja-build`, `pkg-config`, `ccache`, `gdb`, `strace`, `python3`, `python3-venv`, `sqlite3`, and `libsqlite3-dev`. Both hosts have GNU Make 4.3 at `/usr/bin/make`.

`meson`, `sqlite3`, and `libsqlite3-dev` were initially missing and installed with `sudo -n apt-get install -y meson sqlite3 libsqlite3-dev`. This also installed `python3-setuptools` on both hosts and upgraded `libsqlite3-0` on `sb16`. This baseline does not certify the X11/GL/SpaceMouse dependencies required by later gates; inspect those separately.

## Identity build dependency, checked 2026-10-07

The identity key component requires OpenSSL 3.0 or later and its libcrypto development files.
Its independent witnesses require the openssl command.
Both hosts already had libssl-dev and openssl version 3.0.13-0ubuntu3.16.
No package installation or system change was needed for this component.

## Upstream snapshot, checked 2026-10-06

The official XLibre release page displayed `xlibre-xserver-25.1.9` as **Latest**, and `xlibre-xserver-25.2.2` as **Pre-release**. A larger numeric version is not automatically the selected stable release. This is a discovery snapshot, not a dependency lock or a promise that these remain latest. [S1]

At implementation time inspect the official repository's current releases and build documentation. Select one base for a stated reason, resolve its full commit ID, and record the build configuration. Rebase only at an explicit phase boundary, rerunning all cumulative gates. Do not have different actors building unrecorded moving branches.

`tools/pin_base.py` records the exact commit for an explicitly selected fully qualified Git ref. It does not choose a ref, install the server or overwrite a system binary. Example, **only after checking this is the intended ref**:

```sh
python3 tools/pin_base.py \
  --url https://github.com/X11Libre/xserver.git \
  --ref refs/tags/xlibre-xserver-25.1.9 \
  --output local/base.lock.json
```

The checked-out source and actual built server must match that pin. Preserve license notices and record all local server patches. Do not copy a short commit prefix from a document and claim it proves what ran.

## Initial inspection

```sh
python3 tools/inspect_machine.py --output evidence/machine.json
```

Read the output. The helper may analyze it, but Luna checks conclusions on the target. Identify the distribution package manager, build tools, X development headers, GL headers/loader, SQLite, Git, libspnav, XI2 and installed GPU path. Consult that distribution and the selected server's build documentation for package names. Absence is a blocker to that gate, not a reason to stub an API.

The harness itself uses Python 3.11+ standard library. The independent C witness needs a C compiler and Xlib development files; the multi-actor version also needs libXi development files. The disposable smoke tool needs Xvfb; the core native event smoke also uses libXtst. This headless smoke validates the witness, not a native GLX/GPU compositor. [S2,S3,S4]

## Changes and recovery

Before each system-level change, record what changes, the command, the old version/configuration and the exact recovery command in `local/system-changes.md`. First build the new server and compositor in a project-local or separate prefix. Run an explicitly dedicated test display with TCP disabled and appropriate X authority. Avoid global `xhost +` and unauthenticated listeners. The actor token API is not an X11 security sandbox.

The acceptance host owns only its test process group and per-case state. Its workers must not be a pre-existing production session. A missing test display is BLOCKED. Never inject smoke-test input into the operator's real desktop. P00–P03 need no native display; P04 and later require explicit display configuration, even if the first renderer run is headless. The hardware gate eventually runs the native selected server and actual display/GPU.

Keep a verified TTY or authorized SSH route while installing a login session. A recoverable fallback session is an engineering safety device, not a compatibility requirement or acceptable final substitute. Do not reimage storage, remove unrelated data, disable access controls globally or close the rescue route as part of getting a green test.

## Narrow server-change boundary

Use the existing X server machinery for windows, drawing, real input state and selected-device events. Where root-coordinate input routing cannot express the actor-local contract, modify the relevant server boundary. Document the request, validation, device ownership, target generation and grab/focus behavior. Do not reintroduce an invisible interaction atlas, `SendEvent` imitation, or a global focus/keyboard shared by all actors. XI2's actual device/focus semantics are the reference for what is being extended. [S4]

The implementation is free to choose a small extension or a tightly scoped internal bridge; the conformance behavior is fixed. Do not rewrite the whole X protocol merely because the user permits it. Conversely, do not preserve the current implementation at the cost of an unfixable invariant.
