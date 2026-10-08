# Persistent workspace authority

This additive contract connects workspace grants to authenticated selection and confined execution.
It does not complete Spatial-M1.
Both hosts passed Debug and ASan/UBSan qualification on 2026-10-07.
Evidence is in `evidence/workspace-authority-2026-10-07`.

## Provisioning and identity

Create the real workspace through the existing trusted execution broker.
Stop that broker before you provision its grant store.
Run `workspaced --state-dir PATH --init-authority SEED` with a private textproto seed.
`WorkspaceAuthoritySeed` identifies one registered workspace and one private grant directory.
It installs one to 64 roots in one grant-store transaction.
An actor request cannot install roots.

Run the broker with `--authority-config CONFIG` to use persistent grants.
`WorkspaceAuthorityConfig` selects a pinned issuer client and one to 64 workspace bindings.
Each binding opens an existing grant store for that exact workspace domain.
Missing stores, duplicate bindings, unsafe files, and mismatched domains fail startup.
The broker does not infer authority from Git branches, paths, world identity, or host names.

Worldd supplies an authenticated `ActorRef` through its private broker channel.
Access and launch requests must also contain the matching canonical actor name.
The name is `identity:ISSUER_HEX:ACTOR_HEX`.
Each hexadecimal identity contains 64 lowercase characters.
Actor channels cannot choose this identity or send generic broker management requests.

Bound workspaces reject static membership edits and requests without authenticated principals.
Authenticated requests cannot fall back to static membership in an unbound workspace.
Legacy fixture actors retain static membership behavior in unbound workspaces.
The private broker channel remains a trusted supervisor interface, not a public actor endpoint.

## Actor operations

World grant messages now accept an optional `workspace` selector.
An absent selector addresses the actor's current world.
A present selector addresses that exact workspace.
A present empty selector is invalid.
Listing returns only grants whose subject is the authenticated actor.
Pages contain at most 16 records.
Delegation and revocation require the current authority revision.
They consume the same sequence counter as other actor actions, including denied operations.

Workspace administration does not grant world authority.
World administration does not grant workspace authority.
Grant availability does not place geometry or make a workspace available in a world.
Workspace selection still requires explicit world availability.
Room changes keep the actor's selection.
Restart restores a selection only when current workspace authority permits it.

## Execution intersection

Whole-project process mounts require whole-workspace grants.
Narrow file scopes cannot authorize an entire project mount.
The current broker maps persistent grants to its existing process profile as follows.

| Broker capability | Required workspace grants |
| --- | --- |
| Select workspace | `WORKSPACE_ACCESS` |
| Read project | `FILE_BROWSE` and `FILE_READ` |
| Write project | `FILE_CREATE`, `FILE_EDIT`, `FILE_RENAME`, `FILE_MOVE`, `FILE_COPY`, `FILE_TRASH`, `FILE_RESTORE`, and `FILE_PERMANENT_DELETE` |
| Launch process | `PROCESS_EXECUTE` |

Intersect these capabilities with workspace policy and the requested application profile.
A launch requires selection, project read, and process execution authority.
The `read-only` profile removes project write authority.
The development profile permits writes only when the complete write bundle is available.
Unrestricted POSIX writes can delete files permanently, so that bundle includes permanent deletion authority.
Partial file grants remain useful for future mediated cabinet operations.
They do not create a partial writable mount in this increment.

The broker uses the existing real Git worktree and private bare repository.
It applies the same read or write boundary to their Git administration paths.
Namespace, syscall, resource, device, descriptor, and `no_new_privs` restrictions remain mandatory.
The process receives no generic host display or SSH access.
Application state remains inside its granted execution boundary.

## Revocation and failure

Each process record stores its launch principal and effective execution capabilities.
Selection authority is not a continuing process capability.
Losing selection or a session credential does not terminate an otherwise authorized process.
Losing a required file or execution capability cancels its process namespace.
Independent valid grants can preserve that capability.

The broker checks caller and ancestor identities through the pinned issuer service.
It gathers candidate identities before external I/O.
Multiple status batches must have the same security revision.
Unknown, foreign, and revoked identities cannot support a grant chain.
Credential revocation does not revoke the actor's grants.
Actor revocation invalidates dependent grants even when that ancestor has no world session.

Committed grant changes invalidate cached decisions.
The broker reconciles running processes before it sends the grant-change reply.
It consumes issuer events while idle, during partial requests, and during process startup or waits.
Issuer failure disables persistent authority and cancels affected execution.
It does not switch issuers or restart uncertain commands.

Execution records retain the existing schema version 4.
The additive launch protobuf stores the authenticated principal in the existing payload column.
Grants, workspace files, and completed process records survive broker restart.
Uncertain live effects are not replayed.
SQLite does not make issuer state, process effects, and files one transaction.

## Remaining work

Distributed revocation acknowledgement remains a separate requirement.
An issuer commit does not certify that every dependent host has cancelled its effects.
Native application observation and control remain denied for actors using persistent world authority.
Their workspace and X-server integration remains required.
Cabinet actions, nonempty enrollment profiles, remote actor sessions, and full Spatial-M1 qualification remain required.
