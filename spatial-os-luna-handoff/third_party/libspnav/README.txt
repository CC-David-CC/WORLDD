Project-private libspnav 1.1

Upstream: https://github.com/FreeSpacenav/libspnav
Tag v1.1 resolves to commit 1716ccf15fa59a3acaeb9805262fc23613fd6a40.
The four upstream source files retain their original license notices.
LICENSE supplies the notice for binary distributions.

Local correction
Failed socket creation or daemon connection frees the event queue and resets its pointers.
The configuration file closes before either success or failure leaves its parser.
Native sockets use SOCK_CLOEXEC.
The API and native daemon protocol remain unchanged.
spnav_config.h selects the native protocol used by SpaceMouseSource.
The X11 Magellan compatibility API is not built.

Reproducer
Source149 repeated capture epochs fail LeakSanitizer on both test hosts.
The installed libspnav1.1-2 allocates an event queue before a failed connection.
Its close function rejects cleanup when no connection opened.
Evidence: continuous-capture-results-153/ASan-UBSan/capture-presentation.stderr.
Keep the system library unchanged. The project statically links this private copy.
Sanitizers remain enabled for this source.

Upstream source integrity
spnav.c 5355b3aac173fdca9a68fa8de901aa1e320a250296e67ae30c8e231d55a0ddc9
spnav.h bc1f776bb78ea15ee95735d6c71b76062a2f4053ba21dfb73c87cc06d47e94dc
proto.c 6133e1e323466319c56f21462f3c8d6c7d1e16659b90e61a7c5f3520f866e5bd
proto.h 66dab2623eba5918d6019345c50078d8e1fdef4aed9d6f922d708136045a47a9
