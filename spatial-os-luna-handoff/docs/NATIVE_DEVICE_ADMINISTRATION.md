# Shared device administration policy

This component contributes to M04. Full device authority remains incomplete.

## Invariant

Ordinary application and broker connections cannot configure shared keyboard or pointer devices.
Workspace membership does not grant device administration.
Supervisor and compositor connections retain their existing service authority.
Server-internal calls preserve their existing behavior.
Native request connections cannot choose the internal null-client path.

## Checked requests

Deny ChangeKeyboardMapping and ChangeKeyboardControl to ordinary applications and brokers.
Deny Bell, ChangePointerControl, SetPointerMapping, and SetModifierMapping to those roles.
Return native BadAccess before configuration changes or bell effects.
Keep public keyboard, pointer-button, and modifier mapping reads available.

The device callback checks management or bell access intent before inspecting the core opcode.
A previous client opcode must not change authorization for asynchronous reads or event delivery.
The callback preserves existing private actor-seat denials.
A privileged return does not erase another callback's denial.

## Qualification

The independent witness sends real X11 requests to disposable project servers.
Credential setup and completion fences use Protocol Buffers.
Tests compare real control state before and after denied changes.
Positive controls read actual mapping tables and configure devices through trusted services.
Keep locked P00-P10 assertions unchanged.
Require Debug and native-server sanitizer checks on both authorized hosts.
Detect isolated bypass mutations for each of the six denied requests.

## Remaining authority work

This component does not authorize observation of private actor input state.
Application input binding, per-device observation, focus, pointer coordinates, grabs, and event metadata remain unqualified.
The current actor-seat read policy still needs application identity checks.
Interaction leases and application input identities need broker/compositor integration.
Public mapping reads are not permission to inspect held keys or another actor's focus.
Do not expose untrusted native sessions before the complete M04 boundary passes.
Physical input and GLX provider cleanup still need separate qualification.
