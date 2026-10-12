#include "workspace/storage_owner.hpp"
#include "workspace/storage_channel.hpp"
#include "core/protocol.hpp"
#include "identity/tls_support.hpp"
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <iostream>
#include <stdexcept>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {
int descriptor(const char* input) {
    std::size_t used = 0; const std::string text(input); const auto value = std::stoi(text, &used);
    if (used != text.size() || value < 3) throw std::invalid_argument("Invalid private storage descriptor");
    return value;
}
}
int main(int argc, char** argv) {
    try {
        int channel = -1; std::string configuration;
        for (int i = 1; i < argc; ++i) {
            const std::string argument(argv[i]);
            if (argument == "--channel-fd" && channel < 0 && i+1 < argc) channel = descriptor(argv[++i]);
            else if (argument == "--config" && configuration.empty() && i+1 < argc) configuration = argv[++i];
            else throw std::invalid_argument("Invalid storage owner arguments");
        }
        if (getuid() || geteuid() || channel < 3 || configuration.empty())
            throw std::invalid_argument("Storage owner requires operator configuration and a private channel");
        umask(0077); signal(SIGPIPE, SIG_IGN);
        spatial::identity::tls_detail::Fd file(open(configuration.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC));
        struct stat status{};
        if (file.value < 0 || fstat(file.value, &status) < 0 || !S_ISREG(status.st_mode) || status.st_uid != 0 ||
            status.st_nlink != 1 || (status.st_mode & 0077) || status.st_size < 1 || status.st_size > static_cast<off_t>(spatial::max_frame_bytes))
            throw std::invalid_argument("Storage configuration must be a private operator-owned bounded file");
        std::string bytes(static_cast<std::size_t>(status.st_size), '\0'); std::size_t used = 0;
        while (used < bytes.size()) {
            const auto count = pread(file.value, bytes.data()+used, bytes.size()-used, static_cast<off_t>(used));
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) throw std::runtime_error("Incomplete storage configuration");
            used += static_cast<std::size_t>(count);
        }
        spatial::workspace::v1::StorageOwnerConfig config;
        if (!config.ParseFromString(bytes)) throw std::invalid_argument("Invalid storage configuration protobuf");
        spatial::validate_storage_policy(config);
        spatial::StorageChannel transport(channel, config.broker_uid(), static_cast<pid_t>(config.broker_pid()));
        if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0) throw std::runtime_error("Cannot protect storage owner privilege");
        spatial::StorageOwner owner(std::move(config));
        while (const auto frame = transport.receive(spatial::StorageChannel::Clock::time_point::max(), true)) {
            spatial::workspace::v1::StorageOwnerRequest request;
            spatial::workspace::v1::StorageOwnerReply reply;
            if (request.ParseFromString(*frame)) reply = owner.handle(request);
            else {
                reply.set_protocol_version(1);
                reply.mutable_error()->set_code(spatial::workspace::v1::StorageOwnerError::INVALID_ARGUMENT);
                reply.mutable_error()->set_message("Invalid storage protobuf");
            }
            transport.send(reply.SerializeAsString(), spatial::StorageChannel::Clock::now()+std::chrono::seconds(5));
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "spatial-storage-owner: " << error.what() << '\n';
        return 1;
    }
}
