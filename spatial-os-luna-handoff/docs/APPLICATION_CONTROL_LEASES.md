# Broker application control leases, version 1

The production broker owns an ephemeral control reservation for each acquired application group.
The group contains the verified native server, workspace, and application identities.
A private supervisor connection owns the reservations.
Ordinary actors and applications cannot use this private protocol directly.

The new protobuf operations are control_acquire and control_release.
A successful acquisition returns an ApplicationControlLease.
The lease contains a random 32-byte identity, actor principal, and application reference.
The broker uses the existing application-access policy before acquisition.
Observation, control, workspace access, and sufficient execution rights remain separate requirements.
A read-only actor cannot control a writable application.
An unowned or incorrectly attributed application cannot acquire a lease.

One application group has one reservation.
Independent applications can have different controllers.
Repeating acquisition for the same actor and active reservation returns the same lease.
Another actor receives BUSY until the existing reservation is released.
The broker permits at most 256 reservations on the private connection.
Revoked reservations count toward this bound until release.

The broker checks existing reservations using already prepared issuer and grant state.
These checks do not start recursive issuer requests or reap a borrowed process supervisor.
Issuer watch updates invalidate control while the actor is idle.
Checks also run during existing issuer and process waits.
Process exit invalidates the corresponding reservation after normal lifecycle reaping.

Control loss sends an unsolicited Reply.control_revoked frame with request_id zero.
The event contains the complete original lease.
The broker sends one revocation event for each lease.
The event can arrive between ordinary request and reply frames.
A client must process events while waiting for a reply.
Revocation can precede the acquisition reply when authority changes during that operation.
Clients must retain such revocations and must not treat a later matching acquisition reply as renewed authority.

Revocation does not free the reservation.
The trusted world must cancel held input, capture, drags, and focus first.
It then calls control_release with the lease identity and original actor principal.
Only this release permits another controller to acquire the application group.
The same principal may release a revoked lease, even after actor revocation.
Another principal cannot release it.
A released identity cannot release a subsequent lease.
Restored authority does not reactivate a revoked lease automatically.

The broker does not perform native input cancellation itself.
The release request is the trusted world's acknowledgement of cancellation.
Worldd must connect this protocol to checked native input routing before authenticated control is enabled.
The existing worldd input gate remains closed for labeled applications.
These tests certify broker reservations and notifications, not balanced native input release.

The broker preserves complete protobuf frames when replies and events share the output queue.
The queue is bounded to two maximum-sized frames of storage.
Transport failure remains fatal to the private authority connection.
The world must cancel its affected input when that connection fails.
Partial private requests do not stop issuer notifications.

Leases are not durable application state.
Do not restore them after a broker restart or copy them with a workspace.
Application execution authority remains independent.
Loss of observation or control does not terminate otherwise valid execution.

The schema preserves the 18 existing messages and 70 existing fields.
It adds four messages, two request operations, two reply outcomes, and one event outcome.
The core schema, native witness, and acceptance lock remain unchanged.
No XLibre patch or execution database migration is required.
M04, M09, and Spatial-M1 remain incomplete.
