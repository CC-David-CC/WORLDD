#include "x11/input.hpp"
#include "core/protocol.hpp"
#include "platform/compositor_config.hpp"
#include "x11/native_channel.hpp"
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
unsigned little32(const char* bytes) {
    unsigned result = 0;
    for (unsigned i = 0; i < 4; ++i) result |= unsigned(static_cast<unsigned char>(bytes[i])) << (8*i);
    return result;
}
}
NativeInput::NativeInput(int sealed_config_fd) : scoped_(std::make_unique<NativeChannel>()) {
    CompositorConfig sealed(sealed_config_fd);
    const auto& config = sealed.value();
    workspace::v1::NativeLaunchConfig connection;
    connection.set_display_number(config.display_number()); connection.set_broker_cookie(config.compositor_cookie());
    connection.set_server_id(config.server_id()); connection.set_server_pid(config.server_pid());
    connection.set_server_uid(config.server_uid());
    try { scoped_->connect_server(connection, security::v1::COMPOSITOR); }
    catch (...) { explicit_bzero(connection.mutable_broker_cookie()->data(), connection.broker_cookie().size()); throw; }
    explicit_bzero(connection.mutable_broker_cookie()->data(), connection.broker_cookie().size());
    sealed.consume();
    std::string query("\x0d\0\0\0", 4); query += "SPATIAL-INPUT";
    const auto [header, body] = scoped_->request(98, query);
    if (!header[8] || static_cast<unsigned char>(header[9]) < 128 || !body.empty())
        throw std::runtime_error("The X server does not provide SPATIAL-INPUT");
    opcode_ = static_cast<unsigned char>(header[9]);
}
NativeInput::~NativeInput() = default;
const std::string& NativeInput::server_id() const {
    if (!scoped_) throw std::logic_error("Native input has no scoped server identity");
    return scoped_->server_id();
}
const std::string& NativeInput::credential_id() const {
    if(!scoped_)throw std::logic_error("Native input has no scoped credential identity");
    return scoped_->credential_id();
}
NativeInput::NativeInput(Display* display) : display_(display) {
    int event = 0, error = 0;
    if (!XQueryExtension(display_, "SPATIAL-INPUT", &opcode_, &event, &error))
        throw std::runtime_error("The X server does not provide SPATIAL-INPUT");
}
native_input::Reply NativeInput::call(native_input::Request request) {
    if (sequence_ == UINT64_MAX) throw std::runtime_error("Native input sequence exhausted");
    request.set_protocol_version(1); request.set_request_id(++sequence_);
    return exchange(request);
}
native_input::Reply NativeInput::exchange(const native_input::Request& request) {
    validate_message(request);
    const auto bytes = request.SerializeAsString();
    if (bytes.size() > bound) throw std::invalid_argument("Native input request exceeds bound");
    if (scoped_) {
        try {
            std::string payload;
            for (unsigned i = 0; i < 4; ++i) payload += static_cast<char>(bytes.size() >> (8*i));
            payload += bytes;
            const auto [header, body] = scoped_->request(opcode_, std::move(payload));
            const auto size = little32(header.data()+8);
            if (size > bound || body.size() != ((size+3)&~std::size_t(3)))
                throw std::runtime_error("Invalid native input reply framing");
            native_input::Reply result;
            if (!result.ParseFromArray(body.data(), size) || result.protocol_version() != 1 ||
                result.request_id() != request.request_id()) throw std::runtime_error("Invalid native input reply identity");
            validate_message(result);
            return result;
        } catch (...) { scoped_->reset(); throw; }
    }
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
