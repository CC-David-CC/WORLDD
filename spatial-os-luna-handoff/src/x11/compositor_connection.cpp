#include "x11/compositor_connection.hpp"
#include "core/protocol.hpp"
#include "security.pb.h"
#include <X11/Xlibint.h>
#include <xcb/xcb.h>
#include <array>
#include <cerrno>
#include <cstring>
#include <dlfcn.h>
#include <exception>
#include <poll.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace {
using Config = spatial::render::v1::NativeCompositorConfig;
struct File {
    int value = -1;
    ~File() { if (value >= 0) close(value); }
};
void require(bool okay, const char* message) {
    if (!okay) throw std::runtime_error(std::string("Native compositor configuration: ") + message);
}
struct Pending {
    const Config& config;
    const std::string& display;
    bool used = false;
    std::exception_ptr failure;
};
thread_local Pending* pending = nullptr;
thread_local bool startup_error = false;
int record_startup_error(Display*, XErrorEvent*) { startup_error = true; return 0; }

xcb_connection_t* authenticated_connection(Pending& value, const char* display, int* screen) {
    require(!value.used && display && display == value.display, "unexpected Xlib connection");
    value.used = true;
    File socket_fd{socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0)};
    require(socket_fd.value >= 0, "cannot allocate native socket");
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    const auto path = "/tmp/.X11-unix/X" + std::to_string(value.config.display_number());
    require(path.size() < sizeof(address.sun_path), "invalid local display");
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    require(connect(socket_fd.value, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) == 0,
            "cannot connect to configured server");
    ucred peer{}; socklen_t size = sizeof(peer);
    require(getsockopt(socket_fd.value, SOL_SOCKET, SO_PEERCRED, &peer, &size) == 0 && size == sizeof(peer) &&
            peer.pid == static_cast<pid_t>(value.config.server_pid()) && peer.uid == value.config.server_uid(),
            "native server process identity mismatch");
    // No authentication data has been sent before this exact socket is verified.
    char method[] = "MIT-MAGIC-COOKIE-1";
    xcb_auth_info_t auth{18, method, static_cast<int>(value.config.compositor_cookie().size()),
                        const_cast<char*>(value.config.compositor_cookie().data())};
    const int fd = socket_fd.value;
    socket_fd.value = -1; // xcb_connect_to_fd takes ownership, including failure.
    if (screen) *screen = 0;
    return xcb_connect_to_fd(fd, &auth);
}

spatial::security::v1::Identity inspect(Display* display, std::uint64_t sequence = 1) {
    int opcode = 0, event = 0, error = 0;
    require(XQueryExtension(display, "SPATIAL-SECURITY", &opcode, &event, &error), "native security extension absent");
    spatial::security::v1::Request request;
    request.set_protocol_version(1); request.set_request_id(sequence); request.mutable_inspect();
    const auto bytes = request.SerializeAsString();
    struct Header { CARD8 opcode, operation; CARD16 length; CARD32 bytes; };
    struct Reply { BYTE type, pad; CARD16 sequence; CARD32 length, bytes, unused[5]; } reply{};
    static_assert(sizeof(Header) == 8 && sizeof(Reply) == 32);
    constexpr std::size_t bound = 4096;
    std::array<char, bound> payload{};
    bool valid = false;
    Display* dpy = display;
    LockDisplay(dpy);
    auto* header = static_cast<Header*>(_XGetRequest(dpy, opcode, sizeof(Header) + ((bytes.size()+3) & ~std::size_t(3))));
    if (header) {
        header->operation = 0; header->bytes = bytes.size();
        std::memset(header + 1, 0, (bytes.size()+3) & ~std::size_t(3));
        std::memcpy(header + 1, bytes.data(), bytes.size());
        if (_XReply(dpy, reinterpret_cast<xReply*>(&reply), 0, False)) {
            if (reply.bytes <= bound && reply.length == (reply.bytes+3)/4) {
                _XReadPad(dpy, payload.data(), reply.bytes); valid = true;
            } else _XEatDataWords(dpy, reply.length);
        }
    }
    UnlockDisplay(dpy);
    SyncHandle();
    require(valid, "invalid native identity framing");
    spatial::security::v1::Reply result;
    require(result.ParseFromArray(payload.data(), reply.bytes) && result.protocol_version() == 1 &&
            result.request_id() == sequence, "invalid native identity reply");
    try { spatial::validate_message(result); }
    catch (const std::exception&) { require(false, "unsupported native identity fields"); }
    require(result.code() == spatial::security::v1::OK && result.has_identity(), "native identity denied");
    return result.identity();
}
}

// Xlib calls this public XCB entry point. Export only this symbol from spatialwm.
// The normal fixture path forwards to libxcb. The native path uses its exact verified socket.
extern "C" xcb_connection_t* xcb_connect_to_display_with_auth_info(
    const char* display, xcb_auth_info_t* auth, int* screen) {
    if (pending) {
        try { return authenticated_connection(*pending, display, screen); }
        catch (...) {
            pending->failure = std::current_exception();
            return xcb_connect_to_fd(-1, nullptr);
        }
    }
    using Function = xcb_connection_t* (*)(const char*, xcb_auth_info_t*, int*);
    static const auto original = reinterpret_cast<Function>(dlsym(RTLD_NEXT, "xcb_connect_to_display_with_auth_info"));
    return original ? original(display, auth, screen) : xcb_connect_to_fd(-1, nullptr);
}

namespace spatial {
security::v1::Identity inspect_compositor_identity(Display* display, std::uint64_t sequence) {
    require(sequence > 0, "invalid native identity sequence");
    const auto identity = inspect(display, sequence);
    require(identity.role() == security::v1::COMPOSITOR && identity.server_id().size() == 32 &&
        identity.connection_id() > 0 && identity.workspace().empty() && identity.application().empty(),
        "native witness requires compositor identity");
    return identity;
}
Display* open_compositor_display(const std::string& display, const Config& config) {
    require(!pending, "nested display startup");
    Pending value{config, display, false, {}};
    pending = &value;
    startup_error = false;
    const auto previous_handler = XSetErrorHandler(record_startup_error);
    struct Reset {
        XErrorHandler previous;
        ~Reset() { pending = nullptr; XSetAuthorization(nullptr, 0, nullptr, 0); XSetErrorHandler(previous); }
    } reset{previous_handler};
    // The library receives a non-secret placeholder. A missing adapter cannot leak the credential.
    char method[] = "MIT-MAGIC-COOKIE-1";
    std::array<char, 32> placeholder{};
    XSetAuthorization(method, 18, placeholder.data(), placeholder.size());
    Display* result = XOpenDisplay(display.c_str());
    try {
        if (value.failure) std::rethrow_exception(value.failure);
        require(value.used && result, "verified Xlib connection failed");
        const auto identity = inspect(result);
        XSync(result, False);
        require(!startup_error, "native startup request denied");
        require(identity.role() == security::v1::COMPOSITOR && identity.server_id() == config.server_id() &&
                identity.credential_id().size() == 32 && identity.connection_id() > 0 &&
                identity.workspace().empty() && identity.application().empty(), "native compositor identity mismatch");
        return result;
    } catch (...) {
        if (result) XCloseDisplay(result);
        throw;
    }
}
}
