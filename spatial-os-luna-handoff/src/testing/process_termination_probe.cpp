#include "workspace/process.hpp"
#include "workspace/native_launch.hpp"
#include "workspace/resources.hpp"
#include "core/protocol.hpp"
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <linux/audit.h>
#include <iostream>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <thread>
#include <unistd.h>

// Thin fixture: call the real boundary, then make the kernel reject selected cleanup calls.
void deny(int syscall_number) {
    sock_filter instructions[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, static_cast<unsigned>(syscall_number), 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
    };
    sock_fprog program{4,instructions};
    if (prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0) != 0 || prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&program) != 0)
        throw std::runtime_error("Cannot install fixture syscall denial");
}
void deny_single_fd_poll() {
#if !defined(__x86_64__)
#error This fixture filter validates only the x86-64 seccomp argument layout.
#endif
    sock_filter instructions[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_poll, 1, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_ppoll, 0, 5),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, args[1]) + sizeof(unsigned int)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0, 0, 3),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, args[1])),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 1, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
    };
    // Arch mismatch kills; poll skips the ppoll check, ppoll falls through, and other calls jump to ALLOW (11).
    // A nonzero high nfds word or low word !=1 also jumps to ALLOW; exact nfds=1 reaches ERRNO (10).
    sock_fprog program{12, instructions};
    if (prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0) != 0 || prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&program) != 0)
        throw std::runtime_error("Cannot install fixture namespace-poll denial");
}

int main(int argc,char** argv) {
    try {
        if (argc != 6) throw std::invalid_argument("Expected project, repository, state, fault mode, and resource configuration");
        const std::string mode=argv[4];
        if (mode!="normal" && mode!="deny-wait" && mode!="deny-signal" && mode!="destructor-deny-wait" && mode!="destructor-deny-signal" && mode!="deny-namespace-poll" && mode!="wait-exit" && mode!="repeat-request-deadline")
            throw std::invalid_argument("Invalid fixture fault");
        std::string bytes;
        if (!spatial::read_frame(std::cin,bytes)) throw std::runtime_error("Missing launch request");
        spatial::wire::ProcessLaunchRequest request;
        if (!request.ParseFromString(bytes)) throw std::invalid_argument("Invalid launch request");
        spatial::validate_message(request);
        spatial::WorkspaceResources resources(argv[5]);
        spatial::workspace::v1::LaunchRequest launch;
        launch.set_workspace("fixture"); launch.set_actor("fixture"); *launch.mutable_command() = request;
        auto reservation = resources.prepare(launch, {argv[1], argv[2], argv[3]});
        const auto& owned = reservation->scope().identity();
        std::cerr << "termination fixture scope=" << owned.scope_name() << '\n';
        spatial::ApplicationProcess process(argv[1],argv[2],argv[3],true,request,[] {},{},std::move(reservation));
        spatial::workspace::v1::AppEvent ready;ready.set_protocol_version(1);ready.set_started(true);
        spatial::write_frame(std::cout,ready);
        if (!spatial::read_frame(std::cin,bytes)) throw std::runtime_error("Missing stop request");
        spatial::wire::ActorStopRequest stop;
        if (!stop.ParseFromString(bytes)) throw std::invalid_argument("Invalid stop request");
        spatial::validate_message(stop);
        if (mode=="deny-wait" || mode=="destructor-deny-wait") deny(SYS_wait4);
        if (mode=="deny-signal" || mode=="destructor-deny-signal") { deny(SYS_pidfd_send_signal);deny(SYS_kill); }
        if (mode=="deny-namespace-poll") deny_single_fd_poll();
        if (mode.starts_with("destructor-")) return 0;
        if (mode=="repeat-request-deadline") {
            deny(SYS_pidfd_send_signal); deny(SYS_kill);
            const auto first_deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
            try { process.request_stop(first_deadline); } catch (const std::exception&) {}
            std::this_thread::sleep_for(std::chrono::milliseconds(1500));
            try { process.request_stop(std::chrono::steady_clock::now()+std::chrono::seconds(2)); }
            catch (const std::exception&) {}
            try { process.complete_stop(std::chrono::steady_clock::now()+std::chrono::seconds(2)); }
            catch (const std::exception& error) {
                std::cerr << "repeat request deadline cleanup unconfirmed: " << error.what() << '\n';
                return 0; // The production destructor must retain the first deadline and fail closed.
            }
            throw std::runtime_error("Repeated stop request extended a failed cleanup deadline");
        }
        try {
            if (mode=="wait-exit" && !process.wait(2.0))
                throw std::runtime_error("Application did not exit before the fixture deadline");
            process.stop(); process.stop();
        }
        catch (const std::exception& error) {
            std::cerr << "cleanup unconfirmed: " << error.what() << '\n';
            // The kernel denies cleanup for this fixture process. Exit without pretending success.
            _exit(2);
        }
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n';return 3; }
}
