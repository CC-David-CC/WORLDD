#include "supervisor/process_scope.hpp"
#include "core/protocol.hpp"
#include <cerrno>
#include <csignal>
#include <cstddef>
#include <fcntl.h>
#include <iostream>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stdexcept>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {
// Probe-only kernel fault injection, installed after the production launch.
void deny_wait() {
    sock_filter filter[]{
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_wait4, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    sock_fprog program{static_cast<unsigned short>(sizeof(filter)/sizeof(filter[0])), filter};
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) || prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program))
        throw std::runtime_error("Cannot install diagnostic cleanup filter");
}
}
int main(int argc, char** argv) {
    namespace pb = spatial::supervisor::v1;
    std::signal(SIGPIPE, SIG_IGN);
    try {
        if (argc < 2) throw std::invalid_argument("Expected delegated root, optional fault mode, and explicit descriptors");
        const std::string mode = argc >= 3 ? argv[2] : "normal";
        std::vector<int> inherited;
        for (int i = 3; i < argc; ++i) inherited.push_back(std::stoi(argv[i]));
        const auto release_inherited = [&] {
            // The checked child owns its copies. Retained transport endpoints hide child EOF.
            for (const int descriptor : inherited) close(descriptor);
            inherited.clear();
        };
#ifdef SPATIAL_TEST_SECURITY_EVIDENCE_FORWARD
        if (mode != "normal" && mode != "deny-write" && mode != "deny-wait" &&
            mode != "test-security-evidence") throw std::invalid_argument("Unknown probe mode");
#else
        if (mode != "normal" && mode != "deny-write" && mode != "deny-wait") throw std::invalid_argument("Unknown probe mode");
#endif
        const int empty = open("/dev/null", O_RDWR | O_CLOEXEC);
        if (empty < 0) throw std::runtime_error("Cannot open private child streams");
        std::unique_ptr<spatial::supervisor::ProcessScope> process;
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            pb::ProcessScopeRequest request; pb::ProcessScopeReply reply; reply.set_protocol_version(1);
            try {
                if (!request.ParseFromString(bytes)) throw std::invalid_argument("Malformed scope request");
                reply.set_request_id(request.request_id()); spatial::validate_message(request);
                if (request.protocol_version() != 1) {
                    reply.mutable_error()->set_code(pb::ProcessScopeError::UNSUPPORTED_VERSION);
                } else if (request.has_launch()) {
                    if (process) { reply.mutable_error()->set_code(pb::ProcessScopeError::INVALID_STATE); }
                    else {
                        process = spatial::supervisor::ProcessScope::launch(argv[1], request.launch(), {empty, empty, empty}, inherited,
#ifdef SPATIAL_TEST_SECURITY_EVIDENCE_FORWARD
                            mode == "test-security-evidence"
#else
                            false
#endif
                        );
                        release_inherited();
                        *reply.mutable_state() = process->state();
                        if (mode == "deny-wait") deny_wait();
                        if (mode == "deny-write") {
                            // Deny only this owned kernel control. Broad write filtering breaks
                            // ASan's unrelated memory probes before cleanup can be exercised.
                            const auto file = std::filesystem::path(argv[1])/reply.state().identity().scope_name()/"cgroup.kill";
                            if (chmod(file.c_str(), 0400) != 0) throw std::runtime_error("Cannot deny owned kill control");
                        }
                    }
                } else if (request.has_prepare()) {
                    if (process) reply.mutable_error()->set_code(pb::ProcessScopeError::INVALID_STATE);
                    else {
                        process = spatial::supervisor::ProcessScope::prepare(argv[1]);
                        *reply.mutable_state() = process->state();
                    }
                } else if (request.has_prepare_budget()) {
                    if (process) reply.mutable_error()->set_code(pb::ProcessScopeError::INVALID_STATE);
                    else {
                        process = spatial::supervisor::ProcessScope::prepare(argv[1], &request.prepare_budget());
                        *reply.mutable_state() = process->state();
                    }
                } else if (request.has_start() && process) {
                    process->start(request.start(), {empty, empty, empty}, inherited);
                    release_inherited();
                    *reply.mutable_state() = process->state();
                } else if (request.has_state() && process) *reply.mutable_state() = process->state();
                else if (request.has_exit_status() && process) reply.mutable_exit_status()->set_code(process->exit_status());
                else if (request.has_stop() && process) *reply.mutable_stopped() = process->stop();
                else reply.mutable_error()->set_code(pb::ProcessScopeError::INVALID_STATE);
            } catch (const std::invalid_argument& error) {
                reply.mutable_error()->set_code(pb::ProcessScopeError::INVALID_ARGUMENT);
                reply.mutable_error()->set_message(error.what());
            } catch (const std::exception& error) {
                reply.mutable_error()->set_code(request.has_stop() ? pb::ProcessScopeError::CLEANUP_UNCONFIRMED : pb::ProcessScopeError::LAUNCH_FAILED);
                reply.mutable_error()->set_message(error.what());
            }
            spatial::write_frame(std::cout, reply);
            if (reply.has_error() && request.has_stop()) return 2;
        }
        close(empty); return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 2; }
}
