# Project instructions

## Authorized hosts

`sb16` and `sb16-2` are the only authorized hosts for this project. Run Linux builds, tests, native sessions, and remote helper workloads only on these hosts. Do not use another host unless the user explicitly changes this restriction.

Each host has 6 GB VRAM, 16 GB RAM, and an SSD, as reported by the user. Plan build parallelism, model sizes, and runtime resource use within those limits.

SSH access as `dflanag3` and passwordless sudo have been verified on both hosts. Sudo access does not expand the authorized host list.

For work inside `spatial-os-luna-handoff` and 'sovereign-kernel-local-first-handoff-v0.1.0', also follow its `AGENTS.md` and referenced project instructions.
you can build the gnome and xlibre builds in parallel to ensure its not a desktop problem because much of the backend is agnostic

## Git hosting

The workspace root is the Git repository for both handoff folders and shared project notes. The default branch is `main`; `origin` is `dflanag3@box-git:/srv/data/git/spacial_desktop.git`. The bare repository exists on `box-git`. Local `main` tracks `origin/main`. The repository-local commit identity is `David <david@code.contractors>`; no GitHub remote is configured.

The user explicitly authorized `box-git` for Git hosting and repository administration. This exception does not authorize builds, tests, native sessions, or helper workloads on `box-git`; those remain restricted to `sb16` and `sb16-2`.

Push reviewed commits to `origin`. Preserve existing history and do not force-push. Keep local credentials, private keys, environments, and build outputs out of Git.
