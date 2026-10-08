# Broker application access, version 1

Workspaced checks application observation and control against persistent workspace authority.
The application must be a live broker-owned process on the verified native server.
Launching an application does not grant observation or control.

The private broker protobuf now includes ApplicationAccessRequest and ApplicationAccessReply.
ApplicationRef contains workspace, application, and native server identity.
The application value is the broker-allocated process identity.
The actor principal contains issuer and actor identity.
Only the trusted supervisor or world authority may use this inherited channel.
An ActorRef is not an authentication proof.
Ordinary applications must not receive the channel.

Each request contains one to 64 distinct application groups.
Workspace and application labels contain one to 256 bytes without NUL.
Native server identity contains exactly 32 bytes.
The broker rejects malformed or duplicate groups before it constructs decisions.
Results preserve request order and application identity.
Both observe and control have explicit protobuf presence, including false values.

The observation decision requires:

- A configured persistent authority for the workspace.
- A current, active actor identity and valid grant ancestry.
- Workspace access within workspace policy.
- APPLICATION_OBSERVE at scope components ["applications", application_id].
- A matching, live process record and in-memory process supervisor.
- The native server identity verified during the broker handshake.

Root workspace grants can cover application scopes through normal component-prefix rules.
An application grant does not authorize another application or grant workspace access.
Observation does not require filesystem or process-execution grants.
It explicitly authorizes observation, which remains separate from file access.

Control also requires APPLICATION_CONTROL at the same application scope.
The actor's current effective execution rights must cover every right recorded for the application.
Workspace policy limits those actor rights.
A read-only actor cannot control a writable application, even with an explicit control grant.
A read-only actor can control an application launched under the read-only profile when all other checks pass.
Current application profiles require project read and execution rights, with optional project write rights.
The broker rejects process records outside those supported rights combinations.

The broker prepares current issuer status once per requested workspace.
It computes final decisions after status exchanges and process lifecycle callbacks.
Cached authority decisions include exact application scope components.
Parent revocation invalidates dependent grants.
Independent grants remain valid.

Unknown, exited, unowned, nonnative, or incorrectly attributed applications return false decisions.
Restarting the broker does not restore authorization for an old process record.
Malformed requests and unavailable authority return errors instead of successful authorization.
Losing application observation or control does not itself terminate valid execution.
The existing process-authority reconciliation remains independent.

This reply is a decision snapshot. It is not a transferable grant or interaction lease.
Worldd must recheck authority before consequential actions and cancel affected input after revocation.
Worldd must also filter pixels and metadata using authorized application identities.
Those consumers remain the next integration step.
Authenticated actors still receive no native application surfaces in the current runtime.

The schema addition preserves all 14 existing broker messages and all 59 existing fields.
It adds four messages and operation/outcome field 106.
The existing core schema, locked tests, and acceptance lock remain unchanged.
No execution database migration or XLibre server patch is required.

The regression suite uses real issuer services, workspace grants, confined processes, and the native server.
It calls the private broker as a trusted test supervisor.
It does not qualify worldd actor-channel enforcement or real application control.
M04, M09, and Spatial-M1 remain incomplete.
