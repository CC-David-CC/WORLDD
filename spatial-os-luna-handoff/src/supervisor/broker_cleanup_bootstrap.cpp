#include "supervisor/broker_cleanup_bootstrap.hpp"
#include <arpa/inet.h>
#include <algorithm>
#include <cstdint>
#include <cerrno>
#include <cstring>
#include <poll.h>
#include <filesystem>
#include <sys/stat.h>
#include <stdexcept>
#include <sys/socket.h>
#include <unistd.h>
#include <array>
#include <google/protobuf/unknown_field_set.h>

namespace spatial::supervisor {
namespace {
void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
constexpr std::size_t kMaxFrame = 4096;
struct Packet {
    v1::BrokerCleanupBootstrapFrame frame;
    int fd = -1;
    std::size_t descriptor_count = 0;
    pid_t pid = -1;
    uid_t uid = static_cast<uid_t>(-1);
    Packet() = default;
    ~Packet() { if (fd >= 0) close(fd); }
    Packet(Packet&& other) noexcept : frame(std::move(other.frame)), fd(std::exchange(other.fd,-1)),
        descriptor_count(other.descriptor_count), pid(other.pid), uid(other.uid) {}
    Packet& operator=(Packet&& other) noexcept { if(this!=&other){if(fd>=0)close(fd);frame=std::move(other.frame);
        fd=std::exchange(other.fd,-1);descriptor_count=other.descriptor_count;pid=other.pid;uid=other.uid;}return *this;}
    Packet(const Packet&) = delete;
    Packet& operator=(const Packet&) = delete;
};
void validate_frame(const v1::BrokerCleanupBootstrapFrame& frame,
                    v1::BrokerCleanupBootstrapFrame::Kind kind) {
    require(frame.protocol_version() == 1 && frame.correlation_id().size() == 32 &&
            frame.kind() == kind && frame.ByteSizeLong() <= 128 &&
            frame.GetReflection()->GetUnknownFields(frame).empty(),
            "Invalid broker cleanup bootstrap frame");
}
using Clock = std::chrono::steady_clock;
void wait_socket(int socket, short events, Clock::time_point deadline) {
    for (;;) {
        const auto now=Clock::now(); require(now<deadline,"Broker cleanup bootstrap deadline expired");
        const auto left=std::chrono::duration_cast<std::chrono::milliseconds>(deadline-now).count();
        const int timeout=static_cast<int>(std::max<std::int64_t>(1,left));
        pollfd event{socket,events,0}; int ready=poll(&event,1,timeout);
        if(ready<0&&errno==EINTR)continue;
        require(ready>0 && (event.revents&events),"Broker cleanup bootstrap timed out or peer closed");
        return;
    }
}
struct ReceivedRights {
    std::array<int, 8> descriptors{};
    std::size_t count = 0;
    bool overflow = false;
    ReceivedRights() { descriptors.fill(-1); }
    ~ReceivedRights() { for (std::size_t i=0;i<count;++i) if(descriptors[i]>=0) close(descriptors[i]); }
    void own(int fd) noexcept {
        if (count < descriptors.size()) descriptors[count++] = fd;
        else { close(fd); overflow = true; }
    }
    int release_one() noexcept {
        if (count != 1 || overflow) return -1;
        return std::exchange(descriptors[0],-1);
    }
};
void send_frame(int socket, const v1::BrokerCleanupBootstrapFrame& frame, int passed_fd,
                BrokerCleanupBootstrap::Deadline deadline) {
    require(Clock::now()<deadline,"Broker cleanup bootstrap deadline expired before encoding");
    validate_frame(frame, frame.kind());
    std::string payload; require(frame.SerializeToString(&payload) && payload.size() <= kMaxFrame - 4,
                                 "Cannot encode broker cleanup bootstrap frame");
    const auto length = htonl(static_cast<std::uint32_t>(payload.size()));
    std::string bytes(sizeof(length), '\0'); std::memcpy(bytes.data(), &length, sizeof(length)); bytes += payload;
    require(Clock::now()<deadline,"Broker cleanup bootstrap deadline expired while encoding");
    iovec part{bytes.data(), bytes.size()};
    msghdr message{}; message.msg_iov = &part; message.msg_iovlen = 1;
    alignas(cmsghdr) char control[CMSG_SPACE(sizeof(int))]{};
    if (passed_fd >= 0) {
        message.msg_control = control; message.msg_controllen = sizeof(control);
        cmsghdr* header = CMSG_FIRSTHDR(&message); header->cmsg_level = SOL_SOCKET;
        header->cmsg_type = SCM_RIGHTS; header->cmsg_len = CMSG_LEN(sizeof(int));
        std::memcpy(CMSG_DATA(header), &passed_fd, sizeof(passed_fd));
        message.msg_controllen = CMSG_SPACE(sizeof(int));
    }
    for (;;) {
        require(Clock::now()<deadline,"Broker cleanup bootstrap send deadline expired");
        ssize_t sent=sendmsg(socket,&message,MSG_NOSIGNAL|MSG_DONTWAIT);
        if(sent<0&&errno==EINTR)continue;
        if(sent<0&&(errno==EAGAIN||errno==EWOULDBLOCK)){wait_socket(socket,POLLOUT,deadline);continue;}
        require(sent==static_cast<ssize_t>(bytes.size()),"Broker cleanup bootstrap send failed");
        require(Clock::now()<deadline,"Broker cleanup bootstrap send completed after its deadline"); return;
    }
}
Packet receive_frame(int socket, BrokerCleanupBootstrap::Deadline deadline) {
    require(Clock::now()<deadline,"Broker cleanup bootstrap receive deadline expired before allocation");
    std::array<char,kMaxFrame> bytes{};
    iovec part{bytes.data(), bytes.size()}; msghdr message{}; message.msg_iov = &part; message.msg_iovlen = 1;
    alignas(cmsghdr) char control[CMSG_SPACE(sizeof(int) * 8) + CMSG_SPACE(sizeof(ucred))]{};
    message.msg_control = control; message.msg_controllen = sizeof(control);
    ssize_t count;
    for (;;) {
        wait_socket(socket,POLLIN,deadline);
        count=recvmsg(socket,&message,MSG_CMSG_CLOEXEC|MSG_DONTWAIT);
        if(count<0&&(errno==EINTR||errno==EAGAIN||errno==EWOULDBLOCK))continue;
        break;
    }
    require(count >= 0, "Broker cleanup bootstrap receive failed");
    const bool truncated = (message.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) != 0;
    ReceivedRights rights; // Own every kernel-delivered descriptor before any parsing can throw.
    Packet result; bool got_credentials = false; bool unknown = false;
    for (cmsghdr* item = CMSG_FIRSTHDR(&message); item; item = CMSG_NXTHDR(&message, item)) {
        if (item->cmsg_level != SOL_SOCKET) { unknown = true; continue; }
        if (item->cmsg_type == SCM_CREDENTIALS) {
            if (got_credentials || item->cmsg_len != CMSG_LEN(sizeof(ucred))) { unknown = true; continue; }
            ucred credentials{}; std::memcpy(&credentials, CMSG_DATA(item), sizeof(credentials));
            result.pid = credentials.pid; result.uid = credentials.uid; got_credentials = true;
        } else if (item->cmsg_type == SCM_RIGHTS) {
            if (item->cmsg_len < CMSG_LEN(sizeof(int)) ||
                (item->cmsg_len-CMSG_LEN(0))%sizeof(int)!=0) { unknown = true; continue; }
            const std::size_t n = (item->cmsg_len - CMSG_LEN(0)) / sizeof(int);
            const auto* values = reinterpret_cast<const int*>(CMSG_DATA(item));
            result.descriptor_count += n;
            for (std::size_t i=0; i<n; ++i) rights.own(values[i]);
        } else unknown = true;
    }
    require(count > 0 && !truncated && !rights.overflow,
        "Broker cleanup bootstrap packet or ancillary data was truncated or oversized");
    require(!unknown && got_credentials, "Broker cleanup bootstrap credentials are missing or malformed");
    const auto size = static_cast<std::size_t>(count);
    require(size >= sizeof(std::uint32_t), "Broker cleanup bootstrap frame is incomplete");
    std::uint32_t net_length = 0; std::memcpy(&net_length, bytes.data(), sizeof(net_length));
    const auto payload_size = static_cast<std::size_t>(ntohl(net_length));
    require(payload_size <= kMaxFrame - sizeof(net_length) && payload_size == size - sizeof(net_length),
            "Broker cleanup bootstrap length is invalid");
    require(result.frame.ParseFromArray(bytes.data()+sizeof(net_length), static_cast<int>(payload_size)),
            "Malformed broker cleanup bootstrap protobuf");
    require(Clock::now()<deadline,"Broker cleanup bootstrap deadline expired while decoding");
    if (result.descriptor_count==1) result.fd=rights.release_one();
    return result;
}
void check_sender(const Packet& packet, pid_t pid, uid_t uid) {
    require(packet.pid == pid && packet.uid == uid, "Broker cleanup bootstrap sender identity mismatch");
}
}
BrokerCleanupBootstrap::Received::~Received() { if (descriptor >= 0) close(descriptor); }
BrokerCleanupBootstrap::Received::Received(Received&& other) noexcept
    : frame(std::move(other.frame)), descriptor(std::exchange(other.descriptor,-1)), pid(other.pid), uid(other.uid) {}
BrokerCleanupBootstrap::Received& BrokerCleanupBootstrap::Received::operator=(Received&& other) noexcept {
    if (this != &other) { if (descriptor >= 0) close(descriptor); frame=std::move(other.frame);
        descriptor=std::exchange(other.descriptor,-1); pid=other.pid; uid=other.uid; } return *this;
}
void BrokerCleanupBootstrap::require_credentials(int fd) {
    const int enabled=1; require(setsockopt(fd,SOL_SOCKET,SO_PASSCRED,&enabled,sizeof(enabled))==0,
                                 "Cannot enable broker cleanup peer credentials");
}
void BrokerCleanupBootstrap::validate_state_root(int fd,const std::filesystem::path& configured_root) {
    require(configured_root.is_absolute() && configured_root==configured_root.lexically_normal(),
        "Configured broker state root is not canonical");
    struct stat path_info{}, fd_info{};
    require(stat(configured_root.c_str(),&path_info)==0 && S_ISDIR(path_info.st_mode) && fstat(fd,&fd_info)==0 &&
        S_ISDIR(fd_info.st_mode) && path_info.st_dev==fd_info.st_dev && path_info.st_ino==fd_info.st_ino,
        "Cleanup bootstrap descriptor differs from configured broker state");
    const auto submitted=std::filesystem::read_symlink("/proc/self/fd/"+std::to_string(fd));
    require(submitted==configured_root,"Cleanup bootstrap descriptor names a different state root");
}
BrokerCleanupBootstrap::Deadline BrokerCleanupBootstrap::deadline_after(std::chrono::milliseconds timeout) {
    return Clock::now()+timeout;
}
void BrokerCleanupBootstrap::send_request(int fd,const std::string& correlation,int directory_fd,Deadline deadline) {
    v1::BrokerCleanupBootstrapFrame frame; frame.set_protocol_version(1); frame.set_correlation_id(correlation);
    frame.set_kind(v1::BrokerCleanupBootstrapFrame::BIND_REQUEST); send_frame(fd,frame,directory_fd,deadline);
}
void BrokerCleanupBootstrap::send_request(int fd,const std::string& correlation,int directory_fd,
        std::chrono::milliseconds timeout) { send_request(fd,correlation,directory_fd,deadline_after(timeout)); }
BrokerCleanupBootstrap::Received BrokerCleanupBootstrap::receive_request(int fd,pid_t pid,uid_t uid,Deadline deadline) {
    auto packet=receive_frame(fd,deadline); check_sender(packet,pid,uid);
    validate_frame(packet.frame,v1::BrokerCleanupBootstrapFrame::BIND_REQUEST);
    require(Clock::now()<deadline,"Broker cleanup bootstrap deadline expired while validating request");
    require(packet.fd>=0 && packet.descriptor_count==1,"Broker cleanup bootstrap requires exactly one directory descriptor");
    Received result; result.frame=std::move(packet.frame); result.descriptor=std::exchange(packet.fd,-1);
    result.pid=packet.pid; result.uid=packet.uid; return result;
}
BrokerCleanupBootstrap::Received BrokerCleanupBootstrap::receive_request(int fd,pid_t pid,uid_t uid,
        std::chrono::milliseconds timeout) { return receive_request(fd,pid,uid,deadline_after(timeout)); }
void BrokerCleanupBootstrap::send_ack(int fd,const std::string& correlation,Deadline deadline) {
    v1::BrokerCleanupBootstrapFrame frame; frame.set_protocol_version(1); frame.set_correlation_id(correlation);
    frame.set_kind(v1::BrokerCleanupBootstrapFrame::BIND_ACK); send_frame(fd,frame,-1,deadline);
}
void BrokerCleanupBootstrap::send_ack(int fd,const std::string& correlation,std::chrono::milliseconds timeout) {
    send_ack(fd,correlation,deadline_after(timeout));
}
v1::BrokerCleanupBootstrapFrame BrokerCleanupBootstrap::receive_ack(int fd,pid_t pid,uid_t uid,
        Deadline deadline,const std::string& correlation) {
    auto packet=receive_frame(fd,deadline); check_sender(packet,pid,uid);
    validate_frame(packet.frame,v1::BrokerCleanupBootstrapFrame::BIND_ACK);
    require(Clock::now()<deadline,"Broker cleanup bootstrap deadline expired while validating acknowledgment");
    require(packet.fd<0 && packet.descriptor_count==0 && packet.frame.correlation_id()==correlation,
            "Broker cleanup bootstrap acknowledgment mismatch"); return packet.frame;
}
v1::BrokerCleanupBootstrapFrame BrokerCleanupBootstrap::receive_ack(int fd,pid_t pid,uid_t uid,
        std::chrono::milliseconds timeout,const std::string& correlation) {
    return receive_ack(fd,pid,uid,deadline_after(timeout),correlation);
}

}
