#include "supervisor/control_admin.hpp"
#include "core/protocol.hpp"
#include <fcntl.h>
#include <filesystem>
#include <iostream>
#include <signal.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

// Transport fixture only. The separate Python process provides adversarial replies.
int main(int argc, char** argv) {
    const auto descriptor_count = [] {
        std::size_t count = 0;
        for (const auto& entry : std::filesystem::directory_iterator("/proc/self/fd")) {
            (void)entry; ++count;
        }
        return count;
    };
    const auto initial_descriptors = descriptor_count();
    pid_t child = -1; int pin = -1;
    int sockets[]{-1, -1}; int result = 2;
    try {
        if (argc != 3) throw std::runtime_error("Expected witness path and behavior");
        std::string bytes; spatial::workspace::v1::ControlSupervisorRequest request;
        if (!spatial::read_frame(std::cin, bytes) || !request.ParseFromString(bytes))
            throw std::runtime_error("Expected administrative request");
        if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets))
            throw std::runtime_error("Cannot create fixture socket pair");
        child = fork();
        if (child < 0) throw std::runtime_error("Cannot start witness");
        if (child == 0) {
            close(sockets[0]);
            if (dup2(sockets[1], 9) < 0 || fcntl(9, F_SETFD, 0) < 0) _exit(126);
            if (sockets[1] != 9) close(sockets[1]);
            execlp("python3", "python3", argv[1], "9", argv[2], nullptr);
            _exit(127);
        }
        close(sockets[1]); sockets[1] = -1;
        pin = static_cast<int>(syscall(SYS_pidfd_open, child, 0));
        if (pin < 0) throw std::runtime_error("Cannot retain fixture child");
        const int owned_socket = sockets[0];
        sockets[0] = -1;
        spatial::supervisor::ControlAdmin client(owned_socket, child, pin);
        const auto reply = client.exchange(request);
        spatial::write_frame(std::cout, reply);
        result = 0;
    } catch (const std::exception& error) {
        std::cerr << "administrative transport rejected: " << error.what() << '\n';
    }
    if (pin >= 0) { syscall(SYS_pidfd_send_signal, pin, SIGKILL, nullptr, 0); close(pin); }
    else if (child > 0) kill(child, SIGKILL); // An unreaped fixture child cannot have a reused PID.
    if (child > 0) { int status; while (waitpid(child, &status, 0) < 0 && errno == EINTR) {} }
    for (int fd : sockets) if (fd >= 0) close(fd);
    if (descriptor_count() != initial_descriptors) {
        std::cerr << "administrative transport descriptor leak\n";
        return 3;
    }
    return result;
}
