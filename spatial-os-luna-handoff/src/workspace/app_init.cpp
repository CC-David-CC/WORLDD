#include "workspace/app_initializer.hpp"
#include "core/protocol.hpp"
#include "broker.pb.h"
#include <csignal>
#include <fcntl.h>
#include <iostream>
#include <stdexcept>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

int main() {
    namespace protocol = spatial::workspace::v1;
    signal(SIGPIPE, SIG_IGN);
    int state = -1;
    try {
        if (syscall(SYS_close_range, 3u, ~0u, 0u) != 0) throw std::runtime_error("Cannot close inherited mount descriptors");
        if (prctl(PR_SET_DUMPABLE, 0) != 0 || prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1)
            throw std::runtime_error("Missing application supervisor confinement");
        state = open("/state", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        const auto result = spatial::supervise_application(state);
        if (state >= 0) close(state);
        return result;
    } catch (const std::exception& error) {
        if (state >= 0) close(state);
        protocol::AppEvent event; event.set_protocol_version(1); event.set_error(error.what());
        try { spatial::write_frame(std::cout, event); } catch (...) {}
        return 2;
    }
}
