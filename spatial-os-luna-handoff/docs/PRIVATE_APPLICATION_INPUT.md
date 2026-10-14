# Private application input

Authenticated application connections use a genuine DIX master pointer and keyboard pair.
The immutable workspace and application identity owns that pair.
Connections with the same identity share the pair, including separately issued credentials.
Application names alone do not establish identity across workspaces.

Assign the pair before accepting the connection's first native request.
Reject connection authentication if device allocation fails.
Do not fall back to shared core devices after allocation failure.
Initialize the private keyboard with no focus.
Preserve real application-local focus, grabs, pointer queries, and pointer warping.
Device access checks compare the application's private group with the device owner.
Supervisor and compositor roles retain trusted device access.
Unconfigured development displays retain their existing native actor behavior.

Each authenticated connection holds one reference to its application input group.
Closing one connection preserves the remaining connections' device state.
Closing the last connection destroys the pair and frees the group.
Device capacity is bounded by the native server's device registry.
An allocation failure must not prevent later allocation after devices are freed.

## Qualification

The native witness checks focus, held-key privacy, pointer state, and competing grabs.
It checks both native byte orders and same-application positive controls.
It opens and closes 140 application identities to detect unreclaimed devices.
It also fills the device registry and checks rejection and recovery.
The trusted fixture uses real XTEST only to establish shared held-key state.
Ordinary application XTEST access remains prohibited.
Protocol Buffers carry authentication setup and completion fences.
Native X requests and replies remain the actual X11 wire protocol.

The InputFocus resource-attack fixture correction is documented separately.
Its BadAccess assertion remains unchanged.

## Remaining integration

Private idle devices are the uncontrolled application's input context.
They do not complete actor-to-application input binding.
The next component must bind every application connection to its current actor's devices.
Transfer must release keys, buttons, grabs, modifiers, and focus first.
Return connections to private idle devices after cancellation or revocation.
Qualify late connections, shared dialogs, motion history, and asynchronous event metadata.
Do not claim M04 or native Spatial-M1 acceptance from private-device tests.
