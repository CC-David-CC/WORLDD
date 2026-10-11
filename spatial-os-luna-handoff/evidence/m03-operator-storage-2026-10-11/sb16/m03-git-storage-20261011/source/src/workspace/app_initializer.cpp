#include "workspace/app_initializer.hpp"
#include "core/protocol.hpp"
#include "broker.pb.h"
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <iostream>
#include <stdexcept>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace spatial {
namespace {
bool limit(int resource, rlim_t maximum) {
    rlimit value{maximum, maximum};
    return setrlimit(resource, &value) == 0;
}
}
int supervise_application(int state_directory) {
    namespace protocol = workspace::v1;
    signal(SIGPIPE, SIG_IGN);
    try {
        struct stat directory{};
        if (state_directory < 3 || fstat(state_directory, &directory) || !S_ISDIR(directory.st_mode))
            throw std::invalid_argument("Application state directory is unavailable");
        std::string bytes;
        if (!spatial::read_frame(std::cin, bytes)) return 2;
        protocol::AppStart request;
        if (!request.ParseFromString(bytes)) throw std::invalid_argument("Invalid application start frame");
        spatial::validate_message(request);
        if (request.protocol_version() != 1 || request.argv_size() == 0 || request.argv_size() > 256)
            throw std::invalid_argument("Invalid application start request");
        std::vector<std::string> strings(request.argv().begin(), request.argv().end());
        for (const auto& value : strings)
            if (value.find('\0') != std::string::npos) throw std::invalid_argument("NUL in argument");
        std::string executable = strings.front();
        if (executable.empty()) throw std::invalid_argument("Empty executable");
        // Preserve argv[0]. Resolve bare names using the fixed dependency path, without a shell.
        if (executable.find('/') == std::string::npos) executable = "/usr/bin/" + executable;
        std::vector<char*> argv;
        for (auto& value : strings) argv.push_back(value.data());
        argv.push_back(nullptr);
        int errors[2];
        if (pipe2(errors, O_CLOEXEC) != 0) throw std::runtime_error("Cannot create exec witness");
        const auto child = fork();
        if (child == 0) {
            close(errors[0]);
            const int input = open("/dev/null", O_RDONLY | O_CLOEXEC);
            const int output = openat(state_directory, "stdout", O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW, 0600);
            const int diagnostic = openat(state_directory, "stderr", O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW, 0600);
            int failure = 0;
            if (input < 0 || output < 0 || diagnostic < 0 || dup2(input, 0) < 0 || dup2(output, 1) < 0 || dup2(diagnostic, 2) < 0)
                failure = errno;
            if (!failure) {
                close(input); close(output); close(diagnostic);
                if (limit(RLIMIT_CORE, 0) && limit(RLIMIT_NOFILE, 256) && limit(RLIMIT_NPROC, 1024) &&
                    limit(RLIMIT_FSIZE, 64 * 1024 * 1024) && limit(RLIMIT_AS, 4ULL * 1024 * 1024 * 1024) && limit(RLIMIT_CPU, 120))
                    execv(executable.c_str(), argv.data());
                failure = errno;
            }
            while (write(errors[1], &failure, sizeof(failure)) < 0 && errno == EINTR) {}
            _exit(127);
        }
        close(errors[1]);
        if (child < 0) { close(errors[0]); throw std::runtime_error("Cannot fork application"); }
        int failure = 0;
        ssize_t read_count;
        do { read_count = read(errors[0], &failure, sizeof(failure)); } while (read_count < 0 && errno == EINTR);
        close(errors[0]);
        if (read_count != 0) {
            while (waitpid(child, nullptr, 0) < 0 && errno == EINTR) {}
            throw std::runtime_error("Application exec failed");
        }
        protocol::AppEvent event; event.set_protocol_version(1); event.set_started(true);
        spatial::write_frame(std::cout, event);
        int status = 0;
        while (waitpid(child, &status, 0) < 0) if (errno != EINTR) throw std::runtime_error("Application wait failed");
        event.set_exit_code(WIFEXITED(status) ? WEXITSTATUS(status) : -WTERMSIG(status));
        spatial::write_frame(std::cout, event);
        return 0;
    } catch (const std::exception& error) {
        protocol::AppEvent event; event.set_protocol_version(1); event.set_error(error.what());
        try { spatial::write_frame(std::cout, event); } catch (...) {}
        return 2;
    }
}
}
