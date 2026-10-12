#include "workspace/process.hpp"
#include "workspace/native_launch.hpp"
#include "workspace/resources.hpp"
#include "core/protocol.hpp"
#include <cerrno>
#include <cstddef>
#include <iostream>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
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
int main(int argc,char** argv) {
    try {
        if (argc != 6) throw std::invalid_argument("Expected project, repository, state, fault mode, and resource configuration");
        const std::string mode=argv[4];
        if (mode!="normal" && mode!="deny-wait" && mode!="deny-signal" && mode!="destructor-deny-wait" && mode!="destructor-deny-signal")
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
        spatial::ApplicationProcess process(argv[1],argv[2],argv[3],true,request,[] {},{},std::move(reservation));
        spatial::workspace::v1::AppEvent ready;ready.set_protocol_version(1);ready.set_started(true);
        spatial::write_frame(std::cout,ready);
        if (!spatial::read_frame(std::cin,bytes)) throw std::runtime_error("Missing stop request");
        spatial::wire::ActorStopRequest stop;
        if (!stop.ParseFromString(bytes)) throw std::invalid_argument("Invalid stop request");
        spatial::validate_message(stop);
        if (mode=="deny-wait" || mode=="destructor-deny-wait") deny(SYS_wait4);
        if (mode=="deny-signal" || mode=="destructor-deny-signal") { deny(SYS_pidfd_send_signal);deny(SYS_kill); }
        if (mode.starts_with("destructor-")) return 0;
        try { process.stop(); process.stop(); }
        catch (const std::exception& error) {
            std::cerr << "cleanup unconfirmed: " << error.what() << '\n';
            // The kernel denies cleanup for this fixture process. Exit without pretending success.
            _exit(2);
        }
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n';return 3; }
}
