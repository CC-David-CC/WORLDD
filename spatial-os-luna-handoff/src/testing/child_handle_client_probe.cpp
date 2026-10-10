#include "supervisor/child_handle_client.hpp"
#include "core/protocol.hpp"
#include <filesystem>
#include <iostream>
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>

// Independent sender fixture. The production receiver performs all validation.
int main(int argc, char** argv) {
    const auto descriptor_count = [] {
        std::size_t count = 0;
        for (const auto& entry : std::filesystem::directory_iterator("/proc/self/fd")) { (void)entry; ++count; }
        return count;
    };
    const auto before = descriptor_count();
    int endpoints[]{-1, -1}; pid_t child = -1; int result = 2;
    try {
        if (argc != 3) throw std::runtime_error("Expected witness and behavior");
        spatial::supervisor::v1::AuthorityChildHandleRequest request; std::string bytes;
        if (!spatial::read_frame(std::cin, bytes) || !request.ParseFromString(bytes)) throw std::runtime_error("Missing expected fixture launch");
        if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, endpoints)) throw std::runtime_error("Cannot create witness endpoint");
        child = fork(); if (child < 0) throw std::runtime_error("Cannot start handle witness");
        if (child == 0) {
            if (setpgid(0, 0) < 0) _exit(126);
            close(endpoints[0]);
            if (dup2(endpoints[1], 9) < 0 || fcntl(9, F_SETFD, 0) < 0) _exit(126);
            if (endpoints[1] != 9) close(endpoints[1]);
            execlp("python3", "python3", argv[1], "9", argv[2], nullptr); _exit(127);
        }
        if (setpgid(child, child) < 0 && getpgid(child) != child) throw std::runtime_error("Cannot isolate witness process group");
        close(endpoints[1]); endpoints[1] = -1;
        auto pin = spatial::ProcessHandle::take(static_cast<int>(syscall(SYS_pidfd_open, child, 0)), child);
        spatial::supervisor::ChildHandleClient client(std::exchange(endpoints[0], -1), child, pin.descriptor());
        auto broker = client.broker(request.expected());
        if (broker.pid() == child || !broker.live()) throw std::runtime_error("Receiver returned an invalid broker process");
        result = 0;
    } catch (const std::exception& error) { std::cerr << "handle receiver rejected: " << error.what() << '\n'; }
    if (child > 0) {
        // The isolated group belongs to this unreaped fixture child. No runtime process enters it.
        if (getpgid(child) == child) kill(-child, SIGKILL);
        else kill(child, SIGKILL);
        while (waitpid(child, nullptr, 0) < 0 && errno == EINTR) {}
    }
    for (int fd : endpoints) if (fd >= 0) close(fd);
    if (descriptor_count() != before) { std::cerr << "handle receiver leaked a descriptor\n"; return 3; }
    return result;
}
