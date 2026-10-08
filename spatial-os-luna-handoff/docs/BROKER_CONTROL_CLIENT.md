# Checked broker control client

BrokerClient now supports application control acquisition, release, and revocation polling.
All operations use the existing private broker protobuf connection.
The broker remains the authority for application grants and reservations.
The client does not create an alternate authority store.

The client binds each returned lease to the requested actor and native application identity.
It rejects short identities, missing identities, substituted principals, and substituted applications.
It checks release acknowledgements against the existing reservation.
An application cannot receive a replacement lease identity before its previous reservation is released.

The receive buffer persists across requests and idle polls.
Partial frames do not block the caller.
Frames retain the 1 MiB limit and a ten-second assembly deadline.
Each pump processes at most 64 frames and reads at most 256 KiB.
The client bounds live reservations and queued revocation notifications to 256 each.
Unexpected replies, duplicate notifications, invalid identities, and transport failures close the authority channel.
Channel failure makes every local control-validity check false.

An unsolicited revocation must use request_id zero.
It must exactly match a known lease or the pending acquisition.
The client preserves an early revocation when the matching acquire reply arrives later.
A repeated acquisition cannot reactivate a revoked lease.
Revocation does not release the broker reservation.
The caller must cancel native input before it acknowledges release.

Consumers must call poll_control() while idle and from the existing broker wait callback.
Consumers must also poll while waiting for other components.
Poll notifications before each consequential application-control action.
control_active() reads local lease state. It does not refresh authority by itself.
Do not interpret a successful acquire reply as proof that its lease remains active.

Polling is safe from the broker wait callback.
The receive path retains the pending ordinary reply for the outer request.
Recursive broker requests are rejected.
Notification handlers must defer release requests until the active exchange ends.
Consumers must avoid recursive compositor requests while cancelling native input.
All client operations remain on the authority thread.

The transport tests use an independent protocol peer and the production C++ client.
The live tests use the same client with real issuer grants, confined processes, and the production broker.
The probe only forwards operations and exposes notifications.
It does not implement world state or authority decisions.

Worldd has not acquired these leases yet.
Authenticated native input remains disabled for labeled applications.
Worldd cancellation integration, filtered pixels, and full native-session qualification remain required.
This component does not certify native key, button, capture, drag, or focus release.
M04, M09, M10, and Spatial-M1 remain incomplete.
