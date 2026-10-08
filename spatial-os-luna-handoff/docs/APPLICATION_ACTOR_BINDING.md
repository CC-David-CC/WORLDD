# Native application input binding

An application group uses its controlling actor's genuine native input devices.
All connections with the same immutable workspace/application identity share that binding.
An application without a controller uses its private idle devices.

The trusted compositor acquires the binding through an accepted SPATIAL-INPUT button press.
The server validates the target generation, coordinates, button transition, and existing capture first.
A service window cannot acquire an application binding.
A second actor receives BUSY when another actor controls that application.
A rejected acquisition preserves the acquiring actor's existing application and held input.
Different applications can have different simultaneous controllers.
Dialogs and other application windows use the same group binding.

Cancellation sends held releases through DIX before it clears the binding.
It releases active grabs and clears native focus, modifier locks, latches, and motion history.
Connections then return to their application's private idle pair.
The server preserves core passive grab definitions and retargets their device references.
Actor closure cannot leave a passive grab pointing at a removed device.
Actor teardown disables the complete device group before removal.
It publishes one hierarchy update after removing all four devices.
Intermediate hierarchy updates can dereference a freed master through its surviving partner.
Late application connections inherit the current binding.
Target invalidation, controller disconnect, and target credential revocation cancel affected bindings.

This change extends the server's existing protobuf action interface.
It does not change protobuf schemas or locked acceptance contracts.
Native observations use real X11 requests, replies, and events.
No synthetic application event transport replaces DIX delivery.

## Independent checks

tests/native/test_actor_binding.py checks device identity, focus, held input, and native releases.
It covers application groups, simultaneous controllers, rejected acquisitions, transfer, cancellation, and connection lifecycle.
It also covers swapped clients, modifier cleanup, motion history, active grabs, frozen input, and passive grabs.
The preceding server fails 24 of these 25 checks on both hosts.
Its application-local focus control passes.

GetPointerEvents records fresh motion-history samples for button events too.
The first draft incorrectly required an empty history after a new binding's click.
The final check records the previous binding's last timestamp and queries only that time interval.
It requires an empty result for that previous interval.
The prior failure remains in evidence.
The first sanitizer run also found that actor-teardown hierarchy use-after-free.
The local teardown now follows the pinned server's grouped removal order.
The failure remains in evidence. No sanitizer suppression was added.
The next run exposed stale pointer tracking during simultaneous-controller teardown.
Keyboard routing now preserves the shared sprite window because keyboard devices cannot send pointer crossings.
Native pointer processing remains responsible for pointer-window transitions.

## Remaining integration

The compositor remains trusted to submit authorized actor actions.
This server component does not implement workspace grants or application-profile intersection.
Production broker, worldd, and compositor integration must enforce those rules before acquisition.
Distributed revocation, authorized observation, physical input, and native GPU sessions need separate qualification.
M04, M09, and Spatial-M1 remain incomplete.
