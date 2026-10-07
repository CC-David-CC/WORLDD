#include "x11/input.hpp"
#include "core/protocol.hpp"
#include <X11/Xlibint.h>
#include <array>
#include <cstring>
#include <stdexcept>
namespace spatial {
namespace {
struct RequestHeader { CARD8 opcode, operation; CARD16 length; CARD32 bytes; };
struct ReplyHeader { BYTE type, pad; CARD16 sequence; CARD32 length, bytes, unused[5]; };
static_assert(sizeof(RequestHeader) == 8 && sizeof(ReplyHeader) == 32);
constexpr std::size_t bound = 4096;
}
NativeInput::NativeInput(Display* display) : display_(display) {
    int event = 0, error = 0;
    if (!XQueryExtension(display_, "SPATIAL-INPUT", &opcode_, &event, &error))
        throw std::runtime_error("The X server does not provide SPATIAL-INPUT");
}
native_input::Reply NativeInput::exchange(const native_input::Request& request) {
    validate_message(request);
    const auto bytes = request.SerializeAsString();
    if (bytes.size() > bound) throw std::invalid_argument("Native input request exceeds bound");
    std::array<char, bound> payload{};
    ReplyHeader reply{};
    bool valid = false;
    Display* dpy = display_; // Xlib's locking macros require this name.
    LockDisplay(dpy);
    auto* header = static_cast<RequestHeader*>(_XGetRequest(dpy, opcode_, sizeof(RequestHeader) + ((bytes.size() + 3) & ~std::size_t(3))));
    if (header) {
        header->operation = 0;
        header->bytes = bytes.size();
        std::memset(header + 1, 0, (bytes.size() + 3) & ~std::size_t(3));
        std::memcpy(header + 1, bytes.data(), bytes.size());
        if (_XReply(dpy, reinterpret_cast<xReply*>(&reply), 0, False)) {
            if (reply.bytes <= bound && reply.length == (reply.bytes + 3) / 4) {
                _XReadPad(dpy, payload.data(), reply.bytes); valid = true;
            } else {
                _XEatDataWords(dpy, reply.length);
            }
        }
    }
    UnlockDisplay(dpy);
    SyncHandle();
    if (!valid) throw std::runtime_error("Invalid native input reply framing");
    native_input::Reply result;
    if (!result.ParseFromArray(payload.data(), reply.bytes) || result.protocol_version() != 1 ||
        result.request_id() != request.request_id()) throw std::runtime_error("Invalid native input reply identity");
    validate_message(result);
    return result;
}
}
