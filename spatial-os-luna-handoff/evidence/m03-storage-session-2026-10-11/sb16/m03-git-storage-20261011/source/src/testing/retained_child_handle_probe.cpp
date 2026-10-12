#include "supervisor/process_scope.hpp"
#include <chrono>
#include <fcntl.h>
#include <iostream>
#include <poll.h>
#include <stdexcept>
#include <sys/syscall.h>
#include <thread>
#include <unistd.h>

namespace {
void require(bool okay, const char* message) {
    if (!okay) throw std::runtime_error(message);
}
void rejected(const spatial::supervisor::ProcessScope& scope) {
    int fd = -1;
    try { fd = scope.duplicate_child_pidfd(); }
    catch (const std::exception&) { return; }
    close(fd);
    throw std::runtime_error("Scope exported an inactive child handle");
}
}
// Test-only orchestration. All process ownership and cleanup use ProcessScope.
int main(int argc, char** argv) {
    int empty = -1, duplicate = -1;
    try {
        require(argc == 3, "Expected delegated root and scenario");
        const std::string scenario = argv[2];
        require(scenario == "prepared" || scenario == "live" || scenario == "stopped" ||
            scenario == "exited" || scenario == "descendant", "Unknown handle scenario");
        auto scope = spatial::supervisor::ProcessScope::prepare(argv[1]);
        if (scenario == "prepared") { rejected(*scope); scope->stop(); return 0; }
        empty = open("/dev/null", O_RDWR | O_CLOEXEC);
        require(empty >= 0, "Cannot open child streams");
        spatial::supervisor::v1::AuthorityProcessLaunch request;
        request.set_executable("/usr/bin/python3"); request.add_arguments("-c");
        request.add_arguments(scenario == "descendant" ?
            "import os,time; p=os.fork(); time.sleep(30) if p==0 else None" :
            scenario == "exited" ? "pass" : "import time; time.sleep(30)");
        scope->start(request, {empty, empty, empty});
        if (scenario == "stopped") { scope->stop(); rejected(*scope); }
        else if (scenario == "live") {
            duplicate = scope->duplicate_child_pidfd();
            require((fcntl(duplicate, F_GETFD) & FD_CLOEXEC) != 0, "Exported handle is not close-on-exec");
            require(syscall(SYS_pidfd_send_signal, duplicate, 0, nullptr, 0) == 0,
                "Exported descriptor is not a live process handle");
            close(duplicate); duplicate = -1;
            duplicate = scope->duplicate_child_pidfd();
            // Closing one exported handle must preserve the original owned handle.
            require(syscall(SYS_pidfd_send_signal, duplicate, 0, nullptr, 0) == 0,
                "Closing exported handle changed scope ownership");
            scope->stop();
            pollfd event{duplicate, POLLIN, 0};
            require(poll(&event, 1, 1000) == 1 && (event.revents & POLLIN),
                "Exported handle did not observe owned child cleanup");
        } else {
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
            while (!scope->state().direct_child_exited()) {
                require(std::chrono::steady_clock::now() < deadline, "Fixture child did not exit");
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            }
            if (scenario == "descendant") require(scope->state().populated(), "Expected surviving descendant");
            rejected(*scope); scope->stop();
        }
        if (duplicate >= 0) close(duplicate);
        close(empty);
        return 0;
    } catch (const std::exception& error) {
        if (duplicate >= 0) close(duplicate);
        if (empty >= 0) close(empty);
        std::cerr << "retained child handle check failed: " << error.what() << '\n';
        return 2;
    }
}
