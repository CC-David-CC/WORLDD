# Confined workspace processes

Actors launch real applications through workspaced.
Worldd supplies the authenticated actor and its explicitly selected workspace.
The private protobuf broker checks workspace policy, member authority, and the requested profile again.
Rooms do not determine execution context.

## Execution boundary

The development profile requests project read, project write, and execution.
The read-only profile requests project read and execution.
These initial profiles require effective project-read and process-launch authority.
Missing write authority produces read-only project and Git mounts.
Unknown profiles fail before a launch record is created.

Each application receives separate user, mount, PID, network, IPC, and UTS namespaces.
It runs with David's ordinary mapped UID, no effective capabilities, and no_new_privs.
The broker starts the project-specific Bubblewrap entry point without sudo.
System binaries, libraries, and shared dependencies are mounted read-only.
The root filesystem is read-only. Host home directories and /etc are absent.

The workspace worktree appears at /project.
A confined alias preserves the worktree's original path for ordinary Git administration.
Only that workspace's bare repository is mounted at its registered path.
Project and repository directory descriptors pin the intended mounts during setup.
The namespace supervisor closes remaining setup descriptors before launching application code.

Each process record has a private directory under execution-state/applications.
The application sees that directory as /state.
Its home, cache, configuration, data, temporary files, and output logs stay there.
This writable application-state grant is separate from project-write authority.
The broker does not mount the execution database or other application-state directories.

The environment uses a fixed dependency path and explicit workspace state paths.
It excludes host display authorization, SSH agents, desktop buses, and provider credentials.
No generic X connection is available. Native workspace X authorization remains M04 work.
Network namespaces prevent host abstract-socket access and external network access.
The private device filesystem supplies basic devices, without host GPU or input devices.

An inherited seccomp policy rejects namespace creation, mount changes, and process-memory inspection.
It also rejects kernel-module, BPF, keyring, perf, userfaultfd, and io_uring setup operations.
Non-native syscall architectures are rejected. The current policy supports x86-64 only.
The policy allows ordinary fork, threads, files, interpreters, and confined Git.
Initial profiles do not support ptrace debugging. Future profiles require explicit policy and independent qualification.

The child receives these hard limits:

| Resource | Limit |
|---|---|
| Virtual address space | 4 GiB |
| File size | 64 MiB per file |
| CPU time | 120 seconds |
| Open descriptors | 256 |
| Processes | RLIMIT_NPROC 1024 |
| Core dumps | Disabled |
| Simultaneously managed applications | 32 |

RLIMIT_NPROC counts the real UID. It is not a separate workspace process budget.
These limits do not provide aggregate memory, disk, CPU, or descendant quotas.
Delegated cgroups, storage quotas, broader profiles, and complete M03 attack qualification remain required work.
Interactive terminal input and confined GUI surfaces remain later integration work.

## Real launch and completion

The broker records a pending operation before external execution.
Operation identity is scoped to the actor and binds workspace, argv, and requested profile.
Matching live-manager retries return the original process record.
Changed arguments, profile, or selected workspace return OPERATION_CONFLICT.

The namespace supervisor uses argv directly. It does not insert a shell.
It reports startup and actual child exit through versioned protobuf frames.
An exec error cannot produce a successful launch response.
Applications receive private log files and /dev/null input, without broker pipes.

Process wait reports actual exit codes, including negative signal numbers.
Each wait request accepts zero through five seconds.
A timeout leaves the application running. The caller can wait again.
An exit code becomes durable only after the execution database records the observed result.
Database acknowledgment and process effects remain separate operations.

Revoking an effective execution resource stops the affected application and its PID namespace.
Other actors' applications remain independent.
Closing the actor session or clearing selection does not terminate its workspace application.
A lost or damaged application supervisor makes completion uncertain.
It must not prevent unrelated applications from launching.

Broker death tears down its application namespaces, including detached descendants.
Startup marks pending and running records uncertain. It does not replay arbitrary commands.
Completed records retain their observed exit codes.
Configured application restoration remains separate mandatory M13 work.

## Host bootstrap and rollback

Applied on sb16 and sb16-2 on 2026-10-07:

- /usr/local/libexec/spatial-bwrap: root-owned copy of installed Bubblewrap 0.9.0, mode 0755.
- /etc/apparmor.d/spatial-bwrap: a dedicated profile permitting this entry point to create user namespaces.

The installed files were new on both hosts. Their exact hashes are recorded with qualification evidence.
No global sysctl, package, sudo policy, login session, or display setting changed.
The original /usr/bin/bwrap remains unchanged.

Run from the project folder on an authorized host:

```sh
sudo -n python3 tools/bootstrap_execution.py --apply
```

The installer refuses unknown existing contents and symlinked destinations.
Repeat installation accepts the same source and profile.
When the packaged Bubblewrap changes, review and replace the private copy explicitly.

For rollback, stop spatial supervisors first. Preserve SSH or TTY access.
Verify both installed hashes against the recorded bootstrap report.
Then remove only these project-specific files and their loaded profile:

```sh
sudo -n apparmor_parser -R /etc/apparmor.d/spatial-bwrap
sudo -n rm -- /etc/apparmor.d/spatial-bwrap /usr/local/libexec/spatial-bwrap
```

After rollback, new workspace launches fail closed until bootstrap is installed again.
Project data and execution records remain intact.

The policy follows Ubuntu's documented [per-application user-namespace permission](https://documentation.ubuntu.com/security/security-features/privilege-restriction/apparmor/).
The namespace setup uses the installed [Bubblewrap 0.9.0 implementation](https://github.com/containers/bubblewrap/blob/v0.9.0/bubblewrap.c).
The project still defines its own mounts, resource limits, syscall rules, authority, and lifecycle policy.

## Explicit migration

Execution schema version 4 adds durable process records.
Normal startup refuses execution versions 1 through 3 until explicitly migrated.
World storage remains version 2.
Stop the owning supervisor and back up its stopped execution database before migration.

```sh
execution=/absolute/execution/directory
sqlite3 -readonly "$execution/execution.db" 'PRAGMA user_version; PRAGMA quick_check;'
cp --no-clobber -- "$execution/execution.db" "$execution/execution-before-processes.backup.db"
/absolute/build/workspaced --state-dir "$execution" --migrate-v3
sqlite3 -readonly "$execution/execution.db" 'PRAGMA user_version; PRAGMA foreign_key_check;'
```

Use --migrate-v1 or --migrate-v2 when that is the current version.
Each explicit migration reaches version 4 and preserves existing records.
Repeated migration accepts the current schema without replaying commands.
Keep backups. Do not replace a database containing later operations with an older backup.

Inspect uncertain records with read-only queries:

```sh
sqlite3 -readonly "$execution/execution.db" \
  'SELECT id,actor,operation,workspace,state,exit_code FROM processes;'
```

Application setup and output logs are below applications/PROCESS_ID.
These private logs can contain workspace data. Do not commit them to Git.
Do not infer successful completion from output files or reuse host PIDs as durable identities.

## Qualification

Pending filesystem and Git effects keep the broker's authority loop active.
Filesystem flushes run in a joined worker within the existing broker resource boundary.
SQLite operations remain on the broker thread.
The broker completes each flush before recording ready state or acknowledging its effect.
An error still leaves the operation failed or uncertain under its existing recovery rules.

During these waits, managed world channels can request checked access and application control.
They can release control without waiting for the administrative effect to finish.
The broker rejects nested execution, management, and grant mutations with BUSY.
The caller can retry after the pending effect returns its checked result.
Normal authorization, native identity checks, and in-flight revocation checks still apply.
This is an explicit scheduling contract. It does not grant additional actor authority.

M03 contention qualification and its full phase gate remain pending.

Both hosts passed Debug and ASan/UBSan qualification of the final source.
Each build passed all 138 P00-P08 acceptance cases, 199 core checks, and 43 harness self-tests.
There are zero assertion failures, errors, or skips. Thirteen later acceptance cases remain unrun.
All 25 P08 primitives pass. P08 is complete for automated software qualification.

Twelve new process checks use real children, filesystem effects, namespace identities, and kernel status.
They cover descriptor closure, host paths, abstract sockets, syscall restrictions, exits, timeouts, retries, and read-only aliases.
They also cover session independence, detached descendants, revocation, supervisor failure, and explicit migration.
Actor disconnect preserves its application. Revocation terminates affected execution without terminating another actor's application.
Restart preserves completed records and refuses replay of uncertain operations.

An isolated build on sb16 deliberately replaced read-only mounts with writable mounts.
The locked acceptance witness detected the unauthorized write through the real child's nonzero exit code.
The qualified source remained unchanged during that experiment.

Evidence is in evidence/processes-2026-10-07.
It preserves the baseline failure, focused runs, full qualification, mutation result, and installed bootstrap hashes.
All 102 source hashes match reviewed local files and both execution hosts.
The bootstrap installer and its installed files have separate verified hashes.
Each final run records exact commands, build identity, and the private XLibre server hash.
Qualification uses tools/qualify_core.py --through P07 followed by tools/gate.py --through P08.
Sanitizer runs add --sanitize --build-dir build-sanitize.
The tests use authenticated disposable software displays. XLibre remains a private Debug build.
The acceptance lock, locked files, and public protobuf schemas remain unchanged.
Local-model review remains waived under David's hardware-failure instruction.

P08 results do not establish full Spatial-M1, M03, M04, or hardware acceptance.
All remaining milestones in MILESTONES.md remain mandatory.
Next: P09 crash boundaries and effect recovery.
