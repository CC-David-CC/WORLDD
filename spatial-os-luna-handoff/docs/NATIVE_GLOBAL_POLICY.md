# Native global administration policy

This component contributes to M04. Full native isolation remains incomplete.

## Invariant

Ordinary applications and broker connections cannot administer shared X server state.
Supervisor and compositor connections retain the existing privileged service boundary.
Workspace membership and application identity do not grant server administration.
Unconfigured diagnostic servers preserve their trusted legacy behavior.
Production workspace sessions must enable strict native authentication.

## Checked operations

Applications cannot grab or explicitly release the entire server.
They cannot list or change host access controls.
They cannot read or replace shared font paths.
They cannot reap all retained temporary client resources.
They cannot read, change, or force shared screen-saver state.
Broker connections receive no general server administration rights.

Applications may list public font definitions through ListFonts and ListFontsWithInfo.
Configured shared font paths must contain public dependencies.
This exception does not authorize workspace files or arbitrary host paths.
Public font information remains real X server output.

## Enforcement

ServerAccessCallback checks the immutable connection role before server administration.
ScreenSaverAccessCallback checks shared screen-saver observation and control.
Both callbacks register and unregister with the native resource policy.

The maintained patch adds authorization before explicit UngrabServer.
It also adds authorization before KillClient's AllTemporary resource cleanup.
Internal grab release during client teardown remains available.
Host access control checks authority before the defeatAccessControl shortcut.
The -ac flag cannot bypass configured spatial administration authority.
Null internal host-access callers retain their existing server path.
Native requests always supply their authenticated connection.
A request cannot choose the internal null-caller exception.

These checks run before effects. A native denial returns BadAccess.
Actor authority and native service credentials remain separate boundaries.
Broker provisioning and compositor routing still require production integration.

## Independent evidence

The independent witness sends real X11 requests to disposable project servers.
Credential setup and completion fences use Protocol Buffers.
The witness checks actual font names and font information.
It also checks real retained-resource cleanup, both native byte orders, and privileged controls.
Record pre-patch failures and all corrected build attempts.
Run Debug and native-server sanitizers on both authorized hosts.
Use isolated mutations to test the callbacks and added request guards.
Keep locked P00-P10 assertions unchanged.

## Remaining work

This component does not complete global X isolation or M04.
Device authority, focus and pointer queries, atom privacy, and asynchronous metadata remain unqualified.
Private selection namespaces and broker/compositor integration remain required.
Focused GLX-disabled sanitizers do not qualify graphics-provider cleanup or physical sessions.
