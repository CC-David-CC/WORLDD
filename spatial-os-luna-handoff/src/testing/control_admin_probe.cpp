#include "supervisor/control_admin.hpp"
#include "core/protocol.hpp"
#include <fcntl.h>
#include <chrono>
#include <poll.h>
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
        spatial::workspace::v1::ControlSupervisorReply reply;
        if(std::string(argv[2]).starts_with("async-")) {
            using Clock=std::chrono::steady_clock;
            const auto started=Clock::now();const auto token=client.begin(request);
            if(Clock::now()-started>std::chrono::milliseconds(100))throw std::runtime_error("Administrative begin blocked");
            bool busy=false,sync_busy=false,stale=false;
            try{client.begin(request);}catch(const std::exception& error){busy=std::string(error.what())=="Administrative operation already in progress";}
            try{client.exchange(request);}catch(const std::exception& error){sync_busy=std::string(error.what())=="Administrative operation already in progress";}
            try{client.poll(token+1);}catch(const std::exception& error){stale=std::string(error.what())=="Stale administrative operation";}
            if(!busy || !sync_busy || !stale || !client.pending())throw std::runtime_error("Administrative pending ownership escaped");
            try {
                for(;;) {
                    const auto before=Clock::now();auto result=client.poll(token);
                    if(Clock::now()-before>std::chrono::milliseconds(100))throw std::runtime_error("Administrative poll blocked");
                    if(result){reply=std::move(*result);break;}
                    ::poll(nullptr,0,2);
                }
            }catch(...) {
                if(!client.pending())throw std::runtime_error("Uncertain administrative operation lost ownership");
                if(std::string(argv[2])=="async-timeout" &&
                    (Clock::now()-started<std::chrono::milliseconds(4800) || Clock::now()-started>std::chrono::milliseconds(6200)))
                    throw std::runtime_error("Administrative polling reset its original deadline");
                throw;
            }
            if(client.pending())throw std::runtime_error("Completed administrative operation kept its stream busy");
            if(std::string(argv[2])=="async-delay" && Clock::now()-started<std::chrono::milliseconds(300))
                throw std::runtime_error("Independent administrative delay was not exercised");
        }else reply=client.exchange(request);
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
