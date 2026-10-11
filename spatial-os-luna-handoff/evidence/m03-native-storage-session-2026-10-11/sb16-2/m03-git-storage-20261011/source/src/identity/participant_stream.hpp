#pragma once
#include "core/protocol.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <deque>
#include <fcntl.h>
#include <optional>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace spatial::identity {
// Private, bounded, nonblocking protobuf frames. Takes ownership of fd.
class ParticipantStream final {
    using Clock = std::chrono::steady_clock;
    struct Output { std::string bytes; Clock::time_point deadline; };
    int fd_ = -1;
    std::string input_;
    std::size_t expected_ = 4, sent_ = 0, queued_bytes_ = 0;
    Clock::time_point input_deadline_;
    std::deque<Output> output_;
    static void require(bool okay, const char* message) {
        if (!okay) throw std::runtime_error(message);
    }
public:
    explicit ParticipantStream(int fd, pid_t expected_peer) : fd_(fd) {
        try {
            require(fd >= 3 && expected_peer > 0, "Invalid private participant descriptor");
            struct stat info{}; sockaddr_un address{}; socklen_t length = sizeof(address);
            int type = 0; socklen_t type_length = sizeof(type);
            ucred peer{}; socklen_t peer_length = sizeof(peer);
            require(fstat(fd_, &info) == 0 && S_ISSOCK(info.st_mode) &&
                getpeername(fd_, reinterpret_cast<sockaddr*>(&address), &length) == 0 && address.sun_family == AF_UNIX &&
                getsockopt(fd_, SOL_SOCKET, SO_TYPE, &type, &type_length) == 0 && type == SOCK_STREAM &&
                getsockopt(fd_, SOL_SOCKET, SO_PEERCRED, &peer, &peer_length) == 0 &&
                peer_length == sizeof(peer) && peer.pid == expected_peer && peer.uid == geteuid(),
                "Private participant channel has an unexpected kernel peer");
            const int flags = fcntl(fd_, F_GETFL);
            require(flags >= 0 && fcntl(fd_, F_SETFL, flags | O_NONBLOCK) == 0 &&
                fcntl(fd_, F_SETFD, FD_CLOEXEC) == 0, "Cannot configure private participant channel");
        } catch (...) { if (fd_ >= 3) close(fd_); fd_ = -1; throw; }
    }
    ~ParticipantStream() { if (fd_ >= 0) close(fd_); }
    ParticipantStream(const ParticipantStream&) = delete;
    ParticipantStream& operator=(const ParticipantStream&) = delete;
    int descriptor() const { return fd_; }
    bool pending_output() const { return !output_.empty(); }
    void send(std::string bytes) {
        require(bytes.size() <= spatial::max_frame_bytes && output_.size() < 256 &&
            queued_bytes_+bytes.size()+4 <= 2*spatial::max_frame_bytes,
            "Private participant output limit exceeded");
        const auto size = static_cast<std::uint32_t>(bytes.size());
        std::string header(4, '\0');
        for (unsigned i = 0; i < 4; ++i) header[i] = static_cast<char>(size >> (24-8*i));
        header += bytes; queued_bytes_ += header.size();
        output_.push_back({std::move(header), Clock::now()+std::chrono::seconds(5)});
    }
    void flush() {
        std::size_t budget = 65536;
        while (!output_.empty() && budget) {
            auto& front = output_.front();
            require(Clock::now() < front.deadline, "Private participant output deadline exceeded");
            const auto count = ::send(fd_, front.bytes.data()+sent_, std::min(budget, front.bytes.size()-sent_), MSG_NOSIGNAL);
            if (count < 0 && errno == EINTR) continue;
            if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
            require(count > 0, "Private participant output closed");
            sent_ += static_cast<std::size_t>(count); budget -= static_cast<std::size_t>(count);
            if (sent_ == front.bytes.size()) { queued_bytes_ -= front.bytes.size(); output_.pop_front(); sent_ = 0; }
        }
    }
    std::optional<std::string> receive() {
        std::array<char,4096> bytes{};
        for (int pass = 0; pass < 16; ++pass) {
            require(input_.empty() || Clock::now() < input_deadline_, "Private participant frame deadline exceeded");
            if (input_.size() == expected_) {
                if (expected_ == 4) {
                    std::uint32_t size = 0;
                    for (unsigned i = 0; i < 4; ++i) size = (size << 8) | static_cast<unsigned char>(input_[i]);
                    require(size <= spatial::max_frame_bytes, "Excessive private participant frame");
                    expected_ = 4+size;
                }
                if (input_.size() == expected_) {
                    auto result = input_.substr(4); input_.clear(); expected_ = 4; return result;
                }
            }
            const auto count = recv(fd_, bytes.data(), std::min(bytes.size(), expected_-input_.size()), 0);
            if (count < 0 && errno == EINTR) continue;
            if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return {};
            require(count > 0, input_.empty() ? "Private participant channel closed" : "Truncated private participant frame");
            if (input_.empty()) input_deadline_ = Clock::now()+std::chrono::seconds(5);
            input_.append(bytes.data(), static_cast<std::size_t>(count));
        }
        return {};
    }
};
}
