# WorldD

WorldD is a native spatial Linux/X11 operating environment under development.
It uses real files, processes, application windows, input, and persistent state.
The internal project directory remains `spacial_desktop`.

The repository contains spatial runtime code, XLibre integration, versioned
protocol schemas, tests, and qualification evidence. Read the
[project limitations](PROJECT_LIMITATIONS.txt) and
[milestone plan](spatial-os-luna-handoff/docs/MILESTONES.md) before evaluating it.
Recorded test results apply to their stated revision, hardware, and scope.
They do not imply that every planned feature is complete.

## Source layout

- `spatial-os-luna-handoff/`: spatial runtime, native integration, tests, and documentation.
- `sovereign-kernel-local-first-handoff-v0.1.0/`: kernel and local-first design material.
- `AGENTS.md`: project contracts, host responsibilities, and development requirements.

## History and development

This repository preserves the committed Spacial Desktop history from the private
box-git repository and the original WorldD repository initialization history.
Public `main` contains the current local `main` source plus this introduction.
`m02/control-recovery` and `m03/resource-boundary` preserve their respective
committed development branches. Ongoing uncommitted work is not included.

The private box-git repository remains the local development remote.
Publishing or pulling source does not deploy a world or change a running session.

```sh
git clone https://github.com/CC-David-CC/WORLDD.git
```

Linux builds and native qualification currently target `sb16` and `sb16-2`.
Consult the project instructions and component documentation for setup and tests.
