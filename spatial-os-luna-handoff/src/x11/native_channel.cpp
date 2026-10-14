#include "x11/native_channel.hpp"
#include "core/protocol.hpp"
#include "world/store.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
namespace spatial {
namespace {
namespace native_security = ::spatial::security::v1;
using Clock = std::chrono::steady_clock;
struct Fd {
    int value = -1;
    ~Fd() { reset(); }
    void reset() { if (value >= 0) close(value); value = -1; }
};
[[noreturn]] void unavailable() { throw WorldError(wire::UNAVAILABLE, "Native application authority channel is unavailable"); }
void u16(std::string& bytes, unsigned value) { bytes += static_cast<char>(value); bytes += static_cast<char>(value >> 8); }
void u32(std::string& bytes, unsigned value) { u16(bytes, value); u16(bytes, value >> 16); }
unsigned read16(const char* bytes) { return static_cast<unsigned char>(bytes[0]) | (static_cast<unsigned char>(bytes[1]) << 8); }
std::uint32_t read32(const char* bytes) { return read16(bytes) | (std::uint32_t(read16(bytes+2)) << 16); }
void pad(std::string& bytes) { while (bytes.size() % 4) bytes += '\0'; }

}
struct NativeChannel::Impl {
    Fd socket_path, channel;
    std::string number, server_id, credential_id;
    void connect_server(const workspace::v1::NativeLaunchConfig& config, native_security::Role role);
    unsigned opcode = 0, native_sequence = 0;
    std::uint64_t sequence = 0;

    void transfer(char* bytes, std::size_t size, bool output, Clock::time_point deadline) {
        std::size_t used = 0;
        while (used < size) {
            if (channel.value < 0 || Clock::now() >= deadline) unavailable();
            const auto count = output ? send(channel.value, bytes+used, size-used, MSG_NOSIGNAL) : recv(channel.value, bytes+used, size-used, 0);
            if (count > 0) { used += static_cast<std::size_t>(count); continue; }
            if (!count || (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)) unavailable();
            pollfd target{channel.value, static_cast<short>(output ? POLLOUT : POLLIN), 0};
            if (poll(&target, 1, 10) < 0 && errno != EINTR) unavailable();
        }
    }
    std::string receive(std::size_t size, Clock::time_point deadline) {
        std::string bytes(size, '\0'); transfer(bytes.data(), size, false, deadline); return bytes;
    }
    std::pair<std::string, std::string> request(unsigned major, std::string payload) {
        pad(payload);
        std::string data; data += static_cast<char>(major); data += '\0'; u16(data, 1+payload.size()/4); data += payload;
        const unsigned expected = ++native_sequence & 65535;
        const auto deadline = Clock::now()+std::chrono::seconds(3);
        transfer(data.data(), data.size(), true, deadline);
        for (unsigned count = 0; count < 128; ++count) {
            auto header = receive(32, deadline);
            if (static_cast<unsigned char>(header[0]) == 34) continue; // Unsolicited core MappingNotify.
            if (header[0] != 1 || read16(header.data()+2) != expected || read32(header.data()+4) > 1024) unavailable();
            auto body = receive(static_cast<std::size_t>(read32(header.data()+4))*4, deadline);
            return {std::move(header), std::move(body)};
        }
        unavailable();
    }
    native_security::Reply call(native_security::Request request) {
        try {
            request.set_protocol_version(1); request.set_request_id(++sequence);
            validate_message(request);
            auto bytes = request.SerializeAsString();
            if (bytes.size() > 4096) unavailable();
            std::string payload; u32(payload, bytes.size()); payload += bytes;
            auto [header, body] = this->request(opcode, std::move(payload));
            const auto size = read32(header.data()+8);
            if (size > 4096 || body.size() != ((size+3)&~std::size_t(3))) unavailable();
            native_security::Reply reply;
            if (!reply.ParseFromArray(body.data(), size) || reply.protocol_version() != 1 || reply.request_id() != sequence) unavailable();
            validate_message(reply); return reply;
        } catch (...) { channel.reset(); throw; }
    }
};

void NativeChannel::Impl::connect_server(const workspace::v1::NativeLaunchConfig& config, native_security::Role role) {
    number = std::to_string(config.display_number());
    const auto path = "/tmp/.X11-unix/X"+number;
    socket_path.value = open(path.c_str(), O_PATH | O_NOFOLLOW | O_CLOEXEC);
    struct stat info{};
    if (socket_path.value < 0 || fstat(socket_path.value, &info) || !S_ISSOCK(info.st_mode)) unavailable();
    channel.value = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (channel.value < 0) unavailable();
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    const auto pinned = "/proc/self/fd/"+std::to_string(socket_path.value);
    std::memcpy(address.sun_path, pinned.c_str(), pinned.size()+1);
    if (connect(channel.value, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) unavailable();
    ucred peer{}; socklen_t size = sizeof(peer);
    if (getsockopt(channel.value, SOL_SOCKET, SO_PEERCRED, &peer, &size) || size != sizeof(peer) ||
            peer.pid <= 0 || static_cast<unsigned>(peer.pid) != config.server_pid() || peer.uid != config.server_uid()) unavailable();
    std::string hello{"l\0", 2}; u16(hello, 11); u16(hello, 0); u16(hello, 18); u16(hello, 32); u16(hello, 0);
    hello += "MIT-MAGIC-COOKIE-1"; pad(hello); hello += config.broker_cookie();
    const auto deadline = Clock::now()+std::chrono::seconds(3);
    transfer(hello.data(), hello.size(), true, deadline);
    const auto header = receive(8, deadline);
    if (header[0] != 1 || read16(header.data()+2) != 11 || read16(header.data()+4) != 0) unavailable();
    receive(static_cast<std::size_t>(read16(header.data()+6))*4, deadline);
    std::string query; u16(query, 16); u16(query, 0); query += "SPATIAL-SECURITY";
    auto [reply, body] = request(98, query);
    if (!reply[8] || static_cast<unsigned char>(reply[9]) < 128 || !body.empty()) unavailable();
    opcode = static_cast<unsigned char>(reply[9]);
    native_security::Request inspect; inspect.mutable_inspect();
    const auto identity = call(inspect);
    if (identity.code() != native_security::OK || !identity.has_identity() || identity.identity().role() != role ||
            identity.identity().server_id() != config.server_id() || !identity.identity().workspace().empty() ||
            !identity.identity().application().empty()) unavailable();
    if(identity.identity().credential_id().size()!=32 || !identity.identity().connection_id())unavailable();
    server_id = identity.identity().server_id();credential_id=identity.identity().credential_id();
}

void NativeChannel::check() {
    try {
        if (impl_->channel.value < 0) unavailable();
        std::array<char, 32> bytes{};
        for (unsigned count = 0; count < 128; ++count) {
            const auto size = recv(impl_->channel.value, bytes.data(), bytes.size(), MSG_PEEK | MSG_DONTWAIT);
            if (size < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
            if (size < 0 && errno == EINTR) continue;
            if (size <= 0 || bytes[0] != 34) unavailable();
            if (size < 32) return;
            if (recv(impl_->channel.value, bytes.data(), bytes.size(), MSG_DONTWAIT) != 32) unavailable();
        }
        unavailable();
    } catch (...) { impl_->channel.reset(); throw; }
}
NativeChannel::NativeChannel() : impl_(std::make_unique<Impl>()) {}
NativeChannel::~NativeChannel() = default;
void NativeChannel::connect_server(const workspace::v1::NativeLaunchConfig& config, security::v1::Role role) {
    if (connected()) throw std::logic_error("Native channel already connected");
    try { impl_->connect_server(config, role); }
    catch (...) { reset(); throw; }
}
security::v1::Reply NativeChannel::call(security::v1::Request request) { return impl_->call(std::move(request)); }
std::pair<std::string, std::string> NativeChannel::request(unsigned major, std::string payload) {
    try { return impl_->request(major, std::move(payload)); }
    catch (...) { reset(); throw; }
}
void NativeChannel::reset() { impl_->channel.reset(); }
bool NativeChannel::connected() const { return impl_->channel.value >= 0; }
int NativeChannel::socket_fd() const { return impl_->socket_path.value; }
const std::string& NativeChannel::number() const { return impl_->number; }
const std::string& NativeChannel::server_id() const { return impl_->server_id; }
const std::string& NativeChannel::credential_id() const { return impl_->credential_id; }
}
