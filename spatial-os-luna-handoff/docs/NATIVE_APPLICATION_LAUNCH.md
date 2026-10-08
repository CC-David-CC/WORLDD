# Confined native application launch

The production broker can issue native credentials for its confined applications.
The application identity is the broker's allocated application process ID.
The workspace identity comes from the authorized launch request.
The caller cannot choose a separate native application label.

## Trusted configuration

Start workspaced with `--native-security-fd N` to enable native launches.
Descriptor N must contain a fully sealed regular memfd, with one NativeLaunchConfig protobuf message.
The configuration is private supervisor input. It is not an actor operation or world definition.
Require protocol version 1, display number, broker cookie, server generation, server PID, and server UID.
The cookie and generation each contain 32 bytes.
The display number is at most 65535. Explicit display zero is valid.
Reject unknown fields, missing fields, wrong versions, unsealed files, and files larger than 4096 bytes.
Read from offset zero and close the inherited configuration descriptor.
Keep this message and its credential outside Git and persistent workspace state.

The supervisor must keep the native connection that issued the broker credential alive.
Use a BROKER credential. A SUPERVISOR credential is rejected at this boundary.
Pin the selected filesystem socket with O_PATH and reject symlinks or non-socket files.
Connect through the pinned descriptor and verify SO_PEERCRED against the configured server PID and UID.
Authenticate with the native X11 cookie mechanism.
Require SPATIAL-SECURITY and the expected server generation and broker role.
Do not fall back to an ambient display, host-access rule, or another credential.
Disable process dumps before reading the secret configuration.

Native request framing has a three-second deadline and bounded replies.
Validate native sequence numbers, protobuf versions, request IDs, fields, and issued identity labels.
Drain bounded native MappingNotify events without accepting unrelated replies.
Malformed replies or channel loss close the authority channel.
The configured broker then stops its process groups instead of switching to headless execution.

## Application boundary

Apply the existing workspace, actor, and application-profile authority intersection before issuing a credential.
Check actor authority before and after the bounded native request.
Complete that request before processing a later actor-authority denial.
Revoke only the tentative credential when the second check denies launch.
Do not close the shared issuer connection for an actor-specific denial.
The native exchange can delay the second check by at most three seconds.
Do not deliver the credential or start the application before that check succeeds.
Issue one APPLICATION credential through the broker's private native connection.
Put its native Xauthority record in a sealed anonymous file.
Bubblewrap copies this record into a read-only private mount at /run/spatial/Xauthority.
Mount only the pinned X socket at /tmp/.X11-unix/XN.
Set DISPLAY and XAUTHORITY to those explicit application-local endpoints.
Keep the application's private network namespace and existing syscall, process, and filesystem restrictions.
Do not expose the broker connection or supervisor cookie.
The application supervisor closes inherited mount and configuration descriptors before application execution.
Ordinary Xlib clients can then create real native windows with immutable application identities.

Credentials stay in memory and the application namespace's temporary mounts.
The broker does not write them to project files, runtime database rows, or persistent application state.
An application can read its own credential. It cannot issue further credentials through the native extension.
Each launch receives a distinct credential and application identity, including launches in the same workspace.

## Lifetime and failure

Application completion, execution revocation, and failed exec release the application's native credential.
The broker reaps completed processes while idle. A process.wait request is not required for cleanup.
Revocation closes established native connections and rejects future authentication with that credential.
An unrelated application's credential remains valid.
Broker disconnection revokes its child credentials through the existing server ownership rule.
Loss or revocation of the broker's native connection stops configured execution.
Launch retries retain the recorded application identity and do not replay completed execution or reissue expired credentials.
Keep recorded exit status available after the process and native credential have been reclaimed.

## Qualification and remaining work

tests/native/test_native_launch.py uses real workspaced, Bubblewrap namespaces, Python, Xlib, and the patched native server.
It independently reads native identities, tests credential reuse, and observes revoked connections.
It checks read-only project authority, private sockets, empty inherited descriptor sets, and configuration rejection.
Its near-exhaustion test leaves one credential slot and proves failed exec releases that slot.
The first implementation failed idle exit cleanup. That failure remains evidence.
Review also found that an actor-authority exception could close the shared issuer connection.
Two additional checks reproduced that defect and verified the correction on both hosts.
They exercise the production native client and independently authenticate an unrelated credential.
The expanded suite passed all 19 checks on both hosts.
Each host passed 707 core checks across 36 suites, 51 harness checks, and 151 P10 cases.
Those results passed separately in Debug and runtime ASan/UBSan builds.
Each native-server ASan/UBSan profile passed 195 focused checks with GLX disabled and leak detection enabled.
No final executed profile skipped a case.

This adds an optional trusted bootstrap interface. It does not change locked acceptance contracts.
The existing headless mode still runs without a native configuration.
Automatic session credential provisioning and compositor/worldd application-control authority still require integration.
Authenticated compositor resolution of surface application labels remains required.
Physical devices, native GPU output, instrumented GLX lifecycle, and native performance need separate qualification.
M04, M09, and Spatial-M1 remain incomplete.
